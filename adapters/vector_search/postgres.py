"""Adapter Postgres de `VectorSearchPort` (RAG-030).

Usa o índice HNSW sobre `chunks.embedding` (migration 0006,
`vector_cosine_ops`) via o operador `<=>` de distância de cosseno do
pgvector — `ChunkModel.embedding.cosine_distance(...)` (método que
`pgvector-sqlalchemy` adiciona à coluna) compila para exatamente esse
operador, que o planner do Postgres casa com um índice HNSW
automaticamente (mesma limitação de verificação via `EXPLAIN` já
documentada em `adapters/lexical_search/postgres.py`: fora do alcance
deste sandbox sem um Postgres real).

Score devolvido é similaridade de cosseno (`1 - distância`), não a
distância bruta — ver docstring de `VectorSearchPort` — por isso a
consulta ordena por distância ASCENDENTE (menor distância = mais
similar) mas o `ScoredChunk.score` já vem convertido para a convenção
"maior é melhor" das duas portas de busca.

Só chunks da versão ATIVA de cada documento entram no resultado (mesmo
join até `DocumentModel.active_version_id` usado em
`adapters/lexical_search/postgres.py` — ver essa docstring para o
racional completo). Filtros de tenant/base entram no `WHERE` antes de
qualquer ordenação (mesma exigência de RAG-031), junto com
`embedding IS NOT NULL` — um chunk ainda sem embedding (indexação em
andamento, ou falha parcial) nunca aparece num resultado de busca
vetorial."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from adapters.postgres.models.chunk import ChunkModel
from adapters.postgres.models.document import DocumentModel
from packages.application.ports.lexical_search import ScoredChunk
from packages.application.ports.vector_search import VectorSearchPort
from packages.domain.entities.chunk import Chunk


def _to_entity(model: ChunkModel) -> Chunk:
    return Chunk(
        id=model.id,
        tenant_id=model.tenant_id,
        knowledge_base_id=model.knowledge_base_id,
        version_id=model.version_id,
        content=model.content,
        token_count=model.token_count,
        page=model.page,
        section=model.section,
        metadata=model.metadata_,
        embedding=model.embedding,
    )


class PostgresVectorSearch(VectorSearchPort):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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

        distance = ChunkModel.embedding.cosine_distance(query_embedding).label("distance")

        stmt = (
            select(ChunkModel, distance)
            .join(DocumentModel, DocumentModel.active_version_id == ChunkModel.version_id)
            .where(
                ChunkModel.tenant_id == tenant_id,
                ChunkModel.knowledge_base_id == knowledge_base_id,
                ChunkModel.embedding.isnot(None),
            )
            .order_by(distance.asc(), ChunkModel.id.asc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [
            ScoredChunk(chunk=_to_entity(model), score=1.0 - float(chunk_distance))
            for model, chunk_distance in result.all()
        ]
