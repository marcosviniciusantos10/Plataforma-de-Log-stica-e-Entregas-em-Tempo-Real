import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session, require_roles
from app.models.user import Role, User
from app.schemas.auth import RoleUpdateIn, UserOut

router = APIRouter(prefix="/admin", tags=["admin"])

_admin_only = require_roles(Role.ADMIN)


@router.get("/users", response_model=list[UserOut])
async def list_users(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(_admin_only),
    session: AsyncSession = Depends(get_session),
) -> list[UserOut]:
    result = await session.scalars(
        select(User).order_by(User.created_at, User.id).limit(limit).offset(offset)
    )
    return [UserOut.model_validate(u) for u in result]


@router.patch("/users/{user_id}/role", response_model=UserOut)
async def update_role(
    user_id: uuid.UUID,
    data: RoleUpdateIn,
    admin: User = Depends(_admin_only),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    if user_id == admin.id:
        # Evita que o último ADMIN se rebaixe e tranque o sistema.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Não é permitido alterar o próprio perfil",
        )
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado")
    user.role = data.role
    await session.commit()
    await session.refresh(user)
    return UserOut.model_validate(user)
