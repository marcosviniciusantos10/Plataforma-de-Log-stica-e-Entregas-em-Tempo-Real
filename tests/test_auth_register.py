"""Cadastro público (/auth/register) e armazenamento da senha (Argon2id)."""

import pytest
from argon2 import PasswordHasher
from sqlalchemy import select

from tests.conftest import PUBLIC_ROLES, STRONG_PASSWORD, login, register, unique_email

USER_OUT_FIELDS = {"id", "email", "full_name", "role", "is_active", "created_at"}


@pytest.mark.parametrize("role", PUBLIC_ROLES)
def test_register_public_roles_succeeds(client, role):
    email = unique_email()
    response = register(client, email=email, role=role)

    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == USER_OUT_FIELDS
    assert body["email"] == email
    assert body["role"] == role
    assert body["is_active"] is True
    assert body["full_name"] == "Fulano de Tal"


def test_register_response_never_contains_password_or_hash(client):
    response = register(client)

    assert response.status_code == 201
    text = response.text
    assert STRONG_PASSWORD not in text
    assert "password" not in text
    assert "$argon2" not in text


def test_register_duplicate_email_returns_409(client):
    email = unique_email()
    assert register(client, email=email).status_code == 201

    response = register(client, email=email)

    assert response.status_code == 409


def test_register_duplicate_email_is_case_insensitive(client):
    email = unique_email()
    assert register(client, email=email).status_code == 201

    response = register(client, email=email.upper())

    assert response.status_code == 409


def test_register_normalizes_email_to_lowercase(client, db_engine, users_table):
    local = unique_email().split("@")[0]
    response = register(client, email=f"{local.upper()}@Example.COM")

    assert response.status_code == 201, response.text
    expected = f"{local}@example.com"
    assert response.json()["email"] == expected
    with db_engine.connect() as conn:
        stored = conn.execute(select(users_table.c.email)).scalars().all()
    assert stored == [expected]
    # e o login aceita qualquer caixa
    assert login(client, expected.upper()).status_code == 200


def test_register_admin_role_is_rejected(client, db_engine, users_table):
    response = register(client, role="ADMIN")

    assert response.status_code == 422
    with db_engine.connect() as conn:
        assert conn.execute(select(users_table.c.id)).first() is None


@pytest.mark.parametrize("role", ["SUPERUSER", "admin", ""])
def test_register_unknown_role_is_rejected(client, role):
    assert register(client, role=role).status_code == 422


@pytest.mark.parametrize(
    "password",
    ["a" * 9, "", "a" * 129],
    ids=["9-chars", "vazia", "129-chars"],
)
def test_register_weak_password_returns_422(client, password):
    assert register(client, password=password).status_code == 422


@pytest.mark.parametrize("length", [10, 128])
def test_register_password_length_limits_are_inclusive(client, length):
    assert register(client, password="x" * length).status_code == 201


@pytest.mark.parametrize("email", ["sem-arroba", "a@", "@example.com", ""])
def test_register_invalid_email_returns_422(client, email):
    assert register(client, email=email).status_code == 422


def test_register_cannot_set_privileged_fields(client):
    """Mass assignment: campos extras (is_active, id) não podem ser aceitos do cliente."""
    payload = {
        "email": unique_email(),
        "password": STRONG_PASSWORD,
        "full_name": "Fulano",
        "role": "CLIENTE",
        "is_active": False,
        "id": "00000000-0000-0000-0000-000000000001",
    }
    response = client.post("/auth/register", json=payload)

    if response.status_code == 422:  # extra="forbid" também é aceitável
        return
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["is_active"] is True
    assert body["id"] != payload["id"]


def test_password_is_stored_as_argon2id(client, db_engine, users_table):
    email = unique_email()
    assert register(client, email=email).status_code == 201

    with db_engine.connect() as conn:
        stored = conn.execute(
            select(users_table.c.password_hash).where(users_table.c.email == email)
        ).scalar_one()

    assert stored != STRONG_PASSWORD
    assert stored.startswith("$argon2id$")
    assert PasswordHasher().verify(stored, STRONG_PASSWORD)


def test_login_rehashes_password_when_parameters_change(client, db_engine, users_table, set_user_fields):
    email = unique_email()
    assert register(client, email=email).status_code == 201
    weak_hash = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(STRONG_PASSWORD)
    set_user_fields(email, password_hash=weak_hash)

    assert login(client, email).status_code == 200

    with db_engine.connect() as conn:
        new_hash = conn.execute(
            select(users_table.c.password_hash).where(users_table.c.email == email)
        ).scalar_one()
    assert new_hash != weak_hash
    assert new_hash.startswith("$argon2id$")
    assert PasswordHasher().verify(new_hash, STRONG_PASSWORD)
