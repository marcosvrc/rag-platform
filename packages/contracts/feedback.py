"""Contratos HTTP do endpoint `POST /v1/feedback` (RAG-045, seção 10.3
do plano) — standalone, não aninhado sob `/v1/knowledge-bases/{id}`
(a base de conhecimento já está implícita em `query_id`, resolvido a
partir do `QueryLog` correspondente)."""

from __future__ import annotations

from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.domain.enums.feedback_rating import FeedbackRating


class FeedbackRequest(BaseModel):
    """ "Valida rating e motivo" (critério de aceite): `rating` só aceita
    os dois valores de `FeedbackRating` (qualquer outra string vira 422
    automaticamente, validação de enum do Pydantic); `reason` é
    obrigatório quando `rating` é `NEGATIVE` — decisão desta atividade,
    já que o plano não elabora o que "validar motivo" significa e um
    feedback negativo sem motivo não é acionável para quem for revisar
    a resposta depois. Para `POSITIVE`, `reason` continua opcional (um
    "gostei" já é informação suficiente por si só)."""

    model_config = ConfigDict(extra="forbid")

    query_id: UUID
    rating: FeedbackRating
    reason: str | None = Field(default=None, min_length=1, max_length=2000)
    expected_answer: str | None = Field(default=None, min_length=1, max_length=4000)

    @model_validator(mode="after")
    def _require_reason_for_negative_rating(self) -> Self:
        if self.rating == FeedbackRating.NEGATIVE and self.reason is None:
            raise ValueError("um motivo ('reason') é obrigatório para feedback negativo.")
        return self


class FeedbackResponse(BaseModel):
    id: UUID
    query_id: UUID
    rating: FeedbackRating
    reason: str | None
    expected_answer: str | None
    created_at: datetime
