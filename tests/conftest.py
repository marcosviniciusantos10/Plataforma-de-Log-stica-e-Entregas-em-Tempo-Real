"""Configuração e fixtures compartilhadas dos testes.

1. Variáveis de ambiente de teste (rodam sem .env, ex.: CI). São ATRIBUÍDAS (não
   setdefault) para que variáveis exportadas no shell não vazem para a suíte, e o
   Postgres aponta SEMPRE para o banco `logistica_test`, nunca para o de dev.
2. Banco Postgres real de teste (`logistica_test` em 127.0.0.1): recriado do zero a
   cada sessão via `alembic upgrade head` e truncado entre testes. Se o Postgres não
   estiver acessível, os testes que dependem dele são PULADOS com mensagem clara.
3. Redis substituído por fakeredis (dependency_overrides[get_redis] e app.state.redis).
4. Helpers para cadastrar usuários de cada perfil e obter tokens.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
_DOTENV = dotenv_values(ROOT / ".env") if (ROOT / ".env").exists() else {}

# Banco de teste: credenciais do .env (as mesmas do container), host local.
# TEST_POSTGRES_DB permite rodar suítes em paralelo (cada uma recria o próprio banco).
TEST_DB_NAME = os.environ.get("TEST_POSTGRES_DB", "logistica_test")
if not TEST_DB_NAME.endswith("_test") and "_test_" not in TEST_DB_NAME:
    raise RuntimeError(f"TEST_POSTGRES_DB={TEST_DB_NAME!r}: o nome precisa conter '_test' (o banco é APAGADO)")
TEST_DB_HOST = os.environ.get("TEST_POSTGRES_HOST", "127.0.0.1")
TEST_DB_PORT = os.environ.get("TEST_POSTGRES_PORT") or _DOTENV.get("POSTGRES_PORT") or "5432"
TEST_DB_USER = os.environ.get("TEST_POSTGRES_USER") or _DOTENV.get("POSTGRES_USER") or "test"
TEST_DB_PASSWORD = (
    os.environ.get("TEST_POSTGRES_PASSWORD")
    or _DOTENV.get("POSTGRES_PASSWORD")
    or "test-password-123"
)

JWT_TEST_SECRET = "test-jwt-secret-0123456789-abcdefghij"

_TEST_ENV = {
    "APP_ENV": "test",
    "POSTGRES_USER": TEST_DB_USER,
    "POSTGRES_PASSWORD": TEST_DB_PASSWORD,
    "POSTGRES_DB": TEST_DB_NAME,
    "POSTGRES_HOST": TEST_DB_HOST,
    "POSTGRES_PORT": TEST_DB_PORT,
    "REDIS_PASSWORD": "test-password-123",
    "REDIS_HOST": "localhost",
    "RABBITMQ_USER": "test",
    "RABBITMQ_PASSWORD": "test-password-123",
    "RABBITMQ_HOST": "localhost",
    "JWT_SECRET": JWT_TEST_SECRET,
    "ACCESS_TOKEN_TTL_MINUTES": "15",
    "REFRESH_TOKEN_TTL_DAYS": "7",
}

# Executado antes da coleta: o Settings é criado no import de app.main.
os.environ.update(_TEST_ENV)

STRONG_PASSWORD = "Senha-Forte-2026!"
PUBLIC_ROLES = ("CLIENTE", "LOJISTA", "ENTREGADOR")
ALL_ROLES = (*PUBLIC_ROLES, "ADMIN")


# --------------------------------------------------------------------------- banco


def _conninfo(dbname: str) -> str:
    from psycopg.conninfo import make_conninfo

    return make_conninfo(
        host=TEST_DB_HOST,
        port=TEST_DB_PORT,
        user=TEST_DB_USER,
        password=TEST_DB_PASSWORD,
        dbname=dbname,
        connect_timeout=3,
    )


@pytest.fixture(scope="session")
def test_database() -> str:
    """Recria `logistica_test` do zero e aplica as migrations. Pula se não houver Postgres."""
    import psycopg

    try:
        admin = psycopg.connect(_conninfo("postgres"), autocommit=True)
    except psycopg.OperationalError as exc:
        pytest.skip(
            f"Postgres de teste indisponível em {TEST_DB_HOST}:{TEST_DB_PORT} "
            f"({exc.__class__.__name__}). Suba com `docker compose up -d db`."
        )
    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"`alembic upgrade head` falhou no banco de teste:\n{result.stderr[-4000:]}")
    return TEST_DB_NAME


@pytest.fixture
def clean_db(test_database: str) -> None:
    """Trunca todas as tabelas da aplicação antes de cada teste."""
    import psycopg

    with psycopg.connect(_conninfo(test_database), autocommit=True) as conn:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                "AND tablename NOT IN ('alembic_version', 'spatial_ref_sys')"
            )
        ]
        if tables:
            joined = ", ".join(f'"{t}"' for t in tables)
            conn.execute(f"TRUNCATE {joined} RESTART IDENTITY CASCADE")


@pytest.fixture
def db_engine(test_database: str):
    """Engine síncrona (psycopg) para inspecionar/ajustar o banco direto nos testes."""
    from sqlalchemy import URL, create_engine
    from sqlalchemy.pool import NullPool

    engine = create_engine(
        URL.create(
            "postgresql+psycopg",
            username=TEST_DB_USER,
            password=TEST_DB_PASSWORD,
            host=TEST_DB_HOST,
            port=int(TEST_DB_PORT),
            database=test_database,
        ),
        poolclass=NullPool,
    )
    yield engine
    engine.dispose()


@pytest.fixture
def users_table():
    from app.models.user import User

    return User.__table__


# --------------------------------------------------------------------------- redis


@pytest.fixture
def redis_server():
    import fakeredis

    return fakeredis.FakeServer()


@pytest.fixture
def redis_sync(redis_server):
    """Cliente síncrono sobre o MESMO fakeredis do app, para inspecionar chaves."""
    import fakeredis

    return fakeredis.FakeRedis(server=redis_server)


@pytest.fixture
def redis_async(redis_server):
    import fakeredis

    return fakeredis.FakeAsyncRedis(server=redis_server)


# --------------------------------------------------------------------------- app


@pytest.fixture
def client(clean_db, redis_async):
    """TestClient com Postgres de teste (sessão própria, NullPool) e fakeredis."""
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.api.deps import get_redis, get_session
    from app.core.config import get_settings
    from app.main import app

    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _session():
        async with session_factory() as session:
            yield session

    async def _redis():
        return redis_async

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_redis] = _redis
    try:
        with TestClient(app) as test_client:
            original_redis = app.state.redis
            app.state.redis = redis_async  # cobre código que use app.state.redis direto
            try:
                yield test_client
            finally:
                app.state.redis = original_redis
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- helpers


def unique_email(prefix: str = "user") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}@example.com"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def register(client, email=None, password=STRONG_PASSWORD, role="CLIENTE", full_name="Fulano de Tal"):
    payload = {
        "email": email if email is not None else unique_email(role.lower()),
        "password": password,
        "full_name": full_name,
        "role": role,
    }
    return client.post("/auth/register", json=payload)


def login(client, email, password=STRONG_PASSWORD):
    return client.post("/auth/login", json={"email": email, "password": password})


@dataclass
class UserCtx:
    id: str
    email: str
    password: str
    role: str
    access_token: str = ""
    refresh_token: str = ""
    data: dict = field(default_factory=dict)

    @property
    def headers(self) -> dict[str, str]:
        return bearer(self.access_token)


@pytest.fixture
def set_user_fields(db_engine, users_table):
    """Altera colunas de um usuário direto no banco (ex.: role=ADMIN, is_active=False)."""
    from sqlalchemy import update

    from app.models.user import Role

    def _set(email: str, **values) -> None:
        if "role" in values and isinstance(values["role"], str):
            values["role"] = Role(values["role"])
        with db_engine.begin() as conn:
            result = conn.execute(
                update(users_table).where(users_table.c.email == email.lower()).values(**values)
            )
            assert result.rowcount == 1, f"usuário {email} não encontrado no banco"

    return _set


@pytest.fixture
def make_user(client, set_user_fields):
    """Cria um usuário do perfil pedido (ADMIN via banco, como o seed) e faz login."""

    def _make(role: str = "CLIENTE", email: str | None = None, password: str = STRONG_PASSWORD,
              do_login: bool = True) -> UserCtx:
        public_role = role if role in PUBLIC_ROLES else "CLIENTE"
        response = register(client, email=email or unique_email(role.lower()),
                            password=password, role=public_role)
        assert response.status_code == 201, response.text
        data = response.json()
        if role not in PUBLIC_ROLES:
            set_user_fields(data["email"], role=role)
            data["role"] = role
        ctx = UserCtx(id=str(data["id"]), email=data["email"], password=password, role=role, data=data)
        if do_login:
            tokens = login(client, ctx.email, password)
            assert tokens.status_code == 200, tokens.text
            ctx.access_token = tokens.json()["access_token"]
            ctx.refresh_token = tokens.json()["refresh_token"]
        return ctx

    return _make
