"""Testes de RAG-061: métricas puras de avaliação de retrieval
(`packages/evaluation/retrieval_metrics.py`) — Recall@K e MRR."""

from __future__ import annotations

import pytest

from packages.evaluation import retrieval_metrics
from packages.evaluation.golden_dataset import ExpectedEvidence


def _evidence(snippet: str) -> ExpectedEvidence:
    return ExpectedEvidence(document_id="doc", content_contains=snippet)


class TestCountFoundEvidence:
    def test_counts_zero_when_nothing_matches(self) -> None:
        found = retrieval_metrics.count_found_evidence(
            [_evidence("alfa")], ["conteúdo irrelevante"]
        )
        assert found == 0

    def test_counts_each_evidence_found_in_any_content(self) -> None:
        found = retrieval_metrics.count_found_evidence(
            [_evidence("alfa"), _evidence("beta")],
            ["fala sobre alfa aqui", "fala sobre beta aqui"],
        )
        assert found == 2

    def test_a_single_content_can_satisfy_more_than_one_evidence(self) -> None:
        found = retrieval_metrics.count_found_evidence(
            [_evidence("alfa"), _evidence("beta")], ["fala sobre alfa e beta juntos"]
        )
        assert found == 2


class TestRecallAtK:
    def test_raises_for_empty_expected_evidence(self) -> None:
        with pytest.raises(ValueError, match="expected_evidence não pode ser vazio"):
            retrieval_metrics.recall_at_k([], ["qualquer conteúdo"], k=5)

    def test_full_recall_when_all_evidence_is_found(self) -> None:
        recall = retrieval_metrics.recall_at_k(
            [_evidence("alfa"), _evidence("beta")],
            ["contém alfa", "contém beta"],
            k=5,
        )
        assert recall == 1.0

    def test_partial_recall_when_only_some_evidence_is_found(self) -> None:
        recall = retrieval_metrics.recall_at_k(
            [_evidence("alfa"), _evidence("beta")], ["contém só alfa"], k=5
        )
        assert recall == 0.5

    def test_zero_recall_when_nothing_matches(self) -> None:
        recall = retrieval_metrics.recall_at_k([_evidence("alfa")], ["nada a ver"], k=5)
        assert recall == 0.0

    def test_k_truncates_the_retrieved_contents_considered(self) -> None:
        recall = retrieval_metrics.recall_at_k(
            [_evidence("alfa")], ["irrelevante-1", "irrelevante-2", "contém alfa"], k=2
        )
        assert recall == 0.0

    def test_evidence_within_k_is_counted(self) -> None:
        recall = retrieval_metrics.recall_at_k(
            [_evidence("alfa")], ["contém alfa", "irrelevante"], k=2
        )
        assert recall == 1.0


class TestReciprocalRank:
    def test_returns_zero_when_nothing_matches(self) -> None:
        rr = retrieval_metrics.reciprocal_rank([_evidence("alfa")], ["nada", "nada de novo"])
        assert rr == 0.0

    def test_returns_zero_for_empty_retrieved_contents(self) -> None:
        rr = retrieval_metrics.reciprocal_rank([_evidence("alfa")], [])
        assert rr == 0.0

    def test_returns_one_when_the_first_position_matches(self) -> None:
        rr = retrieval_metrics.reciprocal_rank([_evidence("alfa")], ["contém alfa", "irrelevante"])
        assert rr == 1.0

    def test_returns_the_reciprocal_of_the_first_matching_rank(self) -> None:
        rr = retrieval_metrics.reciprocal_rank(
            [_evidence("alfa")], ["irrelevante-1", "irrelevante-2", "contém alfa"]
        )
        assert rr == pytest.approx(1.0 / 3.0)

    def test_matches_any_of_multiple_expected_evidence_items(self) -> None:
        rr = retrieval_metrics.reciprocal_rank(
            [_evidence("alfa"), _evidence("beta")], ["irrelevante", "contém beta"]
        )
        assert rr == pytest.approx(1.0 / 2.0)


class TestMean:
    def test_raises_for_an_empty_sequence(self) -> None:
        with pytest.raises(ValueError, match="values não pode ser vazio"):
            retrieval_metrics.mean([])

    def test_computes_the_arithmetic_mean(self) -> None:
        assert retrieval_metrics.mean([1.0, 0.5, 0.0]) == pytest.approx(0.5)

    def test_single_value_mean_is_itself(self) -> None:
        assert retrieval_metrics.mean([0.75]) == pytest.approx(0.75)
