"""Configuração de métricas via OpenTelemetry (RAG-053).

Mesma arquitetura de `packages/observability/tracing.py` (RAG-052) —
ver aquela docstring para o racional completo de por que a
configuração lê `OTEL_*` diretamente (`os.getenv`), não via
`packages.config.settings.Settings`: os dois motivos (independência de
`get_settings()` na importação da app; `OTEL_EXPORTER_OTLP_*` já são
lidas sozinhas pelo exporter, seguindo a especificação padrão do
OpenTelemetry) valem aqui exatamente da mesma forma.

`OTEL_METRICS_ENABLED` (não é uma variável padrão do OpenTelemetry,
mesmo espírito de `OTEL_TRACES_ENABLED`) — default `false`. Sem ele,
`metrics.get_meter(...)` continua funcionando (a API do OpenTelemetry
nunca falha sem um `MeterProvider` real — devolve um meter "no-op" por
trás de um proxy, que é atualizado automaticamente se um
`MeterProvider` real for definido depois via `set_meter_provider`, sem
precisar recriar nenhum instrumento já criado): zero overhead, zero
chamada de rede, mesma garantia que já vale para tracing.

**Duas categorias de métrica, critério de aceite "métricas técnicas e
de consumo"**:

1. **Técnicas**: HTTP (`FastAPIInstrumentor`) e Celery
   (`CeleryInstrumentor`) — nenhum código novo aqui, são as MESMAS
   instrumentações que `tracing.py` já aplica em
   `instrument_fastapi_app`/`configure_tracing` (RAG-052); uma
   instrumentação do OpenTelemetry emite traces E métricas ao mesmo
   tempo, a partir do que estiver configurado globalmente
   (`TracerProvider`/`MeterProvider`) no momento em que é chamada — daí
   por que `configure_metrics()` precisa rodar antes de
   `instrument_fastapi_app(app)`, mesma ordem que já vale para
   `configure_tracing()`.
2. **De consumo**: instrumentos de negócio definidos abaixo, chamados
   nos MESMOS pontos de entrada (routers da API, task Celery, adapter
   LiteLLM) que já registram auditoria (RAG-054) ou processam o
   trabalho de fato — nunca dentro de `packages/application` (domínio
   e casos de uso não importam OpenTelemetry diretamente, seção 5.1 do
   plano; a mesma razão pela qual RAG-054 registrou auditoria a partir
   dos routers, não de dentro dos comandos de aplicação).

**Cardinalidade**: todo label usado abaixo vem de um conjunto FIXO e
pequeno de valores (tipo MIME aceito — 4 valores fixos em
`packages/application/commands/document.py`; ação de mutação de base
de conhecimento — create/update/delete; status de tentativa de
indexação — succeeded/failed_retryable/failed_final) — nunca
`tenant_id`, `document_id`, `knowledge_base_id` ou qualquer outro valor
de cardinalidade não limitada (critério de aceite "labels não possuem
cardinalidade descontrolada")."""

from __future__ import annotations

import os

from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, Resource

_METER_NAME = "rag_platform"


def _metrics_enabled() -> bool:
    """`OTEL_METRICS_ENABLED` — ver docstring do módulo. Qualquer valor
    ausente ou diferente de "true"/"1"/"yes" (case-insensitive) mantém
    métricas desligadas."""
    return os.getenv("OTEL_METRICS_ENABLED", "false").strip().lower() in {"1", "true", "yes"}


def configure_metrics(*, service_name: str) -> None:
    """Configura métricas para o processo atual (API ou worker).

    Mesma assinatura e mesmo padrão de `tracing.py:configure_tracing`
    — `service_name` identifica o processo no backend de observabilidade
    (sobrescrevível via a variável padrão `OTEL_SERVICE_NAME`). Seguro
    chamar mais de uma vez (definir um novo `MeterProvider` só substitui
    o anterior; não há nenhum `.instrument()` de terceiro aqui para
    proteger contra dupla-instrumentação, ao contrário do
    `CeleryInstrumentor` em `tracing.py`)."""
    if not _metrics_enabled():
        return

    resource = Resource.create({SERVICE_NAME: os.getenv("OTEL_SERVICE_NAME", service_name)})
    # OTLPMetricExporter() sem argumentos lê OTEL_EXPORTER_OTLP_ENDPOINT /
    # OTEL_EXPORTER_OTLP_METRICS_ENDPOINT (padrão OpenTelemetry) sozinho —
    # mesmo racional do OTLPSpanExporter em tracing.py.
    reader = PeriodicExportingMetricReader(OTLPMetricExporter())
    provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(provider)


def _meter() -> metrics.Meter:
    return metrics.get_meter(_METER_NAME)


def record_document_uploaded(*, mime_type: str) -> None:
    """Chamado por `apps/api/routers/documents.py:upload_document`
    depois de um upload aceito. `mime_type` é um dos 4 valores fixos em
    `_ALLOWED_EXTENSIONS_BY_MIME_TYPE` (`packages/application/commands/
    document.py`) — nunca cardinalidade livre."""
    _meter().create_counter(
        "rag_platform.documents.uploaded",
        description="Documentos aceitos no upload, por tipo MIME.",
    ).add(1, {"mime_type": mime_type})


def record_document_reindexed() -> None:
    """Chamado por `apps/api/routers/documents.py:reindex_document`
    depois de uma reindexação aceita. Sem labels — o volume por si só
    já é o sinal de consumo relevante aqui."""
    _meter().create_counter(
        "rag_platform.documents.reindexed",
        description="Reindexações de documento aceitas.",
    ).add(1)


def record_knowledge_base_mutation(*, action: str) -> None:
    """Chamado por `apps/api/routers/knowledge_bases.py` nos mesmos três
    pontos que já chamam `record_audit_event_safely` (RAG-054):
    `action` é sempre um de "create"/"update"/"delete"."""
    _meter().create_counter(
        "rag_platform.knowledge_bases.mutations",
        description="Mutações de base de conhecimento, por ação.",
    ).add(1, {"action": action})


def record_index_job_attempt(*, status: str, duration_seconds: float) -> None:
    """Chamado por `apps/indexing_worker/tasks.py` ao redor de cada
    tentativa de processar um `IndexJob`. `status` é sempre um de
    "succeeded"/"failed_retryable"/"failed_final" (os três desfechos
    possíveis de `process_index_job_attempt`, RAG-022) — nunca o texto
    do erro nem o tipo da exceção, que teriam cardinalidade livre."""
    _meter().create_counter(
        "rag_platform.index_jobs.attempts",
        description="Tentativas de processar um IndexJob, por desfecho.",
    ).add(1, {"status": status})
    _meter().create_histogram(
        "rag_platform.index_jobs.duration",
        description="Duração de uma tentativa de processar um IndexJob.",
        unit="s",
    ).record(duration_seconds, {"status": status})


def record_embedding_batch(*, text_count: int, duration_seconds: float) -> None:
    """Chamado por `adapters/litellm/embedding_provider.py` ao redor de
    cada chamada a `embed()` (todos os lotes HTTP de uma chamada, não
    lote a lote) — o texto em si nunca é um label nem um valor de
    métrica, só a contagem e a duração."""
    _meter().create_counter(
        "rag_platform.embeddings.texts",
        description="Textos enviados para o gateway de embeddings.",
    ).add(text_count)
    _meter().create_histogram(
        "rag_platform.embeddings.request_duration",
        description="Duração de uma chamada completa a EmbeddingProviderPort.embed().",
        unit="s",
    ).record(duration_seconds)
