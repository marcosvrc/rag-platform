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


@lru_cache
def get_engine() -> AsyncEngine:
    """Engine assíncrono (driver `asyncpg`), cacheado por processo — um
    único pool de conexões por processo, não um por request."""
    return create_async_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Fábrica de sessões, vinculada ao engine cacheado por processo."""
    return async_sessionmaker(bind=get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Dependency (`fastapi.Depends`) que entrega uma `AsyncSession` por
    request, fechando-a ao final (mesmo em caso de exceção)."""
    async with get_session_factory()() as session:
        yield session
