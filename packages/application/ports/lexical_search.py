"""Porta de busca lexical de chunks (RAG-031, seção 11 do plano, passo
"recuperar via BM25/FTS").

Domínio e casos de uso não importam PostgreSQL FTS/`tsvector`
diretamente (seção 5.1 do plano) — só esta interface; a implementação
real (`adapters/lexical_search/postgres.py`) usa o índice GIN sobre
`chunks.content_tsv` criado na migration 0004.

Independente de `EmbeddingProviderPort`/busca vetorial (RAG-030, ainda
bloqueada pela dimensão de embedding não escolhida — ver
`adapters/postgres/models/chunk.py`): esta porta não depende de
nenhuma decisão pendente, por isso RAG-031 pôde ser implementada antes
de RAG-030 nesta sequência de atividades. RAG-032 (fusão RRF) é quem
vai combinar os resultados dos dois quando ambos existirem.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from uuid import UUID

from packages.domain.entities.chunk import Chunk


@dataclass(frozen=True, slots=True)
class ScoredChunk:
    """Um chunk recuperado, com o score de relevância que a busca
    (lexical aqui; vetorial em RAG-030) atribuiu a ele. `score` não é
    comparável entre buscas diferentes (lexical usa `ts_rank`, RAG-030
    usará distância/similaridade de embedding) — normalizar/combinar os
    dois é responsabilidade de RAG-032 (fusão RRF), não desta porta."""

    chunk: Chunk
    score: float


class LexicalSearchPort(ABC):
    """Recupera chunks da versão ATIVA de cada documento (nunca de uma
    versão superada — ver `Document.active_version_id`, RAG-026) por
    correspondência textual, sempre filtrando por tenant e base de
    conhecimento antes de rankear (critério de aceite "filtros são
    aplicados antes do resultado")."""

    @abstractmethod
    async def search(
        self, *, tenant_id: UUID, knowledge_base_id: UUID, query: str, limit: int
    ) -> list[ScoredChunk]:
        """Busca `query` nos chunks da versão ativa de cada documento
        de `knowledge_base_id`, restrito a `tenant_id`.

        Devolve no máximo `limit` resultados, ordenados por relevância
        decrescente; em caso de empate, a ordem é determinística
        (critério de aceite "ranking é determinístico") — nunca depende
        da ordem física das linhas no banco. Uma `query` sem nenhum
        termo relevante (string vazia, só stopwords) devolve lista
        vazia, nunca todos os chunks da base."""
