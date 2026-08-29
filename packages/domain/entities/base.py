"""Base compartilhada pelas entidades de domínio (RAG-010).

Convenções da seção 8 do plano aplicadas aqui:
  * IDs públicos são UUID v4 (`EntityId`);
  * datas são timezone-aware em UTC (`UtcDateTime`).

Entidades são imutáveis (``frozen=True``): uma transição de estado (ex.:
``Document.transition_to``) devolve uma nova instância, nunca muta a
existente in-place.
"""

from datetime import datetime, timedelta
from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict


def _ensure_uuid_v4(value: UUID) -> UUID:
    if value.version != 4:
        raise ValueError("IDs públicos devem ser UUID v4 (seção 8 do plano)")
    return value


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("Datas devem ser timezone-aware em UTC (seção 8 do plano)")
    return value


EntityId = Annotated[UUID, AfterValidator(_ensure_uuid_v4)]
UtcDateTime = Annotated[datetime, AfterValidator(_ensure_utc)]


class DomainModel(BaseModel):
    """Base Pydantic para entidades de domínio: imutável e sem campos
    extras não declarados."""

    model_config = ConfigDict(frozen=True, extra="forbid")
