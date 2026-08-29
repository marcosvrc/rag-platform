"""Testes de RAG-031: `InMemoryLexicalSearch` — prova o contrato de
`LexicalSearchPort` (filtros de tenant/base aplicados antes do
ranking, ordenação determinística, `limit`) sem depender de um
Postgres real."""

from uuid import UUID, uuid4

import pytest

from adapters.lexical_search.in_memory import InMemoryLexicalSearch
from packages.domain.entities.chunk import Chunk

TENANT_A = uuid4()
TENANT_B = uuid4()
KNOWLEDGE_BASE_A = uuid4()
KNOWLEDGE_BASE_B = uuid4()


def _chunk(
    *, tenant_id: UUID = TENANT_A, knowledge_base_id: UUID = KNOWLEDGE_BASE_A, content: str
) -> Chunk:
    return Chunk(
        id=uuid4(),
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base_id,
        version_id=uuid4(),
        content=content,
        token_count=max(1, len(content.split())),
        page=None,
        section=None,
        metadata={},
        embedding=None,
    )


@pytest.fixture
def search() -> InMemoryLexicalSearch:
    return InMemoryLexicalSearch()


async def test_search_finds_chunks_matching_the_query(search: InMemoryLexicalSearch) -> None:
    match = _chunk(content="o gato subiu no telhado")
    no_match = _chunk(content="o cachorro correu no parque")
    search.index_chunk(match)
    search.index_chunk(no_match)

    results = await search.search(
        tenant_id=TENANT_A, knowledge_base_id=KNOWLEDGE_BASE_A, query="gato telhado", limit=10
    )

    assert [scored.chunk.id for scored in results] == [match.id]


async def test_search_ranks_more_matching_terms_higher(search: InMemoryLexicalSearch) -> None:
    strong_match = _chunk(content="gato gato gato telhado")
    weak_match = _chunk(content="gato apareceu uma vez")
    search.index_chunk(weak_match)
    search.index_chunk(strong_match)

    results = await search.search(
        tenant_id=TENANT_A, knowledge_base_id=KNOWLEDGE_BASE_A, query="gato", limit=10
    )

    assert [scored.chunk.id for scored in results] == [strong_match.id, weak_match.id]
    assert results[0].score > results[1].score


async def test_search_breaks_score_ties_deterministically(search: InMemoryLexicalSearch) -> None:
    first = _chunk(content="gato telhado")
    second = _chunk(content="gato telhado")
    search.index_chunk(first)
    search.index_chunk(second)
    expected_order = sorted([first.id, second.id], key=str)

    results = await search.search(
        tenant_id=TENANT_A, knowledge_base_id=KNOWLEDGE_BASE_A, query="gato", limit=10
    )

    assert [scored.chunk.id for scored in results] == expected_order


async def test_search_filters_by_tenant_before_ranking(search: InMemoryLexicalSearch) -> None:
    own = _chunk(tenant_id=TENANT_A, content="gato no telhado")
    other_tenant = _chunk(tenant_id=TENANT_B, content="gato no telhado")
    search.index_chunk(own)
    search.index_chunk(other_tenant)

    results = await search.search(
        tenant_id=TENANT_A, knowledge_base_id=KNOWLEDGE_BASE_A, query="gato", limit=10
    )

    assert [scored.chunk.id for scored in results] == [own.id]


async def test_search_filters_by_knowledge_base_before_ranking(
    search: InMemoryLexicalSearch,
) -> None:
    own = _chunk(knowledge_base_id=KNOWLEDGE_BASE_A, content="gato no telhado")
    other_kb = _chunk(knowledge_base_id=KNOWLEDGE_BASE_B, content="gato no telhado")
    search.index_chunk(own)
    search.index_chunk(other_kb)

    results = await search.search(
        tenant_id=TENANT_A, knowledge_base_id=KNOWLEDGE_BASE_A, query="gato", limit=10
    )

    assert [scored.chunk.id for scored in results] == [own.id]


async def test_search_respects_limit(search: InMemoryLexicalSearch) -> None:
    for _ in range(5):
        search.index_chunk(_chunk(content="gato telhado"))

    results = await search.search(
        tenant_id=TENANT_A, knowledge_base_id=KNOWLEDGE_BASE_A, query="gato", limit=2
    )

    assert len(results) == 2


async def test_search_with_no_relevant_terms_returns_empty(search: InMemoryLexicalSearch) -> None:
    search.index_chunk(_chunk(content="gato no telhado"))

    results = await search.search(
        tenant_id=TENANT_A, knowledge_base_id=KNOWLEDGE_BASE_A, query="   ", limit=10
    )

    assert results == []
