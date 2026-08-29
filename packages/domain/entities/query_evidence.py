"""Entidade QueryEvidence (seção 9 do plano).

Vincula uma evidência (chunk recuperado) a uma consulta, com as
pontuações e a posição usadas no ranking final.
"""

from pydantic import Field

from packages.domain.entities.base import DomainModel, EntityId


class QueryEvidence(DomainModel):
    query_id: EntityId
    chunk_id: EntityId
    retrieval_score: float = Field(ge=0.0)
    rerank_score: float | None = Field(default=None, ge=0.0)
    position: int = Field(ge=0)
