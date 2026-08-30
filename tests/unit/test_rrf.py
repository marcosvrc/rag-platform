"""Testes de RAG-032: `reciprocal_rank_fusion` — combina os rankings de
`VectorSearchPort` e `LexicalSearchPort` num único ranking, sem
depender de nenhuma das duas buscas de verdade (usa `ScoredChunk`
diretamente)."""

from uuid import UUID, uuid4

import pytest

from packages.application.ports.lexical_search import ScoredChunk
from packages.domain.entities.chunk import Chunk
from packages.retrieval.rrf import reciprocal_rank_fusion


def _chunk(chunk_id: UUID | None = None) -> Chunk:
    return Chunk(
        id=chunk_id or uuid4(),
        tenant_id=uuid4(),
        knowledge_base_id=uuid4(),
        version_id=uuid4(),
        content="conteúdo qualquer",
        token_count=1,
        page=None,
        section=None,
        metadata={},
        embedding=None,
    )


def _scored(chunk: Chunk, score: float) -> ScoredChunk:
    return ScoredChunk(chunk=chunk, score=score)


def test_chunk_ranked_well_in_both_lists_beats_chunk_ranked_well_in_only_one() -> None:
    in_both = _chunk()
    only_vector = _chunk()

    vector_results = [_scored(in_both, 0.9), _scored(only_vector, 0.8)]
    lexical_results = [_scored(in_both, 5.0)]

    fused = reciprocal_rank_fusion(vector_results=vector_results, lexical_results=lexical_results)

    assert [scored.chunk.id for scored in fused] == [in_both.id, only_vector.id]


def test_duplicate_chunk_appears_once_in_the_fused_result() -> None:
    chunk = _chunk()
    vector_results = [_scored(chunk, 0.9)]
    lexical_results = [_scored(chunk, 5.0)]

    fused = reciprocal_rank_fusion(vector_results=vector_results, lexical_results=lexical_results)

    assert len(fused) == 1
    assert fused[0].chunk.id == chunk.id


def test_score_is_the_sum_of_reciprocal_rank_contributions_from_both_lists() -> None:
    chunk = _chunk()
    vector_results = [_scored(chunk, 0.9)]  # rank 1
    lexical_results = [_scored(_chunk(), 1.0), _scored(chunk, 1.0)]  # rank 2

    fused = reciprocal_rank_fusion(
        vector_results=vector_results, lexical_results=lexical_results, k=60
    )

    expected = 1.0 / (60 + 1) + 1.0 / (60 + 2)
    (result,) = [scored for scored in fused if scored.chunk.id == chunk.id]
    assert result.score == pytest.approx(expected)


def test_weights_scale_each_ranking_contribution_independently() -> None:
    chunk = _chunk()
    vector_results = [_scored(chunk, 0.9)]  # rank 1
    lexical_results: list[ScoredChunk] = []

    fused = reciprocal_rank_fusion(
        vector_results=vector_results,
        lexical_results=lexical_results,
        vector_weight=2.0,
        k=60,
    )

    assert fused[0].score == pytest.approx(2.0 / 61)


def test_a_smaller_k_makes_position_differences_matter_more() -> None:
    first = _chunk()
    second = _chunk()
    vector_results = [_scored(first, 0.9), _scored(second, 0.8)]

    fused_small_k = reciprocal_rank_fusion(vector_results=vector_results, lexical_results=[], k=1)
    fused_large_k = reciprocal_rank_fusion(
        vector_results=vector_results, lexical_results=[], k=1000
    )

    gap_small_k = fused_small_k[0].score - fused_small_k[1].score
    gap_large_k = fused_large_k[0].score - fused_large_k[1].score
    assert gap_small_k > gap_large_k


def test_ties_are_broken_deterministically_by_chunk_id() -> None:
    """Um empate de verdade precisa vir da mesma posição (rank) em
    rankings DIFERENTES — `first` em 1º no vetorial e `second` em 1º no
    lexical, cada um sozinho na sua lista, dão a mesma contribuição
    `1/(k+1)`. Colocar os dois no mesmo ranking (ranks 1 e 2) não seria
    um empate de verdade — o de rank 1 sempre pontuaria mais alto,
    tornando o teste dependente da ordem de geração dos UUIDs (falho
    ~50% das vezes) em vez de testar o desempate."""
    first = _chunk()
    second = _chunk()
    expected_order = sorted([first.id, second.id], key=str)

    fused = reciprocal_rank_fusion(
        vector_results=[_scored(first, 1.0)],
        lexical_results=[_scored(second, 1.0)],
    )

    assert fused[0].score == pytest.approx(fused[1].score)
    assert [scored.chunk.id for scored in fused] == expected_order


def test_limit_truncates_to_the_highest_scoring_chunks() -> None:
    chunks = [_chunk() for _ in range(5)]
    vector_results = [_scored(chunk, 1.0) for chunk in chunks]

    fused = reciprocal_rank_fusion(vector_results=vector_results, lexical_results=[], limit=2)

    assert len(fused) == 2
    assert [scored.chunk.id for scored in fused] == [chunk.id for chunk in chunks[:2]]


def test_both_rankings_empty_returns_empty_list() -> None:
    fused = reciprocal_rank_fusion(vector_results=[], lexical_results=[])

    assert fused == []


def test_non_positive_k_raises_value_error() -> None:
    with pytest.raises(ValueError, match="k precisa ser positivo"):
        reciprocal_rank_fusion(vector_results=[], lexical_results=[], k=0)
