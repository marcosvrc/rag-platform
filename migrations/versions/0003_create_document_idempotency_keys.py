"""create document_idempotency_keys (RAG-021)

Suporte a idempotência do upload de documentos (seção 8 do plano:
"endpoints de criação devem aceitar Idempotency-Key"). Uma linha aqui
mapeia uma `Idempotency-Key` (por tenant + base de conhecimento) para
o Document/DocumentVersion/IndexJob criados na primeira vez que ela foi
usada — uma repetição da mesma chave devolve essa mesma tripla em vez
de criar um documento novo (ver
`adapters/document_repository/postgres.py`).

A unique constraint em (tenant_id, knowledge_base_id, idempotency_key)
é o que faz isso valer sob concorrência: duas requisições simultâneas
com a mesma chave não podem inserir duas linhas aqui, então no máximo
uma "vence" — a outra recebe a violação e busca a linha vencedora (ver
`PostgresDocumentRepository.create_document`).

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-29 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "document_idempotency_keys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("index_job_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_document_idempotency_keys"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_document_idempotency_keys_tenant_id_tenants",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_bases.id"],
            name="fk_document_idempotency_keys_knowledge_base_id_knowledge_bases",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_document_idempotency_keys_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            name="fk_document_idempotency_keys_document_version_id_document_versions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["index_job_id"],
            ["index_jobs.id"],
            name="fk_document_idempotency_keys_index_job_id_index_jobs",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "knowledge_base_id",
            "idempotency_key",
            name="uq_document_idempotency_keys_tenant_id_knowledge_base_id_key",
        ),
    )
    op.create_index(
        "ix_document_idempotency_keys_tenant_id", "document_idempotency_keys", ["tenant_id"]
    )
    op.create_index(
        "ix_document_idempotency_keys_knowledge_base_id",
        "document_idempotency_keys",
        ["knowledge_base_id"],
    )
    op.create_index(
        "ix_document_idempotency_keys_document_id", "document_idempotency_keys", ["document_id"]
    )


def downgrade() -> None:
    op.drop_table("document_idempotency_keys")
