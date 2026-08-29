"""Adapter Postgres de `LexicalSearchPort` (RAG-031).

Usa o índice GIN sobre `chunks.content_tsv` (migration 0004,
`adapters/postgres/models/chunk.py`) via o operador `@@` de full text
search do Postgres — `ChunkModel.content_tsv.op("@@")(...)` compila
para exatamente esse operador, que o planner do Postgres casa com um
índice GIN automaticamente (não há como "forçar" o uso do índice a
partir do SQLAlchemy; é uma propriedade da consulta, verificável via
`EXPLAIN` contra um Postgres real — fora do alcance deste sandbox sem
banco, mesma limitação já documentada nos demais adapters Postgres
deste projeto).

Só chunks da versão ATIVA de cada documento entram no resultado: o
join com `DocumentModel` em `DocumentModel.active_version_id ==
ChunkModel.version_id` é o que garante isso (RAG-026,
`Document.active_version_id`) — uma versão superada por uma
reindexação (RAG-027) nunca aparece aqui, mesmo que seus chunks ainda
existam fisicamente na tabela (`persist_chunks_and_activate_version`
não apaga chunks de versões antigas, só os da versão que está sendo
ativada — um efeito colateral de armazenamento conhecido, não uma
falha de isolamento: este `WHERE` é o que importa para a corretude da
busca).

Filtros de tenant/base (`chunks.tenant_id`/`chunks.knowledge_base_id`,
já denormalizados na tabela, RAG-011) entram no `WHERE` antes de
qualquer ordenação por score — nunca um pós-filtro em Python (critério
de aceite "filtros são aplicados antes do resultado").

Ranking por `ts_rank`, com o `id` do chunk como desempate estável
(critério de aceite "ranking é determinístico" — `ts_rank` sozinho não
garante uma ordem total entre scores empatados)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from adapters.postgres.models.chunk import ChunkModel
from adapters.postgres.models.document import DocumentModel
from packages.application.ports.lexical_search import LexicalSearchPort, ScoredChunk
from packages.domain.entities.chunk import Chunk

_TS_CONFIG = "simple"  # mesma configuração da coluna gerada (migration 0004).


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


class PostgresLexicalSearch(LexicalSearchPort):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def search(
        self, *, tenant_id: UUID, knowledge_base_id: UUID, query: str, limit: int
    ) -> list[ScoredChunk]:
        ts_query = func.plainto_tsquery(_TS_CONFIG, query)
        score = func.ts_rank(ChunkModel.content_tsv, ts_query).label("score")

        stmt = (
            select(ChunkModel, score)
            .join(DocumentModel, DocumentModel.active_version_id == ChunkModel.version_id)
            .where(
                ChunkModel.tenant_id == tenant_id,
                ChunkModel.knowledge_base_id == knowledge_base_id,
                ChunkModel.content_tsv.op("@@")(ts_query),
            )
            .order_by(score.desc(), ChunkModel.id.asc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [
            ScoredChunk(chunk=_to_entity(model), score=float(chunk_score))
            for model, chunk_score in result.all()
        ]
