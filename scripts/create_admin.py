"""Cria um usuário ADMIN.

Uso (dentro do container):
    docker compose exec api python -m scripts.create_admin

Credenciais lidas de ADMIN_EMAIL / ADMIN_PASSWORD / ADMIN_FULL_NAME ou, se ausentes,
pedidas de forma interativa (a senha via getpass). Nunca por argumento de linha de
comando, para não ficar no histórico do shell nem em `ps`.
"""

import asyncio
import getpass
import os
import sys

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.security import hash_password
from app.db.session import SessionLocal, engine
from app.models.user import Role, User
from app.schemas.auth import RegisterIn

DEFAULT_FULL_NAME = "Administrador"


def _read_credentials() -> tuple[str, str, str]:
    env_email = os.environ.get("ADMIN_EMAIL")
    email = env_email or input("E-mail do admin: ")
    full_name = os.environ.get("ADMIN_FULL_NAME")
    if not full_name:
        # Sem terminal (ex.: CI com ADMIN_EMAIL definido) não perguntamos o nome.
        full_name = DEFAULT_FULL_NAME if env_email else input("Nome completo: ")
    full_name = full_name or DEFAULT_FULL_NAME
    password = os.environ.get("ADMIN_PASSWORD")
    if not password:
        password = getpass.getpass("Senha (10 a 128 caracteres): ")
        if password != getpass.getpass("Confirme a senha: "):
            sys.exit("Erro: as senhas não conferem.")
    return email, password, full_name


async def _create(email: str, password: str, full_name: str) -> None:
    # Reaproveita as regras do cadastro (e-mail válido em minúsculas, tamanho da senha).
    try:
        data = RegisterIn(email=email, password=password, full_name=full_name)
    except ValidationError as exc:
        fields = ", ".join(str(err["loc"][0]) for err in exc.errors())
        sys.exit(f"Erro: dados inválidos ({fields}).")  # sem ecoar os valores

    try:
        async with SessionLocal() as session:
            if await session.scalar(select(User.id).where(User.email == data.email)):
                sys.exit("Erro: já existe um usuário com esse e-mail.")
            session.add(
                User(
                    email=data.email,
                    password_hash=hash_password(data.password),
                    full_name=data.full_name,
                    role=Role.ADMIN,
                )
            )
            try:
                await session.commit()
            except IntegrityError:  # outro processo criou o mesmo e-mail nesse meio tempo
                sys.exit("Erro: já existe um usuário com esse e-mail.")
    finally:
        await engine.dispose()
    print("ADMIN criado com sucesso.")


def main() -> None:
    try:
        credentials = _read_credentials()
    except (EOFError, KeyboardInterrupt):
        sys.exit("\nCancelado: informe ADMIN_EMAIL/ADMIN_PASSWORD ou rode em um terminal interativo.")
    asyncio.run(_create(*credentials))


if __name__ == "__main__":
    main()
