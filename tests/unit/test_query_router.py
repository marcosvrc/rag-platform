"""Testes de RAG-044: endpoint `POST /v1/knowledge-bases/{id}/query`
(visão HTTP) — mesmo padrão de `test_retrieval_router.py`: app real
(`apps.api.main.app`), portas trocadas por fakes via
`dependency_overrides`, JWT real mintado por `_make_token`.

Cobre os critérios de aceite: resposta segue o contrato da seção 10.5
do plano (query_id, citations, tokens, trace_id); baixa evidência não
produz afirmação inventada (resposta segura, sem citações); isolamento
por tenant (404, nunca 403)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from adapters.audit_log.in_memory import InMemoryAuditLog
from adapters.document_repository.in_memory import InMemoryDocumentRepository
from adapters.knowledge_base_repository.in_memory import InMemoryKnowledgeBaseRepository
from adapters.lexical_search.in_memory import InMemoryLexicalSearch
from adapters.query_repository.in_memory import InMemoryQueryRepository
from adapters.reranker.passthrough import PassthroughReranker
from adapters.vector_search.in_memory import InMemoryVectorSearch
from apps.api import main
from apps.api.dependencies import get_audit_log, get_settings_dependency
from apps.api.errors import PROBLEM_JSON_MEDIA_TYPE
from apps.api.routers.documents import get_document_repository
from apps.api.routers.knowledge_bases import get_knowledge_base_repository
from apps.api.routers.query import get_generation_provider, get_query_repository
from apps.api.routers.retrieval import (
    get_embedding_provider,
    get_lexical_search,
    get_reranker,
    get_vector_search,
)
from packages.application.ports.embedding_provider import EmbeddingProviderPort
from packages.application.ports.generation_provider import (
    GenerationProviderPort,
    GenerationResult,
    GenerationTimeoutError,
)
from packages.config.settings import Settings
from packages.domain.entities.chunk import Chunk
from packages.domain.entities.document import Document

TENANT_A = str(uuid4())
TENANT_B = str(uuid4())

_JWT_SECRET = "test-jwt-secret-query-router-do-not-use-elsewhere"
_JWT_ISSUER = "rag-platform-tests"
_JWT_AUDIENCE = "rag-platform-tests-api"


class _FakeEmbeddingProvider(EmbeddingProviderPort):
    async def embed(self, *, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


class _FakeGenerationProvider(GenerationProviderPort):
    def __init__(
        self, *, content: str = "resposta qualquer", error: Exception | None = None
    ) -> None:
        self._content = content
        self._error = error

    async def generate(self, *, prompt: str) -> GenerationResult:
        if self._error is not None:
            raise self._error
        return GenerationResult(
            content=self._content,
            used_fallback=False,
            prompt_tokens=15,
            completion_tokens=4,
            total_tokens=19,
        )


def _test_settings(**overrides: object) -> Settings:
    fields: dict[str, object] = {
        "_env_file": None,
        "POSTGRES_PASSWORD": SecretStr("x"),
        "MINIO_ROOT_PASSWORD": SecretStr("x"),
        "JWT_SECRET": SecretStr(_JWT_SECRET),
        "JWT_ISSUER": _JWT_ISSUER,
        "JWT_AUDIENCE": _JWT_AUDIENCE,
    }
    fields.update(overrides)
    return Settings(**fields)  # type: ignore[arg-type]


def _make_token(*, tenant_id: str | None = TENANT_A, subject: str = "test-user") -> str:
    now = datetime.now(tz=UTC)
    payload: dict[str, object] = {
        "sub": subject,
        "iss": _JWT_ISSUER,
        "aud": _JWT_AUDIENCE,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    if tenant_id is not None:
        payload["tenant_id"] = tenant_id
    return jwt.encode(payload, key=_JWT_SECRET, algorithm="HS256")


def _headers(tenant_id: str = TENANT_A) -> dict[str, str]:
    return {"Authorization": f"Bearer {_make_token(tenant_id=tenant_id)}"}


@pytest.fixture
def knowledge_base_repository() -> InMemoryKnowledgeBaseRepository:
    return InMemoryKnowledgeBaseRepository()


@pytest.fixture
def document_repository() -> InMemoryDocumentRepository:
    return InMemoryDocumentRepository()


@pytest.fixture
def query_repository() -> InMemoryQueryRepository:
    return InMemoryQueryRepository()


@pytest.fixture
def vector_search() -> InMemoryVectorSearch:
    return InMemoryVectorSearch()


@pytest.fixture
def lexical_search() -> InMemoryLexicalSearch:
    return InMemoryLexicalSearch()


@pytest.fixture
def generation_provider() -> _FakeGenerationProvider:
    return _FakeGenerationProvider()


@pytest.fixture
def audit_log() -> InMemoryAuditLog:
    return InMemoryAuditLog()


@pytest.fixture
def client(
    knowledge_base_repository: InMemoryKnowledgeBaseRepository,
    document_repository: InMemoryDocumentRepository,
    query_repository: InMemoryQueryRepository,
    vector_search: InMemoryVectorSearch,
    lexical_search: InMemoryLexicalSearch,
    generation_provider: _FakeGenerationProvider,
    audit_log: InMemoryAuditLog,
) -> Iterator[TestClient]:
    main.app.dependency_overrides[get_knowledge_base_repository] = lambda: knowledge_base_repository
    main.app.dependency_overrides[get_document_repository] = lambda: document_repository
    main.app.dependency_overrides[get_query_repository] = lambda: query_repository
    main.app.dependency_overrides[get_vector_search] = lambda: vector_search
    main.app.dependency_overrides[get_lexical_search] = lambda: lexical_search
    main.app.dependency_overrides[get_embedding_provider] = lambda: _FakeEmbeddingProvider()
    main.app.dependency_overrides[get_reranker] = lambda: PassthroughReranker()
    main.app.dependency_overrides[get_generation_provider] = lambda: generation_provider
    main.app.dependency_overrides[get_settings_dependency] = lambda: _test_settings()
    main.app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        yield TestClient(main.app)
    finally:
        main.app.dependency_overrides.clear()


def _create_knowledge_base(client: TestClient, *, tenant_id: str = TENANT_A) -> str:
    response = client.post(
        "/v1/knowledge-bases", json={"name": "Manuais"}, headers=_headers(tenant_id)
    )
    return str(response.json()["id"])


async def _seed_indexed_chunk(
    *,
    tenant_id: str,
    knowledge_base_id: str,
    document_repository: InMemoryDocumentRepository,
    vector_search: InMemoryVectorSearch,
    lexical_search: InMemoryLexicalSearch,
    content: str = "banana prata",
    page: int | None = None,
    section: str | None = None,
) -> tuple[Document, Chunk]:
    upload = await document_repository.create_document(
        tenant_id=UUID(tenant_id),
        knowledge_base_id=UUID(knowledge_base_id),
        name="guia.pdf",
        mime_type="application/pdf",
        checksum=uuid4().hex,
        object_key="kb/doc/guia.pdf",
        idempotency_key=None,
    )
    chunk = Chunk(
        id=uuid4(),
        tenant_id=UUID(tenant_id),
        knowledge_base_id=UUID(knowledge_base_id),
        version_id=upload.version.id,
        content=content,
        token_count=10,
        page=page,
        section=section,
        metadata={"source": "test"},
        embedding=[1.0, 0.0],
    )
    await document_repository.persist_chunks_and_activate_version(
        document_id=upload.document.id,
        version_id=upload.version.id,
        extracted_object_key="kb/doc/extracted.txt",
        chunks=[chunk],
    )
    vector_search.index_chunk(chunk)
    lexical_search.index_chunk(chunk)
    return upload.document, chunk


def test_query_requires_authorization_header(client: TestClient) -> None:
    knowledge_base_id = _create_knowledge_base(client)

    response = client.post(
        f"/v1/knowledge-bases/{knowledge_base_id}/query", json={"query": "banana"}
    )

    assert response.status_code == 401
    assert response.headers["content-type"] == PROBLEM_JSON_MEDIA_TYPE


def test_query_returns_404_for_unknown_knowledge_base(client: TestClient) -> None:
    response = client.post(
        f"/v1/knowledge-bases/{uuid4()}/query", json={"query": "banana"}, headers=_headers()
    )

    assert response.status_code == 404


def test_query_returns_404_for_a_knowledge_base_of_another_tenant(client: TestClient) -> None:
    knowledge_base_id = _create_knowledge_base(client, tenant_id=TENANT_A)

    response = client.post(
        f"/v1/knowledge-bases/{knowledge_base_id}/query",
        json={"query": "banana"},
        headers=_headers(TENANT_B),
    )

    assert response.status_code == 404


def test_query_blocks_an_arbitrary_filter_field(client: TestClient) -> None:
    knowledge_base_id = _create_knowledge_base(client)

    response = client.post(
        f"/v1/knowledge-bases/{knowledge_base_id}/query",
        json={"query": "banana", "filters": {"nao_permitido": "x"}},
        headers=_headers(),
    )

    assert response.status_code == 422


def test_query_with_no_evidence_returns_a_safe_ungrounded_answer(client: TestClient) -> None:
    knowledge_base_id = _create_knowledge_base(client)

    response = client.post(
        f"/v1/knowledge-bases/{knowledge_base_id}/query",
        json={"query": "banana"},
        headers=_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["grounded"] is False
    assert body["citations"] == []
    assert body["usage"] == {"input_tokens": 0, "output_tokens": 0}
    assert UUID(body["query_id"])
    assert UUID(body["trace_id"])


def test_query_with_grounded_answer_returns_citations_and_usage(
    client: TestClient,
    document_repository: InMemoryDocumentRepository,
    vector_search: InMemoryVectorSearch,
    lexical_search: InMemoryLexicalSearch,
    generation_provider: _FakeGenerationProvider,
) -> None:
    knowledge_base_id = _create_knowledge_base(client)
    document, chunk = _run(
        _seed_indexed_chunk(
            tenant_id=TENANT_A,
            knowledge_base_id=knowledge_base_id,
            document_repository=document_repository,
            vector_search=vector_search,
            lexical_search=lexical_search,
            content="a arquitetura é hexagonal",
            page=7,
            section="Arquitetura",
        )
    )
    generation_provider._content = f"A arquitetura é hexagonal [{chunk.id}]."

    response = client.post(
        f"/v1/knowledge-bases/{knowledge_base_id}/query",
        json={"query": "arquitetura"},
        headers=_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["grounded"] is True
    assert body["answer"] == f"A arquitetura é hexagonal [{chunk.id}]."
    assert body["usage"] == {"input_tokens": 15, "output_tokens": 4}
    assert len(body["citations"]) == 1
    citation = body["citations"][0]
    assert citation["document_id"] == str(document.id)
    assert citation["document_name"] == document.name
    assert citation["chunk_id"] == str(chunk.id)
    assert citation["page"] == 7
    assert citation["section"] == "Arquitetura"
    assert citation["excerpt"] == "a arquitetura é hexagonal"


def test_query_returns_503_when_generation_gateway_is_unavailable(
    client: TestClient,
    document_repository: InMemoryDocumentRepository,
    vector_search: InMemoryVectorSearch,
    lexical_search: InMemoryLexicalSearch,
    generation_provider: _FakeGenerationProvider,
) -> None:
    knowledge_base_id = _create_knowledge_base(client)
    _run(
        _seed_indexed_chunk(
            tenant_id=TENANT_A,
            knowledge_base_id=knowledge_base_id,
            document_repository=document_repository,
            vector_search=vector_search,
            lexical_search=lexical_search,
        )
    )
    generation_provider._error = GenerationTimeoutError(detail="timeout")

    response = client.post(
        f"/v1/knowledge-bases/{knowledge_base_id}/query",
        json={"query": "banana"},
        headers=_headers(),
    )

    assert response.status_code == 503
    assert response.headers["content-type"] == PROBLEM_JSON_MEDIA_TYPE


def _run(coro):
    return asyncio.run(coro)
