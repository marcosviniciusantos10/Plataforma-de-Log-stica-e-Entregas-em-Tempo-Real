"""Dependências compartilhadas das rotas: sessão, Redis e guards de autenticação/RBAC."""

import uuid
from collections.abc import Awaitable, Callable

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.db.session import get_session
from app.models.user import Role, User

__all__ = ["get_session", "get_redis", "get_current_user", "require_roles"]

_bearer = HTTPBearer(auto_error=False)


def _unauthorized() -> HTTPException:
    # Instância nova a cada erro: uma exceção global reaproveitada acumularia
    # __traceback__/__context__ (e os frames com credenciais) entre requisições.
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Não autenticado",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_redis(request: Request) -> Redis:
    return request.app.state.redis


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> User:
    """Valida o JWT de acesso e carrega o usuário ativo do banco.

    O perfil usado nas checagens vem do banco (não do token), então uma troca de
    perfil ou desativação vale imediatamente.
    """
    if credentials is None:
        raise _unauthorized()
    try:
        payload = decode_access_token(credentials.credentials)
        user_id = uuid.UUID(payload["sub"])
    except (jwt.InvalidTokenError, ValueError):
        raise _unauthorized() from None

    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise _unauthorized()
    return user


def require_roles(*roles: Role) -> Callable[..., Awaitable[User]]:
    """Guard de perfil. ADMIN sempre tem acesso; os demais precisam estar em `roles`."""
    allowed = {*roles, Role.ADMIN}

    async def _guard(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sem permissão")
        return user

    return _guard
