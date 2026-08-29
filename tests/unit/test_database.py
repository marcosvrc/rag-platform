"""Testes de RAG-006: engine/sessão do SQLAlchemy e integridade das
migrations do Alembic.

Nenhum teste aqui se conecta a um banco real (seção 1 do plano): a
criação do `AsyncEngine` é preguiçosa (não conecta até a primeira query),
e a validação das migrations usa a API do Alembic em modo offline
(`ScriptDirectory`), a mesma usada por `alembic upgrade head --sql`.
"""

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from adapters.postgres.base import Base
from adapters.postgres.engine import get_engine, get_session_factory

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clear_engine_caches() -> None:
    get_engine.cache_clear()
    get_session_factory.cache_clear()


@pytest.fixture
def configured_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_PASSWORD", "s3cr3t-should-not-leak")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "another-secret-should-not-leak")


def test_base_metadata_starts_empty_before_rag_011(configured_env: None) -> None:
    """RAG-006 só cria a infraestrutura; as tabelas do modelo mínimo
    (seção 9 do plano) chegam em RAG-011."""
    assert list(Base.metadata.tables) == []


def test_get_engine_is_cached_and_never_leaks_the_password_in_repr(
    configured_env: None,
) -> None:
    engine = get_engine()

    assert isinstance(engine, AsyncEngine)
    assert get_engine() is engine  # cache por processo

    rendered = f"{engine!r} {engine.url!r} {engine.url!s}"
    assert "s3cr3t-should-not-leak" not in rendered


def test_get_session_factory_is_bound_to_the_cached_engine(configured_env: None) -> None:
    session_factory = get_session_factory()

    assert isinstance(session_factory, async_sessionmaker)
    assert session_factory.kw["bind"] is get_engine()

    # Instanciar uma sessão não conecta nada (SQLAlchemy é preguiçoso até a
    # primeira query), então isso é seguro sem nenhum banco no ar.
    session = session_factory()
    assert isinstance(session, AsyncSession)


def test_migrations_have_a_single_well_formed_head() -> None:
    """Valida a árvore de revisões do Alembic offline (sem conectar a
    nenhum banco): garante que existe uma única head, alcançável a
    partir da base, e que ela é a migration 0001 (habilita pgvector)."""
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    script = ScriptDirectory.from_config(config)

    heads = script.get_heads()
    assert heads == ["0001"]

    revisions = list(script.walk_revisions())
    assert len(revisions) == 1
    assert revisions[0].down_revision is None
    assert "pgvector" in (revisions[0].doc or "")
