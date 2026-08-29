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

configure_celery_app(get_settings())

# Import por efeito colateral: registra `process_index_job_task` na app
# Celery configurada acima. Precisa vir depois de `configure_celery_app`
# nesta ordem só por clareza de leitura — o registro da task em si não
# depende de `configure_celery_app` já ter rodado, só o consumo real de
# mensagens depende (e isso só acontece quando o CLI `celery worker`
# conecta ao broker, bem depois deste módulo terminar de importar).
from apps.indexing_worker import tasks  # noqa: E402,F401
