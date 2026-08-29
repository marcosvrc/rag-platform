"""Testes de RAG-027: endpoint `GET /v1/jobs/{index_job_id}` (visão HTTP).

Mesmo padrão de `test_document_router.py` (RAG-021/RAG-051): app real
com repositórios trocados por fakes via `dependency_overrides`,
autenticação por JWT real via `_headers()`."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

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

_JWT_SECRET = "test-jwt-secret-jobs-router-do-not-use-elsewhere"
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
    client: TestClient, knowledge_base_id: str, *, tenant_id: str = TENANT_A
) -> dict[str, Any]:
    response = client.post(
        f"/v1/knowledge-bases/{knowledge_base_id}/documents",
        files={"file": ("guia.pdf", b"%PDF-1.4 conteudo de teste", "application/pdf")},
        headers=_headers(tenant_id),
    )
    body: dict[str, Any] = response.json()
    return body


def test_get_job_returns_pending_status_right_after_upload(client: TestClient) -> None:
    knowledge_base_id = _create_knowledge_base(client)
    upload = _upload_pdf(client, knowledge_base_id)

    response = client.get(f"/v1/jobs/{upload['index_job_id']}", headers=_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["index_job_id"] == upload["index_job_id"]
    assert body["document_id"] == upload["document_id"]
    assert body["type"] == "INDEX"
    assert body["status"] == "PENDING"
    assert body["attempts"] == 0
    assert body["error_code"] is None
    assert body["error_message"] is None


def test_get_job_requires_authorization_header(client: TestClient) -> None:
    knowledge_base_id = _create_knowledge_base(client)
    upload = _upload_pdf(client, knowledge_base_id)

    response = client.get(f"/v1/jobs/{upload['index_job_id']}")

    assert response.status_code == 401
    assert response.headers["content-type"] == PROBLEM_JSON_MEDIA_TYPE


def test_get_unknown_job_returns_404(client: TestClient) -> None:
    response = client.get(f"/v1/jobs/{uuid4()}", headers=_headers())

    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_JSON_MEDIA_TYPE


def test_get_another_tenants_job_returns_404(client: TestClient) -> None:
    knowledge_base_id = _create_knowledge_base(client, tenant_id=TENANT_A)
    upload = _upload_pdf(client, knowledge_base_id, tenant_id=TENANT_A)

    response = client.get(f"/v1/jobs/{upload['index_job_id']}", headers=_headers(TENANT_B))

    assert response.status_code == 404
