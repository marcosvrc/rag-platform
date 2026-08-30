"""Script de verificação de regressão contra a baseline da POC
(RAG-063, seção 21 do plano: "Regressão máxima permitida de 5% contra
a baseline aprovada").

Lê um relatório JSON já gravado em disco por `scripts/
run_retrieval_evaluation.py` (campos `recall_at_k`, `mrr`) ou por
`scripts/run_generation_evaluation.py` (campos `faithfulness`,
`answer_relevancy`) — ou os dois juntos, se o chamador combinar as
chaves — e compara contra `packages.evaluation.baseline.
get_current_baseline()` via `check_regression`.

Deliberadamente lê o relatório como um `dict` genérico (`json.load`),
nunca importando `RetrievalEvaluationReport`/`GenerationEvaluationReport`
de RAG-061/RAG-062: ver a docstring de
`packages/evaluation/baseline.py` para o racional completo de por que
esta atividade não acopla código com essas duas branches irmãs ainda
não mescladas.

Este script, assim como os de RAG-061/062, não roda como parte de
`pytest tests/unit` (seção 15 do plano) — depende de relatórios já
gerados por uma execução real dos scripts de avaliação."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from packages.evaluation.baseline import check_regression, load_baseline


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verifica regressão de métricas de avaliação contra a baseline da POC (RAG-063)."
        )
    )
    parser.add_argument(
        "--report",
        type=Path,
        action="append",
        required=True,
        help=(
            "Caminho de um relatório JSON (retrieval-evaluation.json e/ou "
            "generation-evaluation.json). Pode ser passado mais de uma vez; as "
            "chaves de nível superior de todos os relatórios são combinadas antes "
            "da checagem."
        ),
    )
    parser.add_argument("--baseline-id", default="poc")
    parser.add_argument("--baseline-version", default="v1")
    return parser.parse_args(argv)


def _load_current_metrics(report_paths: list[Path]) -> dict[str, float]:
    """Combina as chaves numéricas de nível superior de cada relatório
    em `report_paths` num único `dict`. Chaves não-numéricas (ex.:
    `dataset_id`, `generated_at`, `case_results`) são ignoradas: só
    interessam aqui os nomes que também aparecem em
    `Baseline.metrics`."""
    current_metrics: dict[str, float] = {}
    for path in report_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for key, value in payload.items():
            if isinstance(value, int | float) and not isinstance(value, bool):
                current_metrics[key] = float(value)
    return current_metrics


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    baseline = load_baseline(args.baseline_id, args.baseline_version)
    current_metrics = _load_current_metrics(args.report)

    checked_metrics = sorted(set(current_metrics) & set(baseline.metrics))
    skipped_metrics = sorted(set(baseline.metrics) - set(current_metrics))
    if not checked_metrics:
        print(
            "Nenhuma métrica da baseline foi encontrada nos relatórios informados "
            f"(baseline conhece: {sorted(baseline.metrics)}).",
            file=sys.stderr,
        )
        return 1
    if skipped_metrics:
        print(f"Métricas da baseline ausentes dos relatórios (ignoradas): {skipped_metrics}.")

    result = check_regression(current_metrics, baseline=baseline)

    for metric in checked_metrics:
        print(
            f"{metric}: atual={current_metrics[metric]:.4f} baseline={baseline.metrics[metric]:.4f}"
        )

    if not result.passed:
        print("FALHOU na verificação de regressão:", file=sys.stderr)
        for violation in result.violations:
            print(f"  - {violation}", file=sys.stderr)
        return 1

    print("PASSOU na verificação de regressão.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
