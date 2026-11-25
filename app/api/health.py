"""Endpoints de saúde.

- GET /health        -> liveness: só diz que o processo está de pé (barato, sem I/O).
- GET /health/ready  -> readiness: verifica DB, Redis e RabbitMQ. O resultado fica em
  cache por alguns segundos para o endpoint não virar amplificador de carga, e a
  resposta pública traz apenas "ok"/"erro" (detalhes vão só para o log do servidor).
"""

import asyncio
import logging
import time

import aio_pika
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])

READY_CACHE_TTL_SECONDS = 5.0
CHECK_TIMEOUT_SECONDS = 3.0
_ready_cache: dict[str, object] = {"at": 0.0, "checks": None}
_ready_lock = asyncio.Lock()


async def _check_db() -> bool:
    try:
        async with SessionLocal() as session:
            await session.scalar(text("SELECT postgis_version()"))
        return True
    except Exception:
        logger.exception("health: falha ao verificar o banco")
        return False


async def _check_redis(client: Redis) -> bool:
    try:
        await client.ping()
        return True
    except Exception:
        logger.exception("health: falha ao verificar o Redis")
        return False


async def _check_rabbitmq() -> bool:
    try:
        connection = await aio_pika.connect(get_settings().rabbitmq_url, timeout=3)
        await connection.close()
        return True
    except Exception:
        logger.exception("health: falha ao verificar o RabbitMQ")
        return False


async def _with_timeout(name: str, coro) -> bool:
    try:
        return await asyncio.wait_for(coro, timeout=CHECK_TIMEOUT_SECONDS)
    except TimeoutError:
        logger.error("health: timeout ao verificar %s", name)
        return False


async def _run_checks(redis_client: Redis) -> dict[str, str]:
    db, redis_ok, rabbit = await asyncio.gather(
        _with_timeout("banco", _check_db()),
        _with_timeout("redis", _check_redis(redis_client)),
        _with_timeout("rabbitmq", _check_rabbitmq()),
    )
    return {
        "database": "ok" if db else "erro",
        "redis": "ok" if redis_ok else "erro",
        "rabbitmq": "ok" if rabbit else "erro",
    }


@router.get("/health")
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def readiness(request: Request) -> JSONResponse:
    async with _ready_lock:
        now = time.monotonic()
        checks = _ready_cache["checks"]
        if checks is None or now - float(_ready_cache["at"]) > READY_CACHE_TTL_SECONDS:
            checks = await _run_checks(request.app.state.redis)
            _ready_cache.update(at=now, checks=checks)

    healthy = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ok" if healthy else "degraded", "checks": checks},
    )
