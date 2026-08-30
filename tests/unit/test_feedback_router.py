"""Testes de RAG-045: endpoint `POST /v1/feedback` (visão HTTP) — mesmo
padrão de `test_query_router.py`: app real (`apps.api.main.app`), porta
trocada por fake via `dependency_overrides`, JWT real mintado por
`_make_token`.

Cobre os critérios de aceite: valida rating (enum) e motivo (obrigatório
para NEGATIVE); respeita tenant; não permite feedback para query alheia
(404, nunca 403). Semeia um `QueryLog` diretamente via
`InMemoryQueryRepository.persist_query` — o endpoint de feedback não
precisa de uma base de conhecimento nem do pipeline de consulta
completo, só de um `query_id` já existente."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from adapters.query_repository.in_memory import InMemoryQueryRepository
from apps.api import main
from apps.api.dependencies import get_settings_dependency
from apps.api.errors import PROBLEM_JSON_MEDIA_TYPE
from apps.api.routers.query import get_query_repository
from packages.config.settings import Settings
from packages.domain.entities.query_log import QueryLog, TokenUsage

TENANT_A = str(uuid4())
TENANT_B = str(uuid4())
KNOWLEDGE_BASE_ID = uuid4()

_JWT_SECRET = "test-jwt-secret-feedback-router-do-not-use-elsewhere"
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


def _headers(tenant_id: str = TENANT_A) -> dict[str, str]:
    return {"Authorization": f"Bearer {_make_token(tenant_id=tenant_id)}"}


@pytest.fixture
def query_repository() -> InMemoryQueryRepository:
    return InMemoryQueryRepository()


@pytest.fixture
def client(query_repository: InMemoryQueryRepository) -> Iterator[TestClient]:
    main.app.dependency_overrides[get_query_repository] = lambda: query_repository
    main.app.dependency_overrides[get_settings_dependency] = lambda: _test_settings()
    try:
        yield TestClient(main.app)
    finally:
        main.app.dependency_overrides.clear()


def _run(coro):
    return asyncio.run(coro)


def _seed_query_log(query_repository: InMemoryQueryRepository, *, tenant_id: str) -> UUID:
    query_log: QueryLog = _run(
        query_repository.persist_query(
            tenant_id=UUID(tenant_id),
            knowledge_base_id=KNOWLEDGE_BASE_ID,
            question_hash="a" * 64,
            model="m",
            latency_ms=1,
            token_usage=TokenUsage(input_tokens=0, output_tokens=0),
            trace_id=uuid4(),
            evidence=[],
        )
    )
    return query_log.id


def test_feedback_requires_authorization_header(client: TestClient) -> None:
    response = client.post("/v1/feedback", json={"query_id": str(uuid4()), "rating": "POSITIVE"})

    assert response.status_code == 401
    assert response.headers["content-type"] == PROBLEM_JSON_MEDIA_TYPE


def test_feedback_returns_404_for_unknown_query_id(client: TestClient) -> None:
    response = client.post(
        "/v1/feedback",
        json={"query_id": str(uuid4()), "rating": "POSITIVE"},
        headers=_headers(),
    )

    assert response.status_code == 404


def test_feedback_returns_404_for_a_query_of_another_tenant(
    client: TestClient, query_repository: InMemoryQueryRepository
) -> None:
    query_id = _seed_query_log(query_repository, tenant_id=TENANT_A)

    response = client.post(
        "/v1/feedback",
        json={"query_id": str(query_id), "rating": "POSITIVE"},
        headers=_headers(TENANT_B),
    )

    assert response.status_code == 404


def test_feedback_rejects_an_invalid_rating_value(
    client: TestClient, query_repository: InMemoryQueryRepository
) -> None:
    query_id = _seed_query_log(query_repository, tenant_id=TENANT_A)

    response = client.post(
        "/v1/feedback",
        json={"query_id": str(query_id), "rating": "MEH"},
        headers=_headers(),
    )

    assert response.status_code == 422


def test_feedback_requires_a_reason_for_negative_rating(
    client: TestClient, query_repository: InMemoryQueryRepository
) -> None:
    query_id = _seed_query_log(query_repository, tenant_id=TENANT_A)

    response = client.post(
        "/v1/feedback",
        json={"query_id": str(query_id), "rating": "NEGATIVE"},
        headers=_headers(),
    )

    assert response.status_code == 422


def test_feedback_accepts_a_positive_rating_without_reason(
    client: TestClient, query_repository: InMemoryQueryRepository
) -> None:
    query_id = _seed_query_log(query_repository, tenant_id=TENANT_A)

    response = client.post(
        "/v1/feedback",
        json={"query_id": str(query_id), "rating": "POSITIVE"},
        headers=_headers(),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["query_id"] == str(query_id)
    assert body["rating"] == "POSITIVE"
    assert body["reason"] is None
    assert body["expected_answer"] is None
    assert UUID(body["id"])


def test_feedback_accepts_a_negative_rating_with_reason_and_expected_answer(
    client: TestClient, query_repository: InMemoryQueryRepository
) -> None:
    query_id = _seed_query_log(query_repository, tenant_id=TENANT_A)

    response = client.post(
        "/v1/feedback",
        json={
            "query_id": str(query_id),
            "rating": "NEGATIVE",
            "reason": "não citou a fonte certa",
            "expected_answer": "deveria citar o manual v2",
        },
        headers=_headers(),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["rating"] == "NEGATIVE"
    assert body["reason"] == "não citou a fonte certa"
    assert body["expected_answer"] == "deveria citar o manual v2"


def test_feedback_rejects_an_unknown_extra_field(
    client: TestClient, query_repository: InMemoryQueryRepository
) -> None:
    query_id = _seed_query_log(query_repository, tenant_id=TENANT_A)

    response = client.post(
        "/v1/feedback",
        json={"query_id": str(query_id), "rating": "POSITIVE", "nao_permitido": "x"},
        headers=_headers(),
    )

    assert response.status_code == 422
