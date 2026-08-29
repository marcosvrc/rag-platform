"""Tabela `evaluation_runs` (RAG-011, entidade `EvaluationRun` de RAG-010).

Sem `tenant_id`: avaliações são um recurso de produto/plataforma, não
escopadas por tenant (a entidade de RAG-010 tampouco tem esse campo).
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Enum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from adapters.postgres.base import Base
from packages.domain.enums.processing_status import ProcessingStatus


class EvaluationRunModel(Base):
    __tablename__ = "evaluation_runs"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    dataset_version: Mapped[str] = mapped_column(nullable=False)
    config_version: Mapped[str] = mapped_column(nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    status: Mapped[ProcessingStatus] = mapped_column(
        Enum(ProcessingStatus, name="processing_status", native_enum=False, length=16),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(nullable=False)
