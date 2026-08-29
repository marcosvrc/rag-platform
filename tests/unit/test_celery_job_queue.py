"""Testes de RAG-022: `CeleryJobQueue` (adapter de `JobQueuePort`).

Não conecta a nenhum broker real: `celery_app.send_task` é substituído
por um fake que só grava a chamada — publicar uma mensagem não precisa
de uma conexão de rede de verdade para provar que o adapter monta a
chamada certa (nome da task, argumento)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import SecretStr

from adapters.queue.celery_app import celery_app
from adapters.queue.celery_job_queue import INDEX_JOB_TASK_NAME, CeleryJobQueue
from packages.config.settings import Settings


def _make_settings(**overrides: object) -> Settings:
    fields: dict[str, object] = {
        "_env_file": None,
        "POSTGRES_PASSWORD": SecretStr("x"),
        "MINIO_ROOT_PASSWORD": SecretStr("x"),
        "JWT_ISSUER": "rag-platform-tests",
        "JWT_AUDIENCE": "rag-platform-tests-api",
    }
    fields.update(overrides)
    return Settings(**fields)  # type: ignore[arg-type]


class _FakeSendTask:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[object]]] = []

    def __call__(self, name: str, args: list[object]) -> None:
        self.calls.append((name, args))


def test_enqueue_index_job_sends_the_expected_task_name_and_argument(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_send_task = _FakeSendTask()
    monkeypatch.setattr(celery_app, "send_task", fake_send_task)

    job_queue = CeleryJobQueue(_make_settings())
    index_job_id = uuid4()

    job_queue.enqueue_index_job(index_job_id=index_job_id)

    assert fake_send_task.calls == [(INDEX_JOB_TASK_NAME, [str(index_job_id)])]


def test_configures_broker_and_backend_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(celery_app, "send_task", _FakeSendTask())
    settings = _make_settings(REDIS_HOST="redis-test-host", REDIS_PORT=6380)

    CeleryJobQueue(settings)

    assert celery_app.conf.broker_url == "redis://redis-test-host:6380/0"
    assert celery_app.conf.result_backend == "redis://redis-test-host:6380/0"
