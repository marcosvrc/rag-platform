"""Testes de RAG-034: `retrieve_evidence` (caso de uso de recuperação).

Usa os fakes em memória já estabelecidos para cada porta (RAG-012/030/
031) e um fake local só para `EmbeddingProviderPort` (não existe um
adapter `in_memory` para essa porta ainda — mesma situação de
`RerankerPort`, cujos fakes também são locais a cada arquivo de teste
que precisa, ver `tests/unit/test_reranker.py`)."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID, uuid4

import pytest

from adapters.knowledge_base_repository.in_memory import InMemoryKnowledgeBaseRepository
from adapters.lexical_search.in_memory import InMemoryLexicalSearch
from adapters.reranker.passthrough import PassthroughReranker
from adapters.vector_search.in_memory import InMemoryVectorSearch
from packages.application.errors import NotFoundError
from packages.application.ports.embedding_provider import EmbeddingProviderPort
from packages.application.ports.lexical_search import ScoredChunk
from packages.application.ports.reranker import RerankerPort
from packages.application.queries.retrieval import (
    CANDIDATE_POOL_SIZE,
    RetrievalFilters,
    retrieve_evidence,
)
from packages.domain.entities.chunk import Chunk


class _FakeEmbeddingProvider(EmbeddingProviderPort):
    """Devolve sempre o mesmo embedding fixo — este caso de uso só
    precisa de UM embedding (o da query), então basta ser
    determinístico; a busca vetorial de verdade é testada em
    `test_vector_search_in_memory.py`, não aqui."""

    async def embed(self, *, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


class _ReversingReranker(RerankerPort):
    """Fake local (mesmo padrão de `test_reranker.py`): inverte a ordem
    dos candidatos e usa `-índice` como score, só para provar que o
    resultado final de `retrieve_evidence` reflete o que o reranker
    devolveu (posição/score), não a ordem pré-reranking."""

    async def rerank(
        self, *, query: str, candidates: Sequence[ScoredChunk], top_n: int
    ) -> list[ScoredChunk]:
        reversed_candidates = list(reversed(candidates))
        return [
            ScoredChunk(chunk=scored.chunk, score=float(-index))
            for index, scored in enumerate(reversed_candidates)
        ][:top_n]


def _chunk(
    *,
    tenant_id: UUID,
    knowledge_base_id: UUID,
    page: int | None = None,
    section: str | None = None,
    content: str = "conteúdo",
) -> Chunk:
    return Chunk(
        id=uuid4(),
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base_id,
        version_id=uuid4(),
        content=content,
        token_count=1,
        page=page,
        section=section,
        metadata={},
        embedding=[1.0, 0.0],
    )


class _Fixture:
    def __init__(self) -> None:
        self.knowledge_base_repository = InMemoryKnowledgeBaseRepository()
        self.vector_search = InMemoryVectorSearch()
        self.lexical_search = InMemoryLexicalSearch()
        self.embedding_provider = _FakeEmbeddingProvider()
        self.reranker: RerankerPort = PassthroughReranker()
        self.reranker_enabled = False


async def _make_fixture_with_knowledge_base():
    fixture = _Fixture()
    tenant_id = uuid4()
    knowledge_base = await fixture.knowledge_base_repository.create(
        tenant_id=tenant_id, name="Base", description=None, config={}
    )
    return fixture, tenant_id, knowledge_base.id


async def test_retrieve_evidence_raises_not_found_for_unknown_knowledge_base() -> None:
    fixture = _Fixture()

    with pytest.raises(NotFoundError):
        await retrieve_evidence(
            knowledge_base_repository=fixture.knowledge_base_repository,
            embedding_provider=fixture.embedding_provider,
            vector_search=fixture.vector_search,
            lexical_search=fixture.lexical_search,
            reranker=fixture.reranker,
            reranker_enabled=fixture.reranker_enabled,
            tenant_id=uuid4(),
            knowledge_base_id=uuid4(),
            query="qualquer coisa",
            top_k=10,
        )


async def test_retrieve_evidence_raises_not_found_for_a_knowledge_base_of_another_tenant() -> None:
    fixture, _tenant_id, knowledge_base_id = await _make_fixture_with_knowledge_base()

    with pytest.raises(NotFoundError):
        await retrieve_evidence(
            knowledge_base_repository=fixture.knowledge_base_repository,
            embedding_provider=fixture.embedding_provider,
            vector_search=fixture.vector_search,
            lexical_search=fixture.lexical_search,
            reranker=fixture.reranker,
            reranker_enabled=fixture.reranker_enabled,
            tenant_id=uuid4(),  # outro tenant
            knowledge_base_id=knowledge_base_id,
            query="qualquer coisa",
            top_k=10,
        )


async def test_retrieve_evidence_fuses_vector_and_lexical_results() -> None:
    fixture, tenant_id, knowledge_base_id = await _make_fixture_with_knowledge_base()
    only_vector = _chunk(
        tenant_id=tenant_id, knowledge_base_id=knowledge_base_id, content="só no vetorial"
    )
    only_lexical = _chunk(
        tenant_id=tenant_id, knowledge_base_id=knowledge_base_id, content="banana"
    )
    both = _chunk(tenant_id=tenant_id, knowledge_base_id=knowledge_base_id, content="banana")
    for chunk in (only_vector, both):
        fixture.vector_search.index_chunk(chunk)
    for chunk in (only_lexical, both):
        fixture.lexical_search.index_chunk(chunk)

    evidence = await retrieve_evidence(
        knowledge_base_repository=fixture.knowledge_base_repository,
        embedding_provider=fixture.embedding_provider,
        vector_search=fixture.vector_search,
        lexical_search=fixture.lexical_search,
        reranker=fixture.reranker,
        reranker_enabled=fixture.reranker_enabled,
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base_id,
        query="banana",
        top_k=10,
    )

    chunk_ids = {item.chunk.id for item in evidence}
    assert chunk_ids == {only_vector.id, only_lexical.id, both.id}
    # `both` aparece nos dois rankings — RRF deve pontuá-lo mais alto
    # que qualquer chunk que aparece em só um.
    assert evidence[0].chunk.id == both.id


async def test_retrieve_evidence_deduplicates_a_chunk_present_in_both_rankings() -> None:
    fixture, tenant_id, knowledge_base_id = await _make_fixture_with_knowledge_base()
    chunk = _chunk(tenant_id=tenant_id, knowledge_base_id=knowledge_base_id, content="banana")
    fixture.vector_search.index_chunk(chunk)
    fixture.lexical_search.index_chunk(chunk)

    evidence = await retrieve_evidence(
        knowledge_base_repository=fixture.knowledge_base_repository,
        embedding_provider=fixture.embedding_provider,
        vector_search=fixture.vector_search,
        lexical_search=fixture.lexical_search,
        reranker=fixture.reranker,
        reranker_enabled=fixture.reranker_enabled,
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base_id,
        query="banana",
        top_k=10,
    )

    assert len(evidence) == 1


async def test_retrieve_evidence_filters_by_page() -> None:
    fixture, tenant_id, knowledge_base_id = await _make_fixture_with_knowledge_base()
    page_one = _chunk(
        tenant_id=tenant_id, knowledge_base_id=knowledge_base_id, page=1, content="banana"
    )
    page_two = _chunk(
        tenant_id=tenant_id, knowledge_base_id=knowledge_base_id, page=2, content="banana"
    )
    for chunk in (page_one, page_two):
        fixture.vector_search.index_chunk(chunk)
        fixture.lexical_search.index_chunk(chunk)

    evidence = await retrieve_evidence(
        knowledge_base_repository=fixture.knowledge_base_repository,
        embedding_provider=fixture.embedding_provider,
        vector_search=fixture.vector_search,
        lexical_search=fixture.lexical_search,
        reranker=fixture.reranker,
        reranker_enabled=fixture.reranker_enabled,
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base_id,
        query="banana",
        top_k=10,
        filters=RetrievalFilters(page=1),
    )

    assert [item.chunk.id for item in evidence] == [page_one.id]


async def test_retrieve_evidence_filters_by_section() -> None:
    fixture, tenant_id, knowledge_base_id = await _make_fixture_with_knowledge_base()
    intro = _chunk(
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base_id,
        section="Introdução",
        content="banana",
    )
    conclusion = _chunk(
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base_id,
        section="Conclusão",
        content="banana",
    )
    for chunk in (intro, conclusion):
        fixture.vector_search.index_chunk(chunk)
        fixture.lexical_search.index_chunk(chunk)

    evidence = await retrieve_evidence(
        knowledge_base_repository=fixture.knowledge_base_repository,
        embedding_provider=fixture.embedding_provider,
        vector_search=fixture.vector_search,
        lexical_search=fixture.lexical_search,
        reranker=fixture.reranker,
        reranker_enabled=fixture.reranker_enabled,
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base_id,
        query="banana",
        top_k=10,
        filters=RetrievalFilters(section="Conclusão"),
    )

    assert [item.chunk.id for item in evidence] == [conclusion.id]


async def test_retrieve_evidence_respects_top_k() -> None:
    fixture, tenant_id, knowledge_base_id = await _make_fixture_with_knowledge_base()
    for _ in range(5):
        chunk = _chunk(tenant_id=tenant_id, knowledge_base_id=knowledge_base_id, content="banana")
        fixture.vector_search.index_chunk(chunk)
        fixture.lexical_search.index_chunk(chunk)

    evidence = await retrieve_evidence(
        knowledge_base_repository=fixture.knowledge_base_repository,
        embedding_provider=fixture.embedding_provider,
        vector_search=fixture.vector_search,
        lexical_search=fixture.lexical_search,
        reranker=fixture.reranker,
        reranker_enabled=fixture.reranker_enabled,
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base_id,
        query="banana",
        top_k=2,
    )

    assert len(evidence) == 2


async def test_retrieve_evidence_clamps_top_k_above_the_maximum() -> None:
    fixture, tenant_id, knowledge_base_id = await _make_fixture_with_knowledge_base()
    for _ in range(3):
        chunk = _chunk(tenant_id=tenant_id, knowledge_base_id=knowledge_base_id, content="banana")
        fixture.vector_search.index_chunk(chunk)
        fixture.lexical_search.index_chunk(chunk)

    evidence = await retrieve_evidence(
        knowledge_base_repository=fixture.knowledge_base_repository,
        embedding_provider=fixture.embedding_provider,
        vector_search=fixture.vector_search,
        lexical_search=fixture.lexical_search,
        reranker=fixture.reranker,
        reranker_enabled=fixture.reranker_enabled,
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base_id,
        query="banana",
        top_k=10_000,
    )

    assert len(evidence) == 3


async def test_retrieve_evidence_positions_are_zero_indexed_and_sequential() -> None:
    fixture, tenant_id, knowledge_base_id = await _make_fixture_with_knowledge_base()
    for _ in range(3):
        chunk = _chunk(tenant_id=tenant_id, knowledge_base_id=knowledge_base_id, content="banana")
        fixture.vector_search.index_chunk(chunk)
        fixture.lexical_search.index_chunk(chunk)

    evidence = await retrieve_evidence(
        knowledge_base_repository=fixture.knowledge_base_repository,
        embedding_provider=fixture.embedding_provider,
        vector_search=fixture.vector_search,
        lexical_search=fixture.lexical_search,
        reranker=fixture.reranker,
        reranker_enabled=fixture.reranker_enabled,
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base_id,
        query="banana",
        top_k=10,
    )

    assert [item.position for item in evidence] == [0, 1, 2]


async def test_retrieve_evidence_rerank_score_is_none_when_reranker_is_disabled() -> None:
    fixture, tenant_id, knowledge_base_id = await _make_fixture_with_knowledge_base()
    chunk = _chunk(tenant_id=tenant_id, knowledge_base_id=knowledge_base_id, content="banana")
    fixture.vector_search.index_chunk(chunk)
    fixture.lexical_search.index_chunk(chunk)
    fixture.reranker = PassthroughReranker()
    fixture.reranker_enabled = False

    evidence = await retrieve_evidence(
        knowledge_base_repository=fixture.knowledge_base_repository,
        embedding_provider=fixture.embedding_provider,
        vector_search=fixture.vector_search,
        lexical_search=fixture.lexical_search,
        reranker=fixture.reranker,
        reranker_enabled=fixture.reranker_enabled,
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base_id,
        query="banana",
        top_k=10,
    )

    assert evidence[0].rerank_score is None
    assert evidence[0].retrieval_score > 0.0


async def test_retrieve_evidence_uses_the_rerankers_order_and_score_when_enabled() -> None:
    fixture, tenant_id, knowledge_base_id = await _make_fixture_with_knowledge_base()
    first = _chunk(tenant_id=tenant_id, knowledge_base_id=knowledge_base_id, content="banana")
    second = _chunk(tenant_id=tenant_id, knowledge_base_id=knowledge_base_id, content="banana maçã")
    fixture.vector_search.index_chunk(first)
    fixture.vector_search.index_chunk(second)
    fixture.lexical_search.index_chunk(first)
    fixture.lexical_search.index_chunk(second)
    fixture.reranker = _ReversingReranker()
    fixture.reranker_enabled = True

    evidence = await retrieve_evidence(
        knowledge_base_repository=fixture.knowledge_base_repository,
        embedding_provider=fixture.embedding_provider,
        vector_search=fixture.vector_search,
        lexical_search=fixture.lexical_search,
        reranker=fixture.reranker,
        reranker_enabled=fixture.reranker_enabled,
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base_id,
        query="banana",
        top_k=10,
    )

    # O `_ReversingReranker` inverte a ordem de fusão e usa `-índice`
    # como score — o resultado final precisa refletir isso, não a
    # ordem pré-reranking.
    assert [item.rerank_score for item in evidence] == [0.0, -1.0]
    assert all(item.retrieval_score > 0.0 for item in evidence)


async def test_candidate_pool_size_is_generous_relative_to_max_top_k() -> None:
    """Documenta a invariante do módulo (ver seu comentário): o pool de
    candidatos precisa ser maior que o teto de `top_k` para que um
    filtro não esvazie o resultado à toa."""
    from packages.application.queries.retrieval import MAX_TOP_K

    assert CANDIDATE_POOL_SIZE > MAX_TOP_K
