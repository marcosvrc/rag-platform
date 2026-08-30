"""Serialização de `RetrievalEvaluationReport` em JSON e Markdown
(RAG-061, critério de aceite "relatório JSON e Markdown").

Módulo separado de `retrieval_evaluation.py` (que só computa as
métricas): formatar um relatório para leitura humana (Markdown) ou
para consumo por outra ferramenta (JSON, ex.: um passo de CI, RAG-073)
é uma responsabilidade própria, a mesma divisão já usada entre
`packages/generation/groundedness.py` (computa) e o router que serializa
a resposta HTTP (formata)."""

from __future__ import annotations

import json
from typing import Any

from packages.evaluation.retrieval_evaluation import (
    CaseRetrievalResult,
    RetrievalEvaluationReport,
    ThresholdCheck,
)


def _case_to_dict(case: CaseRetrievalResult) -> dict[str, Any]:
    return {
        "case_id": case.case_id,
        "expected_evidence_count": case.expected_evidence_count,
        "found_evidence_count": case.found_evidence_count,
        "retrieved_chunk_count": case.retrieved_chunk_count,
        "recall_at_k": case.recall_at_k,
        "reciprocal_rank": case.reciprocal_rank,
    }


def report_to_dict(report: RetrievalEvaluationReport) -> dict[str, Any]:
    """Representação do relatório em tipos nativos (str/int/float/list/
    dict) — sem `UUID`/`datetime` crus, para que `json.dumps` funcione
    sem um encoder customizado."""
    return {
        "dataset_id": report.dataset_id,
        "dataset_version": report.dataset_version,
        "k": report.k,
        "generated_at": report.generated_at.isoformat(),
        "evaluated_case_count": report.evaluated_case_count,
        "recall_at_k": report.recall_at_k,
        "mrr": report.mrr,
        "case_results": [_case_to_dict(case) for case in report.case_results],
    }


def render_json(report: RetrievalEvaluationReport) -> str:
    """`report` como uma string JSON (indentada, para ser legível tanto
    por humano quanto por ferramenta) — pronta para gravar em disco ou
    publicar como artefato de CI (RAG-073)."""
    return json.dumps(report_to_dict(report), indent=2, ensure_ascii=False) + "\n"


def render_markdown(
    report: RetrievalEvaluationReport, *, threshold_check: ThresholdCheck | None = None
) -> str:
    """`report` como Markdown legível — um resumo agregado, o
    veredito do limiar quando informado (`threshold_check`), e uma
    tabela por caso para depuração (qual caso específico puxou a média
    para baixo)."""
    lines = [
        f"# Avaliação de retrieval — dataset `{report.dataset_id}` {report.dataset_version}",
        "",
        f"- Gerado em: {report.generated_at.isoformat()}",
        f"- Casos avaliados: {report.evaluated_case_count}",
        f"- K: {report.k}",
        f"- Recall@{report.k}: {report.recall_at_k:.4f}",
        f"- MRR: {report.mrr:.4f}",
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
            "| Caso | Evidências esperadas | Evidências encontradas | Chunks recuperados "
            "| Recall@K | RR |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for case in report.case_results:
        lines.append(
            f"| {case.case_id} | {case.expected_evidence_count} | "
            f"{case.found_evidence_count} | {case.retrieved_chunk_count} | "
            f"{case.recall_at_k:.4f} | {case.reciprocal_rank:.4f} |"
        )

    return "\n".join(lines) + "\n"
