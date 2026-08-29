"""Testes de RAG-021: endpoint `POST /v1/knowledge-bases/{id}/documents`
(visão HTTP).

Usa o app real (`apps.api.main.app`), com os repositórios/object storage
trocados por fakes em memória via `dependency_overrides` — mesmo padrão
de `test_knowledge_base_router.py` (RAG-012). A base de conhecimento é
criada através do próprio endpoint de RAG-012 (`POST /v1/knowledge-bases`),
já que os dois routers compartilham a mesma instância de
`InMemoryKnowledgeBaseRepository` nestes testes.

Autenticação (RAG-051): `_headers()` minta um JWT real (mesma chave/
issuer/audience de `_test_settings()`) e o envia como `Authorization:
Bearer <token>` — não existe mais um cabeçalho `X-Tenant-Id` não
verificado.
"""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

import apps.api.routers.documents as documents_router
from adapters.audit_log.in_memory import InMemoryAuditLog
from adapters.document_repository.in_memory import InMemoryDocumentRepository
from adapters.knowledge_base_repository.in_memory import InMemoryKnowledgeBaseRepository
from adapters.object_storage.in_memory import InMemoryObjectStorage
from adapters.queue.in_memory import InMemoryJobQueue
from apps.api import main
from apps.api.dependencies import get_audit_log, get_settings_dependency
from apps.api.errors import PROBLEM_JSON_MEDIA_TYPE
from apps.api.routers.documents import get_document_repository, get_job_queue, get_object_storage
from apps.api.routers.knowledge_bases import get_knowledge_base_repository
from packages.config.settings import Settings

TENANT_A = str(uuid4())
TENANT_B = str(uuid4())

_JWT_SECRET = "test-jwt-secret-document-router-do-not-use-elsewhere"
_JWT_ISSUER = "rag-platform-tests"
_JWT_AUDIENCE = "rag-platform-tests-api"


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


def _test_settings(**overrides: object) -> Settings:
    fields: dict[str, object] = {
        "_env_file": None,
        "POSTGRES_PASSWORD": SecretStr("x"),
        "MINIO_ROOT_PASSWORD": SecretStr("x"),
        "JWT_SECRET": SecretStr(_JWT_SECRET),
        "JWT_ISSUER": _JWT_ISSUER,
        "JWT_AUDIENCE": _JWT_AUDIENCE,
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
def job_queue() -> InMemoryJobQueue:
    return InMemoryJobQueue()


@pytest.fixture
def audit_log() -> InMemoryAuditLog:
    return InMemoryAuditLog()


@pytest.fixture
def client(
    knowledge_base_repository: InMemoryKnowledgeBaseRepository,
    document_repository: InMemoryDocumentRepository,
    object_storage: InMemoryObjectStorage,
    job_queue: InMemoryJobQueue,
    audit_log: InMemoryAuditLog,
) -> Iterator[TestClient]:
    main.app.dependency_overrides[get_knowledge_base_repository] = lambda: knowledge_base_repository
    main.app.dependency_overrides[get_document_repository] = lambda: document_repository
    main.app.dependency_overrides[get_object_storage] = lambda: object_storage
    main.app.dependency_overrides[get_job_queue] = lambda: job_queue
    main.app.dependency_overrides[get_audit_log] = lambda: audit_log
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


def test_upload_enqueues_the_index_job(client: TestClient, job_queue: InMemoryJobQueue) -> None:
    knowledge_base_id = _create_knowledge_base(client)

    response = _upload_pdf(client, knowledge_base_id)

    index_job_id = UUID(response.json()["index_job_id"])
    assert job_queue.enqueued_index_job_ids == [index_job_id]


def test_upload_requires_authorization_header(client: TestClient) -> None:
    knowledge_base_id = _create_knowledge_base(client)

    response = client.post(
        f"/v1/knowledge-bases/{knowledge_base_id}/documents",
        files={"file": ("guia.pdf", b"conteudo", "application/pdf")},
    )

    assert response.status_code == 401
    assert response.headers["content-type"] == PROBLEM_JSON_MEDIA_TYPE


def test_upload_rejects_token_without_tenant_id_claim(client: TestClient) -> None:
    knowledge_base_id = _create_knowledge_base(client)

    response = client.post(
        f"/v1/knowledge-bases/{knowledge_base_id}/documents",
        files={"file": ("guia.pdf", b"conteudo", "application/pdf")},
        headers={"Authorization": f"Bearer {_make_token(tenant_id=None)}"},
    )

    assert response.status_code == 401


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


async def _index_document(
    document_repository: InMemoryDocumentRepository, *, document_id: UUID, version_id: UUID
) -> None:
    """Simula uma indexação inicial bem-sucedida (RAG-026) diretamente
    no fake, sem subir um worker real — só para deixar o documento em
    INDEXED antes de exercitar o endpoint de reindexação (RAG-027)."""
    await document_repository.mark_document_processing(document_id=document_id)
    await document_repository.persist_chunks_and_activate_version(
        document_id=document_id,
        version_id=version_id,
        extracted_object_key=f"{document_id}/v1/extracted.md",
        chunks=[],
    )


def test_reindex_returns_202_with_new_version_and_job(
    client: TestClient, document_repository: InMemoryDocumentRepository
) -> None:
    knowledge_base_id = _create_knowledge_base(client)
    upload_response = _upload_pdf(client, knowledge_base_id)
    document_id = upload_response.json()["document_id"]
    version = asyncio.run(document_repository.get_latest_version(document_id=UUID(document_id)))
    assert version is not None
    asyncio.run(
        _index_document(document_repository, document_id=UUID(document_id), version_id=version.id)
    )

    response = client.post(
        f"/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/reindex",
        headers=_headers(),
    )

    assert response.status_code == 202
    body = response.json()
    assert body["document_id"] == document_id
    assert body["knowledge_base_id"] == knowledge_base_id
    assert body["version"] == 2
    assert body["index_job_type"] == "REINDEX"
    assert body["index_job_status"] == "PENDING"


def test_reindex_enqueues_the_new_job(
    client: TestClient,
    document_repository: InMemoryDocumentRepository,
    job_queue: InMemoryJobQueue,
) -> None:
    knowledge_base_id = _create_knowledge_base(client)
    upload_response = _upload_pdf(client, knowledge_base_id)
    document_id = upload_response.json()["document_id"]
    version = asyncio.run(document_repository.get_latest_version(document_id=UUID(document_id)))
    assert version is not None
    asyncio.run(
        _index_document(document_repository, document_id=UUID(document_id), version_id=version.id)
    )
    job_queue.enqueued_index_job_ids.clear()

    response = client.post(
        f"/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/reindex",
        headers=_headers(),
    )

    new_index_job_id = UUID(response.json()["index_job_id"])
    assert job_queue.enqueued_index_job_ids == [new_index_job_id]


def test_reindex_a_document_not_yet_indexed_returns_409(client: TestClient) -> None:
    knowledge_base_id = _create_knowledge_base(client)
    upload_response = _upload_pdf(client, knowledge_base_id)
    document_id = upload_response.json()["document_id"]

    response = client.post(
        f"/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/reindex",
        headers=_headers(),
    )

    assert response.status_code == 409
    assert response.headers["content-type"] == PROBLEM_JSON_MEDIA_TYPE


def test_reindex_unknown_document_returns_404(client: TestClient) -> None:
    knowledge_base_id = _create_knowledge_base(client)

    response = client.post(
        f"/v1/knowledge-bases/{knowledge_base_id}/documents/{uuid4()}/reindex",
        headers=_headers(),
    )

    assert response.status_code == 404


def test_reindex_another_tenants_document_returns_404(
    client: TestClient, document_repository: InMemoryDocumentRepository
) -> None:
    knowledge_base_id = _create_knowledge_base(client, tenant_id=TENANT_A)
    upload_response = _upload_pdf(client, knowledge_base_id, tenant_id=TENANT_A)
    document_id = upload_response.json()["document_id"]
    version = asyncio.run(document_repository.get_latest_version(document_id=UUID(document_id)))
    assert version is not None
    asyncio.run(
        _index_document(document_repository, document_id=UUID(document_id), version_id=version.id)
    )

    response = client.post(
        f"/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/reindex",
        headers=_headers(TENANT_B),
    )

    assert response.status_code == 404


class TestAuditLog:
    """RAG-054: enviar/reindexar documento registra um evento de
    auditoria com ator, tenant, ação e recurso corretos."""

    def test_upload_records_an_audit_event(
        self, client: TestClient, audit_log: InMemoryAuditLog
    ) -> None:
        knowledge_base_id = _create_knowledge_base(client)
        audit_log.events.clear()

        response = _upload_pdf(client, knowledge_base_id)

        assert len(audit_log.events) == 1
        event = audit_log.events[0]
        assert event.action == "document.upload"
        assert event.resource_type == "document"
        assert str(event.resource_id) == response.json()["document_id"]
        assert event.actor == "test-user"
        assert str(event.tenant_id) == TENANT_A

    def test_reindex_records_an_audit_event(
        self,
        client: TestClient,
        document_repository: InMemoryDocumentRepository,
        audit_log: InMemoryAuditLog,
    ) -> None:
        knowledge_base_id = _create_knowledge_base(client)
        upload_response = _upload_pdf(client, knowledge_base_id)
        document_id = upload_response.json()["document_id"]
        version = asyncio.run(document_repository.get_latest_version(document_id=UUID(document_id)))
        assert version is not None
        asyncio.run(
            _index_document(
                document_repository, document_id=UUID(document_id), version_id=version.id
            )
        )
        audit_log.events.clear()

        client.post(
            f"/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/reindex",
            headers=_headers(),
        )

        assert len(audit_log.events) == 1
        event = audit_log.events[0]
        assert event.action == "document.reindex"
        assert event.resource_type == "document"
        assert str(event.resource_id) == document_id


class TestMetrics:
    """RAG-053: enviar/reindexar documento registra uma métrica de
    consumo (dublada — a lógica de `record_document_uploaded`/
    `record_document_reindexed` em si é testada em
    tests/unit/test_metrics.py)."""

    def test_upload_records_a_document_uploaded_metric(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        knowledge_base_id = _create_knowledge_base(client)
        fake_record = MagicMock()
        monkeypatch.setattr(documents_router, "record_document_uploaded", fake_record)

        _upload_pdf(client, knowledge_base_id)

        fake_record.assert_called_once_with(mime_type="application/pdf")

    def test_reindex_records_a_document_reindexed_metric(
        self,
        client: TestClient,
        document_repository: InMemoryDocumentRepository,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        knowledge_base_id = _create_knowledge_base(client)
        upload_response = _upload_pdf(client, knowledge_base_id)
        document_id = upload_response.json()["document_id"]
        version = asyncio.run(document_repository.get_latest_version(document_id=UUID(document_id)))
        assert version is not None
        asyncio.run(
            _index_document(
                document_repository, document_id=UUID(document_id), version_id=version.id
            )
        )
        fake_record = MagicMock()
        monkeypatch.setattr(documents_router, "record_document_reindexed", fake_record)

        client.post(
            f"/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/reindex",
            headers=_headers(),
        )

        fake_record.assert_called_once_with()
