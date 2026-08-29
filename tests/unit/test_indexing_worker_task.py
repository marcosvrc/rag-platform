"""Testes de RAG-022: `apps.indexing_worker.tasks.process_index_job_task`.

A lógica de negócio (reivindicar/retry/falha definitiva) já é testada
sem nenhum Celery em `tests/unit/test_index_job_processing.py` — aqui
só a fiação específica do Celery é verificada: a task está registrada
com o nome esperado pelo produtor (`CeleryJobQueue`), configurada para
reagendar automaticamente só em `RetryableIndexJobError` com backoff
exponencial, e repassa `self.request.retries` corretamente como
`attempt_number` (1-based) para `_run_attempt`.

Não exercita o laço de reagendamento do Celery de verdade (isso
dispararia o backoff real, com `time.sleep`, tornando o teste lento e
testando o próprio Celery, não este código) — `_run_attempt` é
substituído por um fake para isolar só a fiação.

Desde o RAG-053, também cobre a métrica de consumo registrada ao redor
de cada tentativa (`record_index_job_attempt`, dublada aqui): desfecho
correto (succeeded/failed_final/failed_retryable) a partir do que
`_run_attempt` devolve ou levanta, e nenhuma métrica quando o outcome é
`None` (job já reivindicado por outro worker)."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from adapters.queue.celery_job_queue import INDEX_JOB_TASK_NAME
from apps.indexing_worker import tasks
from packages.application.commands.index_job import (
    IndexJobAttemptOutcome,
    RetryableIndexJobError,
)


def test_task_is_registered_with_the_name_the_producer_expects() -> None:
    assert tasks.process_index_job_task.name == INDEX_JOB_TASK_NAME


def test_task_only_autoretries_on_retryable_index_job_error() -> None:
    assert tasks.process_index_job_task.autoretry_for == (RetryableIndexJobError,)


def test_task_retry_backoff_is_configured() -> None:
    assert tasks.process_index_job_task.retry_backoff is True
    assert tasks.process_index_job_task.retry_backoff_max == tasks._RETRY_BACKOFF_MAX_SECONDS
    assert tasks.process_index_job_task.retry_jitter is True
    assert tasks.process_index_job_task.max_retries == tasks._MAX_ATTEMPTS - 1


def test_records_a_succeeded_metric_with_the_attempt_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run_attempt(
        index_job_id: UUID, *, attempt_number: int, max_attempts: int
    ) -> IndexJobAttemptOutcome:
        return IndexJobAttemptOutcome.SUCCEEDED

    monkeypatch.setattr(tasks, "_run_attempt", fake_run_attempt)
    fake_record = MagicMock()
    monkeypatch.setattr(tasks, "record_index_job_attempt", fake_record)

    tasks.process_index_job_task.apply(args=[str(uuid4())], throw=True)

    assert fake_record.call_args.kwargs["status"] == "succeeded"
    assert fake_record.call_args.kwargs["duration_seconds"] >= 0.0


def test_records_a_failed_final_metric(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_attempt(
        index_job_id: UUID, *, attempt_number: int, max_attempts: int
    ) -> IndexJobAttemptOutcome:
        return IndexJobAttemptOutcome.FAILED_FINAL

    monkeypatch.setattr(tasks, "_run_attempt", fake_run_attempt)
    fake_record = MagicMock()
    monkeypatch.setattr(tasks, "record_index_job_attempt", fake_record)

    tasks.process_index_job_task.apply(args=[str(uuid4())], throw=True)

    assert fake_record.call_args.kwargs["status"] == "failed_final"


def test_records_no_metric_when_the_job_was_already_claimed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run_attempt(
        index_job_id: UUID, *, attempt_number: int, max_attempts: int
    ) -> None:
        return None

    monkeypatch.setattr(tasks, "_run_attempt", fake_run_attempt)
    fake_record = MagicMock()
    monkeypatch.setattr(tasks, "record_index_job_attempt", fake_record)

    tasks.process_index_job_task.apply(args=[str(uuid4())], throw=True)

    fake_record.assert_not_called()


def test_records_a_failed_retryable_metric_and_still_reraises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run_attempt(
        index_job_id: UUID, *, attempt_number: int, max_attempts: int
    ) -> None:
        raise RetryableIndexJobError

    monkeypatch.setattr(tasks, "_run_attempt", fake_run_attempt)
    fake_record = MagicMock()
    monkeypatch.setattr(tasks, "record_index_job_attempt", fake_record)
    # `max_retries=0` faz o Celery esgotar o autoretry na primeira falha
    # e relançar a exceção original, em vez de engolir e reagendar de
    # verdade (o que exigiria um broker/eventloop de teste completo).
    monkeypatch.setattr(tasks.process_index_job_task, "max_retries", 0)

    with pytest.raises(RetryableIndexJobError):
        tasks.process_index_job_task.apply(args=[str(uuid4())], throw=True)

    assert fake_record.call_args.kwargs["status"] == "failed_retryable"


def test_first_call_passes_attempt_number_one(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[UUID, int, int]] = []

    async def fake_run_attempt(
        index_job_id: UUID, *, attempt_number: int, max_attempts: int
    ) -> None:
        calls.append((index_job_id, attempt_number, max_attempts))

    monkeypatch.setattr(tasks, "_run_attempt", fake_run_attempt)
    index_job_id = uuid4()

    tasks.process_index_job_task.apply(args=[str(index_job_id)], throw=True)

    assert calls == [(index_job_id, 1, tasks._MAX_ATTEMPTS)]
