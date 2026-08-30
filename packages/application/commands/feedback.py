"""Caso de uso do endpoint `POST /v1/feedback` (RAG-045, seção 10.3 do
plano): registra a avaliação do usuário sobre uma resposta já dada
(`QueryLog`, RAG-044).

**"respeita tenant; não permite feedback para query alheia"** (critério
de aceite): `submit_feedback` resolve `query_id` via
`QueryRepositoryPort.get_query_log` (sem filtro de tenant na porta,
ver docstring dela) e só aceita o feedback se o `QueryLog` encontrado
pertencer a `tenant_id` — uma consulta inexistente e uma consulta de
outro tenant levantam exatamente o mesmo erro (`NotFoundError`, 404,
nunca 403), mesmo critério "404, nunca 403" de todo o resto da API
(RAG-012/RAG-021/RAG-034)."""

from __future__ import annotations

from uuid import UUID

from packages.application.errors import NotFoundError
from packages.application.ports.query_repository import QueryRepositoryPort
from packages.domain.entities.feedback import Feedback
from packages.domain.enums.feedback_rating import FeedbackRating


async def submit_feedback(
    *,
    query_repository: QueryRepositoryPort,
    tenant_id: UUID,
    query_id: UUID,
    rating: FeedbackRating,
    reason: str | None,
    expected_answer: str | None,
) -> Feedback:
    """Registra um `Feedback` para `query_id`.

    Levanta `NotFoundError` se `query_id` não existe ou pertence a
    outro tenant. "Valida rating e motivo" (critério de aceite):
    `rating` já chega validado como `FeedbackRating` (só os dois
    valores do enum passam pela validação do contrato HTTP, RAG-013);
    `reason` obrigatório para feedback `NEGATIVE` é responsabilidade do
    contrato (`packages/contracts/feedback.py`), não deste caso de uso
    — a mesma disciplina de "validação de forma pertence ao contrato,
    regra de negócio pertence ao caso de uso" já usada em toda a API."""
    query_log = await query_repository.get_query_log(query_id=query_id)
    if query_log is None or query_log.tenant_id != tenant_id:
        raise NotFoundError(detail="Consulta não encontrada.")

    return await query_repository.persist_feedback(
        query_id=query_id, rating=rating, reason=reason, expected_answer=expected_answer
    )
