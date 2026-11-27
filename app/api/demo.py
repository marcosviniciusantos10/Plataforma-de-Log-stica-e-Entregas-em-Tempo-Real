"""Rotas de demonstração dos guards de perfil (ADMIN acessa todas)."""

from fastapi import APIRouter, Depends

from app.api.deps import require_roles
from app.models.user import Role, User

router = APIRouter(tags=["demo"])


def _pong(user: User) -> dict[str, str]:
    return {"message": "pong", "role": user.role.value}


@router.get("/cliente/ping")
async def cliente_ping(user: User = Depends(require_roles(Role.CLIENTE))) -> dict[str, str]:
    return _pong(user)


@router.get("/lojista/ping")
async def lojista_ping(user: User = Depends(require_roles(Role.LOJISTA))) -> dict[str, str]:
    return _pong(user)


@router.get("/entregador/ping")
async def entregador_ping(
    user: User = Depends(require_roles(Role.ENTREGADOR)),
) -> dict[str, str]:
    return _pong(user)
