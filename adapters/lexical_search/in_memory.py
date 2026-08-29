"""Fake em memória de `LexicalSearchPort`, para testes (RAG-031).

Prova o contrato da porta em isolamento — filtros de tenant/base
aplicados antes do ranking, ordenação determinística, `limit` — sem
depender de um Postgres real. Deliberadamente NÃO modela versões nem
"versão ativa" (`Chunk` não carrega `document_id`, só `version_id`):
isso é responsabilidade de `adapters/lexical_search/postgres.py`, que
faz o join até `documents.active_version_id` de verdade. Quem usa este
fake em teste só deve indexar (`index_chunk`) os chunks que já
representem uma versão ativa — o mesmo cuidado que qualquer teste com
`InMemoryDocumentRepository` já precisa ter ao montar seu cenário.

O ranking aqui é uma aproximação simples (contagem de termos, sem
stemming nem IDF) — não tenta reproduzir `ts_rank` do Postgres, só
precisa ser determinístico e respeitar a ordem "mais termos correspondentes
primeiro" para provar o contrato da porta."""

from __future__ import annotations

import re
from uuid import UUID

from packages.application.ports.lexical_search import LexicalSearchPort, ScoredChunk
from packages.domain.entities.chunk import Chunk

_TOKEN_PATTERN = re.compile(r"\w+", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    return [match.lower() for match in _TOKEN_PATTERN.findall(text)]


class InMemoryLexicalSearch(LexicalSearchPort):
    def __init__(self) -> None:
        self._chunks: list[Chunk] = []

    def index_chunk(self, chunk: Chunk) -> None:
        """Só para testes: registra um chunk como pesquisável. Não faz
        parte de LexicalSearchPort."""
        self._chunks.append(chunk)

    async def search(
        self, *, tenant_id: UUID, knowledge_base_id: UUID, query: str, limit: int
    ) -> list[ScoredChunk]:
        query_terms = _tokenize(query)
        if not query_terms:
            return []

        results: list[ScoredChunk] = []
        for chunk in self._chunks:
            if chunk.tenant_id != tenant_id or chunk.knowledge_base_id != knowledge_base_id:
                continue
            content_terms = _tokenize(chunk.content)
            score = float(sum(content_terms.count(term) for term in query_terms))
            if score > 0:
                results.append(ScoredChunk(chunk=chunk, score=score))

        results.sort(key=lambda scored: (-scored.score, str(scored.chunk.id)))
        return results[:limit]
