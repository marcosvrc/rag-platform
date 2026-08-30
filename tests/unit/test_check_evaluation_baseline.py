"""Testes de RAG-063: `scripts/check_evaluation_baseline.py`.

Ao contrário de `run_retrieval_evaluation.py`/`run_generation_evaluation.py`
(RAG-061/062), este script não chama nenhum modelo real — só lê
relatórios JSON já gravados em disco e compara contra a baseline
versionada (`packages/evaluation/baseline.py`) — por isso, mesmo
padrão de `test_check_security_exceptions.py` (RAG-071), é testado
diretamente."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from packages.evaluation import baseline as baseline_module
from scripts.check_evaluation_baseline import _load_current_metrics, main


@pytest.fixture(autouse=True)
def _custom_baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    (tmp_path / "poc.v1.yaml").write_text(
        "id: poc\nversion: v1\nmeasured: false\nmax_regression_pct: 0.05\n"
        "metrics:\n  recall_at_k: 0.80\n  mrr: 0.70\nlimitations:\n  - x\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(baseline_module, "_BASELINES_DIR", tmp_path)
    baseline_module.load_baseline.cache_clear()
    yield
    baseline_module.load_baseline.cache_clear()


def _write_report(path: Path, payload: dict[str, object]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class TestLoadCurrentMetrics:
    def test_extracts_only_numeric_top_level_fields(self, tmp_path: Path) -> None:
        report = _write_report(
            tmp_path / "r.json",
            {
                "dataset_id": "golden",
                "recall_at_k": 0.9,
                "mrr": 0.75,
                "case_results": [{"recall_at_k": 1.0}],
            },
        )

        metrics = _load_current_metrics([report])

        assert metrics == {"recall_at_k": 0.9, "mrr": 0.75}

    def test_excludes_booleans_even_though_they_are_ints_in_python(self, tmp_path: Path) -> None:
        report = _write_report(tmp_path / "r.json", {"recall_at_k": 0.9, "measured": True})

        metrics = _load_current_metrics([report])

        assert "measured" not in metrics

    def test_merges_keys_from_multiple_reports(self, tmp_path: Path) -> None:
        retrieval = _write_report(tmp_path / "retrieval.json", {"recall_at_k": 0.9, "mrr": 0.8})
        generation = _write_report(
            tmp_path / "generation.json", {"faithfulness": 0.95, "answer_relevancy": 0.9}
        )

        metrics = _load_current_metrics([retrieval, generation])

        assert metrics == {
            "recall_at_k": 0.9,
            "mrr": 0.8,
            "faithfulness": 0.95,
            "answer_relevancy": 0.9,
        }


class TestMain:
    def test_returns_zero_when_metrics_meet_the_baseline(self, tmp_path: Path) -> None:
        report = _write_report(tmp_path / "r.json", {"recall_at_k": 0.80, "mrr": 0.70})

        exit_code = main(["--report", str(report)])

        assert exit_code == 0

    def test_returns_one_when_a_metric_regresses(self, tmp_path: Path) -> None:
        report = _write_report(tmp_path / "r.json", {"recall_at_k": 0.10, "mrr": 0.70})

        exit_code = main(["--report", str(report)])

        assert exit_code == 1

    def test_returns_one_when_no_baseline_metric_is_present_in_the_report(
        self, tmp_path: Path
    ) -> None:
        report = _write_report(tmp_path / "r.json", {"outra_coisa": 1.0})

        exit_code = main(["--report", str(report)])

        assert exit_code == 1

    def test_accepts_multiple_report_flags(self, tmp_path: Path) -> None:
        retrieval = _write_report(tmp_path / "retrieval.json", {"recall_at_k": 0.80})
        generation = _write_report(tmp_path / "generation.json", {"mrr": 0.70})

        exit_code = main(["--report", str(retrieval), "--report", str(generation)])

        assert exit_code == 0
