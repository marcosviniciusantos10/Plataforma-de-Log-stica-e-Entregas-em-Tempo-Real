import pytest
from fastapi.testclient import TestClient

from app.api import health
from app.main import app


@pytest.fixture(autouse=True)
def _reset_cache():
    health._ready_cache.update(at=0.0, checks=None)
    yield


def _patch(monkeypatch, db=True, redis=True, rabbit=True, calls=None):
    async def fake_db():
        if calls is not None:
            calls.append("db")
        return db

    async def fake_redis(_client):
        return redis

    async def fake_rabbit():
        return rabbit

    monkeypatch.setattr(health, "_check_db", fake_db)
    monkeypatch.setattr(health, "_check_redis", fake_redis)
    monkeypatch.setattr(health, "_check_rabbitmq", fake_rabbit)


def test_root_does_not_expose_environment():
    with TestClient(app) as client:
        body = client.get("/").json()
    assert body == {"app": "logistica-api"}


def test_liveness():
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_ok(monkeypatch):
    _patch(monkeypatch)
    with TestClient(app) as client:
        response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"database": "ok", "redis": "ok", "rabbitmq": "ok"},
    }


def test_ready_degraded_hides_internal_details(monkeypatch):
    _patch(monkeypatch, redis=False)
    with TestClient(app) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["redis"] == "erro"


def test_ready_returns_503_quickly_when_a_check_hangs(monkeypatch):
    import asyncio
    import time

    _patch(monkeypatch)

    async def hanging_db():
        await asyncio.sleep(3600)

    monkeypatch.setattr(health, "_check_db", hanging_db)
    monkeypatch.setattr(health, "CHECK_TIMEOUT_SECONDS", 0.2)
    with TestClient(app) as client:
        start = time.monotonic()
        response = client.get("/health/ready")
        elapsed = time.monotonic() - start
    assert response.status_code == 503
    assert response.json()["checks"]["database"] == "erro"
    assert elapsed < 2


def test_ready_result_is_cached(monkeypatch):
    calls: list[str] = []
    _patch(monkeypatch, calls=calls)
    with TestClient(app) as client:
        client.get("/health/ready")
        client.get("/health/ready")
    assert calls == ["db"]
