"""Regressão do achado I1: segredos de exemplo não podem subir a aplicação.

Roda sem Postgres/Redis (só instancia o Settings), então vale também no CI sem Docker.
"""

import secrets
import traceback

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from tests.conftest import ROOT

SECRET_FIELDS = ("postgres_password", "redis_password", "rabbitmq_password", "jwt_secret")
PLACEHOLDER_MARKERS = ("troque", "changeme", "change-me", "change_me", "example", "senha123")


def _strong() -> str:
    # token_urlsafe usa só [A-Za-z0-9_-]; reamostra no caso (raríssimo) de conter um marcador.
    while True:
        value = secrets.token_urlsafe(48)
        if not any(m in value.lower() for m in PLACEHOLDER_MARKERS):
            return value


def _base() -> dict:
    return {
        "postgres_user": "u",
        "postgres_db": "d",
        "rabbitmq_user": "u",
        **{field: _strong() for field in SECRET_FIELDS},
    }


def _placeholder(marker: str, upper: bool) -> str:
    value = f"{marker}-0123456789-abcdefghijklmnopqrstuvwxyz"  # >= 32 chars: passa no min_length
    return value.upper() if upper else value


def test_strong_random_secrets_are_accepted():
    settings = Settings(_env_file=None, **_base())
    for field in SECRET_FIELDS:
        assert len(getattr(settings, field).get_secret_value()) >= 32


@pytest.mark.parametrize("upper", [False, True], ids=["minusculas", "MAIUSCULAS"])
@pytest.mark.parametrize("marker", PLACEHOLDER_MARKERS)
@pytest.mark.parametrize("field", SECRET_FIELDS)
def test_placeholder_secret_is_rejected(field, marker, upper):
    value = _placeholder(marker, upper)

    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None, **{**_base(), field: value})

    assert [e["loc"] for e in excinfo.value.errors()] == [(field,)]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("jwt_secret", "troque-por-um-segredo-aleatorio-de-pelo-menos-32-caracteres"),
        ("postgres_password", "troque-esta-senha"),
        ("redis_password", "troque-esta-senha"),
        ("rabbitmq_password", "troque-esta-senha"),
    ],
    ids=["jwt-antigo", "postgres-antigo", "redis-antigo", "rabbitmq-antigo"],
)
def test_old_env_example_placeholders_are_rejected(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{**_base(), field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("jwt_secret", "troque-por-um-segredo-aleatorio-de-pelo-menos-32-caracteres"),  # placeholder
        ("jwt_secret", "segredo-curto-SONDA-xyz"),  # min_length
        ("postgres_password", "ChangeMe-Sonda-1234"),  # placeholder
        ("redis_password", "curta-SONDA"),  # min_length
    ],
    ids=["jwt-placeholder", "jwt-curto", "postgres-placeholder", "redis-curto"],
)
def test_rejected_secret_never_appears_in_error_output(field, value):
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None, **{**_base(), field: value})

    exc = excinfo.value
    rendered = "".join(traceback.format_exception(exc))
    assert value not in str(exc)
    assert value not in repr(exc)
    assert value not in rendered
    assert field in str(exc)  # a mensagem ainda diz QUAL campo está errado


def test_env_example_file_is_rejected(monkeypatch):
    """O .env.example versionado (segredos vazios) não sobe a aplicação como está."""
    for field in SECRET_FIELDS:
        monkeypatch.delenv(field.upper(), raising=False)

    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=ROOT / ".env.example")

    rejected = {e["loc"][0] for e in excinfo.value.errors()}
    assert rejected == set(SECRET_FIELDS)


def test_env_example_has_no_secret_values():
    lines = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    assignments = dict(
        line.split("=", 1) for line in lines if "=" in line and not line.lstrip().startswith("#")
    )
    for field in SECRET_FIELDS:
        assert assignments.get(field.upper(), None) == "", f"{field.upper()} deve ficar vazio"
