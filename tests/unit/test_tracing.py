"""Testes de RAG-052: `packages.observability.tracing`.

Nenhum teste aqui sobe um Collector real nem deixa uma
`BatchSpanProcessor` de verdade rodando em background (ela cria uma
thread de exportação periódica ao ser construída) — os limites com
efeito colateral real (exportador, processor, provider, os dois
instrumentors de terceiros) são sempre dublês; só a lógica de decisão
deste módulo (ligado/desligado, idempotência, nome de serviço) é
exercitada de verdade.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import packages.observability.tracing as tracing_module
from packages.observability.tracing import (
    configure_tracing,
    instrument_fastapi_app,
    instrument_sqlalchemy_engine,
)


@pytest.fixture(autouse=True)
def _reset_celery_instrumented_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_celery_instrumented` é um flag de idempotência por processo
    (mesmo padrão de `configure_celery_app`) — resetado entre testes
    para que cada um comece do mesmo estado."""
    monkeypatch.setattr(tracing_module, "_celery_instrumented", False)


@pytest.mark.parametrize("value", ["true", "True", "TRUE", "1", "yes", "YES"])
def test_traces_enabled_true_for_accepted_values(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OTEL_TRACES_ENABLED", value)

    assert tracing_module._traces_enabled() is True


@pytest.mark.parametrize("value", ["false", "0", "no", "", "garbage"])
def test_traces_enabled_false_for_other_values(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_TRACES_ENABLED", value)

    assert tracing_module._traces_enabled() is False


def test_traces_enabled_defaults_to_false_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OTEL_TRACES_ENABLED", raising=False)

    assert tracing_module._traces_enabled() is False


def test_configure_tracing_instruments_celery_exactly_once_across_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OTEL_TRACES_ENABLED", raising=False)
    fake_instrumentor = MagicMock()
    monkeypatch.setattr(
        tracing_module, "CeleryInstrumentor", MagicMock(return_value=fake_instrumentor)
    )

    configure_tracing(service_name="svc-a")
    configure_tracing(service_name="svc-b")

    fake_instrumentor.instrument.assert_called_once()


def test_configure_tracing_disabled_never_sets_a_real_tracer_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OTEL_TRACES_ENABLED", raising=False)
    monkeypatch.setattr(tracing_module, "CeleryInstrumentor", MagicMock())
    fake_set_tracer_provider = MagicMock()
    monkeypatch.setattr(tracing_module.trace, "set_tracer_provider", fake_set_tracer_provider)

    configure_tracing(service_name="svc-a")

    fake_set_tracer_provider.assert_not_called()


def test_configure_tracing_enabled_wires_a_real_exporter_and_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OTEL_TRACES_ENABLED", "true")
    monkeypatch.setattr(tracing_module, "CeleryInstrumentor", MagicMock())
    fake_exporter = MagicMock()
    monkeypatch.setattr(tracing_module, "OTLPSpanExporter", MagicMock(return_value=fake_exporter))
    fake_processor = MagicMock()
    monkeypatch.setattr(
        tracing_module, "BatchSpanProcessor", MagicMock(return_value=fake_processor)
    )
    fake_provider = MagicMock()
    monkeypatch.setattr(tracing_module, "TracerProvider", MagicMock(return_value=fake_provider))
    fake_set_tracer_provider = MagicMock()
    monkeypatch.setattr(tracing_module.trace, "set_tracer_provider", fake_set_tracer_provider)

    configure_tracing(service_name="rag-platform-api")

    fake_provider.add_span_processor.assert_called_once_with(fake_processor)
    fake_set_tracer_provider.assert_called_once_with(fake_provider)


def test_configure_tracing_honors_otel_service_name_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OTEL_TRACES_ENABLED", "true")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "overridden-name")
    monkeypatch.setattr(tracing_module, "CeleryInstrumentor", MagicMock())
    monkeypatch.setattr(tracing_module, "OTLPSpanExporter", MagicMock())
    monkeypatch.setattr(tracing_module, "BatchSpanProcessor", MagicMock())
    fake_provider = MagicMock()
    monkeypatch.setattr(tracing_module, "TracerProvider", MagicMock(return_value=fake_provider))
    monkeypatch.setattr(tracing_module.trace, "set_tracer_provider", MagicMock())

    configure_tracing(service_name="rag-platform-api")

    mock_provider = tracing_module.TracerProvider
    resource = mock_provider.call_args.kwargs["resource"]  # type: ignore[attr-defined]
    assert resource.attributes[tracing_module.SERVICE_NAME] == "overridden-name"


def test_instrument_fastapi_app_delegates_to_the_instrumentor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_instrument_app = MagicMock()
    monkeypatch.setattr(tracing_module.FastAPIInstrumentor, "instrument_app", fake_instrument_app)
    app = object()

    instrument_fastapi_app(app)  # type: ignore[arg-type]

    fake_instrument_app.assert_called_once_with(app)


def test_instrument_sqlalchemy_engine_delegates_to_the_instrumentor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_instrumentor = MagicMock()
    monkeypatch.setattr(
        tracing_module, "SQLAlchemyInstrumentor", MagicMock(return_value=fake_instrumentor)
    )
    engine = object()

    instrument_sqlalchemy_engine(engine)  # type: ignore[arg-type]

    fake_instrumentor.instrument.assert_called_once_with(engine=engine)
