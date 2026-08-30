"""Testes de RAG-034: endpoint `POST /v1/knowledge-bases/{id}/retrieve`
(visão HTTP).

Mesmo padrão de `test_knowledge_base_router.py`/`test_document_router.py`:
app real (`apps.api.main.app`), portas trocadas por fakes via
`dependency_overrides`, JWT real mintado por `_make_token` para
exercitar `get_current_tenant_id`/`get_current_identity` fim a fim. A
base de conhecimento usada em cada teste é criada pelo endpoint real
`POST /v1/knowledge-bases` (mesmo padrão de
`test_document_router.py::_create_knowledge_base`), nunca inserida
direto no fake.

Cobre os critérios de aceite: retorna evidências/metadados/scores;
suporta filtros permitidos (`page`/`section`); bloqueia filtro
arbitrário (422, via `extra="forbid"` do contrato); isolamento por
tenant (404, nunca 403, mesmo critério do resto da API)."""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from adapters.audit_log.in_memory import InMemoryAuditLog
from adapters.knowledge_base_repository.in_memory import InMemoryKnowledgeBaseRepository
from adapters.lexical_search.in_memory import InMemoryLexicalSearch
from adapters.reranker.litellm import LiteLLMReranker
from adapters.reranker.passthrough import PassthroughReranker
from adapters.vector_search.in_memory import InMemoryVectorSearch
from apps.api import main
from apps.api.dependencies import get_audit_log, get_settings_dependency
from apps.api.errors import PROBLEM_JSON_MEDIA_TYPE
from apps.api.routers.knowledge_bases import get_knowledge_base_repository
from apps.api.routers.retrieval import (
    get_embedding_provider,
    get_lexical_search,
    get_reranker,
    get_vector_search,
)
from packages.application.ports.embedding_provider import EmbeddingProviderPort
from packages.config.settings import Settings
from packages.domain.entities.chunk import Chunk

TENANT_A = str(uuid4())
TENANT_B = str(uuid4())

_JWT_SECRET = "test-jwt-secret-retrieval-router-do-not-use-elsewhere"
_JWT_ISSUER = "rag-platform-tests"
_JWT_AUDIENCE = "rag-platform-tests-api"


class _FakeEmbeddingProvider(EmbeddingProviderPort):
    async def embed(self, *, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


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
def vector_search() -> InMemoryVectorSearch:
    return InMemoryVectorSearch()


@pytest.fixture
def lexical_search() -> InMemoryLexicalSearch:
    return InMemoryLexicalSearch()


@pytest.fixture
def audit_log() -> InMemoryAuditLog:
    return InMemoryAuditLog()


@pytest.fixture
def client(
    knowledge_base_repository: InMemoryKnowledgeBaseRepository,
    vector_search: InMemoryVectorSearch,
    lexical_search: InMemoryLexicalSearch,
    audit_log: InMemoryAuditLog,
) -> Iterator[TestClient]:
    main.app.dependency_overrides[get_knowledge_base_repository] = lambda: knowledge_base_repository
    main.app.dependency_overrides[get_vector_search] = lambda: vector_search
    main.app.dependency_overrides[get_lexical_search] = lambda: lexical_search
    main.app.dependency_overrides[get_embedding_provider] = lambda: _FakeEmbeddingProvider()
    main.app.dependency_overrides[get_reranker] = lambda: PassthroughReranker()
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


def _seed_chunk(
    *,
    tenant_id: str,
    knowledge_base_id: str,
    vector_search: InMemoryVectorSearch,
    lexical_search: InMemoryLexicalSearch,
    content: str = "banana",
    page: int | None = None,
    section: str | None = None,
) -> Chunk:
    chunk = Chunk(
        id=uuid4(),
        tenant_id=UUID(tenant_id),
        knowledge_base_id=UUID(knowledge_base_id),
        version_id=uuid4(),
        content=content,
        token_count=1,
        page=page,
        section=section,
        metadata={"source": "test"},
        embedding=[1.0, 0.0],
    )
    vector_search.index_chunk(chunk)
    lexical_search.index_chunk(chunk)
    return chunk


def test_retrieve_returns_200_with_evidence_metadata_and_scores(
    client: TestClient,
    vector_search: InMemoryVectorSearch,
    lexical_search: InMemoryLexicalSearch,
) -> None:
    knowledge_base_id = _create_knowledge_base(client)
    chunk = _seed_chunk(
        tenant_id=TENANT_A,
        knowledge_base_id=knowledge_base_id,
        vector_search=vector_search,
        lexical_search=lexical_search,
        content="banana prata",
        page=3,
        section="Introdução",
    )

    response = client.post(
        f"/v1/knowledge-bases/{knowledge_base_id}/retrieve",
        json={"query": "banana"},
        headers=_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["knowledge_base_id"] == knowledge_base_id
    assert body["query"] == "banana"
    assert len(body["evidence"]) == 1
    evidence = body["evidence"][0]
    assert evidence["chunk_id"] == str(chunk.id)
    assert evidence["knowledge_base_id"] == knowledge_base_id
    assert evidence["content"] == "banana prata"
    assert evidence["page"] == 3
    assert evidence["section"] == "Introdução"
    assert evidence["metadata"] == {"source": "test"}
    assert evidence["retrieval_score"] > 0.0
    assert evidence["rerank_score"] is None
    assert evidence["position"] == 0


def test_retrieve_requires_authorization_header(client: TestClient) -> None:
    knowledge_base_id = _create_knowledge_base(client)

    response = client.post(
        f"/v1/knowledge-bases/{knowledge_base_id}/retrieve", json={"query": "banana"}
    )

    assert response.status_code == 401
    assert response.headers["content-type"] == PROBLEM_JSON_MEDIA_TYPE


def test_retrieve_returns_404_for_unknown_knowledge_base(client: TestClient) -> None:
    response = client.post(
        f"/v1/knowledge-bases/{uuid4()}/retrieve", json={"query": "banana"}, headers=_headers()
    )

    assert response.status_code == 404


def test_retrieve_returns_404_for_a_knowledge_base_of_another_tenant(
    client: TestClient,
) -> None:
    knowledge_base_id = _create_knowledge_base(client, tenant_id=TENANT_A)

    response = client.post(
        f"/v1/knowledge-bases/{knowledge_base_id}/retrieve",
        json={"query": "banana"},
        headers=_headers(TENANT_B),
    )

    assert response.status_code == 404


def test_retrieve_rejects_empty_query(client: TestClient) -> None:
    knowledge_base_id = _create_knowledge_base(client)

    response = client.post(
        f"/v1/knowledge-bases/{knowledge_base_id}/retrieve",
        json={"query": ""},
        headers=_headers(),
    )

    assert response.status_code == 422


def test_retrieve_rejects_top_k_above_the_maximum(client: TestClient) -> None:
    knowledge_base_id = _create_knowledge_base(client)

    response = client.post(
        f"/v1/knowledge-bases/{knowledge_base_id}/retrieve",
        json={"query": "banana", "top_k": 51},
        headers=_headers(),
    )

    assert response.status_code == 422


def test_retrieve_accepts_the_allowed_page_filter(
    client: TestClient,
    vector_search: InMemoryVectorSearch,
    lexical_search: InMemoryLexicalSearch,
) -> None:
    knowledge_base_id = _create_knowledge_base(client)
    page_one = _seed_chunk(
        tenant_id=TENANT_A,
        knowledge_base_id=knowledge_base_id,
        vector_search=vector_search,
        lexical_search=lexical_search,
        content="banana",
        page=1,
    )
    _seed_chunk(
        tenant_id=TENANT_A,
        knowledge_base_id=knowledge_base_id,
        vector_search=vector_search,
        lexical_search=lexical_search,
        content="banana",
        page=2,
    )

    response = client.post(
        f"/v1/knowledge-bases/{knowledge_base_id}/retrieve",
        json={"query": "banana", "filters": {"page": 1}},
        headers=_headers(),
    )

    assert response.status_code == 200
    evidence = response.json()["evidence"]
    assert [item["chunk_id"] for item in evidence] == [str(page_one.id)]


def test_retrieve_rejects_an_arbitrary_filter_key(client: TestClient) -> None:
    knowledge_base_id = _create_knowledge_base(client)

    response = client.post(
        f"/v1/knowledge-bases/{knowledge_base_id}/retrieve",
        json={"query": "banana", "filters": {"author": "alguém"}},
        headers=_headers(),
    )

    assert response.status_code == 422


def test_get_reranker_returns_passthrough_when_disabled() -> None:
    reranker = asyncio.run(get_reranker(_test_settings(RERANKER_ENABLED=False)))

    assert isinstance(reranker, PassthroughReranker)


def test_get_reranker_returns_litellm_reranker_when_enabled() -> None:
    reranker = asyncio.run(get_reranker(_test_settings(RERANKER_ENABLED=True)))

    assert isinstance(reranker, LiteLLMReranker)
