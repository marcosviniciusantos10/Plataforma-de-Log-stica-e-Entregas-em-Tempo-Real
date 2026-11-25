"""Variáveis de ambiente para os testes, para rodar sem .env (ex.: CI)."""

import os

_TEST_ENV = {
    "APP_ENV": "test",
    "POSTGRES_USER": "test",
    "POSTGRES_PASSWORD": "test-password-123",
    "POSTGRES_DB": "test",
    "POSTGRES_HOST": "localhost",
    "REDIS_PASSWORD": "test-password-123",
    "REDIS_HOST": "localhost",
    "RABBITMQ_USER": "test",
    "RABBITMQ_PASSWORD": "test-password-123",
    "RABBITMQ_HOST": "localhost",
}

# Executado antes da coleta: o Settings é criado no import de app.main.
for key, value in _TEST_ENV.items():
    os.environ.setdefault(key, value)
