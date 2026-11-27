"""Validação do JWT de acesso, refresh rotativo com detecção de reuso, logout e Redis."""

import base64
import hashlib
import json
import time
import uuid

import jwt
import pytest

from tests.conftest import JWT_TEST_SECRET, bearer, login

REFRESH_TTL_SECONDS = 7 * 24 * 3600


def _claims(user, **overrides) -> dict:
    now = int(time.time())
    claims = {
        "sub": user.id,
        "role": user.role,
        "type": "access",
        "iat": now,
        "exp": now + 900,
        "jti": uuid.uuid4().hex,
    }
    claims.update(overrides)
    return {k: v for k, v in claims.items() if v is not None}


def _b64(data: dict) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _refresh(client, token):
    return client.post("/auth/refresh", json={"refresh_token": token})


def _logout(client, token):
    return client.post("/auth/logout", json={"refresh_token": token})


def _sha256_forms(token: str) -> set[str]:
    digest = hashlib.sha256(token.encode()).digest()
    return {digest.hex(), base64.urlsafe_b64encode(digest).rstrip(b"=").decode()}


def _dump_redis(redis_sync) -> list[tuple[str, str]]:
    """Todas as chaves e valores do fakeredis, em texto."""
    items = []
    for key in redis_sync.scan_iter("*"):
        kind = redis_sync.type(key).decode()
        if kind == "string":
            value = redis_sync.get(key)
        elif kind == "hash":
            value = redis_sync.hgetall(key)
        elif kind == "set":
            value = redis_sync.smembers(key)
        elif kind == "list":
            value = redis_sync.lrange(key, 0, -1)
        elif kind == "zset":
            value = redis_sync.zrange(key, 0, -1)
        else:
            value = b""
        items.append((key.decode(), repr(value)))
    return items


def _refresh_key(redis_sync, token: str) -> str:
    """Chave `refresh:{sha256}` do token. Aceita um namespace antes (ex.: `auth:refresh:{sha256}`)."""
    forms = _sha256_forms(token)
    for key, _ in _dump_redis(redis_sync):
        prefix, _, digest = key.rpartition(":")
        if (prefix == "refresh" or prefix.endswith(":refresh")) and digest in forms:
            return key
    raise AssertionError(f"chave refresh:{{sha256}} não encontrada; chaves: {_dump_redis(redis_sync)}")


# --------------------------------------------------------------------------- JWT inválido


def test_valid_forged_token_with_test_secret_is_accepted(client, make_user):
    """Sanidade: um token montado como o contrato manda é aceito (valida os testes abaixo)."""
    user = make_user("CLIENTE")
    token = jwt.encode(_claims(user), JWT_TEST_SECRET, algorithm="HS256")
    assert client.get("/users/me", headers=bearer(token)).status_code == 200


def test_expired_access_token_returns_401(client, make_user):
    user = make_user("CLIENTE")
    past = int(time.time()) - 3600
    token = jwt.encode(_claims(user, iat=past - 900, exp=past), JWT_TEST_SECRET, algorithm="HS256")
    assert client.get("/users/me", headers=bearer(token)).status_code == 401


def test_token_with_invalid_signature_returns_401(client, make_user):
    user = make_user("CLIENTE")
    token = jwt.encode(_claims(user), "outro-segredo-qualquer-0123456789-xyz", algorithm="HS256")
    assert client.get("/users/me", headers=bearer(token)).status_code == 401


def test_tampered_payload_returns_401(client, make_user):
    user = make_user("CLIENTE")
    header, _, signature = user.access_token.split(".")
    payload = _b64(_claims(user, role="ADMIN"))
    response = client.get("/admin/users", headers=bearer(f"{header}.{payload}.{signature}"))
    assert response.status_code == 401


def test_alg_none_token_returns_401(client, make_user):
    user = make_user("CLIENTE")
    token = f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64(_claims(user))}."
    assert client.get("/users/me", headers=bearer(token)).status_code == 401


@pytest.mark.filterwarnings("ignore:The HMAC key")  # chave de teste curta para HS512 é proposital
def test_other_hmac_algorithm_is_rejected(client, make_user):
    """Só HS256 é aceito (sem confusão de algoritmo)."""
    user = make_user("CLIENTE")
    token = jwt.encode(_claims(user), JWT_TEST_SECRET, algorithm="HS512")
    assert client.get("/users/me", headers=bearer(token)).status_code == 401


@pytest.mark.parametrize("token_type", ["refresh", None, "ACCESS"])
def test_wrong_token_type_returns_401(client, make_user, token_type):
    user = make_user("CLIENTE")
    token = jwt.encode(_claims(user, type=token_type), JWT_TEST_SECRET, algorithm="HS256")
    assert client.get("/users/me", headers=bearer(token)).status_code == 401


@pytest.mark.parametrize("sub", [None, "nao-e-uuid", "00000000-0000-0000-0000-000000000000"])
def test_token_with_missing_or_unknown_subject_returns_401(client, make_user, sub):
    user = make_user("CLIENTE")
    claims = _claims(user)
    claims.pop("sub")
    if sub is not None:
        claims["sub"] = sub
    token = jwt.encode(claims, JWT_TEST_SECRET, algorithm="HS256")
    assert client.get("/users/me", headers=bearer(token)).status_code == 401


def test_token_without_exp_returns_401(client, make_user):
    user = make_user("CLIENTE")
    claims = _claims(user)
    claims.pop("exp")
    token = jwt.encode(claims, JWT_TEST_SECRET, algorithm="HS256")
    assert client.get("/users/me", headers=bearer(token)).status_code == 401


# --------------------------------------------------------------------------- refresh


def test_refresh_rotates_and_returns_new_pair(client, make_user):
    user = make_user("CLIENTE")

    response = _refresh(client, user.refresh_token)

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) >= {"access_token", "refresh_token", "token_type", "expires_in"}
    assert body["refresh_token"] != user.refresh_token
    assert body["access_token"] != user.access_token
    assert body["expires_in"] == 900
    assert client.get("/users/me", headers=bearer(body["access_token"])).status_code == 200


def test_old_refresh_token_stops_working_after_rotation(client, make_user):
    user = make_user("CLIENTE")
    assert _refresh(client, user.refresh_token).status_code == 200

    assert _refresh(client, user.refresh_token).status_code == 401


def test_reuse_revokes_whole_family(client, make_user):
    user = make_user("CLIENTE")
    second = _refresh(client, user.refresh_token).json()["refresh_token"]

    assert _refresh(client, user.refresh_token).status_code == 401  # reuso detectado

    assert _refresh(client, second).status_code == 401  # família inteira revogada


def test_reuse_does_not_affect_other_sessions(client, make_user):
    user = make_user("CLIENTE")
    other_session = login(client, user.email).json()["refresh_token"]
    _refresh(client, user.refresh_token)
    _refresh(client, user.refresh_token)  # reuso na família 1

    assert _refresh(client, other_session).status_code == 200


def test_expired_refresh_token_returns_401(client, make_user, redis_sync):
    user = make_user("CLIENTE")
    key = _refresh_key(redis_sync, user.refresh_token)
    redis_sync.pexpire(key, 1)
    time.sleep(0.05)

    assert _refresh(client, user.refresh_token).status_code == 401


def test_refresh_ttl_is_seven_days(client, make_user, redis_sync):
    user = make_user("CLIENTE")
    ttl = redis_sync.ttl(_refresh_key(redis_sync, user.refresh_token))
    assert REFRESH_TTL_SECONDS - 120 <= ttl <= REFRESH_TTL_SECONDS


@pytest.mark.parametrize("token", ["", "token-inventado", "a" * 64])
def test_unknown_refresh_token_returns_401(client, make_user, token):
    make_user("CLIENTE")
    assert _refresh(client, token).status_code in (401, 422)
    if token:
        assert _refresh(client, token).status_code == 401


def test_access_token_cannot_be_used_as_refresh(client, make_user):
    """401, ou 422 se o JWT passar do tamanho máximo aceito para o refresh (ambos recusam)."""
    user = make_user("CLIENTE")
    assert _refresh(client, user.access_token).status_code in (401, 422)


def test_refresh_for_inactive_user_returns_401(client, make_user, set_user_fields):
    user = make_user("CLIENTE")
    set_user_fields(user.email, is_active=False)
    assert _refresh(client, user.refresh_token).status_code == 401


def test_refresh_uses_current_role_from_database(client, make_user, set_user_fields):
    user = make_user("LOJISTA")
    set_user_fields(user.email, role="ENTREGADOR")

    access = _refresh(client, user.refresh_token).json()["access_token"]

    assert jwt.decode(access, JWT_TEST_SECRET, algorithms=["HS256"])["role"] == "ENTREGADOR"


# --------------------------------------------------------------------------- Redis


def test_redis_stores_only_sha256_of_refresh_token(client, make_user, redis_sync):
    user = make_user("CLIENTE")

    dump = _dump_redis(redis_sync)
    key = _refresh_key(redis_sync, user.refresh_token)

    assert all(user.refresh_token not in k and user.refresh_token not in v for k, v in dump)
    value = dict(dump)[key]
    assert user.id in value  # refresh:{hash} -> {user_id, family_id}


def test_rotated_token_hash_is_removed_from_redis(client, make_user, redis_sync):
    user = make_user("CLIENTE")
    old_key = _refresh_key(redis_sync, user.refresh_token)
    new_token = _refresh(client, user.refresh_token).json()["refresh_token"]

    assert not redis_sync.exists(old_key)
    assert _refresh_key(redis_sync, new_token)
    assert all(new_token not in k and new_token not in v for k, v in _dump_redis(redis_sync))


# --------------------------------------------------------------------------- logout


def test_logout_returns_204_and_revokes_refresh(client, make_user, redis_sync):
    user = make_user("CLIENTE")
    key = _refresh_key(redis_sync, user.refresh_token)

    response = _logout(client, user.refresh_token)

    assert response.status_code == 204
    assert response.content == b""
    assert not redis_sync.exists(key)
    assert _refresh(client, user.refresh_token).status_code == 401


def test_logout_with_rotated_token_revokes_family(client, make_user):
    """Vítima faz logout com o refresh antigo (já rotacionado pelo atacante): o par novo morre."""
    user = make_user("CLIENTE")
    attacker_refresh = _refresh(client, user.refresh_token).json()["refresh_token"]

    assert _logout(client, user.refresh_token).status_code == 204

    assert _refresh(client, attacker_refresh).status_code == 401


def test_logout_keeps_other_sessions(client, make_user):
    user = make_user("CLIENTE")
    other_session = login(client, user.email).json()["refresh_token"]

    assert _logout(client, user.refresh_token).status_code == 204

    assert _refresh(client, other_session).status_code == 200


def test_logout_with_unknown_token_does_not_error(client):
    """Decisão: logout é idempotente; token desconhecido não pode gerar 5xx."""
    assert _logout(client, "token-inventado").status_code in (204, 401)
