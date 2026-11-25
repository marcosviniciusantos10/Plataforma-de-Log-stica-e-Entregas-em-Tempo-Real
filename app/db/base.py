from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base declarativa de todos os modelos ORM."""


# Importe os modelos aqui para que o Alembic os detecte no autogenerate.
from app.models import *  # noqa: E402,F401,F403
