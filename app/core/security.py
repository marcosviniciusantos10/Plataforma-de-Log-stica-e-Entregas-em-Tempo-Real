"""Primitivas de segurança: hash de senha (Argon2id), JWT de acesso e refresh opaco.

Nada aqui registra senhas, hashes ou tokens em log.
"""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_TYPE = "access"
_REQUIRED_CLAIMS = ["sub", "role", "type", "iat", "exp", "jti"]

# PasswordHasher usa Argon2id com os parâmetros recomendados (RFC 9106, perfil low-memory).
_hasher = PasswordHasher()


# --- Senhas -------------------------------------------------------------------------------


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True quando o hash foi gerado com parâmetros diferentes dos atuais."""
    return _hasher.check_needs_rehash(password_hash)


# Calculado na carga do módulo (e não sob demanda) para a 1ª tentativa com e-mail
# inexistente não ser mais lenta que as outras.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def verify_dummy(password: str) -> None:
    """Gasta o mesmo tempo de um verify real, para o login não revelar se o e-mail existe."""
    verify_password(_DUMMY_HASH, password)


# --- JWT de acesso ------------------------------------------------------------------------


def access_token_ttl_seconds() -> int:
    return get_settings().access_token_ttl_minutes * 60


def create_access_token(user_id: uuid.UUID, role: str) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "role": role,
        "type": ACCESS_TOKEN_TYPE,
        "iat": now,
        "exp": now + timedelta(seconds=access_token_ttl_seconds()),
        "jti": uuid.uuid4().hex,
    }
    secret = get_settings().jwt_secret.get_secret_value()
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    """Valida assinatura, expiração, claims obrigatórias e o tipo do token.

    Levanta jwt.InvalidTokenError (ou subclasse) se algo estiver errado.
    """
    secret = get_settings().jwt_secret.get_secret_value()
    payload = jwt.decode(
        token,
        secret,
        algorithms=[JWT_ALGORITHM],  # lista fixa: impede "alg: none" e confusão de algoritmo
        options={"require": _REQUIRED_CLAIMS},
    )
    if payload.get("type") != ACCESS_TOKEN_TYPE:
        raise jwt.InvalidTokenError("tipo de token inválido")
    return payload


# --- Refresh token opaco ------------------------------------------------------------------


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    """SHA-256 do token: é só isso que vai para o Redis, nunca o token puro."""
    return hashlib.sha256(token.encode()).hexdigest()
