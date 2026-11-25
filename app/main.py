from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from app.api.health import router as health_router
from app.core.config import get_settings
from app.db.session import engine

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Clientes compartilhados, criados uma vez e reaproveitados entre requisições.
    app.state.redis = Redis.from_url(
        settings.redis_url, socket_timeout=3, socket_connect_timeout=3
    )
    try:
        yield
    finally:
        await app.state.redis.aclose()
        await engine.dispose()


# Documentação interativa só fora de produção.
_docs = {} if not settings.is_production else {
    "docs_url": None,
    "redoc_url": None,
    "openapi_url": None,
}

app = FastAPI(
    title="Plataforma de Logística e Entregas em Tempo Real",
    version="0.1.0",
    lifespan=lifespan,
    **_docs,
)
app.include_router(health_router)


@app.get("/")
async def root() -> dict[str, str]:
    return {"app": settings.app_name}
