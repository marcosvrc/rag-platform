"""Testes de RAG-061: serialização de `RetrievalEvaluationReport`
(`packages/evaluation/retrieval_report.py`) — JSON e Markdown,
critério de aceite "relatório JSON e Markdown"."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from packages.evaluation import retrieval_report
from packages.evaluation.retrieval_evaluation import (
    CaseRetrievalResult,
    RetrievalEvaluationReport,
    ThresholdCheck,
)

_GENERATED_AT = datetime(2026, 1, 15, 12, 30, tzinfo=UTC)


def _report() -> RetrievalEvaluationReport:
    return RetrievalEvaluationReport(
        dataset_id="golden",
        dataset_version="v1",
        k=5,
        generated_at=_GENERATED_AT,
        recall_at_k=0.9,
        mrr=0.8,
        case_results=(
            CaseRetrievalResult(
                case_id="gc-001",
                expected_evidence_count=1,
                found_evidence_count=1,
                retrieved_chunk_count=5,
                recall_at_k=1.0,
                reciprocal_rank=1.0,
            ),
            CaseRetrievalResult(
                case_id="gc-002",
                expected_evidence_count=2,
                found_evidence_count=1,
                retrieved_chunk_count=5,
                recall_at_k=0.5,
                reciprocal_rank=0.5,
            ),
        ),
    )


class TestReportToDict:
    def test_uses_only_json_native_types(self) -> None:
        payload = retrieval_report.report_to_dict(_report())

        assert payload["dataset_id"] == "golden"
        assert payload["dataset_version"] == "v1"
        assert payload["k"] == 5
        assert payload["generated_at"] == "2026-01-15T12:30:00+00:00"
        assert payload["evaluated_case_count"] == 2
        assert payload["recall_at_k"] == 0.9
        assert payload["mrr"] == 0.8
        assert payload["case_results"] == [
            {
                "case_id": "gc-001",
                "expected_evidence_count": 1,
                "found_evidence_count": 1,
                "retrieved_chunk_count": 5,
                "recall_at_k": 1.0,
                "reciprocal_rank": 1.0,
            },
            {
                "case_id": "gc-002",
                "expected_evidence_count": 2,
                "found_evidence_count": 1,
                "retrieved_chunk_count": 5,
                "recall_at_k": 0.5,
                "reciprocal_rank": 0.5,
            },
        ]


class TestRenderJson:
    def test_renders_valid_json_matching_report_to_dict(self) -> None:
        rendered = retrieval_report.render_json(_report())

        assert json.loads(rendered) == retrieval_report.report_to_dict(_report())

    def test_ends_with_a_trailing_newline(self) -> None:
        assert retrieval_report.render_json(_report()).endswith("\n")


class TestRenderMarkdown:
    def test_includes_the_aggregate_metrics(self) -> None:
        rendered = retrieval_report.render_markdown(_report())

        assert "Recall@5: 0.9000" in rendered
        assert "MRR: 0.8000" in rendered
        assert "Casos avaliados: 2" in rendered

    def test_includes_a_row_per_case(self) -> None:
        rendered = retrieval_report.render_markdown(_report())

        assert "| gc-001 |" in rendered
        assert "| gc-002 |" in rendered

    def test_omits_the_threshold_verdict_when_not_given(self) -> None:
        rendered = retrieval_report.render_markdown(_report())

        assert "Limiar" not in rendered

    def test_includes_a_passing_verdict(self) -> None:
        rendered = retrieval_report.render_markdown(
            _report(), threshold_check=ThresholdCheck(passed=True, violations=())
        )

        assert "PASSOU" in rendered

    def test_includes_a_failing_verdict_with_violations(self) -> None:
        rendered = retrieval_report.render_markdown(
            _report(),
            threshold_check=ThresholdCheck(
                passed=False, violations=("Recall@5 0.1000 abaixo do limiar mínimo 0.8000.",)
            ),
        )

        assert "FALHOU" in rendered
        assert "Recall@5 0.1000 abaixo do limiar mínimo 0.8000." in rendered
