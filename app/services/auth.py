"""Regras de autenticação: cadastro, login, refresh rotativo e logout.

Chaves no Redis (o token de refresh puro nunca é gravado, só o SHA-256):
- auth:refresh:{hash}          -> {"user_id", "family_id"}  token ATIVO; consumido com GETDEL
- auth:refresh_family:{hash}   -> family_id                  registro do token emitido; sobrevive
                                                              ao consumo e permite detectar reuso
- auth:family:{family_id}      -> hash do refresh ativo da família (só há um por vez)
- auth:family_revoked:{fid}    -> "1"  família revogada (reuso ou logout)
- auth:login_fail:{sha256(email|ip)} -> contador de tentativas, TTL = janela de bloqueio

Todas as chaves de refresh expiram junto com o token (REFRESH_TOKEN_TTL_DAYS).
"""

import hashlib
import json
import uuid
from dataclasses import dataclass

from redis.asyncio import Redis
from redis.exceptions import WatchError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core import security
from app.core.config import get_settings
from app.models.user import User
from app.schemas.auth import RegisterIn

LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_SECONDS = 15 * 60

_PREFIX = "auth"


class EmailAlreadyRegisteredError(Exception):
    pass


class InvalidCredentialsError(Exception):
    pass


class TooManyAttemptsError(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__("muitas tentativas")
        self.retry_after = retry_after


class InvalidRefreshTokenError(Exception):
    pass


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int


def _refresh_ttl_seconds() -> int:
    return get_settings().refresh_token_ttl_days * 24 * 60 * 60


def _k(*parts: str) -> str:
    return ":".join((_PREFIX, *parts))


def _str(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else value


# --- Cadastro -----------------------------------------------------------------------------


async def register(session: AsyncSession, data: RegisterIn) -> User:
    exists = await session.scalar(select(User.id).where(User.email == data.email))
    if exists is not None:
        raise EmailAlreadyRegisteredError

    user = User(
        email=data.email,
        password_hash=await run_in_threadpool(security.hash_password, data.password),
        full_name=data.full_name,
        role=data.role,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:  # corrida entre dois cadastros com o mesmo e-mail
        await session.rollback()
        raise EmailAlreadyRegisteredError from None
    await session.refresh(user)
    return user


# --- Login --------------------------------------------------------------------------------


def _login_fail_key(email: str, ip: str) -> str:
    # O e-mail não vai em claro para o Redis.
    digest = hashlib.sha256(f"{email}|{ip}".encode()).hexdigest()
    return _k("login_fail", digest)


async def _count_attempt(redis: Redis, key: str) -> int:
    """INCR + EXPIRE NX numa transação: a chave nunca fica sem TTL (sem bloqueio eterno)."""
    async with redis.pipeline(transaction=True) as pipe:
        pipe.incr(key)
        pipe.expire(key, LOGIN_WINDOW_SECONDS, nx=True)
        attempts, _ = await pipe.execute()
    return attempts


async def login(
    session: AsyncSession, redis: Redis, email: str, password: str, ip: str
) -> TokenPair:
    email = email.strip().lower()
    fail_key = _login_fail_key(email, ip)

    # A tentativa é contada ANTES de verificar a senha: o INCR é atômico, então nem
    # requisições concorrentes passam do limite. Login bem-sucedido zera o contador.
    if await _count_attempt(redis, fail_key) > LOGIN_MAX_FAILURES:
        ttl = await redis.ttl(fail_key)
        raise TooManyAttemptsError(retry_after=ttl if ttl > 0 else LOGIN_WINDOW_SECONDS)

    user = await session.scalar(select(User).where(User.email == email))
    if user is None:
        await run_in_threadpool(security.verify_dummy, password)
        raise InvalidCredentialsError

    valid = await run_in_threadpool(security.verify_password, user.password_hash, password)
    if not valid or not user.is_active:
        raise InvalidCredentialsError

    await redis.delete(fail_key)

    if security.needs_rehash(user.password_hash):
        user.password_hash = await run_in_threadpool(security.hash_password, password)
        await session.commit()

    return await _issue_tokens(redis, user, uuid.uuid4().hex)


# --- Refresh rotativo ---------------------------------------------------------------------


async def _issue_tokens(redis: Redis, user: User, family_id: str) -> TokenPair:
    """Grava o novo refresh da família, só se ela não tiver sido revogada.

    O WATCH no marcador de revogação garante que um logout/reuso concorrente não deixe
    passar um token novo: se o marcador surgir entre a checagem e o EXEC, nada é gravado.
    """
    refresh_token = security.generate_refresh_token()
    token_hash = security.hash_token(refresh_token)
    ttl = _refresh_ttl_seconds()
    revoked_key = _k("family_revoked", family_id)

    async with redis.pipeline(transaction=True) as pipe:
        try:
            await pipe.watch(revoked_key)
            if await pipe.exists(revoked_key):
                raise InvalidRefreshTokenError
            pipe.multi()
            pipe.set(
                _k("refresh", token_hash),
                json.dumps({"user_id": str(user.id), "family_id": family_id}),
                ex=ttl,
            )
            pipe.set(_k("refresh_family", token_hash), family_id, ex=ttl)
            pipe.set(_k("family", family_id), token_hash, ex=ttl)
            await pipe.execute()
        except WatchError:
            raise InvalidRefreshTokenError from None

    return TokenPair(
        access_token=security.create_access_token(user.id, user.role.value),
        refresh_token=refresh_token,
        expires_in=security.access_token_ttl_seconds(),
    )


async def _revoke_family(redis: Redis, family_id: str) -> None:
    # 1º o marcador: a partir daqui nenhuma emissão em andamento consegue gravar (WATCH).
    await redis.set(_k("family_revoked", family_id), "1", ex=_refresh_ttl_seconds())
    # 2º derruba o refresh ativo (inclusive um emitido logo antes do marcador).
    current = await redis.getdel(_k("family", family_id))
    if current is not None:
        await redis.delete(_k("refresh", _str(current)))


async def _consume(redis: Redis, refresh_token: str) -> dict[str, str] | None:
    """Consome o refresh (GETDEL atômico) e devolve {user_id, family_id}, ou None.

    Se o token já tinha sido consumido (reuso), revoga a família inteira. Como o registro
    refresh_family é gravado na EMISSÃO, duas requisições simultâneas com o mesmo token
    não escapam da detecção: a que perde o GETDEL já encontra o registro.
    """
    token_hash = security.hash_token(refresh_token)
    raw = await redis.getdel(_k("refresh", token_hash))
    if raw is not None:
        return json.loads(raw)

    family_id = await redis.get(_k("refresh_family", token_hash))
    if family_id is not None:
        await _revoke_family(redis, _str(family_id))
    return None


async def refresh(session: AsyncSession, redis: Redis, refresh_token: str) -> TokenPair:
    data = await _consume(redis, refresh_token)
    if data is None:
        raise InvalidRefreshTokenError
    family_id = data["family_id"]

    try:
        user_id = uuid.UUID(data["user_id"])
    except ValueError:
        raise InvalidRefreshTokenError from None
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        await _revoke_family(redis, family_id)
        raise InvalidRefreshTokenError

    # _issue_tokens recusa (de forma atômica) se a família foi revogada nesse meio tempo.
    return await _issue_tokens(redis, user, family_id)


async def logout(redis: Redis, refresh_token: str) -> None:
    """Revoga a sessão (família) do refresh informado. Idempotente: nunca falha.

    Funciona também com um token já rotacionado: se um atacante trocou o refresh antes
    do usuário sair, o logout derruba a família e com ela a sessão do atacante.
    """
    data = await _consume(redis, refresh_token)  # já revoga a família em caso de reuso
    if data is not None:
        await _revoke_family(redis, data["family_id"])
