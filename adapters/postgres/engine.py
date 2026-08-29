"""Engine e fábrica de sessões assíncronas do SQLAlchemy (RAG-006)."""

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from packages.config.settings import get_settings
from packages.observability.tracing import instrument_sqlalchemy_engine


@lru_cache
def get_engine() -> AsyncEngine:
    """Engine assíncrono (driver `asyncpg`), cacheado por processo — um
    único pool de conexões por processo, não um por request.

    Instrumentado para tracing (RAG-052) aqui, não globalmente: um
    `instrument(engine=...)` por instância, porque testes recriam o
    engine cacheado via `get_engine.cache_clear()` (ver
    `tests/unit/test_database.py`) e cada instância nova precisa da sua
    própria instrumentação."""
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    instrument_sqlalchemy_engine(engine.sync_engine)
    return engine


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Fábrica de sessões, vinculada ao engine cacheado por processo."""
    return async_sessionmaker(bind=get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Dependency (`fastapi.Depends`) que entrega uma `AsyncSession` por
    request, fechando-a ao final (mesmo em caso de exceção)."""
    async with get_session_factory()() as session:
        yield session
