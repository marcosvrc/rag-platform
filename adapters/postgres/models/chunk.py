"""Tabela `chunks` (RAG-011, entidade `Chunk` de RAG-010).

`embedding` usa o tipo `vector` do pgvector (pacote `pgvector`, biblioteca
de infraestrutura — por isso vive em `adapters/`, nunca em
`packages/domain`, ver seção 5.1 do plano) com dimensão fixa 1.024 desde
a migration 0006 (RAG-030): o modelo escolhido foi o Qwen3-Embedding-0.6B
(self-hospedado via Ollama, atrás do gateway LiteLLM — RAG-025, decisão
de produto do RAG-030), cuja dimensão nativa já é 1.024. O índice ANN
(HNSW, `vector_cosine_ops`) também é criado na migration 0006, junto com
a dimensão — um índice ANN não pode existir sem uma dimensão fixa.

`content_tsv` (RAG-031, migration 0004) é uma coluna GERADA pelo
Postgres (`GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED`)
com um índice GIN — nunca escrita pela aplicação (SQLAlchemy trata
`Computed(...)` como somente leitura); ver
`adapters/lexical_search/postgres.py` para a busca que usa esse índice,
e `adapters/vector_search/postgres.py` para a busca vetorial equivalente
sobre `embedding`.
"""

from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, Computed, ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from adapters.postgres.base import Base


class ChunkModel(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        # Isolamento por tenant (RAG-011): chunk é a tabela mais quente
        # do caminho de retrieval, por isso carrega tenant_id/
        # knowledge_base_id diretamente (sem exigir join), com índice.
        Index("ix_chunks_tenant_id", "tenant_id"),
        Index("ix_chunks_knowledge_base_id", "knowledge_base_id"),
        Index("ix_chunks_version_id", "version_id"),
        # RAG-031: índice GIN sobre `content_tsv` para busca lexical.
        Index("ix_chunks_content_tsv", "content_tsv", postgresql_using="gin"),
        # RAG-030: índice HNSW (mesma métrica de cosseno usada por
        # `adapters/vector_search/postgres.py` via `cosine_distance`)
        # sobre `embedding` para busca vetorial.
        Index(
            "ix_chunks_embedding_hnsw_cosine",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        CheckConstraint("token_count >= 1", name="token_count_positive"),
        CheckConstraint("page IS NULL OR page >= 1", name="page_positive"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("tenants.id", name="fk_chunks_tenant_id_tenants"), nullable=False
    )
    knowledge_base_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", name="fk_chunks_knowledge_base_id_knowledge_bases"),
        nullable=False,
    )
    version_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "document_versions.id",
            name="fk_chunks_version_id_document_versions",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(nullable=False)
    token_count: Mapped[int] = mapped_column(nullable=False)
    page: Mapped[int | None] = mapped_column(nullable=True)
    section: Mapped[str | None] = mapped_column(nullable=True)
    # Atributo com sufixo `_` porque `metadata` já é reservado pelo
    # SQLAlchemy (Base.metadata); a coluna real no banco chama-se `metadata`.
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default="{}"
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024), nullable=True)
    content_tsv: Mapped[str | None] = mapped_column(
        TSVECTOR, Computed("to_tsvector('simple', content)", persisted=True), nullable=True
    )
