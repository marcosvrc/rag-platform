"""Testes de RAG-005: endpoints /health/live e /health/ready.

Cobrem os dois critérios de aceite:
  * liveness não depende de recursos externos;
  * readiness valida dependências críticas (aqui, com as dependências
    mockadas — nenhum teste de pull request chama serviços externos reais,
    ver seção 1 do plano).
"""

from collections.abc import Iterator
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from apps.api import main
from apps.api.dependencies import get_settings_dependency
from apps.api.routers import health
from packages.config.settings import Settings, get_settings

FAKE_POSTGRES_PASSWORD = "fake-postgres-password"
FAKE_MINIO_PASSWORD = "fake-minio-password"


@pytest.fixture
def fake_settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        POSTGRES_PASSWORD=SecretStr(FAKE_POSTGRES_PASSWORD),
        MINIO_ROOT_PASSWORD=SecretStr(FAKE_MINIO_PASSWORD),
        JWT_ISSUER="rag-platform-tests",
        JWT_AUDIENCE="rag-platform-tests-api",
    )


@pytest.fixture
def client(fake_settings: Settings) -> Iterator[TestClient]:
    main.app.dependency_overrides[get_settings_dependency] = lambda: fake_settings
    try:
        yield TestClient(main.app)
    finally:
        main.app.dependency_overrides.clear()


def test_liveness_returns_ok_without_any_dependency_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Liveness não deve nem tentar falar com Postgres/Redis/MinIO: forçamos
    isso quebrando as três checagens e confirmando que /health/live nem
    assim é afetado (só /health/ready seria)."""

    async def _boom(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("liveness não deve chamar checagens de dependência externa")

    monkeypatch.setattr(health, "_check_postgres", _boom)
    monkeypatch.setattr(health, "_check_redis", _boom)
    monkeypatch.setattr(health, "_check_minio", _boom)

    with TestClient(main.app) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_returns_200_when_all_dependencies_are_healthy(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health, "_check_postgres", AsyncMock(return_value="ok"))
    monkeypatch.setattr(health, "_check_redis", AsyncMock(return_value="ok"))
    monkeypatch.setattr(health, "_check_minio", AsyncMock(return_value="ok"))

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "checks": {"postgres": "ok", "redis": "ok", "minio": "ok"},
    }


def test_readiness_returns_503_when_a_dependency_is_unavailable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health, "_check_postgres", AsyncMock(return_value="ok"))
    monkeypatch.setattr(health, "_check_redis", AsyncMock(return_value="error"))
    monkeypatch.setattr(health, "_check_minio", AsyncMock(return_value="ok"))

    response = client.get("/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "error"
    assert body["checks"]["redis"] == "error"


def test_readiness_never_exposes_connection_details_on_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mesmo com uma exceção de driver "de verdade" (com host/porta/DSN no
    texto), a resposta HTTP deve conter só "ok"/"error" — nunca a exceção."""

    async def _raise_with_sensitive_details(settings: Settings) -> str:
        raise RuntimeError(
            f"connection to server at {settings.postgres_host}:{settings.postgres_port} failed, "
            f"password={settings.postgres_password.get_secret_value()}"
        )

    monkeypatch.setattr(health, "_check_postgres", _raise_with_sensitive_details)
    monkeypatch.setattr(health, "_check_redis", AsyncMock(return_value="ok"))
    monkeypatch.setattr(health, "_check_minio", AsyncMock(return_value="ok"))

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert FAKE_POSTGRES_PASSWORD not in response.text
    assert response.json()["checks"]["postgres"] == "error"


def test_get_settings_dependency_returns_the_cached_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POSTGRES_PASSWORD", "fake-postgres-password")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "fake-minio-password")
    monkeypatch.setenv("JWT_ISSUER", "rag-platform-tests")
    monkeypatch.setenv("JWT_AUDIENCE", "rag-platform-tests-api")
    get_settings.cache_clear()
    try:
        assert get_settings_dependency() is get_settings()
    finally:
        get_settings.cache_clear()
