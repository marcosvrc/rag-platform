"""Testes de RAG-044: `answer_query` (caso de uso do endpoint
`/v1/query`) — integra recuperação, contexto, geração e groundedness,
com fakes em memória para toda porta (mesmo padrão de
`test_retrieval_query.py`)."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from adapters.document_repository.in_memory import InMemoryDocumentRepository
from adapters.knowledge_base_repository.in_memory import InMemoryKnowledgeBaseRepository
from adapters.lexical_search.in_memory import InMemoryLexicalSearch
from adapters.query_repository.in_memory import InMemoryQueryRepository
from adapters.reranker.passthrough import PassthroughReranker
from adapters.vector_search.in_memory import InMemoryVectorSearch
from packages.application.commands import query as query_commands
from packages.application.commands.query import NO_GENERATION_MODEL_LABEL
from packages.application.errors import NotFoundError, ServiceUnavailableError
from packages.application.ports.embedding_provider import EmbeddingProviderPort
from packages.application.ports.generation_provider import (
    GenerationProviderPort,
    GenerationResult,
    GenerationTimeoutError,
)
from packages.application.ports.reranker import RerankerPort
from packages.domain.entities.chunk import Chunk
from packages.domain.entities.document import Document
from packages.generation.prompts import get_default_answer_prompt

_NO_EVIDENCE_RESPONSE = get_default_answer_prompt().no_evidence_response
_GENERATION_ALIAS = "generation-model-alias"
_FALLBACK_ALIAS = "generation-fallback-model-alias"


class _FakeEmbeddingProvider(EmbeddingProviderPort):
    async def embed(self, *, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


class _FakeGenerationProvider(GenerationProviderPort):
    """Fake configurável: devolve um conteúdo fixo, ou levanta um erro
    fixo, e grava todo prompt recebido (para inspecionar o que o caso
    de uso de fato montou)."""

    def __init__(
        self,
        *,
        content: str = "resposta qualquer",
        prompt_tokens: int = 20,
        completion_tokens: int = 8,
        used_fallback: bool = False,
        error: Exception | None = None,
    ) -> None:
        self._content = content
        self._prompt_tokens = prompt_tokens
        self._completion_tokens = completion_tokens
        self._used_fallback = used_fallback
        self._error = error
        self.received_prompts: list[str] = []

    async def generate(self, *, prompt: str) -> GenerationResult:
        self.received_prompts.append(prompt)
        if self._error is not None:
            raise self._error
        return GenerationResult(
            content=self._content,
            used_fallback=self._used_fallback,
            prompt_tokens=self._prompt_tokens,
            completion_tokens=self._completion_tokens,
            total_tokens=self._prompt_tokens + self._completion_tokens,
        )


class _Fixture:
    def __init__(self) -> None:
        self.knowledge_base_repository = InMemoryKnowledgeBaseRepository()
        self.document_repository = InMemoryDocumentRepository()
        self.query_repository = InMemoryQueryRepository()
        self.vector_search = InMemoryVectorSearch()
        self.lexical_search = InMemoryLexicalSearch()
        self.embedding_provider = _FakeEmbeddingProvider()
        self.reranker: RerankerPort = PassthroughReranker()
        self.reranker_enabled = False
        self.generation_provider: _FakeGenerationProvider = _FakeGenerationProvider()
        self.generation_model_alias = _GENERATION_ALIAS
        self.generation_fallback_alias: str | None = None
        self.retrieval_minimum_score = 0.0
        self.context_token_budget = 3000

    async def run(
        self,
        *,
        tenant_id: UUID,
        knowledge_base_id: UUID,
        query: str = "qual é a arquitetura do sistema?",
        top_k: int = 10,
    ) -> query_commands.QueryAnswer:
        return await query_commands.answer_query(
            knowledge_base_repository=self.knowledge_base_repository,
            document_repository=self.document_repository,
            query_repository=self.query_repository,
            embedding_provider=self.embedding_provider,
            vector_search=self.vector_search,
            lexical_search=self.lexical_search,
            reranker=self.reranker,
            reranker_enabled=self.reranker_enabled,
            generation_provider=self.generation_provider,
            generation_model_alias=self.generation_model_alias,
            generation_fallback_alias=self.generation_fallback_alias,
            prompt_template=get_default_answer_prompt(),
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base_id,
            query=query,
            top_k=top_k,
            filters=None,
            retrieval_minimum_score=self.retrieval_minimum_score,
            context_token_budget=self.context_token_budget,
            trace_id=uuid4(),
        )


async def _make_fixture_with_knowledge_base() -> tuple[_Fixture, UUID, UUID]:
    fixture = _Fixture()
    tenant_id = uuid4()
    knowledge_base = await fixture.knowledge_base_repository.create(
        tenant_id=tenant_id, name="Base", description=None, config={}
    )
    return fixture, tenant_id, knowledge_base.id


async def _seed_indexed_chunk(
    fixture: _Fixture,
    *,
    tenant_id: UUID,
    knowledge_base_id: UUID,
    content: str = "conteúdo qualquer",
    page: int | None = None,
    section: str | None = None,
) -> tuple[Document, Chunk]:
    """Cria um Document/DocumentVersion reais (via
    `document_repository`) e um Chunk apontando para essa versão,
    indexado em `vector_search`/`lexical_search` — o suficiente para
    `get_documents_by_chunk_ids` resolver `document_id`/`document_name`
    de verdade, mesma jornada de RAG-026 em produção."""
    upload = await fixture.document_repository.create_document(
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base_id,
        name="guia.pdf",
        mime_type="application/pdf",
        checksum=uuid4().hex,
        object_key="kb/doc/guia.pdf",
        idempotency_key=None,
    )
    chunk = Chunk(
        id=uuid4(),
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base_id,
        version_id=upload.version.id,
        content=content,
        token_count=10,
        page=page,
        section=section,
        metadata={},
        embedding=[1.0, 0.0],
    )
    await fixture.document_repository.persist_chunks_and_activate_version(
        document_id=upload.document.id,
        version_id=upload.version.id,
        extracted_object_key="kb/doc/extracted.txt",
        chunks=[chunk],
    )
    fixture.vector_search.index_chunk(chunk)
    fixture.lexical_search.index_chunk(chunk)
    return upload.document, chunk


async def test_answer_query_raises_not_found_for_unknown_knowledge_base() -> None:
    fixture = _Fixture()

    with pytest.raises(NotFoundError):
        await fixture.run(tenant_id=uuid4(), knowledge_base_id=uuid4())


async def test_answer_query_raises_not_found_for_a_knowledge_base_of_another_tenant() -> None:
    fixture, _tenant_id, knowledge_base_id = await _make_fixture_with_knowledge_base()

    with pytest.raises(NotFoundError):
        await fixture.run(tenant_id=uuid4(), knowledge_base_id=knowledge_base_id)


async def test_answer_query_with_no_evidence_never_calls_generation() -> None:
    fixture, tenant_id, knowledge_base_id = await _make_fixture_with_knowledge_base()

    answer = await fixture.run(tenant_id=tenant_id, knowledge_base_id=knowledge_base_id)

    assert answer.answer == _NO_EVIDENCE_RESPONSE
    assert answer.grounded is False
    assert answer.citations == ()
    assert answer.model == NO_GENERATION_MODEL_LABEL
    assert answer.token_usage.input_tokens == 0
    assert answer.token_usage.output_tokens == 0
    assert fixture.generation_provider.received_prompts == []


async def test_answer_query_with_no_evidence_still_persists_a_query_log() -> None:
    fixture, tenant_id, knowledge_base_id = await _make_fixture_with_knowledge_base()

    answer = await fixture.run(tenant_id=tenant_id, knowledge_base_id=knowledge_base_id)

    persisted = fixture.query_repository.query_logs[answer.query_id]
    assert persisted.tenant_id == tenant_id
    assert persisted.knowledge_base_id == knowledge_base_id
    assert persisted.model == NO_GENERATION_MODEL_LABEL
    assert fixture.query_repository.query_evidences == []


async def test_answer_query_below_minimum_score_never_calls_generation() -> None:
    fixture, tenant_id, knowledge_base_id = await _make_fixture_with_knowledge_base()
    await _seed_indexed_chunk(
        fixture, tenant_id=tenant_id, knowledge_base_id=knowledge_base_id, content="banana"
    )
    fixture.retrieval_minimum_score = 999.0

    answer = await fixture.run(
        tenant_id=tenant_id, knowledge_base_id=knowledge_base_id, query="banana"
    )

    assert answer.answer == _NO_EVIDENCE_RESPONSE
    assert answer.grounded is False
    assert fixture.generation_provider.received_prompts == []


async def test_answer_query_below_minimum_score_still_persists_all_retrieved_evidence() -> None:
    fixture, tenant_id, knowledge_base_id = await _make_fixture_with_knowledge_base()
    await _seed_indexed_chunk(
        fixture, tenant_id=tenant_id, knowledge_base_id=knowledge_base_id, content="banana"
    )
    fixture.retrieval_minimum_score = 999.0

    answer = await fixture.run(
        tenant_id=tenant_id, knowledge_base_id=knowledge_base_id, query="banana"
    )

    persisted_evidence = [
        e for e in fixture.query_repository.query_evidences if e.query_id == answer.query_id
    ]
    assert len(persisted_evidence) == 1


async def test_answer_query_with_context_budget_too_small_never_calls_generation() -> None:
    fixture, tenant_id, knowledge_base_id = await _make_fixture_with_knowledge_base()
    await _seed_indexed_chunk(
        fixture, tenant_id=tenant_id, knowledge_base_id=knowledge_base_id, content="banana"
    )
    fixture.context_token_budget = 1  # o chunk (token_count=10) nunca cabe.

    answer = await fixture.run(
        tenant_id=tenant_id, knowledge_base_id=knowledge_base_id, query="banana"
    )

    assert answer.answer == _NO_EVIDENCE_RESPONSE
    assert fixture.generation_provider.received_prompts == []


async def test_answer_query_grounded_answer_returns_citation_with_resolved_document() -> None:
    fixture, tenant_id, knowledge_base_id = await _make_fixture_with_knowledge_base()
    document, chunk = await _seed_indexed_chunk(
        fixture,
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base_id,
        content="a arquitetura é hexagonal",
        page=7,
        section="Arquitetura",
    )
    fixture.generation_provider = _FakeGenerationProvider(
        content=f"A arquitetura é hexagonal [{chunk.id}].", prompt_tokens=30, completion_tokens=6
    )

    answer = await fixture.run(
        tenant_id=tenant_id, knowledge_base_id=knowledge_base_id, query="arquitetura"
    )

    assert answer.grounded is True
    assert answer.answer == f"A arquitetura é hexagonal [{chunk.id}]."
    assert answer.model == _GENERATION_ALIAS
    assert answer.token_usage.input_tokens == 30
    assert answer.token_usage.output_tokens == 6
    assert len(answer.citations) == 1
    citation = answer.citations[0]
    assert citation.document_id == document.id
    assert citation.document_name == document.name
    assert citation.chunk_id == chunk.id
    assert citation.page == 7
    assert citation.section == "Arquitetura"
    assert citation.excerpt == "a arquitetura é hexagonal"
    assert citation.score > 0.0


async def test_answer_query_persists_query_log_and_all_retrieved_evidence() -> None:
    fixture, tenant_id, knowledge_base_id = await _make_fixture_with_knowledge_base()
    _document, chunk = await _seed_indexed_chunk(
        fixture, tenant_id=tenant_id, knowledge_base_id=knowledge_base_id, content="banana"
    )
    fixture.generation_provider = _FakeGenerationProvider(content=f"Resposta [{chunk.id}].")

    answer = await fixture.run(
        tenant_id=tenant_id, knowledge_base_id=knowledge_base_id, query="banana"
    )

    persisted = fixture.query_repository.query_logs[answer.query_id]
    assert persisted.model == _GENERATION_ALIAS
    persisted_evidence = [
        e for e in fixture.query_repository.query_evidences if e.query_id == answer.query_id
    ]
    assert len(persisted_evidence) == 1
    assert persisted_evidence[0].chunk_id == chunk.id


async def test_answer_query_invalid_citation_uses_safe_fallback_but_still_records_real_model() -> (
    None
):
    fixture, tenant_id, knowledge_base_id = await _make_fixture_with_knowledge_base()
    await _seed_indexed_chunk(
        fixture, tenant_id=tenant_id, knowledge_base_id=knowledge_base_id, content="banana"
    )
    hallucinated_id = uuid4()
    fixture.generation_provider = _FakeGenerationProvider(content=f"Resposta [{hallucinated_id}].")

    answer = await fixture.run(
        tenant_id=tenant_id, knowledge_base_id=knowledge_base_id, query="banana"
    )

    assert answer.grounded is False
    assert answer.answer == _NO_EVIDENCE_RESPONSE
    assert answer.citations == ()
    # A chamada de geração de fato aconteceu (ao contrário do caminho
    # "sem evidência") — o modelo real usado ainda é registrado.
    assert answer.model == _GENERATION_ALIAS


async def test_answer_query_propagates_service_unavailable_on_generation_failure() -> None:
    fixture, tenant_id, knowledge_base_id = await _make_fixture_with_knowledge_base()
    await _seed_indexed_chunk(
        fixture, tenant_id=tenant_id, knowledge_base_id=knowledge_base_id, content="banana"
    )
    fixture.generation_provider = _FakeGenerationProvider(
        error=GenerationTimeoutError(detail="timeout")
    )

    with pytest.raises(ServiceUnavailableError):
        await fixture.run(tenant_id=tenant_id, knowledge_base_id=knowledge_base_id, query="banana")


async def test_answer_query_records_the_fallback_alias_when_generation_used_fallback() -> None:
    fixture, tenant_id, knowledge_base_id = await _make_fixture_with_knowledge_base()
    await _seed_indexed_chunk(
        fixture, tenant_id=tenant_id, knowledge_base_id=knowledge_base_id, content="banana"
    )
    fixture.generation_fallback_alias = _FALLBACK_ALIAS
    fixture.generation_provider = _FakeGenerationProvider(
        content="Sem citação nenhuma.", used_fallback=True
    )

    answer = await fixture.run(
        tenant_id=tenant_id, knowledge_base_id=knowledge_base_id, query="banana"
    )

    assert answer.model == _FALLBACK_ALIAS


async def test_answer_query_truncates_a_long_excerpt() -> None:
    fixture, tenant_id, knowledge_base_id = await _make_fixture_with_knowledge_base()
    long_content = "palavra " * 100  # bem além de EXCERPT_MAX_CHARS
    _document, chunk = await _seed_indexed_chunk(
        fixture, tenant_id=tenant_id, knowledge_base_id=knowledge_base_id, content=long_content
    )
    fixture.generation_provider = _FakeGenerationProvider(content=f"Resposta [{chunk.id}].")

    answer = await fixture.run(
        tenant_id=tenant_id, knowledge_base_id=knowledge_base_id, query="palavra"
    )

    assert answer.grounded is True
    excerpt = answer.citations[0].excerpt
    assert len(excerpt) <= 301  # EXCERPT_MAX_CHARS + "…"
    assert excerpt.endswith("…")


async def test_answer_query_sends_the_rendered_prompt_with_context_and_question() -> None:
    fixture, tenant_id, knowledge_base_id = await _make_fixture_with_knowledge_base()
    _document, chunk = await _seed_indexed_chunk(
        fixture, tenant_id=tenant_id, knowledge_base_id=knowledge_base_id, content="banana prata"
    )
    fixture.generation_provider = _FakeGenerationProvider(content=f"Resposta [{chunk.id}].")

    await fixture.run(
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base_id,
        query="qual é a fruta?",
    )

    [prompt] = fixture.generation_provider.received_prompts
    assert f"[{chunk.id}] banana prata" in prompt
    assert "qual é a fruta?" in prompt
