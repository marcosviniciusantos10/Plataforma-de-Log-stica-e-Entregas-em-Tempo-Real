from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.demo import router as demo_router
from app.api.health import router as health_router
from app.api.users import router as users_router
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


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    """422 no formato padrão do FastAPI, mas sem o campo "input".

    O "input" ecoaria o valor enviado (senha, refresh_token) na resposta.
    """
    errors = [{k: v for k, v in err.items() if k != "input"} for err in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})


app.include_router(health_router)
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(admin_router)
app.include_router(demo_router)


@app.get("/")
async def root() -> dict[str, str]:
    return {"app": settings.app_name}
