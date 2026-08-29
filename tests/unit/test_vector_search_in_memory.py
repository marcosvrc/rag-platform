"""Testes de RAG-030: `InMemoryVectorSearch` — prova o contrato de
`VectorSearchPort` (filtros de tenant/base aplicados antes do ranking,
ordenação determinística, `limit`) sem depender de um Postgres real."""

from uuid import UUID, uuid4

import pytest

from adapters.vector_search.in_memory import InMemoryVectorSearch
from packages.domain.entities.chunk import Chunk

TENANT_A = uuid4()
TENANT_B = uuid4()
KNOWLEDGE_BASE_A = uuid4()
KNOWLEDGE_BASE_B = uuid4()


def _chunk(
    *,
    tenant_id: UUID = TENANT_A,
    knowledge_base_id: UUID = KNOWLEDGE_BASE_A,
    embedding: list[float] | None,
) -> Chunk:
    return Chunk(
        id=uuid4(),
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base_id,
        version_id=uuid4(),
        content="conteúdo qualquer",
        token_count=1,
        page=None,
        section=None,
        metadata={},
        embedding=embedding,
    )


@pytest.fixture
def search() -> InMemoryVectorSearch:
    return InMemoryVectorSearch()


async def test_search_ranks_more_similar_embeddings_higher(search: InMemoryVectorSearch) -> None:
    close = _chunk(embedding=[1.0, 0.0])
    far = _chunk(embedding=[0.0, 1.0])
    search.index_chunk(far)
    search.index_chunk(close)

    results = await search.search(
        tenant_id=TENANT_A,
        knowledge_base_id=KNOWLEDGE_BASE_A,
        query_embedding=[1.0, 0.0],
        limit=10,
    )

    assert [scored.chunk.id for scored in results] == [close.id, far.id]
    assert results[0].score > results[1].score


async def test_search_breaks_score_ties_deterministically(search: InMemoryVectorSearch) -> None:
    first = _chunk(embedding=[1.0, 0.0])
    second = _chunk(embedding=[1.0, 0.0])
    search.index_chunk(first)
    search.index_chunk(second)
    expected_order = sorted([first.id, second.id], key=str)

    results = await search.search(
        tenant_id=TENANT_A,
        knowledge_base_id=KNOWLEDGE_BASE_A,
        query_embedding=[1.0, 0.0],
        limit=10,
    )

    assert [scored.chunk.id for scored in results] == expected_order


async def test_search_filters_by_tenant_before_ranking(search: InMemoryVectorSearch) -> None:
    own = _chunk(tenant_id=TENANT_A, embedding=[1.0, 0.0])
    other_tenant = _chunk(tenant_id=TENANT_B, embedding=[1.0, 0.0])
    search.index_chunk(own)
    search.index_chunk(other_tenant)

    results = await search.search(
        tenant_id=TENANT_A,
        knowledge_base_id=KNOWLEDGE_BASE_A,
        query_embedding=[1.0, 0.0],
        limit=10,
    )

    assert [scored.chunk.id for scored in results] == [own.id]


async def test_search_filters_by_knowledge_base_before_ranking(
    search: InMemoryVectorSearch,
) -> None:
    own = _chunk(knowledge_base_id=KNOWLEDGE_BASE_A, embedding=[1.0, 0.0])
    other_kb = _chunk(knowledge_base_id=KNOWLEDGE_BASE_B, embedding=[1.0, 0.0])
    search.index_chunk(own)
    search.index_chunk(other_kb)

    results = await search.search(
        tenant_id=TENANT_A,
        knowledge_base_id=KNOWLEDGE_BASE_A,
        query_embedding=[1.0, 0.0],
        limit=10,
    )

    assert [scored.chunk.id for scored in results] == [own.id]


async def test_search_respects_limit(search: InMemoryVectorSearch) -> None:
    for _ in range(5):
        search.index_chunk(_chunk(embedding=[1.0, 0.0]))

    results = await search.search(
        tenant_id=TENANT_A,
        knowledge_base_id=KNOWLEDGE_BASE_A,
        query_embedding=[1.0, 0.0],
        limit=2,
    )

    assert len(results) == 2


async def test_search_skips_chunks_without_embedding(search: InMemoryVectorSearch) -> None:
    without_embedding = _chunk(embedding=None)
    with_embedding = _chunk(embedding=[1.0, 0.0])
    search.index_chunk(without_embedding)
    search.index_chunk(with_embedding)

    results = await search.search(
        tenant_id=TENANT_A,
        knowledge_base_id=KNOWLEDGE_BASE_A,
        query_embedding=[1.0, 0.0],
        limit=10,
    )

    assert [scored.chunk.id for scored in results] == [with_embedding.id]


async def test_search_treats_a_zero_vector_embedding_as_zero_similarity(
    search: InMemoryVectorSearch,
) -> None:
    zero_vector = _chunk(embedding=[0.0, 0.0])
    search.index_chunk(zero_vector)

    results = await search.search(
        tenant_id=TENANT_A,
        knowledge_base_id=KNOWLEDGE_BASE_A,
        query_embedding=[1.0, 0.0],
        limit=10,
    )

    assert [scored.score for scored in results] == [0.0]


async def test_search_with_empty_query_embedding_raises(search: InMemoryVectorSearch) -> None:
    search.index_chunk(_chunk(embedding=[1.0, 0.0]))

    with pytest.raises(ValueError, match="query_embedding"):
        await search.search(
            tenant_id=TENANT_A,
            knowledge_base_id=KNOWLEDGE_BASE_A,
            query_embedding=[],
            limit=10,
        )
