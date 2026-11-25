# Plataforma de Logística e Entregas em Tempo Real

API em **FastAPI** com **PostgreSQL + PostGIS**, **Redis** e **RabbitMQ**, orquestrada via Docker Compose.

## Estrutura

```
app/
  api/        # rotas HTTP / WebSocket
  core/       # configurações (lidas do .env)
  db/         # engine, sessão e Base do SQLAlchemy
  models/     # modelos ORM (GeoAlchemy2 para geometrias)
  services/   # regras de negócio
  workers/    # consumidores RabbitMQ
migrations/   # Alembic
tests/
```

## Subindo o ambiente

```bash
cp .env.example .env        # ajuste as senhas
docker compose up -d --build
curl http://localhost:8000/health
```

O container da API roda `alembic upgrade head` antes de iniciar.

| Serviço   | Porta(s)                 |
|-----------|--------------------------|
| API       | 8000 (docs em `/docs`)   |
| PostGIS   | 5432                     |
| Redis     | 6379                     |
| RabbitMQ  | 5672 · painel em 15672   |

## Migrations

```bash
docker compose exec api alembic revision --autogenerate -m "descricao"
docker compose exec api alembic upgrade head
```

## Testes

```bash
pip install -r requirements-dev.txt
pytest -q
```
