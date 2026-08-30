"""Serialização de `GenerationEvaluationReport` em JSON e Markdown
(RAG-062, critério de aceite "relatório JSON e Markdown" — mesma
exigência da RAG-061, mesmo padrão de
`packages.evaluation.retrieval_report`, módulo próprio pela mesma razão
de separação de responsabilidade (computar vs. formatar)."""

from __future__ import annotations

import json
from typing import Any

from packages.evaluation.generation_evaluation import (
    CaseGenerationResult,
    GenerationEvaluationReport,
    ThresholdCheck,
)


def _case_to_dict(case: CaseGenerationResult) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "grounded": case.grounded,
        "faithfulness": case.faithfulness,
        "answer_relevancy": case.answer_relevancy,
        "prompt_tokens": case.prompt_tokens,
        "completion_tokens": case.completion_tokens,
        "total_tokens": case.total_tokens,
    }


def report_to_dict(report: GenerationEvaluationReport) -> dict[str, Any]:
    """Representação do relatório em tipos nativos (str/int/float/bool/
    list/dict) — sem `datetime` cru, para que `json.dumps` funcione sem
    um encoder customizado."""
    return {
        "dataset_id": report.dataset_id,
        "dataset_version": report.dataset_version,
        "generation_model_alias": report.generation_model_alias,
        "prompt_id": report.prompt_id,
        "prompt_version": report.prompt_version,
        "evaluator_model_alias": report.evaluator_model_alias,
        "generated_at": report.generated_at.isoformat(),
        "evaluated_case_count": report.evaluated_case_count,
        "faithfulness": report.faithfulness,
        "answer_relevancy": report.answer_relevancy,
        "total_prompt_tokens": report.total_prompt_tokens,
        "total_completion_tokens": report.total_completion_tokens,
        "case_results": [_case_to_dict(case) for case in report.case_results],
    }


def render_json(report: GenerationEvaluationReport) -> str:
    """`report` como uma string JSON (indentada) — pronta para gravar
    em disco ou publicar como artefato de CI (RAG-073)."""
    return json.dumps(report_to_dict(report), indent=2, ensure_ascii=False) + "\n"


def render_markdown(
    report: GenerationEvaluationReport, *, threshold_check: ThresholdCheck | None = None
) -> str:
    """`report` como Markdown legível — o resumo agregado (incluindo
    QUAL prompt/modelo de geração e QUAL modelo-juiz produziram estes
    números, critério de aceite "resultados ligados às versões de
    prompt/modelo"), o veredito do limiar quando informado, e uma
    tabela por caso para depuração."""
    lines = [
        f"# Avaliação de geração — dataset `{report.dataset_id}` {report.dataset_version}",
        "",
        f"- Gerado em: {report.generated_at.isoformat()}",
        f"- Casos avaliados: {report.evaluated_case_count}",
        f"- Modelo de geração: `{report.generation_model_alias}`",
        f"- Prompt de resposta: `{report.prompt_id}` {report.prompt_version}",
        f"- Modelo avaliador: `{report.evaluator_model_alias}`",
        f"- Faithfulness: {report.faithfulness:.4f}",
        f"- Answer relevancy: {report.answer_relevancy:.4f}",
        f"- Tokens de avaliação (prompt/completion): "
        f"{report.total_prompt_tokens}/{report.total_completion_tokens}",
    ]

    if threshold_check is not None:
        veredito = "PASSOU" if threshold_check.passed else "FALHOU"
        lines.append(f"- Limiar: {veredito}")
        for violation in threshold_check.violations:
            lines.append(f"  - {violation}")

    lines.extend(
        [
            "",
            "## Por caso",
            "",
            "| Caso | Fundamentada | Faithfulness | Answer relevancy | Tokens (total) |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for case in report.case_results:
        lines.append(
            f"| {case.case_id} | {'sim' if case.grounded else 'não'} | "
            f"{case.faithfulness:.4f} | {case.answer_relevancy:.4f} | {case.total_tokens} |"
        )

    return "\n".join(lines) + "\n"
