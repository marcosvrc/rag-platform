"""Ambiente do Alembic (RAG-006).

A URL de conexão vem de `packages/config/settings.py` — nunca hardcoded
aqui nem em `alembic.ini`. Usa o engine assíncrono (driver `asyncpg`) via
`connection.run_sync(...)`, o padrão recomendado pelo Alembic para
projetos com SQLAlchemy assíncrono (ver adapters/postgres/engine.py).
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from adapters.postgres.base import Base
from packages.config.settings import get_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metadata usado por `alembic revision --autogenerate` (o schema de
# negócio em si, seção 9 do plano, é criado a partir de RAG-011).
target_metadata = Base.metadata


def _get_url() -> str:
    return get_settings().database_url


def run_migrations_offline() -> None:
    """Gera o SQL das migrations sem se conectar a um banco real
    (`alembic upgrade head --sql` / `alembic downgrade base --sql`)."""
    context.configure(
        url=_get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Conecta de verdade (driver assíncrono) e aplica as migrations."""
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _get_url()
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
