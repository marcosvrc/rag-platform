"""Testes de RAG-033: `PassthroughReranker` — a implementação de
`RerankerPort` usada quando `Settings.reranker_enabled` é `False`."""

from __future__ import annotations

from uuid import uuid4

from adapters.reranker.passthrough import PassthroughReranker
from packages.application.ports.lexical_search import ScoredChunk
from packages.domain.entities.chunk import Chunk


def _chunk() -> Chunk:
    return Chunk(
        id=uuid4(),
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


def _scored(score: float) -> ScoredChunk:
    return ScoredChunk(chunk=_chunk(), score=score)


async def test_returns_candidates_verbatim_in_the_same_order() -> None:
    candidates = [_scored(0.1), _scored(0.9), _scored(0.5)]

    result = await PassthroughReranker().rerank(query="q", candidates=candidates, top_n=10)

    assert result == candidates


async def test_truncates_to_top_n() -> None:
    candidates = [_scored(1.0) for _ in range(5)]

    result = await PassthroughReranker().rerank(query="q", candidates=candidates, top_n=2)

    assert result == candidates[:2]


async def test_empty_candidates_returns_empty_list() -> None:
    result = await PassthroughReranker().rerank(query="q", candidates=[], top_n=10)

    assert result == []
