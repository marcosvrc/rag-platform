"""Tabela `chunks` (RAG-011, entidade `Chunk` de RAG-010).

`embedding` usa o tipo `vector` do pgvector (pacote `pgvector`, biblioteca
de infraestrutura — por isso vive em `adapters/`, nunca em
`packages/domain`, ver seção 5.1 do plano) **sem dimensão fixa**: o
modelo/alias de embeddings ainda não foi escolhido (isso é RAG-025), e um
índice ANN (ivfflat/hnsw) exige uma dimensão fixa para existir — por
isso a criação desse índice fica para RAG-030 ("usa índice pgvector"),
quando a dimensão real for conhecida. Pelo mesmo motivo, a busca lexical
(RAG-031, "índice GIN utilizado") também não cria aqui uma coluna
`tsvector`/índice GIN: RAG-011 cria só o `content` textual bruto.
"""

from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSONB
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
    embedding: Mapped[list[float] | None] = mapped_column(Vector(), nullable=True)
