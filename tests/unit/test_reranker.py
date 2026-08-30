"""Testes de RAG-033: `rerank_safely` — a única parte de
`packages.application.ports.reranker` com lógica própria (o resto é só
a interface `RerankerPort` e a taxonomia de erro, mesmo padrão de
`packages.application.ports.embedding_provider`, RAG-025).

Cobre o critério de aceite "timeout usa ranking anterior" — na
prática, QUALQUER falha de `RerankerError`, não só timeout (reranking
nunca deve derrubar uma consulta, ver docstring do módulo)."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import uuid4

import pytest

from packages.application.ports.lexical_search import ScoredChunk
from packages.application.ports.reranker import (
    RerankerPort,
    RerankerTimeoutError,
    RerankerUnavailableError,
    rerank_safely,
)
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


def _scored(chunk: Chunk, score: float) -> ScoredChunk:
    return ScoredChunk(chunk=chunk, score=score)


class _AlwaysTimingOutReranker(RerankerPort):
    async def rerank(
        self, *, query: str, candidates: Sequence[ScoredChunk], top_n: int
    ) -> list[ScoredChunk]:
        raise RerankerTimeoutError(detail="simulado")


class _AlwaysUnavailableReranker(RerankerPort):
    async def rerank(
        self, *, query: str, candidates: Sequence[ScoredChunk], top_n: int
    ) -> list[ScoredChunk]:
        raise RerankerUnavailableError(detail="simulado")


class _ReversingReranker(RerankerPort):
    """Dublê que de fato reordena (inverte), para provar que
    `rerank_safely` devolve o resultado do reranker quando ele
    funciona — não sempre o ranking original."""

    async def rerank(
        self, *, query: str, candidates: Sequence[ScoredChunk], top_n: int
    ) -> list[ScoredChunk]:
        return list(reversed(candidates))[:top_n]


async def test_rerank_safely_returns_the_reranked_result_on_success() -> None:
    first, second = _scored(_chunk(), 0.1), _scored(_chunk(), 0.2)

    result = await rerank_safely(
        _ReversingReranker(), query="q", candidates=[first, second], top_n=10
    )

    assert result == [second, first]


@pytest.mark.parametrize("reranker_cls", [_AlwaysTimingOutReranker, _AlwaysUnavailableReranker])
async def test_rerank_safely_falls_back_to_the_previous_ranking_on_any_failure(
    reranker_cls: type[RerankerPort],
) -> None:
    first, second, third = (
        _scored(_chunk(), 0.3),
        _scored(_chunk(), 0.2),
        _scored(_chunk(), 0.1),
    )

    result = await rerank_safely(
        reranker_cls(), query="q", candidates=[first, second, third], top_n=10
    )

    assert result == [first, second, third]


async def test_rerank_safely_truncates_the_fallback_to_top_n() -> None:
    candidates = [_scored(_chunk(), 1.0) for _ in range(5)]

    result = await rerank_safely(
        _AlwaysTimingOutReranker(), query="q", candidates=candidates, top_n=2
    )

    assert result == candidates[:2]
