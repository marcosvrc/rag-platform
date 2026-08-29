"""App Celery compartilhada entre produtor e consumidor (RAG-022).

Um único objeto `Celery` é construído aqui, sem broker/backend
configurado — construir `Celery("rag_platform")` não faz nenhuma
chamada de rede nem exige `Settings` válido, então este módulo pode ser
importado livremente (inclusive em testes sem infraestrutura nenhuma,
mesmo padrão de `adapters/postgres/base.py` para o `Base` declarativo).

Broker e backend só são configurados em `configure_celery_app`, chamada
explicitamente por quem sabe que tem um `Settings` válido: o adapter
produtor (`CeleryJobQueue.__init__`) e o processo do worker
(`apps/indexing_worker/worker.py`) — nunca aqui, e nunca lendo
`get_settings()` direto neste módulo (regra de `packages/config/settings.py`:
"nenhum outro módulo deve ler configuração fora de `get_settings()`",
e mesmo essa leitura deve ser adiada até o momento de uso real).
"""

from __future__ import annotations

from celery import Celery

from packages.config.settings import Settings

celery_app = Celery("rag_platform")


def configure_celery_app(settings: Settings) -> None:
    """Aplica `Settings` (broker/backend Redis) à app Celery
    compartilhada. Idempotente — pode ser chamada mais de uma vez (ex.:
    uma vez por request no lado produtor) sem efeito colateral além de
    reatribuir os mesmos valores."""
    celery_app.conf.update(
        broker_url=settings.redis_url,
        result_backend=settings.redis_url,
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        task_track_started=True,
    )
