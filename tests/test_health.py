from fastapi.testclient import TestClient

from app.api import health
from app.db.session import get_session
from app.main import app


async def _fake_session():
    yield None


def _client(monkeypatch, db="ok (postgis 3.4)", redis="ok", rabbit="ok") -> TestClient:
    async def fake_db(_session):
        return db

    async def fake_redis():
        return redis

    async def fake_rabbit():
        return rabbit

    monkeypatch.setattr(health, "_check_db", fake_db)
    monkeypatch.setattr(health, "_check_redis", fake_redis)
    monkeypatch.setattr(health, "_check_rabbitmq", fake_rabbit)
    app.dependency_overrides[get_session] = _fake_session
    return TestClient(app)


def test_root():
    response = TestClient(app).get("/")
    assert response.status_code == 200
    assert response.json()["app"] == "logistica-api"


def test_health_ok(monkeypatch):
    response = _client(monkeypatch).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_degraded_when_a_service_fails(monkeypatch):
    response = _client(monkeypatch, redis="erro: ConnectionError").get("/health")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["redis"] == "erro: ConnectionError"
