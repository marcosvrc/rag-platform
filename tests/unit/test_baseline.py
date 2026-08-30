"""Testes da baseline versionada e verificação de regressão (RAG-063)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from packages.evaluation import baseline as baseline_module
from packages.evaluation.baseline import (
    Baseline,
    BaselineNotFoundError,
    RegressionCheck,
    check_regression,
    get_current_baseline,
    load_baseline,
)


def _baseline(**overrides: object) -> Baseline:
    fields: dict[str, object] = {
        "id": "poc",
        "version": "v1",
        "measured": False,
        "max_regression_pct": 0.05,
        "metrics": {"recall_at_k": 0.80, "mrr": 0.70},
        "limitations": ("valores-alvo, não medidos",),
    }
    fields.update(overrides)
    return Baseline(**fields)  # type: ignore[arg-type]


# --- carregamento da baseline real (poc/v1) ---------------------------------


def test_load_baseline_poc_v1_tem_id_e_versao_corretos() -> None:
    baseline = load_baseline("poc", "v1")

    assert isinstance(baseline, Baseline)
    assert baseline.id == "poc"
    assert baseline.version == "v1"


def test_load_baseline_poc_v1_tem_as_quatro_metricas_da_secao_21() -> None:
    baseline = load_baseline("poc", "v1")

    assert baseline.metrics["recall_at_k"] == 0.80
    assert baseline.metrics["mrr"] == 0.70
    assert baseline.metrics["faithfulness"] == 0.85
    assert baseline.metrics["answer_relevancy"] == 0.85


def test_load_baseline_poc_v1_regressao_maxima_e_cinco_por_cento() -> None:
    baseline = load_baseline("poc", "v1")

    assert baseline.max_regression_pct == 0.05


def test_load_baseline_poc_v1_ainda_nao_foi_medida_de_verdade() -> None:
    baseline = load_baseline("poc", "v1")

    assert baseline.measured is False


def test_load_baseline_poc_v1_documenta_limitacoes() -> None:
    baseline = load_baseline("poc", "v1")

    assert len(baseline.limitations) >= 1
    for limitation in baseline.limitations:
        assert limitation.strip() != ""


def test_load_baseline_versao_inexistente_levanta_not_found_error() -> None:
    with pytest.raises(BaselineNotFoundError):
        load_baseline("poc", "v999")


def test_load_baseline_id_inexistente_levanta_not_found_error() -> None:
    with pytest.raises(BaselineNotFoundError):
        load_baseline("nao-existe", "v1")


def test_load_baseline_e_cacheado_por_id_e_versao() -> None:
    a = load_baseline("poc", "v1")
    b = load_baseline("poc", "v1")

    assert a is b


def test_get_current_baseline_retorna_poc_v1() -> None:
    baseline = get_current_baseline()

    assert baseline.id == "poc"
    assert baseline.version == "v1"


def test_load_baseline_com_campos_divergentes_do_nome_do_arquivo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "poc.v2.yaml").write_text(
        "id: poc\nversion: v1\nmeasured: false\nmax_regression_pct: 0.05\n"
        "metrics:\n  recall_at_k: 0.8\nlimitations:\n  - x\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(baseline_module, "_BASELINES_DIR", tmp_path)
    load_baseline.cache_clear()

    try:
        with pytest.raises(ValueError, match="não correspondem ao nome do arquivo"):
            load_baseline("poc", "v2")
    finally:
        load_baseline.cache_clear()


# --- schema Baseline ---------------------------------------------------------


def test_baseline_e_imutavel() -> None:
    baseline = _baseline()

    with pytest.raises(ValidationError):
        baseline.id = "outro"


def test_baseline_rejeita_campo_desconhecido() -> None:
    with pytest.raises(ValidationError):
        _baseline(campo_extra="x")


def test_baseline_rejeita_metricas_vazias() -> None:
    with pytest.raises(ValidationError):
        _baseline(metrics={})


def test_baseline_rejeita_limitacoes_vazias() -> None:
    with pytest.raises(ValidationError):
        _baseline(limitations=())


def test_baseline_rejeita_max_regression_pct_fora_do_intervalo() -> None:
    with pytest.raises(ValidationError):
        _baseline(max_regression_pct=0.0)
    with pytest.raises(ValidationError):
        _baseline(max_regression_pct=1.5)


class TestMinimumAcceptable:
    def test_computes_the_value_below_which_a_metric_regresses(self) -> None:
        baseline = _baseline(metrics={"recall_at_k": 0.80}, max_regression_pct=0.05)

        assert baseline.minimum_acceptable("recall_at_k") == pytest.approx(0.76)

    def test_raises_key_error_for_an_unknown_metric(self) -> None:
        baseline = _baseline(metrics={"recall_at_k": 0.80})

        with pytest.raises(KeyError):
            baseline.minimum_acceptable("mrr")


# --- check_regression ---------------------------------------------------------


class TestCheckRegression:
    def test_passes_when_current_metrics_match_the_baseline_exactly(self) -> None:
        baseline = _baseline(metrics={"recall_at_k": 0.80, "mrr": 0.70})

        result = check_regression({"recall_at_k": 0.80, "mrr": 0.70}, baseline=baseline)

        assert result == RegressionCheck(passed=True, violations=())

    def test_passes_when_current_metrics_improve_on_the_baseline(self) -> None:
        baseline = _baseline(metrics={"recall_at_k": 0.80})

        result = check_regression({"recall_at_k": 0.95}, baseline=baseline)

        assert result.passed is True

    def test_passes_with_a_drop_within_the_allowed_regression(self) -> None:
        baseline = _baseline(metrics={"recall_at_k": 0.80}, max_regression_pct=0.05)

        # 0.80 * (1 - 0.05) = 0.76 é o mínimo aceitável; 0.77 ainda passa.
        result = check_regression({"recall_at_k": 0.77}, baseline=baseline)

        assert result.passed is True

    def test_fails_with_a_drop_beyond_the_allowed_regression(self) -> None:
        baseline = _baseline(metrics={"recall_at_k": 0.80}, max_regression_pct=0.05)

        result = check_regression({"recall_at_k": 0.70}, baseline=baseline)

        assert result.passed is False
        assert len(result.violations) == 1
        assert "recall_at_k" in result.violations[0]

    def test_fails_exactly_at_the_regression_boundary(self) -> None:
        baseline = _baseline(metrics={"recall_at_k": 0.80}, max_regression_pct=0.05)

        # 0.76 é o mínimo aceitável — abaixo dele já é regressão; o
        # próprio limite (0.76) ainda deve passar (>=, não >).
        result = check_regression({"recall_at_k": 0.76}, baseline=baseline)

        assert result.passed is True

    def test_reports_one_violation_per_regressed_metric(self) -> None:
        baseline = _baseline(metrics={"recall_at_k": 0.80, "mrr": 0.70})

        result = check_regression({"recall_at_k": 0.10, "mrr": 0.10}, baseline=baseline)

        assert result.passed is False
        assert len(result.violations) == 2

    def test_ignores_a_baseline_metric_missing_from_current_metrics(self) -> None:
        baseline = _baseline(metrics={"recall_at_k": 0.80, "mrr": 0.70})

        # current_metrics só traz recall_at_k — mrr é ignorado, não
        # tratado como regressão nem como erro (permite comparar
        # contra um relatório parcial, ex.: só de retrieval).
        result = check_regression({"recall_at_k": 0.80}, baseline=baseline)

        assert result.passed is True

    def test_ignores_a_current_metric_unknown_to_the_baseline(self) -> None:
        baseline = _baseline(metrics={"recall_at_k": 0.80})

        result = check_regression(
            {"recall_at_k": 0.80, "alguma_metrica_nova": 0.01}, baseline=baseline
        )

        assert result.passed is True

    def test_handles_a_zero_baseline_value_without_dividing_by_zero(self) -> None:
        baseline = _baseline(metrics={"recall_at_k": 0.0}, max_regression_pct=0.05)

        result = check_regression({"recall_at_k": 0.0}, baseline=baseline)

        assert result.passed is True

    def test_violation_message_reports_the_regression_percentage(self) -> None:
        baseline = _baseline(metrics={"recall_at_k": 0.80}, max_regression_pct=0.05)

        result = check_regression({"recall_at_k": 0.40}, baseline=baseline)

        assert "50.0%" in result.violations[0]
