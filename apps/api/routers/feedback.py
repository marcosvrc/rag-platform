"""Endpoint de feedback (RAG-045, seção 10.3 do plano): registra a
avaliação do usuário sobre uma resposta já dada (`QueryLog`, RAG-044).

Standalone (`/v1/feedback`, não aninhado sob uma base de conhecimento)
— a base já está implícita em `query_id`. Mesmo isolamento por tenant
do resto da API: uma consulta inexistente ou de outro tenant retorna
404, nunca 403 (`packages/application/commands/feedback.py`)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status

from apps.api.dependencies import get_current_tenant_id
from apps.api.routers.query import get_query_repository
from packages.application.commands import feedback as feedback_commands
from packages.application.ports.query_repository import QueryRepositoryPort
from packages.contracts.feedback import FeedbackRequest, FeedbackResponse
from packages.domain.entities.feedback import Feedback

router = APIRouter(prefix="/v1", tags=["feedback"])


def _to_response(feedback: Feedback) -> FeedbackResponse:
    return FeedbackResponse(
        id=feedback.id,
        query_id=feedback.query_id,
        rating=feedback.rating,
        reason=feedback.reason,
        expected_answer=feedback.expected_answer,
        created_at=feedback.created_at,
    )


@router.post("/feedback", response_model=FeedbackResponse, status_code=status.HTTP_201_CREATED)
async def create_feedback(
    payload: FeedbackRequest,
    tenant_id: UUID = Depends(get_current_tenant_id),
    query_repository: QueryRepositoryPort = Depends(get_query_repository),
) -> FeedbackResponse:
    feedback = await feedback_commands.submit_feedback(
        query_repository=query_repository,
        tenant_id=tenant_id,
        query_id=payload.query_id,
        rating=payload.rating,
        reason=payload.reason,
        expected_answer=payload.expected_answer,
    )
    return _to_response(feedback)
