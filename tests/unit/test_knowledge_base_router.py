"""Testes de RAG-012: endpoints `/v1/knowledge-bases` (visão HTTP).

Usa o app real (`apps.api.main.app`), com o repositório trocado por
`InMemoryKnowledgeBaseRepository` via `dependency_overrides` — mesmo
padrão de `test_health.py` (RAG-005). `_headers()` minta um JWT real
(RAG-050) e o envia como `Authorization: Bearer <token>` em cada
requisição (não é sobrescrito via Depends), para também exercitar
`get_current_tenant_id`/`get_current_identity` (RAG-012/RAG-051) fim a
fim — o cabeçalho `X-Tenant-Id` não verificado do RAG-012 não existe
mais.

Cobre os critérios de aceite da atividade: endpoints seguem o
contrato HTTP da seção 10.1 do plano; paginação por cursor funciona;
tenant A não acessa (nem lista, nem vê, nem altera, nem exclui) uma
base do tenant B.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from adapters.audit_log.in_memory import InMemoryAuditLog
from adapters.knowledge_base_repository.in_memory import InMemoryKnowledgeBaseRepository
from apps.api import main
from apps.api.dependencies import get_audit_log, get_settings_dependency
from apps.api.errors import PROBLEM_JSON_MEDIA_TYPE
from apps.api.routers.knowledge_bases import get_knowledge_base_repository
from packages.config.settings import Settings

TENANT_A = str(uuid4())
TENANT_B = str(uuid4())

_JWT_SECRET = "test-jwt-secret-knowledge-base-router-do-not-use-elsewhere"
_JWT_ISSUER = "rag-platform-tests"
_JWT_AUDIENCE = "rag-platform-tests-api"


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


@pytest.fixture
def repository() -> InMemoryKnowledgeBaseRepository:
    return InMemoryKnowledgeBaseRepository()


@pytest.fixture
def audit_log() -> InMemoryAuditLog:
    return InMemoryAuditLog()


@pytest.fixture
def client(
    repository: InMemoryKnowledgeBaseRepository, audit_log: InMemoryAuditLog
) -> Iterator[TestClient]:
    main.app.dependency_overrides[get_knowledge_base_repository] = lambda: repository
    main.app.dependency_overrides[get_audit_log] = lambda: audit_log
    main.app.dependency_overrides[get_settings_dependency] = lambda: _test_settings()
    try:
        yield TestClient(main.app)
    finally:
        main.app.dependency_overrides.clear()


def _headers(tenant_id: str = TENANT_A) -> dict[str, str]:
    return {"Authorization": f"Bearer {_make_token(tenant_id=tenant_id)}"}


def test_create_returns_201_with_the_created_knowledge_base(client: TestClient) -> None:
    response = client.post(
        "/v1/knowledge-bases",
        json={"name": "Manuais", "description": "Manuais internos", "config": {"chunk_size": 512}},
        headers=_headers(),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Manuais"
    assert body["status"] == "ACTIVE"
    assert body["tenant_id"] == TENANT_A


def test_create_requires_authorization_header(client: TestClient) -> None:
    response = client.post("/v1/knowledge-bases", json={"name": "Manuais"})

    assert response.status_code == 401
    assert response.headers["content-type"] == PROBLEM_JSON_MEDIA_TYPE


def test_create_rejects_non_bearer_authorization_header(client: TestClient) -> None:
    response = client.post(
        "/v1/knowledge-bases",
        json={"name": "Manuais"},
        headers={"Authorization": "Basic dXNlcjpwYXNz"},
    )

    assert response.status_code == 401


def test_create_rejects_invalid_token(client: TestClient) -> None:
    response = client.post(
        "/v1/knowledge-bases",
        json={"name": "Manuais"},
        headers={"Authorization": "Bearer not-a-valid-jwt"},
    )

    assert response.status_code == 401


def test_create_rejects_token_without_tenant_id_claim(client: TestClient) -> None:
    response = client.post(
        "/v1/knowledge-bases",
        json={"name": "Manuais"},
        headers={"Authorization": f"Bearer {_make_token(tenant_id=None)}"},
    )

    assert response.status_code == 401


def test_create_rejects_missing_name_with_problem_details_422(client: TestClient) -> None:
    response = client.post("/v1/knowledge-bases", json={}, headers=_headers())

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_JSON_MEDIA_TYPE
    assert "request_id" in response.json()


def test_create_returns_409_on_duplicate_name_for_same_tenant(client: TestClient) -> None:
    client.post("/v1/knowledge-bases", json={"name": "Manuais"}, headers=_headers())
    response = client.post("/v1/knowledge-bases", json={"name": "Manuais"}, headers=_headers())

    assert response.status_code == 409


def test_get_returns_the_knowledge_base(client: TestClient) -> None:
    created = client.post(
        "/v1/knowledge-bases", json={"name": "Manuais"}, headers=_headers()
    ).json()

    response = client.get(f"/v1/knowledge-bases/{created['id']}", headers=_headers())

    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


def test_get_returns_404_for_unknown_id(client: TestClient) -> None:
    response = client.get(f"/v1/knowledge-bases/{uuid4()}", headers=_headers())

    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_JSON_MEDIA_TYPE


def test_patch_updates_only_given_fields(client: TestClient) -> None:
    created = client.post(
        "/v1/knowledge-bases",
        json={"name": "Manuais", "description": "antiga"},
        headers=_headers(),
    ).json()

    response = client.patch(
        f"/v1/knowledge-bases/{created['id']}",
        json={"description": "nova"},
        headers=_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Manuais"
    assert body["description"] == "nova"


def test_patch_rejects_null_name_with_422(client: TestClient) -> None:
    created = client.post(
        "/v1/knowledge-bases", json={"name": "Manuais"}, headers=_headers()
    ).json()

    response = client.patch(
        f"/v1/knowledge-bases/{created['id']}", json={"name": None}, headers=_headers()
    )

    assert response.status_code == 422


def test_patch_returns_404_for_unknown_id(client: TestClient) -> None:
    response = client.patch(
        f"/v1/knowledge-bases/{uuid4()}", json={"name": "Novo"}, headers=_headers()
    )

    assert response.status_code == 404


def test_delete_then_get_returns_404(client: TestClient) -> None:
    created = client.post(
        "/v1/knowledge-bases", json={"name": "Manuais"}, headers=_headers()
    ).json()

    delete_response = client.delete(f"/v1/knowledge-bases/{created['id']}", headers=_headers())
    assert delete_response.status_code == 204

    get_response = client.get(f"/v1/knowledge-bases/{created['id']}", headers=_headers())
    assert get_response.status_code == 404


def test_delete_twice_returns_404_on_the_second_call(client: TestClient) -> None:
    created = client.post(
        "/v1/knowledge-bases", json={"name": "Manuais"}, headers=_headers()
    ).json()

    client.delete(f"/v1/knowledge-bases/{created['id']}", headers=_headers())
    second = client.delete(f"/v1/knowledge-bases/{created['id']}", headers=_headers())

    assert second.status_code == 404


def test_list_paginates_with_cursor(client: TestClient) -> None:
    for i in range(3):
        client.post("/v1/knowledge-bases", json={"name": f"kb-{i}"}, headers=_headers())

    first_page = client.get("/v1/knowledge-bases", params={"limit": 2}, headers=_headers()).json()
    assert [kb["name"] for kb in first_page["items"]] == ["kb-0", "kb-1"]
    assert first_page["next_cursor"] is not None

    second_page = client.get(
        "/v1/knowledge-bases",
        params={"limit": 2, "cursor": first_page["next_cursor"]},
        headers=_headers(),
    ).json()
    assert [kb["name"] for kb in second_page["items"]] == ["kb-2"]
    assert second_page["next_cursor"] is None


class TestTenantIsolation:
    """Critério de aceite explícito da atividade: "tenant A não acessa
    tenant B"."""

    def test_tenant_a_cannot_see_tenant_b_in_list(self, client: TestClient) -> None:
        client.post("/v1/knowledge-bases", json={"name": "SegredosB"}, headers=_headers(TENANT_B))

        response = client.get("/v1/knowledge-bases", headers=_headers(TENANT_A))

        assert response.json()["items"] == []

    def test_tenant_a_cannot_get_tenant_b_knowledge_base(self, client: TestClient) -> None:
        created = client.post(
            "/v1/knowledge-bases", json={"name": "SegredosB"}, headers=_headers(TENANT_B)
        ).json()

        response = client.get(f"/v1/knowledge-bases/{created['id']}", headers=_headers(TENANT_A))

        assert response.status_code == 404

    def test_tenant_a_cannot_update_tenant_b_knowledge_base(self, client: TestClient) -> None:
        created = client.post(
            "/v1/knowledge-bases", json={"name": "SegredosB"}, headers=_headers(TENANT_B)
        ).json()

        response = client.patch(
            f"/v1/knowledge-bases/{created['id']}",
            json={"name": "Sequestrada"},
            headers=_headers(TENANT_A),
        )

        assert response.status_code == 404
        # A base do tenant B continua intacta.
        untouched = client.get(
            f"/v1/knowledge-bases/{created['id']}", headers=_headers(TENANT_B)
        ).json()
        assert untouched["name"] == "SegredosB"

    def test_tenant_a_cannot_delete_tenant_b_knowledge_base(self, client: TestClient) -> None:
        created = client.post(
            "/v1/knowledge-bases", json={"name": "SegredosB"}, headers=_headers(TENANT_B)
        ).json()

        response = client.delete(f"/v1/knowledge-bases/{created['id']}", headers=_headers(TENANT_A))

        assert response.status_code == 404
        still_there = client.get(f"/v1/knowledge-bases/{created['id']}", headers=_headers(TENANT_B))
        assert still_there.status_code == 200

    def test_both_tenants_can_use_the_same_knowledge_base_name(self, client: TestClient) -> None:
        response_a = client.post(
            "/v1/knowledge-bases", json={"name": "Manuais"}, headers=_headers(TENANT_A)
        )
        response_b = client.post(
            "/v1/knowledge-bases", json={"name": "Manuais"}, headers=_headers(TENANT_B)
        )

        assert response_a.status_code == 201
        assert response_b.status_code == 201
        assert response_a.json()["id"] != response_b.json()["id"]


class TestAuditLog:
    """RAG-054: criar/atualizar/excluir base de conhecimento registra
    um evento de auditoria com ator, tenant, ação e recurso corretos."""

    def test_create_records_an_audit_event(
        self, client: TestClient, audit_log: InMemoryAuditLog
    ) -> None:
        created = client.post(
            "/v1/knowledge-bases", json={"name": "Manuais"}, headers=_headers()
        ).json()

        assert len(audit_log.events) == 1
        event = audit_log.events[0]
        assert event.action == "knowledge_base.create"
        assert event.resource_type == "knowledge_base"
        assert str(event.resource_id) == created["id"]
        assert event.actor == "test-user"
        assert str(event.tenant_id) == TENANT_A

    def test_update_records_an_audit_event(
        self, client: TestClient, audit_log: InMemoryAuditLog
    ) -> None:
        created = client.post(
            "/v1/knowledge-bases", json={"name": "Manuais"}, headers=_headers()
        ).json()
        audit_log.events.clear()

        client.patch(
            f"/v1/knowledge-bases/{created['id']}",
            json={"description": "nova"},
            headers=_headers(),
        )

        assert len(audit_log.events) == 1
        assert audit_log.events[0].action == "knowledge_base.update"
        assert str(audit_log.events[0].resource_id) == created["id"]

    def test_delete_records_an_audit_event(
        self, client: TestClient, audit_log: InMemoryAuditLog
    ) -> None:
        created = client.post(
            "/v1/knowledge-bases", json={"name": "Manuais"}, headers=_headers()
        ).json()
        audit_log.events.clear()

        client.delete(f"/v1/knowledge-bases/{created['id']}", headers=_headers())

        assert len(audit_log.events) == 1
        assert audit_log.events[0].action == "knowledge_base.delete"
        assert str(audit_log.events[0].resource_id) == created["id"]
