"""Testes de RAG-021: endpoint `POST /v1/knowledge-bases/{id}/documents`
(visão HTTP).

Usa o app real (`apps.api.main.app`), com os repositórios/object storage
trocados por fakes em memória via `dependency_overrides` — mesmo padrão
de `test_knowledge_base_router.py` (RAG-012). A base de conhecimento é
criada através do próprio endpoint de RAG-012 (`POST /v1/knowledge-bases`),
já que os dois routers compartilham a mesma instância de
`InMemoryKnowledgeBaseRepository` nestes testes.
"""

from collections.abc import Iterator
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from adapters.document_repository.in_memory import InMemoryDocumentRepository
from adapters.knowledge_base_repository.in_memory import InMemoryKnowledgeBaseRepository
from adapters.object_storage.in_memory import InMemoryObjectStorage
from apps.api import main
from apps.api.dependencies import get_settings_dependency
from apps.api.errors import PROBLEM_JSON_MEDIA_TYPE
from apps.api.routers.documents import get_document_repository, get_object_storage
from apps.api.routers.knowledge_bases import get_knowledge_base_repository
from packages.config.settings import Settings

TENANT_A = str(uuid4())
TENANT_B = str(uuid4())


def _headers(tenant_id: str = TENANT_A) -> dict[str, str]:
    return {"X-Tenant-Id": tenant_id}


def _test_settings(**overrides: object) -> Settings:
    fields: dict[str, object] = {
        "_env_file": None,
        "POSTGRES_PASSWORD": SecretStr("x"),
        "MINIO_ROOT_PASSWORD": SecretStr("x"),
        "JWT_ISSUER": "rag-platform-tests",
        "JWT_AUDIENCE": "rag-platform-tests-api",
        "DOCUMENT_MAX_SIZE_BYTES": 1024,
    }
    fields.update(overrides)
    return Settings(**fields)  # type: ignore[arg-type]


@pytest.fixture
def knowledge_base_repository() -> InMemoryKnowledgeBaseRepository:
    return InMemoryKnowledgeBaseRepository()


@pytest.fixture
def document_repository() -> InMemoryDocumentRepository:
    return InMemoryDocumentRepository()


@pytest.fixture
def object_storage() -> InMemoryObjectStorage:
    return InMemoryObjectStorage()


@pytest.fixture
def client(
    knowledge_base_repository: InMemoryKnowledgeBaseRepository,
    document_repository: InMemoryDocumentRepository,
    object_storage: InMemoryObjectStorage,
) -> Iterator[TestClient]:
    main.app.dependency_overrides[get_knowledge_base_repository] = lambda: knowledge_base_repository
    main.app.dependency_overrides[get_document_repository] = lambda: document_repository
    main.app.dependency_overrides[get_object_storage] = lambda: object_storage
    main.app.dependency_overrides[get_settings_dependency] = lambda: _test_settings()
    try:
        yield TestClient(main.app)
    finally:
        main.app.dependency_overrides.clear()


def _create_knowledge_base(client: TestClient, *, tenant_id: str = TENANT_A) -> str:
    response = client.post(
        "/v1/knowledge-bases", json={"name": "Manuais"}, headers=_headers(tenant_id)
    )
    return str(response.json()["id"])


def _upload_pdf(
    client: TestClient,
    knowledge_base_id: str,
    *,
    tenant_id: str = TENANT_A,
    filename: str = "guia.pdf",
    content: bytes = b"%PDF-1.4 conteudo de teste",
    content_type: str = "application/pdf",
    idempotency_key: str | None = None,
) -> httpx.Response:
    headers = _headers(tenant_id)
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    response: httpx.Response = client.post(
        f"/v1/knowledge-bases/{knowledge_base_id}/documents",
        files={"file": (filename, content, content_type)},
        headers=headers,
    )
    return response


def test_upload_returns_202_with_pending_document_and_job(client: TestClient) -> None:
    knowledge_base_id = _create_knowledge_base(client)

    response = _upload_pdf(client, knowledge_base_id)

    assert response.status_code == 202
    body = response.json()
    assert body["knowledge_base_id"] == knowledge_base_id
    assert body["name"] == "guia.pdf"
    assert body["document_status"] == "PENDING"
    assert body["version"] == 1
    assert body["index_job_status"] == "PENDING"
    assert body["index_job_type"] == "INDEX"


def test_upload_requires_tenant_header(client: TestClient) -> None:
    knowledge_base_id = _create_knowledge_base(client)

    response = client.post(
        f"/v1/knowledge-bases/{knowledge_base_id}/documents",
        files={"file": ("guia.pdf", b"conteudo", "application/pdf")},
    )

    assert response.status_code == 401
    assert response.headers["content-type"] == PROBLEM_JSON_MEDIA_TYPE


def test_upload_to_unknown_knowledge_base_returns_404(client: TestClient) -> None:
    response = _upload_pdf(client, str(uuid4()))

    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_JSON_MEDIA_TYPE


def test_upload_to_another_tenants_knowledge_base_returns_404(client: TestClient) -> None:
    knowledge_base_id = _create_knowledge_base(client, tenant_id=TENANT_A)

    response = _upload_pdf(client, knowledge_base_id, tenant_id=TENANT_B)

    assert response.status_code == 404


def test_upload_rejects_unsupported_file_type_with_422(client: TestClient) -> None:
    knowledge_base_id = _create_knowledge_base(client)

    response = _upload_pdf(
        client,
        knowledge_base_id,
        filename="virus.exe",
        content=b"MZ",
        content_type="application/x-msdownload",
    )

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_JSON_MEDIA_TYPE


def test_upload_rejects_file_larger_than_configured_max_size(client: TestClient) -> None:
    knowledge_base_id = _create_knowledge_base(client)

    response = _upload_pdf(client, knowledge_base_id, content=b"x" * 2048)

    assert response.status_code == 422


def test_upload_detects_duplicate_checksum_with_409(client: TestClient) -> None:
    knowledge_base_id = _create_knowledge_base(client)
    _upload_pdf(client, knowledge_base_id, content=b"conteudo identico")

    response = _upload_pdf(
        client, knowledge_base_id, filename="outro.pdf", content=b"conteudo identico"
    )

    assert response.status_code == 409
    assert response.headers["content-type"] == PROBLEM_JSON_MEDIA_TYPE


def test_upload_with_same_idempotency_key_replays_the_same_document(client: TestClient) -> None:
    knowledge_base_id = _create_knowledge_base(client)

    first = _upload_pdf(client, knowledge_base_id, idempotency_key="req-1")
    second = _upload_pdf(client, knowledge_base_id, idempotency_key="req-1")

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["document_id"] == second.json()["document_id"]


def test_upload_with_same_idempotency_key_and_different_content_returns_409(
    client: TestClient,
) -> None:
    knowledge_base_id = _create_knowledge_base(client)

    _upload_pdf(client, knowledge_base_id, idempotency_key="req-2", content=b"conteudo original")
    response = _upload_pdf(
        client, knowledge_base_id, idempotency_key="req-2", content=b"conteudo diferente"
    )

    assert response.status_code == 409
