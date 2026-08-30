"""Adapter "desativado" de `RerankerPort` (RAG-033).

Devolve `candidates` verbatim (truncados a `top_n`, sem tocar na
ordem) — a implementação de `RerankerPort` usada quando
`Settings.reranker_enabled` é `False` (RAG-034, o endpoint retrieve,
escolhe entre este e `LiteLLMReranker` a partir desse flag). Não é um
fake de teste (esse papel também cabe aqui, mas o motivo de existir é
de produção): "pode ser desativado" (critério de aceite) significa que
a aplicação nunca faz uma chamada de rede a mais quando reranking está
desligado — não um `if reranker_enabled` espalhado pelos casos de uso."""

from __future__ import annotations

from collections.abc import Sequence

from packages.application.ports.lexical_search import ScoredChunk
from packages.application.ports.reranker import RerankerPort


class PassthroughReranker(RerankerPort):
    async def rerank(
        self, *, query: str, candidates: Sequence[ScoredChunk], top_n: int
    ) -> list[ScoredChunk]:
        return list(candidates[:top_n])
