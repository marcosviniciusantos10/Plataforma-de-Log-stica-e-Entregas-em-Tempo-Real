import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.user import Role

PASSWORD_MIN = 10
PASSWORD_MAX = 128  # limite superior também evita DoS com senhas gigantes no Argon2

PUBLIC_ROLES = {Role.CLIENTE, Role.LOJISTA, Role.ENTREGADOR}


class RegisterIn(BaseModel):
    email: EmailStr = Field(max_length=254)
    password: str = Field(min_length=PASSWORD_MIN, max_length=PASSWORD_MAX)
    full_name: str = Field(min_length=1, max_length=120)
    role: Role = Role.CLIENTE

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email(cls, v: object) -> object:
        # "before": normaliza antes do EmailStr, para espaços e maiúsculas não importarem.
        return v.strip().lower() if isinstance(v, str) else v

    @field_validator("full_name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("nome obrigatório")
        return v

    @field_validator("password")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("senha fraca")
        return v

    @field_validator("role")
    @classmethod
    def _public_role(cls, v: Role) -> Role:
        if v not in PUBLIC_ROLES:
            raise ValueError("perfil não permitido no cadastro público")
        return v


class LoginIn(BaseModel):
    # str (e não EmailStr): qualquer falha de login vira o mesmo 401 genérico.
    email: str = Field(max_length=254)
    password: str = Field(max_length=PASSWORD_MAX)


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=256)


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    role: Role
    is_active: bool
    created_at: datetime


class RoleUpdateIn(BaseModel):
    role: Role
