import aio_pika
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_session

router = APIRouter(tags=["health"])


async def _check_db(session: AsyncSession) -> str:
    try:
        version = await session.scalar(text("SELECT postgis_version()"))
        return f"ok (postgis {version})"
    except Exception as exc:  # noqa: BLE001
        return f"erro: {exc.__class__.__name__}"


async def _check_redis() -> str:
    client = Redis.from_url(get_settings().redis_url)
    try:
        await client.ping()
        return "ok"
    except Exception as exc:  # noqa: BLE001
        return f"erro: {exc.__class__.__name__}"
    finally:
        await client.aclose()


async def _check_rabbitmq() -> str:
    try:
        connection = await aio_pika.connect_robust(get_settings().rabbitmq_url, timeout=5)
        await connection.close()
        return "ok"
    except Exception as exc:  # noqa: BLE001
        return f"erro: {exc.__class__.__name__}"


@router.get("/health")
async def health(session: AsyncSession = Depends(get_session)) -> JSONResponse:
    checks = {
        "database": await _check_db(session),
        "redis": await _check_redis(),
        "rabbitmq": await _check_rabbitmq(),
    }
    healthy = all(v.startswith("ok") for v in checks.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ok" if healthy else "degraded", "checks": checks},
    )
