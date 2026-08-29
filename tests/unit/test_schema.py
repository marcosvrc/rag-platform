"""Testes de RAG-011: schema inicial (tabelas, constraints, índices).

Nenhum teste aqui se conecta a um banco real (mesma abordagem de
test_database.py): tudo é verificado a partir de `Base.metadata`, a
mesma fonte que gera a migration 0002 e o SQL de
`alembic upgrade head --sql`.

O critério de aceite "isolamento por tenant é verificável" é conferido
no nível de schema: toda tabela que carrega `tenant_id` diretamente
(`knowledge_bases`, `chunks`, `query_logs` — ver seção 9 do plano) tem
essa coluna NOT NULL, com FK para `tenants.id` e com um índice próprio,
de modo que filtrar por tenant é sempre uma consulta indexada e nunca
depende de aplicação correta de um filtro opcional. Um teste de
isolamento fim a fim contra um Postgres real (inserir dois tenants e
provar que uma consulta de um não vê o outro) pertence a
`tests/integration/`, quando essa suíte existir com um banco de
verdade — o que este teste NÃO tenta simular com SQLite, já que
`chunks`/`knowledge_bases` usam tipos específicos do Postgres
(`vector`, `JSONB`).
"""

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, UniqueConstraint

import adapters.postgres.models  # noqa: F401 — registra as tabelas em Base.metadata
from adapters.postgres.base import Base

TENANT_SCOPED_TABLES = {"knowledge_bases", "chunks", "query_logs"}


def _indexed_columns(table_name: str) -> set[str]:
    """Nomes de coluna cobertos por algum índice ou PK/UNIQUE composta
    (que também aceleram um filtro pelo primeiro campo, no Postgres)."""
    table = Base.metadata.tables[table_name]
    covered: set[str] = set()
    for index in table.indexes:
        first_col = next(iter(index.columns))
        covered.add(first_col.name)
    for constraint in table.constraints:
        columns = list(getattr(constraint, "columns", []))
        if columns:
            covered.add(columns[0].name)
    return covered


def test_every_table_has_a_primary_key() -> None:
    for table in Base.metadata.tables.values():
        assert table.primary_key is not None
        assert len(table.primary_key.columns) >= 1, table.name


def test_tenant_scoped_tables_have_tenant_id_not_null_fk_and_index() -> None:
    for table_name in TENANT_SCOPED_TABLES:
        table = Base.metadata.tables[table_name]
        tenant_id = table.columns["tenant_id"]

        assert not tenant_id.nullable, f"{table_name}.tenant_id deveria ser NOT NULL"

        fk_targets = {fk.target_fullname for fk in tenant_id.foreign_keys}
        assert "tenants.id" in fk_targets, f"{table_name}.tenant_id deveria referenciar tenants.id"

        assert "tenant_id" in _indexed_columns(table_name), (
            f"{table_name}.tenant_id deveria ter um índice (isolamento por tenant verificável)"
        )


def test_tables_without_direct_tenant_id_are_scoped_transitively() -> None:
    """Document, DocumentVersion, IndexJob, Feedback e QueryEvidence não
    têm `tenant_id` próprio (não está na entidade, seção 9 do plano) —
    mas cada um tem uma FK que leva de volta a uma tabela com tenant_id,
    então o isolamento continua verificável via join."""
    transitively_scoped = {
        "documents": "knowledge_base_id",
        "document_versions": "document_id",
        "index_jobs": "document_id",
        "feedbacks": "query_id",
        "query_evidences": "query_id",
    }
    for table_name, fk_column in transitively_scoped.items():
        table = Base.metadata.tables[table_name]
        assert fk_column in table.columns, f"{table_name} deveria ter a coluna {fk_column}"
        assert table.columns[fk_column].foreign_keys, (
            f"{table_name}.{fk_column} deveria ser uma FK (join de volta até um tenant)"
        )


def test_foreign_keys_reference_the_expected_tables() -> None:
    expected: dict[str, set[str]] = {
        "knowledge_bases": {"tenants.id"},
        "documents": {"knowledge_bases.id", "document_versions.id"},
        "document_versions": {"documents.id"},
        "chunks": {"tenants.id", "knowledge_bases.id", "document_versions.id"},
        "index_jobs": {"documents.id"},
        "query_logs": {"tenants.id", "knowledge_bases.id"},
        "query_evidences": {"query_logs.id", "chunks.id"},
        "feedbacks": {"query_logs.id"},
    }
    for table_name, expected_targets in expected.items():
        table = Base.metadata.tables[table_name]
        actual_targets = {fk.target_fullname for col in table.columns for fk in col.foreign_keys}
        assert actual_targets == expected_targets, table_name


def test_documents_active_version_id_fk_uses_alter_to_break_the_cycle() -> None:
    """documents.active_version_id <-> document_versions.document_id é
    circular; a FK precisa ser `use_alter=True` para o Alembic conseguir
    ordenar `CREATE TABLE`s sem depender de uma tabela que ainda não
    existe (ver migration 0002)."""
    documents = Base.metadata.tables["documents"]
    (fk,) = documents.columns["active_version_id"].foreign_keys
    assert fk.constraint is not None
    assert fk.constraint.use_alter is True


def test_unique_constraints_enforce_expected_natural_keys() -> None:
    expected_unique_columns: dict[str, set[str]] = {
        "knowledge_bases": {"tenant_id", "name"},
        "documents": {"knowledge_base_id", "checksum"},
        "document_versions": {"document_id", "version"},
        "query_evidences": {"query_id", "position"},
    }
    for table_name, expected_columns in expected_unique_columns.items():
        table = Base.metadata.tables[table_name]
        unique_column_sets = {
            frozenset(col.name for col in uq.columns)
            for uq in table.constraints
            if isinstance(uq, UniqueConstraint)
        }
        assert frozenset(expected_columns) in unique_column_sets, table_name


def test_check_constraints_enforce_non_negative_and_positive_invariants() -> None:
    expected_check_sql_fragments: dict[str, list[str]] = {
        "document_versions": ["version >= 1"],
        "chunks": ["token_count >= 1", "page IS NULL OR page >= 1"],
        "index_jobs": ["attempts >= 0"],
        "query_logs": ["latency_ms >= 0", "input_tokens >= 0", "output_tokens >= 0"],
        "query_evidences": [
            "retrieval_score >= 0",
            "rerank_score IS NULL OR rerank_score >= 0",
            "position >= 0",
        ],
    }
    for table_name, fragments in expected_check_sql_fragments.items():
        table = Base.metadata.tables[table_name]
        check_sqltexts = [
            str(c.sqltext) for c in table.constraints if isinstance(c, CheckConstraint)
        ]
        for fragment in fragments:
            assert any(fragment in text for text in check_sqltexts), (
                f"esperava um CHECK contendo {fragment!r} em {table_name}"
            )


def test_chunk_embedding_column_has_no_fixed_dimension_yet() -> None:
    """Decisão registrada em RAG-011: a dimensão do embedding depende do
    modelo/alias escolhido em RAG-025, então a coluna fica sem dimensão
    fixa por enquanto (e, por isso, sem índice ANN — isso é RAG-030)."""
    chunks = Base.metadata.tables["chunks"]
    embedding_type = chunks.columns["embedding"].type

    assert isinstance(embedding_type, Vector)
    assert embedding_type.dim is None
