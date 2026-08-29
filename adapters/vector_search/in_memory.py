"""Fake em memória de `VectorSearchPort`, para testes (RAG-030).

Mesmo papel de `adapters/lexical_search/in_memory.py`: prova o
contrato da porta (filtros de tenant/base aplicados antes do ranking,
ordenação determinística, `limit`) sem depender de um Postgres real.
Calcula similaridade de cosseno em Python puro — este projeto não usa
numpy em produção (ver `packages/ingestion/chunking.py`), e este fake
só precisa ser correto e determinístico, não performático.

Deliberadamente NÃO modela "versão ativa" (mesmo motivo do fake
lexical, ver sua docstring) — quem usa este fake em teste só deve
indexar (`index_chunk`) chunks que já representem a versão ativa, e
que já tenham `embedding` preenchido."""

from __future__ import annotations

import math
from uuid import UUID

from packages.application.ports.lexical_search import ScoredChunk
from packages.application.ports.vector_search import VectorSearchPort
from packages.domain.entities.chunk import Chunk


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class InMemoryVectorSearch(VectorSearchPort):
    def __init__(self) -> None:
        self._chunks: list[Chunk] = []

    def index_chunk(self, chunk: Chunk) -> None:
        """Só para testes: registra um chunk (com `embedding` já
        preenchido) como pesquisável. Não faz parte de
        `VectorSearchPort`."""
        self._chunks.append(chunk)

    async def search(
        self,
        *,
        tenant_id: UUID,
        knowledge_base_id: UUID,
        query_embedding: list[float],
        limit: int,
    ) -> list[ScoredChunk]:
        if not query_embedding:
            raise ValueError("query_embedding não pode ser vazio.")

        results: list[ScoredChunk] = []
        for chunk in self._chunks:
            if chunk.tenant_id != tenant_id or chunk.knowledge_base_id != knowledge_base_id:
                continue
            if chunk.embedding is None:
                continue
            score = _cosine_similarity(query_embedding, chunk.embedding)
            results.append(ScoredChunk(chunk=chunk, score=score))

        results.sort(key=lambda scored: (-scored.score, str(scored.chunk.id)))
        return results[:limit]
