"""add tsvector column and GIN index for lexical search on chunks (RAG-031)

Escopo previsto desde a migration 0002 (ver adapters/postgres/models/
chunk.py e o comentario da migration 0002): nenhuma coluna
tsvector/indice GIN de busca lexical foi criada ate agora -- isso
ficou para RAG-031.

`content_tsv` e uma coluna GERADA (GENERATED ALWAYS AS ... STORED,
suportado desde o PostgreSQL 12 -- o docker-compose usa
pgvector/pgvector:pg16): o Postgres recalcula automaticamente sempre
que `content` muda, entao a aplicacao nunca escreve nela diretamente
(SQLAlchemy trata uma coluna com `Computed(...)` como somente leitura,
nunca incluida em INSERT/UPDATE).

Configuracao de busca `simple` (nao `portuguese` nem outro idioma
especifico): o conteudo de um chunk pode estar em qualquer idioma que
o tenant carregar (a plataforma nao pergunta o idioma do documento em
nenhum lugar do fluxo de upload/RAG-021) -- `simple` faz tokenizacao
sem stemming especifico de idioma, um denominador comum seguro. Se um
idioma dominante for conhecido no futuro (por base de conhecimento,
por exemplo), trocar para uma configuracao dedicada e uma migration
nova, nao uma edicao desta.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-29 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chunks",
        sa.Column(
            "content_tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple', content)", persisted=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_chunks_content_tsv", "chunks", ["content_tsv"], postgresql_using="gin"
    )


def downgrade() -> None:
    op.drop_index("ix_chunks_content_tsv", table_name="chunks")
    op.drop_column("chunks", "content_tsv")
