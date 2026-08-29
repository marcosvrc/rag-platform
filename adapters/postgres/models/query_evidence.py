"""Tabela `query_evidences` (RAG-011, entidade `QueryEvidence` de RAG-010).

A entidade não tem `id` próprio (é um objeto de ligação entre uma
consulta e um chunk recuperado); a chave primária composta
`(query_id, chunk_id)` é a chave natural — um chunk aparece no máximo
uma vez como evidência de uma mesma consulta.
"""

from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from adapters.postgres.base import Base


class QueryEvidenceModel(Base):
    __tablename__ = "query_evidences"
    __table_args__ = (
        Index("ix_query_evidences_chunk_id", "chunk_id"),
        # Garante que o ranking de uma consulta não tem duas evidências
        # na mesma posição.
        UniqueConstraint("query_id", "position", name="uq_query_evidences_query_id_position"),
        CheckConstraint("retrieval_score >= 0", name="retrieval_score_non_negative"),
        CheckConstraint(
            "rerank_score IS NULL OR rerank_score >= 0",
            name="rerank_score_non_negative",
        ),
        CheckConstraint("position >= 0", name="position_non_negative"),
    )

    query_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "query_logs.id", name="fk_query_evidences_query_id_query_logs", ondelete="CASCADE"
        ),
        primary_key=True,
    )
    chunk_id: Mapped[UUID] = mapped_column(
        ForeignKey("chunks.id", name="fk_query_evidences_chunk_id_chunks"),
        primary_key=True,
    )
    retrieval_score: Mapped[float] = mapped_column(nullable=False)
    rerank_score: Mapped[float | None] = mapped_column(nullable=True)
    position: Mapped[int] = mapped_column(nullable=False)
