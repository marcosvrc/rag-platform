"""create audit_events (RAG-054)

Trilho de auditoria de acoes administrativas: criar/atualizar/excluir
base de conhecimento (RAG-012) e enviar/reindexar documento
(RAG-021/RAG-027) -- as acoes administrativas que ja existem hoje na
API. Ver adapters/postgres/models/audit_event.py sobre por que
resource_id nao e uma foreign key (e polimorfico) e
packages/application/ports/audit_log.py sobre o escopo desta
atividade (so registrar, nao consultar).

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-29 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("resource_type", sa.String(), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_audit_events"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_audit_events_tenant_id_tenants",
        ),
    )
    op.create_index("ix_audit_events_tenant_id", "audit_events", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("audit_events")
