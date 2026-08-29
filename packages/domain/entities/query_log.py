"""Entidade QueryLog (seção 9 do plano).

Uso de tokens é modelado como um objeto de valor (``TokenUsage``) em
vez de um ``dict`` solto, para validar ``input_tokens``/``output_tokens``
como parte das invariantes desta atividade.
"""

from uuid import UUID

from pydantic import Field

from packages.domain.entities.base import DomainModel, EntityId


class TokenUsage(DomainModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class QueryLog(DomainModel):
    id: EntityId
    tenant_id: EntityId
    knowledge_base_id: EntityId
    question_hash: str = Field(min_length=1)
    model: str = Field(min_length=1)
    latency_ms: int = Field(ge=0)
    token_usage: TokenUsage
    trace_id: UUID
