"""fix chunk embedding dimension and create ANN index (RAG-030)

Decisão de produto tomada: embeddings via Qwen3-Embedding-0.6B
(self-hospedado via Ollama, atrás do gateway LiteLLM — RAG-025),
dimensão 1.024 (ver `adapters/postgres/models/chunk.py` para o
racional completo e `adapters/vector_search/postgres.py` para a busca
que passa a poder existir agora que a dimensão está fixada).

`chunks.embedding` foi criada em 0002 como `vector` SEM dimensão
(RAG-011), porque RAG-025 deliberadamente não escolheu o modelo ainda
naquele momento. Esta migration corrige o tipo da coluna para
`vector(1024)` — linhas com `embedding IS NULL` não são afetadas pelo
`USING`; só haveria erro aqui se já existisse uma linha com embedding
de dimensão diferente de 1024, o que não é o caso (nenhum pipeline de
indexação real rodou ainda contra este schema).

Índice HNSW (`vector_cosine_ops`, a métrica que
`adapters/vector_search/postgres.py` usa via `cosine_distance`) —
escolhido em vez de ivfflat porque não exige um passo de "treino" com
dados existentes (relevante aqui: a tabela está vazia até agora) e é a
recomendação atual do próprio pgvector para a maioria dos casos.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-29 00:00:00
"""

from collections.abc import Sequence

from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_INDEX_NAME = "ix_chunks_embedding_hnsw_cosine"


def upgrade() -> None:
    op.alter_column(
        "chunks",
        "embedding",
        type_=Vector(1024),
        existing_type=Vector(),
        postgresql_using="embedding::vector(1024)",
        existing_nullable=True,
    )
    op.create_index(
        _INDEX_NAME,
        "chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="chunks")
    op.alter_column(
        "chunks",
        "embedding",
        type_=Vector(),
        existing_type=Vector(1024),
        existing_nullable=True,
    )
