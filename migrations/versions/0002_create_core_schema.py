"""create core schema (RAG-011)

Cria as tabelas do modelo mínimo de domínio (seção 9 do plano, entidades
de RAG-010): tenants, knowledge_bases, documents, document_versions,
chunks, index_jobs, query_logs, query_evidences, feedbacks e
evaluation_runs.

Escopo desta migration (ver adapters/postgres/models/chunk.py para o
raciocínio completo): a coluna `chunks.embedding` usa o tipo `vector` do
pgvector SEM dimensão fixa — o modelo/alias de embeddings ainda não foi
escolhido (RAG-025) — por isso nenhum índice ANN (ivfflat/hnsw) é criado
aqui; isso é RAG-030. Pelo mesmo motivo, nenhuma coluna `tsvector`/índice
GIN de busca lexical é criada aqui; isso é RAG-031.

`documents.active_version_id` e `document_versions.document_id` se
referenciam mutuamente — por isso a FK de `active_version_id` é
adicionada só no final (`op.create_foreign_key`), depois que as duas
tabelas já existem.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-29 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("dataset_version", sa.String(), nullable=False),
        sa.Column("config_version", sa.String(), nullable=False),
        sa.Column(
            "metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "RUNNING",
                "SUCCEEDED",
                "FAILED",
                name="processing_status",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_evaluation_runs"),
    )

    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("ACTIVE", "SUSPENDED", name="tenant_status", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_tenants"),
    )

    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "ACTIVE", "DELETED", name="knowledge_base_status", native_enum=False, length=32
            ),
            nullable=False,
        ),
        sa.Column(
            "config", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_bases"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_knowledge_bases_tenant_id_tenants"
        ),
        sa.UniqueConstraint("tenant_id", "name", name="uq_knowledge_bases_tenant_id_name"),
    )
    op.create_index("ix_knowledge_bases_tenant_id", "knowledge_bases", ["tenant_id"])

    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("mime_type", sa.String(), nullable=False),
        sa.Column("checksum", sa.String(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "PROCESSING",
                "INDEXED",
                "FAILED",
                "QUARANTINED",
                "DELETED",
                name="document_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("active_version_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_bases.id"],
            name="fk_documents_knowledge_base_id_knowledge_bases",
        ),
        sa.UniqueConstraint(
            "knowledge_base_id", "checksum", name="uq_documents_knowledge_base_id_checksum"
        ),
    )
    op.create_index("ix_documents_knowledge_base_id", "documents", ["knowledge_base_id"])

    op.create_table(
        "query_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("question_hash", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("trace_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("latency_ms >= 0", name="latency_ms_non_negative"),
        sa.CheckConstraint("input_tokens >= 0", name="input_tokens_non_negative"),
        sa.CheckConstraint("output_tokens >= 0", name="output_tokens_non_negative"),
        sa.PrimaryKeyConstraint("id", name="pk_query_logs"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_query_logs_tenant_id_tenants"
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_bases.id"],
            name="fk_query_logs_knowledge_base_id_knowledge_bases",
        ),
    )
    op.create_index("ix_query_logs_tenant_id", "query_logs", ["tenant_id"])
    op.create_index("ix_query_logs_knowledge_base_id", "query_logs", ["knowledge_base_id"])

    op.create_table(
        "document_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("object_key", sa.String(), nullable=False),
        sa.Column("extracted_object_key", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("version >= 1", name="version_positive"),
        sa.PrimaryKeyConstraint("id", name="pk_document_versions"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_document_versions_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "document_id", "version", name="uq_document_versions_document_id_version"
        ),
    )
    op.create_index("ix_document_versions_document_id", "document_versions", ["document_id"])

    op.create_table(
        "feedbacks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("query_id", sa.Uuid(), nullable=False),
        sa.Column(
            "rating",
            sa.Enum("POSITIVE", "NEGATIVE", name="feedback_rating", native_enum=False, length=16),
            nullable=False,
        ),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("expected_answer", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_feedbacks"),
        sa.ForeignKeyConstraint(
            ["query_id"],
            ["query_logs.id"],
            name="fk_feedbacks_query_id_query_logs",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_feedbacks_query_id", "feedbacks", ["query_id"])

    op.create_table(
        "index_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column(
            "type",
            sa.Enum("INDEX", "REINDEX", name="index_job_type", native_enum=False, length=16),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "RUNNING",
                "SUCCEEDED",
                "FAILED",
                name="processing_status",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        sa.PrimaryKeyConstraint("id", name="pk_index_jobs"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_index_jobs_document_id_documents",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_index_jobs_document_id", "index_jobs", ["document_id"])
    op.create_index("ix_index_jobs_status", "index_jobs", ["status"])

    op.create_table(
        "chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("section", sa.String(), nullable=True),
        sa.Column(
            "metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"
        ),
        sa.Column("embedding", Vector(), nullable=True),
        sa.CheckConstraint("token_count >= 1", name="token_count_positive"),
        sa.CheckConstraint("page IS NULL OR page >= 1", name="page_positive"),
        sa.PrimaryKeyConstraint("id", name="pk_chunks"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_chunks_tenant_id_tenants"),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_bases.id"],
            name="fk_chunks_knowledge_base_id_knowledge_bases",
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["document_versions.id"],
            name="fk_chunks_version_id_document_versions",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_chunks_tenant_id", "chunks", ["tenant_id"])
    op.create_index("ix_chunks_knowledge_base_id", "chunks", ["knowledge_base_id"])
    op.create_index("ix_chunks_version_id", "chunks", ["version_id"])

    op.create_table(
        "query_evidences",
        sa.Column("query_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("retrieval_score", sa.Float(), nullable=False),
        sa.Column("rerank_score", sa.Float(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "retrieval_score >= 0", name="retrieval_score_non_negative"
        ),
        sa.CheckConstraint(
            "rerank_score IS NULL OR rerank_score >= 0",
            name="rerank_score_non_negative",
        ),
        sa.CheckConstraint("position >= 0", name="position_non_negative"),
        sa.PrimaryKeyConstraint("query_id", "chunk_id", name="pk_query_evidences"),
        sa.ForeignKeyConstraint(
            ["query_id"],
            ["query_logs.id"],
            name="fk_query_evidences_query_id_query_logs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id"], ["chunks.id"], name="fk_query_evidences_chunk_id_chunks"
        ),
        sa.UniqueConstraint("query_id", "position", name="uq_query_evidences_query_id_position"),
    )
    op.create_index("ix_query_evidences_chunk_id", "query_evidences", ["chunk_id"])

    # FK circular: documents.active_version_id -> document_versions.id,
    # adicionada só agora que as duas tabelas já existem.
    op.create_foreign_key(
        "fk_documents_active_version_id_document_versions",
        "documents",
        "document_versions",
        ["active_version_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_documents_active_version_id_document_versions", "documents", type_="foreignkey"
    )
    op.drop_table("query_evidences")
    op.drop_table("chunks")
    op.drop_table("index_jobs")
    op.drop_table("feedbacks")
    op.drop_table("document_versions")
    op.drop_table("query_logs")
    op.drop_table("documents")
    op.drop_table("knowledge_bases")
    op.drop_table("tenants")
    op.drop_table("evaluation_runs")
