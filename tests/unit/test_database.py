"""Testes de RAG-006: engine/sessão do SQLAlchemy e integridade das
migrations do Alembic.

Nenhum teste aqui se conecta a um banco real (seção 1 do plano): a
criação do `AsyncEngine` é preguiçosa (não conecta até a primeira query),
e a validação das migrations usa a API do Alembic em modo offline
(`ScriptDirectory`), a mesma usada por `alembic upgrade head --sql`.

As tabelas do schema em si (RAG-011) têm testes próprios em
`test_schema.py`; aqui só se valida que `Base.metadata` as contém
(importar `adapters.postgres.models` é o que as registra).
"""

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

import adapters.postgres.models  # noqa: F401 — registra as tabelas em Base.metadata
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
    monkeypatch.setenv("JWT_ISSUER", "rag-platform-tests")
    monkeypatch.setenv("JWT_AUDIENCE", "rag-platform-tests-api")


def test_base_metadata_registers_all_core_tables(configured_env: None) -> None:
    """RAG-006 só cria a infraestrutura; as tabelas do modelo mínimo
    (seção 9 do plano) chegam em RAG-011, uma por entidade de RAG-010.
    `document_idempotency_keys` e `audit_events` são exceções: nenhuma
    é uma entidade de domínio, são infraestrutura de aplicação
    (Idempotency-Key, RAG-021, migration 0003; trilho de auditoria,
    RAG-054, migration 0005)."""
    assert set(Base.metadata.tables) == {
        "tenants",
        "knowledge_bases",
        "documents",
        "document_versions",
        "document_idempotency_keys",
        "chunks",
        "index_jobs",
        "query_logs",
        "query_evidences",
        "feedbacks",
        "evaluation_runs",
        "audit_events",
    }


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
    partir da base, e que ela é a migration 0005 (audit_events,
    RAG-054), encadeada corretamente depois da 0004 (índice de busca
    lexical, RAG-031), da 0003 (document_idempotency_keys, RAG-021),
    da 0002 (schema inicial, RAG-011) e da 0001 (pgvector)."""
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    script = ScriptDirectory.from_config(config)

    heads = script.get_heads()
    assert heads == ["0005"]

    revisions = list(script.walk_revisions())
    assert [r.revision for r in revisions] == ["0005", "0004", "0003", "0002", "0001"]
    assert revisions[0].down_revision == "0004"
    assert revisions[1].down_revision == "0003"
    assert revisions[2].down_revision == "0002"
    assert revisions[3].down_revision == "0001"
    assert revisions[4].down_revision is None
    assert "audit_events" in (revisions[0].doc or "")
    assert "lexical search" in (revisions[1].doc or "")
    assert "document_idempotency_keys" in (revisions[2].doc or "")
    assert "core schema" in (revisions[3].doc or "")
    assert "pgvector" in (revisions[4].doc or "")
