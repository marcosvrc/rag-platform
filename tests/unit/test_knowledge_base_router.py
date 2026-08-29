"""Testes de RAG-012: endpoints `/v1/knowledge-bases` (visão HTTP).

Usa o app real (`apps.api.main.app`), com o repositório trocado por
`InMemoryKnowledgeBaseRepository` via `dependency_overrides` — mesmo
padrão de `test_health.py` (RAG-005). `X-Tenant-Id` é enviado como
cabeçalho real em cada requisição (não é sobrescrito via Depends),
para também exercitar `get_current_tenant_id` (RAG-012) fim a fim.

Cobre os critérios de aceite da atividade: endpoints seguem o
contrato HTTP da seção 10.1 do plano; paginação por cursor funciona;
tenant A não acessa (nem lista, nem vê, nem altera, nem exclui) uma
base do tenant B.
"""

from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from adapters.knowledge_base_repository.in_memory import InMemoryKnowledgeBaseRepository
from apps.api import main
from apps.api.errors import PROBLEM_JSON_MEDIA_TYPE
from apps.api.routers.knowledge_bases import get_knowledge_base_repository

TENANT_A = str(uuid4())
TENANT_B = str(uuid4())


@pytest.fixture
def repository() -> InMemoryKnowledgeBaseRepository:
    return InMemoryKnowledgeBaseRepository()


@pytest.fixture
def client(repository: InMemoryKnowledgeBaseRepository) -> Iterator[TestClient]:
    main.app.dependency_overrides[get_knowledge_base_repository] = lambda: repository
    try:
        yield TestClient(main.app)
    finally:
        main.app.dependency_overrides.clear()


def _headers(tenant_id: str = TENANT_A) -> dict[str, str]:
    return {"X-Tenant-Id": tenant_id}


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


def test_create_requires_tenant_header(client: TestClient) -> None:
    response = client.post("/v1/knowledge-bases", json={"name": "Manuais"})

    assert response.status_code == 401
    assert response.headers["content-type"] == PROBLEM_JSON_MEDIA_TYPE


def test_create_rejects_malformed_tenant_header(client: TestClient) -> None:
    response = client.post(
        "/v1/knowledge-bases", json={"name": "Manuais"}, headers={"X-Tenant-Id": "not-a-uuid"}
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
