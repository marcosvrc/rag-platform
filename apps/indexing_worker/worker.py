"""Ponto de entrada do processo worker (RAG-022).

Só este módulo lê `Settings` de verdade (via `get_settings()`) e
registra as tasks — `apps/indexing_worker/__init__.py` e
`apps/indexing_worker/tasks.py` continuam importáveis sem nenhuma
configuração real (ver `tests/unit/test_repo_bootstrap.py`, RAG-001), o
que só é possível porque a configuração fica isolada aqui, carregada
apenas quando o worker realmente sobe.

Uso (mesmo app Celery usado por `CeleryJobQueue` do lado produtor):

    celery -A apps.indexing_worker.worker worker --loglevel=info
"""

from __future__ import annotations

from adapters.queue.celery_app import configure_celery_app
from packages.config.settings import get_settings
from packages.observability.metrics import configure_metrics
from packages.observability.tracing import configure_tracing

configure_celery_app(get_settings())
# RAG-052 — precisa rodar no processo do worker também (não só na API):
# é o que faz `CeleryInstrumentor` conseguir ler o contexto de trace
# propagado na mensagem publicada pelo lado produtor e continuar o
# mesmo trace na task consumida aqui.
configure_tracing(service_name="rag-platform-indexing-worker")
# RAG-053 — mesmo racional de configure_tracing acima: precisa rodar no
# processo do worker também, antes de qualquer task ser executada.
configure_metrics(service_name="rag-platform-indexing-worker")

# Import por efeito colateral: registra `process_index_job_task` na app
# Celery configurada acima. Precisa vir depois de `configure_celery_app`
# nesta ordem só por clareza de leitura — o registro da task em si não
# depende de `configure_celery_app` já ter rodado, só o consumo real de
# mensagens depende (e isso só acontece quando o CLI `celery worker`
# conecta ao broker, bem depois deste módulo terminar de importar).
from apps.indexing_worker import tasks  # noqa: E402,F401
