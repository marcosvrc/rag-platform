"""Testes de RAG-005: corpo real das checagens de dependência (`_check_*`).

`test_health.py` testa o endpoint `/health/ready` com as checagens
mockadas por inteiro; aqui testamos o comportamento real de cada
`_check_*` — incluindo o próprio tratamento de exceção — sem depender de
infraestrutura real (nenhum teste de pull request chama serviços
externos reais, ver seção 1 do plano).
"""

from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from apps.api.routers import health
from packages.config.settings import Settings


@pytest.fixture
def fake_settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        POSTGRES_PASSWORD=SecretStr("fake-postgres-password"),
        MINIO_ROOT_PASSWORD=SecretStr("fake-minio-password"),
    )


async def test_check_postgres_returns_ok_when_connection_succeeds(
    fake_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_conn = AsyncMock()
    monkeypatch.setattr(health.asyncpg, "connect", AsyncMock(return_value=fake_conn))

    result = await health._check_postgres(fake_settings)

    assert result == "ok"
    fake_conn.execute.assert_awaited_once_with("SELECT 1")
    fake_conn.close.assert_awaited_once()


async def test_check_postgres_returns_error_when_connection_fails(
    fake_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        health.asyncpg, "connect", AsyncMock(side_effect=OSError("connection refused"))
    )

    result = await health._check_postgres(fake_settings)

    assert result == "error"


async def test_check_redis_returns_ok_when_ping_succeeds(
    fake_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_client = AsyncMock()
    fake_client.ping = AsyncMock(return_value=True)
    fake_client.aclose = AsyncMock(return_value=None)
    monkeypatch.setattr(health.redis_asyncio, "Redis", lambda **_kwargs: fake_client)

    result = await health._check_redis(fake_settings)

    assert result == "ok"
    fake_client.ping.assert_awaited_once()
    fake_client.aclose.assert_awaited_once()


async def test_check_redis_returns_error_when_ping_fails(
    fake_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_client = AsyncMock()
    fake_client.ping = AsyncMock(side_effect=TimeoutError())
    fake_client.aclose = AsyncMock(return_value=None)
    monkeypatch.setattr(health.redis_asyncio, "Redis", lambda **_kwargs: fake_client)

    result = await health._check_redis(fake_settings)

    assert result == "error"
    fake_client.aclose.assert_awaited_once()  # finally: sempre fecha o client


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _FakeAsyncClient:
    def __init__(self, status_code: int | None = None, error: Exception | None = None) -> None:
        self._status_code = status_code
        self._error = error

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, _url: str) -> _FakeResponse:
        if self._error is not None:
            raise self._error
        assert self._status_code is not None
        return _FakeResponse(self._status_code)


async def test_check_minio_returns_ok_when_response_is_200(
    fake_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        health.httpx, "AsyncClient", lambda **_kwargs: _FakeAsyncClient(status_code=200)
    )

    result = await health._check_minio(fake_settings)

    assert result == "ok"


async def test_check_minio_returns_error_when_response_is_not_200(
    fake_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        health.httpx, "AsyncClient", lambda **_kwargs: _FakeAsyncClient(status_code=503)
    )

    result = await health._check_minio(fake_settings)

    assert result == "error"


async def test_check_minio_returns_error_when_request_raises(
    fake_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        health.httpx,
        "AsyncClient",
        lambda **_kwargs: _FakeAsyncClient(error=OSError("connection refused")),
    )

    result = await health._check_minio(fake_settings)

    assert result == "error"
