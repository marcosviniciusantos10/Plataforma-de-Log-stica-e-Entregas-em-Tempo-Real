import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Enum, String, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Role(str, enum.Enum):
    """Perfis de acesso. Os valores são gravados em MAIÚSCULAS no enum do Postgres."""

    CLIENTE = "CLIENTE"
    LOJISTA = "LOJISTA"
    ENTREGADOR = "ENTREGADOR"
    ADMIN = "ADMIN"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("email = lower(email)", name="ck_users_email_lowercase"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[Role] = mapped_column(
        Enum(Role, name="user_role", values_callable=lambda e: [m.value for m in e]),
        default=Role.CLIENTE,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:  # sem e-mail nem hash, para não vazar em logs
        return f"<User id={self.id} role={self.role.value}>"
