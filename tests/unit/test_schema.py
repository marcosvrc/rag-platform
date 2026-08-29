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

TENANT_SCOPED_TABLES = {
    "knowledge_bases",
    "chunks",
    "query_logs",
    # document_idempotency_keys não é uma entidade de domínio (RAG-021,
    # migration 0003) mas carrega tenant_id direto, então a mesma
    # verificação de isolamento vale para ela.
    "document_idempotency_keys",
    # audit_events (RAG-054, migration 0005): mesmo caso de
    # document_idempotency_keys — infraestrutura de aplicação, não uma
    # entidade de domínio, mas com tenant_id direto.
    "audit_events",
}


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
        "document_idempotency_keys": {
            "tenants.id",
            "knowledge_bases.id",
            "documents.id",
            "document_versions.id",
            "index_jobs.id",
        },
        # audit_events.resource_id é polimórfico (aponta para
        # knowledge_bases.id OU documents.id, dependendo de
        # resource_type) — por isso não é FK, só tenant_id é.
        "audit_events": {"tenants.id"},
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
        "document_idempotency_keys": {"tenant_id", "knowledge_base_id", "idempotency_key"},
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


def test_chunk_embedding_column_has_a_fixed_dimension() -> None:
    """RAG-030 (migration 0006): a dimensão foi fixada em 1.024 —
    dimensão nativa do modelo escolhido, Qwen3-Embedding-0.6B
    (self-hospedado via Ollama, atrás do gateway LiteLLM). Um índice
    ANN (abaixo) não pode existir sem uma dimensão fixa."""
    chunks = Base.metadata.tables["chunks"]
    embedding_type = chunks.columns["embedding"].type

    assert isinstance(embedding_type, Vector)
    assert embedding_type.dim == 1024


def test_chunks_embedding_has_an_hnsw_index() -> None:
    """RAG-030 (migration 0006): índice HNSW com `vector_cosine_ops` —
    a mesma métrica (distância de cosseno) que
    `adapters/vector_search/postgres.py` usa via `cosine_distance` —
    para que a busca vetorial use o índice em vez de fazer table scan
    (critério de aceite "usa índice pgvector")."""
    chunks = Base.metadata.tables["chunks"]

    hnsw_indexes = [
        index
        for index in chunks.indexes
        if index.name == "ix_chunks_embedding_hnsw_cosine"
        and index.dialect_options["postgresql"]["using"] == "hnsw"
    ]
    assert len(hnsw_indexes) == 1
    (hnsw_index,) = hnsw_indexes
    assert {col.name for col in hnsw_index.columns} == {"embedding"}
    assert hnsw_index.dialect_options["postgresql"]["ops"] == {"embedding": "vector_cosine_ops"}


def test_chunks_content_tsv_has_a_gin_index() -> None:
    """RAG-031 (migration 0004): `content_tsv` é uma coluna gerada
    (`Computed`) com um índice GIN — o que faz a busca lexical usar o
    índice em vez de fazer table scan (critério de aceite "índice GIN
    utilizado")."""
    chunks = Base.metadata.tables["chunks"]
    content_tsv = chunks.columns["content_tsv"]

    assert content_tsv.computed is not None

    gin_indexes = [
        index
        for index in chunks.indexes
        if index.name == "ix_chunks_content_tsv"
        and index.dialect_options["postgresql"]["using"] == "gin"
    ]
    assert len(gin_indexes) == 1
    (gin_index,) = gin_indexes
    assert {col.name for col in gin_index.columns} == {"content_tsv"}


def test_audit_events_required_columns_are_not_nullable() -> None:
    """RAG-054 (migration 0005): ator, ação, tipo/id de recurso e
    timestamp são sempre obrigatórios — um evento de auditoria
    incompleto não é um evento de auditoria válido."""
    audit_events = Base.metadata.tables["audit_events"]
    for column_name in ("actor", "action", "resource_type", "resource_id", "occurred_at"):
        assert not audit_events.columns[column_name].nullable, column_name


def test_audit_events_resource_id_is_not_a_foreign_key() -> None:
    """resource_id é polimórfico (RAG-054): aponta para
    knowledge_bases.id OU documents.id dependendo de resource_type, e
    uma FK exigiria uma única tabela de destino."""
    audit_events = Base.metadata.tables["audit_events"]
    assert not audit_events.columns["resource_id"].foreign_keys
