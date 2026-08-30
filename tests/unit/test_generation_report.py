"""Testes de RAG-062: serialização de `GenerationEvaluationReport`
(`packages/evaluation/generation_report.py`) — JSON e Markdown,
critério de aceite "relatório JSON e Markdown", mesmo padrão de
`test_retrieval_report.py` (RAG-061)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from packages.evaluation import generation_report
from packages.evaluation.generation_evaluation import (
    CaseGenerationResult,
    GenerationEvaluationReport,
    ThresholdCheck,
)

_GENERATED_AT = datetime(2026, 1, 15, 12, 30, tzinfo=UTC)


def _report() -> GenerationEvaluationReport:
    return GenerationEvaluationReport(
        dataset_id="golden",
        dataset_version="v1",
        generation_model_alias="generation-model-alias",
        prompt_id="answer",
        prompt_version="v1",
        evaluator_model_alias="generation-evaluator-model-alias",
        generated_at=_GENERATED_AT,
        faithfulness=0.9,
        answer_relevancy=0.8,
        total_prompt_tokens=120,
        total_completion_tokens=30,
        case_results=(
            CaseGenerationResult(
                case_id="gc-001",
                grounded=True,
                faithfulness=1.0,
                answer_relevancy=1.0,
                prompt_tokens=60,
                completion_tokens=15,
                total_tokens=75,
            ),
            CaseGenerationResult(
                case_id="gc-002",
                grounded=False,
                faithfulness=0.5,
                answer_relevancy=0.5,
                prompt_tokens=60,
                completion_tokens=15,
                total_tokens=75,
            ),
        ),
    )


class TestReportToDict:
    def test_uses_only_json_native_types(self) -> None:
        payload = generation_report.report_to_dict(_report())

        assert payload["dataset_id"] == "golden"
        assert payload["generation_model_alias"] == "generation-model-alias"
        assert payload["prompt_id"] == "answer"
        assert payload["prompt_version"] == "v1"
        assert payload["evaluator_model_alias"] == "generation-evaluator-model-alias"
        assert payload["generated_at"] == "2026-01-15T12:30:00+00:00"
        assert payload["evaluated_case_count"] == 2
        assert payload["faithfulness"] == 0.9
        assert payload["answer_relevancy"] == 0.8
        assert payload["total_prompt_tokens"] == 120
        assert payload["total_completion_tokens"] == 30
        assert payload["case_results"][0] == {
            "case_id": "gc-001",
            "grounded": True,
            "faithfulness": 1.0,
            "answer_relevancy": 1.0,
            "prompt_tokens": 60,
            "completion_tokens": 15,
            "total_tokens": 75,
        }


class TestRenderJson:
    def test_renders_valid_json_matching_report_to_dict(self) -> None:
        rendered = generation_report.render_json(_report())

        assert json.loads(rendered) == generation_report.report_to_dict(_report())

    def test_ends_with_a_trailing_newline(self) -> None:
        assert generation_report.render_json(_report()).endswith("\n")


class TestRenderMarkdown:
    def test_includes_the_aggregate_metrics_and_provenance(self) -> None:
        rendered = generation_report.render_markdown(_report())

        assert "Faithfulness: 0.9000" in rendered
        assert "Answer relevancy: 0.8000" in rendered
        assert "generation-model-alias" in rendered
        assert "`answer` v1" in rendered  # prompt_version já é "v1", sem "v" duplicado
        assert "generation-evaluator-model-alias" in rendered

    def test_does_not_duplicate_the_v_prefix_already_in_the_version_strings(self) -> None:
        rendered = generation_report.render_markdown(_report())

        assert "vv1" not in rendered

    def test_includes_a_row_per_case(self) -> None:
        rendered = generation_report.render_markdown(_report())

        assert "| gc-001 |" in rendered
        assert "| gc-002 |" in rendered
        assert "sim" in rendered
        assert "não" in rendered

    def test_omits_the_threshold_verdict_when_not_given(self) -> None:
        rendered = generation_report.render_markdown(_report())

        assert "Limiar" not in rendered

    def test_includes_a_passing_verdict(self) -> None:
        rendered = generation_report.render_markdown(
            _report(), threshold_check=ThresholdCheck(passed=True, violations=())
        )

        assert "PASSOU" in rendered

    def test_includes_a_failing_verdict_with_violations(self) -> None:
        rendered = generation_report.render_markdown(
            _report(),
            threshold_check=ThresholdCheck(
                passed=False, violations=("Faithfulness 0.1000 abaixo do limiar mínimo 0.8500.",)
            ),
        )

        assert "FALHOU" in rendered
        assert "Faithfulness 0.1000 abaixo do limiar mínimo 0.8500." in rendered
