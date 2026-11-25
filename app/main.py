from fastapi import FastAPI

from app.api.health import router as health_router
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(title="Plataforma de Logística e Entregas em Tempo Real", version="0.1.0")
app.include_router(health_router)


@app.get("/")
async def root() -> dict[str, str]:
    return {"app": settings.app_name, "env": settings.app_env}
