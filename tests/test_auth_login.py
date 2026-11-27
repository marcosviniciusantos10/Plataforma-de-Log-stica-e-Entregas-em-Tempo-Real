"""Login (/auth/login), claims do JWT de acesso e anti força bruta."""

import jwt
import pytest

from tests.conftest import JWT_TEST_SECRET, STRONG_PASSWORD, bearer, login, unique_email

ACCESS_TTL_SECONDS = 15 * 60


def test_login_success_returns_token_pair(client, make_user):
    user = make_user("CLIENTE", do_login=False)

    response = login(client, user.email)

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) >= {"access_token", "refresh_token", "token_type", "expires_in"}
    assert body["token_type"].lower() == "bearer"
    assert body["expires_in"] == ACCESS_TTL_SECONDS
    assert body["access_token"] != body["refresh_token"]


def test_access_token_claims_follow_contract(client, make_user):
    user = make_user("LOJISTA")

    claims = jwt.decode(user.access_token, JWT_TEST_SECRET, algorithms=["HS256"])

    assert jwt.get_unverified_header(user.access_token)["alg"] == "HS256"
    assert claims["sub"] == user.id
    assert claims["role"] == "LOJISTA"
    assert claims["type"] == "access"
    assert claims["jti"]
    assert claims["exp"] - claims["iat"] == ACCESS_TTL_SECONDS


def test_each_login_gets_unique_jti_and_refresh(client, make_user):
    user = make_user("CLIENTE", do_login=False)
    first = login(client, user.email).json()
    second = login(client, user.email).json()

    jti = lambda t: jwt.decode(t, JWT_TEST_SECRET, algorithms=["HS256"])["jti"]  # noqa: E731
    assert jti(first["access_token"]) != jti(second["access_token"])
    assert first["refresh_token"] != second["refresh_token"]


def test_refresh_token_is_opaque_not_jwt(client, make_user):
    user = make_user("CLIENTE")
    assert user.refresh_token.count(".") != 2  # não é um JWT (header.payload.signature)
    assert len(user.refresh_token) >= 48


def test_login_email_is_case_insensitive(client, make_user):
    user = make_user("CLIENTE", do_login=False)
    assert login(client, user.email.upper()).status_code == 200


def test_wrong_password_and_unknown_email_return_identical_401(client, make_user):
    user = make_user("CLIENTE", do_login=False)

    wrong_password = login(client, user.email, "Senha-Errada-999")
    unknown_email = login(client, unique_email("ninguem"), STRONG_PASSWORD)

    assert wrong_password.status_code == 401
    assert unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()  # sem enumeração de usuários


def test_inactive_user_cannot_login(client, make_user, set_user_fields):
    """Decisão (mais seguro): usuário inativo recebe o MESMO 401 genérico do login."""
    user = make_user("CLIENTE", do_login=False)
    set_user_fields(user.email, is_active=False)

    inactive = login(client, user.email)
    generic = login(client, user.email, "Senha-Errada-999")

    assert inactive.status_code == 401
    assert "access_token" not in inactive.json()
    assert inactive.json() == generic.json()


def test_inactive_user_existing_access_token_is_rejected(client, make_user, set_user_fields):
    user = make_user("CLIENTE")
    set_user_fields(user.email, is_active=False)

    assert client.get("/users/me", headers=user.headers).status_code == 401


def test_login_blocked_with_429_after_5_failures(client, make_user):
    user = make_user("CLIENTE", do_login=False)

    for attempt in range(5):
        response = login(client, user.email, f"Senha-Errada-{attempt:03d}")
        assert response.status_code == 401, f"tentativa {attempt + 1}: {response.status_code}"

    assert login(client, user.email, "Senha-Errada-xyz").status_code == 429
    # Decisão (mais seguro): bloqueado inclusive com a senha correta.
    assert login(client, user.email, STRONG_PASSWORD).status_code == 429


def test_brute_force_limit_also_applies_to_unknown_email(client):
    """Mesmo comportamento para e-mail inexistente (não revela se o e-mail existe)."""
    email = unique_email("ninguem")
    for _ in range(5):
        assert login(client, email, "Senha-Errada-000").status_code == 401
    assert login(client, email, "Senha-Errada-000").status_code == 429


def test_login_fail_counter_always_has_ttl(client, make_user, redis_sync):
    """O contador de falhas sempre expira (janela de 15 min) e não expõe o e-mail na chave."""
    user = make_user("CLIENTE", do_login=False)
    for _ in range(3):
        login(client, user.email, "Senha-Errada-000")

    keys = [k.decode() for k in redis_sync.scan_iter("*login_fail*")]
    assert keys, "nenhuma chave de falha de login encontrada"
    for key in keys:
        assert 0 < redis_sync.ttl(key) <= 15 * 60, key
        assert user.email not in key


def test_brute_force_limit_is_per_email(client, make_user):
    victim = make_user("CLIENTE", do_login=False)
    other = make_user("CLIENTE", do_login=False)
    for _ in range(5):
        login(client, victim.email, "Senha-Errada-000")

    assert login(client, victim.email).status_code == 429
    assert login(client, other.email).status_code == 200


@pytest.mark.parametrize("payload", [{}, {"email": "x@example.com"}, {"password": "abcdefghijk"}])
def test_login_missing_fields_returns_422(client, payload):
    assert client.post("/auth/login", json=payload).status_code == 422


def test_users_me_returns_current_user_without_hash(client, make_user):
    user = make_user("ENTREGADOR")

    response = client.get("/users/me", headers=user.headers)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == user.id
    assert body["email"] == user.email
    assert body["role"] == "ENTREGADOR"
    assert "password" not in response.text
    assert "$argon2" not in response.text


def test_users_me_without_token_returns_401(client):
    assert client.get("/users/me").status_code == 401


def test_users_me_rejects_non_bearer_scheme(client, make_user):
    user = make_user("CLIENTE")
    response = client.get("/users/me", headers={"Authorization": f"Basic {user.access_token}"})
    assert response.status_code == 401


def test_users_me_with_garbage_token_returns_401(client):
    assert client.get("/users/me", headers=bearer("nao-e-um-jwt")).status_code == 401
