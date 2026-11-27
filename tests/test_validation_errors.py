"""Regressão do achado M1: respostas 422 não ecoam o valor enviado (senha, refresh, role).

Os casos de /auth/* rodam sem Postgres: a validação do corpo falha antes de qualquer acesso
ao banco ou ao Redis. O PATCH de role precisa de um ADMIN real (usa o Postgres de teste).
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app

SENTINEL = "SONDA-eco"  # marcador presente em todos os valores enviados


def _assert_no_echo(response, sent_values: list[str]) -> None:
    assert response.status_code == 422, response.text
    body = response.json()
    assert set(body) == {"detail"}
    assert body["detail"], "422 sem erros detalhados"
    for err in body["detail"]:
        assert "input" not in err
        assert {"type", "loc", "msg"} <= set(err)
    for value in sent_values:
        assert value not in response.text
    assert SENTINEL not in response.text


CASES = [
    pytest.param(
        "/auth/register",
        {"email": "a@example.com", "password": SENTINEL, "full_name": "x"},  # 9 chars (< 10)
        id="register-senha-curta",
    ),
    pytest.param(
        "/auth/register",
        {"email": "a@example.com", "password": f"{SENTINEL}-" + "x" * 130, "full_name": "x"},
        id="register-senha-longa",
    ),
    pytest.param(
        "/auth/register",
        {"email": f"{SENTINEL}-sem-arroba", "password": f"{SENTINEL}-senha-valida-1", "full_name": "x"},
        id="register-email-invalido",
    ),
    pytest.param(
        "/auth/register",
        {"email": "a@example.com", "password": f"{SENTINEL}-senha-valida-1", "full_name": "x", "role": "ADMIN"},
        id="register-admin",
    ),
    pytest.param(
        "/auth/register",
        {"email": "a@example.com", "password": [f"{SENTINEL}-lista-1234"], "full_name": "x"},
        id="register-senha-tipo-errado",
    ),
    pytest.param(
        "/auth/login",
        {"email": "a@example.com", "password": f"{SENTINEL}-" + "x" * 130},
        id="login-senha-longa",
    ),
    pytest.param(
        "/auth/login",
        {"password": f"{SENTINEL}-senha-valida-1"},
        id="login-sem-email",
    ),
    pytest.param("/auth/refresh", {"refresh_token": f"{SENTINEL}-" + "r" * 300}, id="refresh-longo"),
    pytest.param("/auth/refresh", {"refresh_token": {"t": f"{SENTINEL}-obj"}}, id="refresh-tipo-errado"),
    pytest.param("/auth/logout", {"refresh_token": f"{SENTINEL}-" + "r" * 300}, id="logout-longo"),
    pytest.param("/auth/logout", {"refresh_token": [f"{SENTINEL}-lista"]}, id="logout-tipo-errado"),
]


def _flatten(value) -> list[str]:
    if isinstance(value, dict):
        return [s for v in value.values() for s in _flatten(v)]
    if isinstance(value, list):
        return [s for v in value for s in _flatten(v)]
    return [value] if isinstance(value, str) and SENTINEL in value else []


@pytest.mark.parametrize(("path", "payload"), CASES)
def test_auth_422_does_not_echo_input(path, payload):
    with TestClient(app) as client:
        response = client.post(path, json=payload)
    _assert_no_echo(response, _flatten(payload))


def test_malformed_json_does_not_echo_body():
    raw = f'{{"email":"a@example.com","password":"{SENTINEL}-quebrado'
    with TestClient(app) as client:
        response = client.post(
            "/auth/login", content=raw.encode(), headers={"content-type": "application/json"}
        )
    _assert_no_echo(response, [f"{SENTINEL}-quebrado"])


@pytest.mark.parametrize("role", [f"{SENTINEL}-ROLE", "admin", ""])
def test_patch_role_422_does_not_echo_input(client, make_user, role):
    admin = make_user("ADMIN")
    target = make_user("CLIENTE")

    response = client.patch(
        f"/admin/users/{target.id}/role", json={"role": role}, headers=admin.headers
    )

    _assert_no_echo(response, [role] if role else [])


def test_patch_role_invalid_uuid_does_not_echo_path(client, make_user):
    admin = make_user("ADMIN")

    response = client.patch(
        f"/admin/users/{SENTINEL}-id/role", json={"role": "LOJISTA"}, headers=admin.headers
    )

    _assert_no_echo(response, [f"{SENTINEL}-id"])


def test_valid_patch_still_works_after_handler(client, make_user):
    """Sanidade: o handler de 422 não interfere em requisições válidas."""
    admin = make_user("ADMIN")
    target = make_user("CLIENTE")
    response = client.patch(
        f"/admin/users/{target.id}/role", json={"role": "LOJISTA"}, headers=admin.headers
    )
    assert response.status_code == 200
    assert uuid.UUID(response.json()["id"]) == uuid.UUID(target.id)
