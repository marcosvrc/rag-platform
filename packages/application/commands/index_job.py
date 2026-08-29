"""Comando de processamento de um `IndexJob` (RAG-022, seção 11 do
plano, passos 6-7: publicar o job na fila e o worker adquirir um lock
idempotente).

Toda a lógica de reivindicação/retry/falha definitiva mora aqui, como
uma função pura de aplicação — nunca em `apps/indexing_worker/tasks.py`
— para ser testável sem Celery nem broker nenhum (mesmo princípio de
desacoplamento da seção 5.1 do plano: a aplicação não conhece Celery).
A task Celery (`apps/indexing_worker/tasks.py`) é só um adapter fino
que chama `process_index_job_attempt` e traduz o resultado em
reagendar (ou não) via Celery — e, desde RAG-053, também em métrica de
consumo (`packages.observability.metrics.record_index_job_attempt`),
usando o valor de retorno de `IndexJobAttemptOutcome` para saber se a
tentativa teve sucesso ou falhou definitivamente (o único caso em que
a task NÃO consegue distinguir os dois só pela ausência de exceção).
"""

from __future__ import annotations

from enum import Enum
from uuid import UUID

from packages.application.ports.document_processor import DocumentProcessorPort
from packages.application.ports.document_repository import DocumentRepositoryPort

# Tamanho da mensagem de erro persistida em IndexJob.error_message —
# defensivo contra uma exceção com uma mensagem gigante (ex.: stack
# trace embutido por engano em str(exc)) inflar a linha no banco.
_MAX_ERROR_MESSAGE_LENGTH = 500


class RetryableIndexJobError(Exception):
    """Sinaliza que a tentativa falhou mas ainda há tentativas
    restantes — o único caso em que quem chama (a task Celery) deve
    reagendar. A exceção original que causou a falha fica em
    `__cause__`; o motivo já foi registrado em `IndexJob.error_code`/
    `error_message` antes desta ser levantada."""


class IndexJobAttemptOutcome(Enum):
    """Desfecho de uma tentativa que de fato rodou (ver
    `process_index_job_attempt`) — o terceiro desfecho possível, "job
    já reivindicado por outro worker ou inexistente", não entra aqui:
    nesse caso a função devolve `None`, porque nenhuma tentativa real
    aconteceu (RAG-053, `packages.observability.metrics`, não registra
    métrica nenhuma para esse caso)."""

    SUCCEEDED = "succeeded"
    FAILED_FINAL = "failed_final"


async def process_index_job_attempt(
    document_repository: DocumentRepositoryPort,
    document_processor: DocumentProcessorPort,
    *,
    index_job_id: UUID,
    attempt_number: int,
    max_attempts: int,
) -> IndexJobAttemptOutcome | None:
    """Executa uma tentativa de processar `index_job_id`.

    `attempt_number` é 1-based (a primeira tentativa é 1) — só nela se
    tenta reivindicar o job (`claim_index_job`, o lock idempotente do
    passo 7); tentativas seguintes (reagendadas pelo Celery após uma
    falha retryable) já são donas do job (`status=RUNNING`), então
    pulam a reivindicação.

    Levanta `RetryableIndexJobError` quando a tentativa falhou mas
    ainda há tentativas restantes (`attempt_number < max_attempts`) —
    o único caso em que quem chama deve reagendar. Nos outros três
    casos (sucesso, falha definitiva, job já reivindicado por outro
    worker ou inexistente) a função retorna normalmente; o estado
    final já foi persistido em `IndexJob` antes de retornar. Devolve
    `IndexJobAttemptOutcome.SUCCEEDED`/`FAILED_FINAL` nos dois
    primeiros casos, e `None` no terceiro (nenhuma tentativa real
    aconteceu) — RAG-053 usa esse valor para métricas de consumo, sem
    precisar que quem chama inspecione `IndexJob` de novo.
    """
    if attempt_number == 1:
        claimed = await document_repository.claim_index_job(index_job_id=index_job_id)
        if claimed is None:
            return None

    try:
        await document_processor.process(index_job_id=index_job_id)
    except Exception as exc:
        is_final = attempt_number >= max_attempts
        await document_repository.mark_index_job_failed(
            index_job_id=index_job_id,
            attempts=attempt_number,
            error_code=type(exc).__name__,
            error_message=str(exc)[:_MAX_ERROR_MESSAGE_LENGTH],
            final=is_final,
        )
        if is_final:
            return IndexJobAttemptOutcome.FAILED_FINAL
        raise RetryableIndexJobError from exc
    else:
        await document_repository.mark_index_job_succeeded(index_job_id=index_job_id)
        return IndexJobAttemptOutcome.SUCCEEDED
