# Plataforma de Logística e Entregas em Tempo Real

API em **FastAPI** com **PostgreSQL + PostGIS**, **Redis** e **RabbitMQ**, orquestrada via Docker Compose.

## Estrutura

```
app/
  api/        # rotas HTTP / WebSocket
  core/       # configurações (lidas do .env)
  db/         # engine, sessão e Base do SQLAlchemy
  models/     # modelos ORM (GeoAlchemy2 para geometrias)
  schemas/    # modelos Pydantic de entrada/saída
  services/   # regras de negócio
  workers/    # consumidores RabbitMQ
migrations/   # Alembic
scripts/      # utilitários (ex.: criar ADMIN)
tests/
```

## Subindo o ambiente

```bash
cp .env.example .env        # ajuste as senhas
docker compose up -d --build
curl http://localhost:8000/health        # liveness
curl http://localhost:8000/health/ready  # DB, Redis e RabbitMQ
```

O container da API roda `alembic upgrade head` antes de iniciar, como usuário sem privilégios.

> O `docker-compose.yml` é voltado para **desenvolvimento** (código montado como volume e `--reload`).
> Em produção, defina `APP_ENV=production` (desativa `/docs`) e use senhas fortes no `.env`.
> Atrás de um proxy reverso, rode o uvicorn com `--proxy-headers --forwarded-allow-ips=<IP do proxy>`:
> sem isso, o limite de tentativas de login enxerga todos os clientes com o IP do proxy.

| Serviço   | Porta(s)                 |
|-----------|--------------------------|
| API       | 8000 (docs em `/docs`)   |
| PostGIS   | 5432 (só 127.0.0.1)      |
| Redis     | 6379 (só 127.0.0.1, com senha) |
| RabbitMQ  | 5672 · painel em 15672 (só 127.0.0.1) |

## Autenticação e perfis (RBAC)

- Senhas com **Argon2id**; access token **JWT HS256** (15 min) e refresh token **opaco** (7 dias)
  guardado no Redis apenas como SHA-256, com **rotação** a cada uso e **detecção de reuso**
  (reusar um refresh já trocado revoga a sessão inteira).
- Login bloqueado (429) após 5 falhas em 15 min para o mesmo e-mail + IP. Erro de login é sempre genérico.
- Perfis: `CLIENTE`, `LOJISTA`, `ENTREGADOR` e `ADMIN`. O cadastro público não aceita `ADMIN`.
- `JWT_SECRET` é obrigatório (mín. 32 caracteres):
  `python -c "import secrets;print(secrets.token_urlsafe(48))"`

| Método | Rota | Acesso |
|---|---|---|
| POST | `/auth/register`, `/auth/login`, `/auth/refresh`, `/auth/logout` | público |
| GET | `/users/me` | autenticado |
| GET | `/admin/users` · PATCH `/admin/users/{id}/role` | ADMIN |
| GET | `/cliente/ping`, `/lojista/ping`, `/entregador/ping` | perfil respectivo (ADMIN acessa todas) |

Criar o primeiro ADMIN. **Use o modo interativo** (recomendado): o script pede e-mail,
nome e senha, e a senha é digitada sem eco (`getpass`):

```bash
docker compose exec api python -m scripts.create_admin
```

> **Não** passe a senha na linha de comando (`docker compose exec -e ADMIN_PASSWORD=... api ...`):
> ela fica no histórico do shell e visível em `ps`. As variáveis `ADMIN_EMAIL`, `ADMIN_PASSWORD`
> e `ADMIN_FULL_NAME` existem só para automação (CI) que injeta segredos de um cofre, nunca digitados.

### Limitações conhecidas

| # | Limitação | Melhoria futura |
|---|---|---|
| L1 | O access token (JWT) continua válido até expirar, **no máximo 15 min**, mesmo após logout ou revogação da sessão. Perfil e `is_active` são lidos do banco a cada request, então troca de perfil e desativação valem na hora. | Denylist de `jti` no Redis (com TTL até o `exp`) consultada no `get_current_user`. |
| L2 | Sessão **deslizante**: cada refresh emite um token novo com mais 7 dias, então uma sessão usada com frequência não expira. | Prazo absoluto por família (ex.: 30 dias desde o login), gravado na criação da família. |
| L3 | Dois refreshes **simultâneos** com o mesmo token são tratados como reuso e derrubam a sessão. O front-end deve fazer **um refresh por vez** (fila/lock no cliente). | Janela de tolerância curta (ex.: 2–5 s) em que o reuso devolve o mesmo par recém-emitido em vez de revogar. |

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
