"""Guards de RBAC: rotas de demonstração, /admin/* e troca de perfil."""

import uuid

import pytest

from tests.conftest import ALL_ROLES, bearer, login

PINGS = {
    "CLIENTE": "/cliente/ping",
    "LOJISTA": "/lojista/ping",
    "ENTREGADOR": "/entregador/ping",
}


def _matrix():
    for user_role in ALL_ROLES:
        for route_role, path in PINGS.items():
            allowed = user_role in (route_role, "ADMIN")
            yield pytest.param(user_role, path, allowed, id=f"{user_role}->{path}")


@pytest.mark.parametrize(("user_role", "path", "allowed"), list(_matrix()))
def test_ping_access_matrix(client, make_user, user_role, path, allowed):
    user = make_user(user_role)

    response = client.get(path, headers=user.headers)

    if allowed:
        assert response.status_code == 200, response.text
        assert response.json()["message"] == "pong"
    else:
        assert response.status_code == 403, response.text


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/cliente/ping"),
        ("GET", "/lojista/ping"),
        ("GET", "/entregador/ping"),
        ("GET", "/users/me"),
        ("GET", "/admin/users"),
        ("PATCH", f"/admin/users/{uuid.uuid4()}/role"),
    ],
)
def test_protected_routes_without_token_return_401(client, method, path):
    response = client.request(method, path, json={"role": "LOJISTA"})
    assert response.status_code == 401


# --------------------------------------------------------------------------- /admin/users


def test_admin_lists_users_without_hashes(client, make_user):
    admin = make_user("ADMIN")
    lojista = make_user("LOJISTA")

    response = client.get("/admin/users", headers=admin.headers)

    assert response.status_code == 200, response.text
    body = response.json()
    items = body if isinstance(body, list) else body.get("items", body)
    emails = {u["email"] for u in items}
    assert {admin.email, lojista.email} <= emails
    assert "password" not in response.text
    assert "$argon2" not in response.text


@pytest.mark.parametrize("role", ["CLIENTE", "LOJISTA", "ENTREGADOR"])
def test_non_admin_cannot_list_users(client, make_user, role):
    user = make_user(role)
    assert client.get("/admin/users", headers=user.headers).status_code == 403


# --------------------------------------------------------------------------- PATCH role


def _patch_role(client, headers, user_id, role):
    return client.patch(f"/admin/users/{user_id}/role", json={"role": role}, headers=headers)


def test_admin_changes_user_role(client, make_user):
    admin = make_user("ADMIN")
    target = make_user("CLIENTE")

    response = _patch_role(client, admin.headers, target.id, "LOJISTA")

    assert response.status_code == 200, response.text
    assert response.json()["id"] == target.id
    assert response.json()["role"] == "LOJISTA"
    new_access = login(client, target.email).json()["access_token"]
    assert client.get("/lojista/ping", headers=bearer(new_access)).status_code == 200
    assert client.get("/cliente/ping", headers=bearer(new_access)).status_code == 403


def test_admin_can_promote_to_admin(client, make_user):
    admin = make_user("ADMIN")
    target = make_user("ENTREGADOR")

    response = _patch_role(client, admin.headers, target.id, "ADMIN")

    assert response.status_code == 200, response.text
    assert response.json()["role"] == "ADMIN"


def test_demoted_user_old_token_loses_access(client, make_user):
    """Decisão (mais seguro): a permissão vale pelo perfil ATUAL no banco, não pelo claim
    do token. Um token emitido antes do rebaixamento não pode manter o acesso antigo."""
    admin = make_user("ADMIN")
    target = make_user("LOJISTA")
    assert client.get("/lojista/ping", headers=target.headers).status_code == 200

    assert _patch_role(client, admin.headers, target.id, "CLIENTE").status_code == 200

    assert client.get("/lojista/ping", headers=target.headers).status_code == 403


def test_demoted_admin_loses_admin_routes(client, make_user):
    admin = make_user("ADMIN")
    other_admin = make_user("ADMIN")

    assert _patch_role(client, admin.headers, other_admin.id, "CLIENTE").status_code == 200

    assert client.get("/admin/users", headers=other_admin.headers).status_code == 403


@pytest.mark.parametrize("role", ["CLIENTE", "LOJISTA", "ENTREGADOR"])
def test_non_admin_cannot_change_roles(client, make_user, role):
    user = make_user(role)
    target = make_user("CLIENTE")

    assert _patch_role(client, user.headers, target.id, "ADMIN").status_code == 403
    assert _patch_role(client, user.headers, user.id, "ADMIN").status_code == 403  # auto-promoção


@pytest.mark.parametrize("role", ["SUPERUSER", "admin", ""])
def test_patch_role_with_invalid_role_returns_422(client, make_user, role):
    admin = make_user("ADMIN")
    target = make_user("CLIENTE")
    assert _patch_role(client, admin.headers, target.id, role).status_code == 422


def test_patch_role_unknown_user_returns_404(client, make_user):
    admin = make_user("ADMIN")
    assert _patch_role(client, admin.headers, uuid.uuid4(), "LOJISTA").status_code == 404


def test_patch_role_invalid_uuid_returns_422(client, make_user):
    admin = make_user("ADMIN")
    assert _patch_role(client, admin.headers, "nao-e-uuid", "LOJISTA").status_code == 422
