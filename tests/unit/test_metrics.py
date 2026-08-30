"""Testes de RAG-053: `packages.observability.metrics`.

Mesmo espírito de `tests/unit/test_tracing.py` (RAG-052): nenhum teste
aqui sobe um Collector real nem deixa um `PeriodicExportingMetricReader`
de verdade rodando em background — os limites com efeito colateral
real (exportador, reader, provider) são sempre dublês; só a lógica de
decisão deste módulo (ligado/desligado, nome de serviço, os
instrumentos e labels que cada `record_*` gera) é exercitada de
verdade."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import packages.observability.metrics as metrics_module
from packages.observability.metrics import (
    configure_metrics,
    record_document_reindexed,
    record_document_uploaded,
    record_embedding_batch,
    record_index_job_attempt,
    record_knowledge_base_mutation,
)


@pytest.mark.parametrize("value", ["true", "True", "TRUE", "1", "yes", "YES"])
def test_metrics_enabled_true_for_accepted_values(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OTEL_METRICS_ENABLED", value)

    assert metrics_module._metrics_enabled() is True


@pytest.mark.parametrize("value", ["false", "0", "no", "", "garbage"])
def test_metrics_enabled_false_for_other_values(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OTEL_METRICS_ENABLED", value)

    assert metrics_module._metrics_enabled() is False


def test_metrics_enabled_defaults_to_false_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OTEL_METRICS_ENABLED", raising=False)

    assert metrics_module._metrics_enabled() is False


def test_configure_metrics_disabled_never_sets_a_real_meter_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OTEL_METRICS_ENABLED", raising=False)
    fake_set_meter_provider = MagicMock()
    monkeypatch.setattr(metrics_module.metrics, "set_meter_provider", fake_set_meter_provider)

    configure_metrics(service_name="svc-a")

    fake_set_meter_provider.assert_not_called()


def test_configure_metrics_enabled_wires_a_real_exporter_and_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OTEL_METRICS_ENABLED", "true")
    fake_exporter = MagicMock()
    monkeypatch.setattr(metrics_module, "OTLPMetricExporter", MagicMock(return_value=fake_exporter))
    fake_reader = MagicMock()
    monkeypatch.setattr(
        metrics_module, "PeriodicExportingMetricReader", MagicMock(return_value=fake_reader)
    )
    fake_provider = MagicMock()
    monkeypatch.setattr(metrics_module, "MeterProvider", MagicMock(return_value=fake_provider))
    fake_set_meter_provider = MagicMock()
    monkeypatch.setattr(metrics_module.metrics, "set_meter_provider", fake_set_meter_provider)

    configure_metrics(service_name="rag-platform-api")

    fake_set_meter_provider.assert_called_once_with(fake_provider)


def test_configure_metrics_honors_otel_service_name_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OTEL_METRICS_ENABLED", "true")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "overridden-name")
    monkeypatch.setattr(metrics_module, "OTLPMetricExporter", MagicMock())
    monkeypatch.setattr(metrics_module, "PeriodicExportingMetricReader", MagicMock())
    fake_provider = MagicMock()
    monkeypatch.setattr(metrics_module, "MeterProvider", MagicMock(return_value=fake_provider))
    monkeypatch.setattr(metrics_module.metrics, "set_meter_provider", MagicMock())

    configure_metrics(service_name="rag-platform-api")

    mock_provider = metrics_module.MeterProvider
    resource = mock_provider.call_args.kwargs["resource"]  # type: ignore[attr-defined]
    assert resource.attributes[metrics_module.SERVICE_NAME] == "overridden-name"


@pytest.fixture
def fake_meter(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    meter = MagicMock()
    monkeypatch.setattr(metrics_module, "_meter", lambda: meter)
    return meter


def test_record_document_uploaded_increments_counter_labeled_by_mime_type(
    fake_meter: MagicMock,
) -> None:
    record_document_uploaded(mime_type="application/pdf")

    fake_meter.create_counter.assert_called_once_with(
        "rag_platform.documents.uploaded",
        description="Documentos aceitos no upload, por tipo MIME.",
    )
    fake_meter.create_counter.return_value.add.assert_called_once_with(
        1, {"mime_type": "application/pdf"}
    )


def test_record_document_reindexed_increments_counter_without_labels(
    fake_meter: MagicMock,
) -> None:
    record_document_reindexed()

    fake_meter.create_counter.return_value.add.assert_called_once_with(1)


def test_record_knowledge_base_mutation_increments_counter_labeled_by_action(
    fake_meter: MagicMock,
) -> None:
    record_knowledge_base_mutation(action="delete")

    fake_meter.create_counter.return_value.add.assert_called_once_with(1, {"action": "delete"})


def test_record_index_job_attempt_increments_counter_and_records_histogram(
    fake_meter: MagicMock,
) -> None:
    record_index_job_attempt(status="succeeded", duration_seconds=1.5)

    fake_meter.create_counter.return_value.add.assert_called_once_with(1, {"status": "succeeded"})
    fake_meter.create_histogram.return_value.record.assert_called_once_with(
        1.5, {"status": "succeeded"}
    )


def test_record_embedding_batch_increments_counter_and_records_histogram(
    fake_meter: MagicMock,
) -> None:
    record_embedding_batch(text_count=42, duration_seconds=0.25)

    fake_meter.create_counter.return_value.add.assert_called_once_with(42)
    fake_meter.create_histogram.return_value.record.assert_called_once_with(0.25)
