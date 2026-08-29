"""Porta de busca vetorial de chunks (RAG-030, seção 11 do plano, passo
"recuperar via similaridade vetorial (pgvector)").

Domínio e casos de uso não importam pgvector diretamente (seção 5.1 do
plano) — só esta interface; a implementação real
(`adapters/vector_search/postgres.py`) usa o índice HNSW criado na
migration 0006 sobre `chunks.embedding` (`vector(1024)`, dimensão
fixada nessa mesma migration — ver `adapters/postgres/models/chunk.py`
para a decisão de modelo: Qwen3-Embedding-0.6B self-hospedado via
Ollama, atrás do gateway LiteLLM, RAG-025).

Reusa `ScoredChunk` de `lexical_search.py` (já pensado para os dois
tipos de busca desde RAG-031 — ver a docstring de `ScoredChunk`): o
score aqui é similaridade de cosseno (`1 - distância`), não distância
bruta, para manter a mesma convenção "maior é melhor" da busca
lexical. RAG-032 (fusão RRF) é quem combina os rankings de RAG-030 e
RAG-031 — RRF usa apenas a posição no ranking, não a magnitude do
score, então as duas escalas nunca precisam ser comparáveis entre si,
só internamente consistentes (maior é melhor) em cada porta."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from packages.application.ports.lexical_search import ScoredChunk


class VectorSearchPort(ABC):
    """Recupera chunks da versão ATIVA de cada documento (nunca de uma
    versão superada — ver `Document.active_version_id`, RAG-026) por
    similaridade de embedding, sempre filtrando por tenant e base de
    conhecimento antes de rankear (mesmo critério de aceite de
    RAG-031: "aplica tenant/base/ACL na query")."""

    @abstractmethod
    async def search(
        self,
        *,
        tenant_id: UUID,
        knowledge_base_id: UUID,
        query_embedding: list[float],
        limit: int,
    ) -> list[ScoredChunk]:
        """Busca os `limit` chunks mais similares a `query_embedding`
        na versão ativa de cada documento de `knowledge_base_id`,
        restrito a `tenant_id`.

        Devolve no máximo `limit` resultados, ordenados por
        similaridade decrescente; em caso de empate, a ordem é
        determinística (mesmo critério de RAG-031) — nunca depende da
        ordem física das linhas no banco.

        `query_embedding` vazio levanta `ValueError`: diferente da
        busca lexical (onde uma query "sem termos relevantes" é um
        resultado válido e devolve lista vazia), aqui não existe um
        embedding vazio legítimo — todo embedding vem de um texto
        não-vazio gerado por `EmbeddingProviderPort` (RAG-025); um
        `query_embedding` vazio só pode ser um erro de uso de quem
        chama esta porta."""
