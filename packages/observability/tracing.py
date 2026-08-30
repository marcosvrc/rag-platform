"""Configuração de tracing distribuído via OpenTelemetry (RAG-052).

Cobre o fluxo upload -> indexação de ponta a ponta: a API publica o
`IndexJob` no Celery (`adapters/queue/celery_job_queue.py`) e o worker
consome (`apps/indexing_worker/tasks.py`) — `CeleryInstrumentor`
propaga o contexto de trace através da própria mensagem do broker
(Redis), então o span da requisição de upload e o span da task de
indexação que ela disparou aparecem correlacionados (mesmo trace ID)
mesmo sendo processos diferentes. Isso vale automaticamente para
qualquer fluxo futuro também (ex.: o endpoint `/v1/query` do RAG-044,
que ainda não existe): a instrumentação é aplicada uma vez, no nível
da app FastAPI/Celery/SQLAlchemy, nunca por rota ou por task.

Decisão deliberada: ao contrário da configuração de negócio da
aplicação (RAG-004), esta configuração lê variáveis de ambiente
`OTEL_*` diretamente (`os.getenv`), não via
`packages.config.settings.Settings`. Dois motivos:

1. `configure_tracing()` roda na importação de `apps/api/main.py`
   (dentro de `create_app()`), e esse módulo nunca chama
   `get_settings()` — múltiplos testes importam a app sem nenhuma
   variável de ambiente de negócio configurada (ex.:
   `test_health.py::test_liveness_returns_ok_without_any_dependency_override`,
   que testa exatamente que a liveness independe de configuração). Se
   tracing dependesse de `Settings`, essa independência quebraria.
2. As variáveis `OTEL_EXPORTER_OTLP_*` seguem a especificação padrão
   do OpenTelemetry (mesmos nomes em qualquer linguagem/SDK) — o
   próprio `OTLPSpanExporter()`, chamado sem argumentos, já as lê
   sozinho (endpoint, protocolo, headers); reimplementar essa leitura
   via `Settings` só duplicaria lógica que o SDK já resolve.

`OTEL_TRACES_ENABLED` é a exceção: um interruptor nosso (não faz parte
da especificação do OpenTelemetry), default `false` — sem ele, a
instrumentação (FastAPI/SQLAlchemy/Celery) ainda é aplicada, mas contra
o tracer "no-op" padrão do OpenTelemetry (nenhum `TracerProvider` real
é definido): zero overhead, zero thread em background, zero chamada de
rede. É assim que os testes unitários (nenhum deles sobe um Collector
real, seção 1 do plano) continuam passando sem nenhuma configuração
extra. `docker-compose.yml`/`.env.example` ligam essa variável para
desenvolvimento local; produção deve ligá-la também.

Conteúdo sensível (texto de documento, chunks, resposta gerada) nunca
vira atributo de span, em nenhuma das três instrumentações, e nenhum
hook de captura de corpo/payload é habilitado:

* FastAPI: a instrumentação automática só registra método HTTP, rota e
  status code — nunca corpo da requisição/resposta.
* SQLAlchemy: `db.statement` é o SQL parametrizado (bind parameters),
  nunca o valor literal — é assim que o SQLAlchemy compila por padrão;
  não habilitamos nenhuma opção de captura de parâmetro.
* Celery: só nome da task e ID do job aparecem — nunca `args`/`kwargs`.
  Nenhuma task atual receberia texto de documento como argumento de
  qualquer forma: `process_index_job_task` só recebe um UUID (ver
  `apps/indexing_worker/tasks.py`).
"""

from __future__ import annotations

import os
from uuid import UUID

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.celery import CeleryInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from sqlalchemy import Engine

_celery_instrumented = False


def _traces_enabled() -> bool:
    """`OTEL_TRACES_ENABLED` (não é uma variável padrão do OpenTelemetry
    — ver docstring do módulo). Qualquer valor ausente ou diferente de
    "true"/"1"/"yes" (case-insensitive) mantém tracing desligado."""
    return os.getenv("OTEL_TRACES_ENABLED", "false").strip().lower() in {"1", "true", "yes"}


def configure_tracing(*, service_name: str) -> None:
    """Configura tracing para o processo atual (API ou worker).

    Idempotente por processo (mesmo padrão de
    `adapters/queue/celery_app.py:configure_celery_app`): seguro chamar
    mais de uma vez. `service_name` identifica o processo no backend de
    observabilidade (ex.: "rag-platform-api",
    "rag-platform-indexing-worker") — pode ser sobrescrito via a
    variável padrão `OTEL_SERVICE_NAME`, se definida.
    """
    global _celery_instrumented

    if _traces_enabled():
        resource = Resource.create({SERVICE_NAME: os.getenv("OTEL_SERVICE_NAME", service_name)})
        provider = TracerProvider(resource=resource)
        # OTLPSpanExporter() sem argumentos lê OTEL_EXPORTER_OTLP_ENDPOINT /
        # OTEL_EXPORTER_OTLP_TRACES_ENDPOINT (padrão OpenTelemetry) sozinho;
        # sem nenhuma das duas definida, usa o default do próprio SDK
        # (http://localhost:4318/v1/traces), que já bate com
        # OTEL_HTTP_PORT do docker-compose.yml (RAG-003).
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(provider)

    # Sempre instrumentado (não só quando `_traces_enabled()`): contra o
    # tracer no-op padrão, isso não tem custo real, e mantém um único
    # ponto de controle (o `TracerProvider` real, não a instrumentação
    # em si) para ligar/desligar tracing.
    if not _celery_instrumented:
        CeleryInstrumentor().instrument()
        _celery_instrumented = True


def instrument_fastapi_app(app: FastAPI) -> None:
    """Instrumenta uma app FastAPI específica (chamado por
    `apps/api/main.py:create_app`)."""
    FastAPIInstrumentor.instrument_app(app)


def instrument_sqlalchemy_engine(engine: Engine) -> None:
    """Instrumenta um engine específico do SQLAlchemy (chamado por
    `adapters/postgres/engine.py:get_engine`, um por engine — nunca
    globalmente — porque múltiplos testes recriam o engine cacheado via
    `get_engine.cache_clear()`, ver `tests/unit/test_database.py`)."""
    SQLAlchemyInstrumentor().instrument(engine=engine)


def get_current_trace_id() -> UUID:
    """Trace ID do span ativo no momento da chamada (RAG-044,
    `QueryLog.trace_id`), convertido de um inteiro de 128 bits (o
    formato nativo do OpenTelemetry) para `UUID` via `UUID(int=...)` —
    os dois são só representações diferentes do mesmo tamanho em bits;
    nenhuma conversão com perda acontece.

    Fora de um span válido — tracing desligado (`_traces_enabled()`
    falso, tracer no-op padrão) ou chamado fora de uma requisição
    instrumentada — `get_current_span()` devolve `INVALID_SPAN`, cujo
    trace ID é sempre 0: o resultado aqui é o UUID nulo
    (`00000000-0000-0000-0000-000000000000`), um valor previsível que
    sinaliza "nenhum trace real estava ativo", nunca uma exceção."""
    span_context = trace.get_current_span().get_span_context()
    return UUID(int=span_context.trace_id)
