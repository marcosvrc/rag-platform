"""Contratos HTTP de bases de conhecimento (RAG-012, seção 10.1 do plano).

Separados da entidade de domínio `KnowledgeBase` de propósito: o
contrato é o formato estável exposto ao cliente da API; a entidade é
livre para evoluir sem quebrar ninguém de fora (seção 5.1 do plano).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from packages.domain.enums.knowledge_base_status import KnowledgeBaseStatus


class KnowledgeBaseCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    config: dict[str, Any] = Field(default_factory=dict)


class KnowledgeBaseUpdateRequest(BaseModel):
    """PATCH parcial: só os campos enviados são alterados.

    `apps/api/routers/knowledge_bases.py` usa
    ``model_dump(exclude_unset=True)`` para distinguir "campo omitido"
    de "campo enviado como null" — só `description` aceita `null`
    (limpa o campo); `name`/`config` enviados como `null` viram 422
    (`packages/application/commands/knowledge_base.py`).
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    config: dict[str, Any] | None = None


class KnowledgeBaseResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    name: str
    description: str | None
    status: KnowledgeBaseStatus
    config: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class KnowledgeBaseListResponse(BaseModel):
    items: list[KnowledgeBaseResponse]
    next_cursor: str | None
