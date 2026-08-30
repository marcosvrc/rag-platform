"""Testes de RAG-042: `LiteLLMGenerationProvider`.

Mesmo racional de `tests/unit/test_litellm_reranker.py`: timeout, retry
e erro são tratados; o alias de modelo configurado é usado; nenhum
teste chama um serviço real — todo o transporte HTTP é substituído por
um `httpx.MockTransport` determinístico. Testes adicionais cobrem o
fallback configurável (critério de aceite exclusivo de RAG-042)."""

from __future__ import annotations

import json
from collections.abc import Callable
from unittest.mock import MagicMock

import httpx
import pytest
from pydantic import SecretStr

from adapters.litellm import generation_provider as litellm_generation_module
from adapters.litellm.generation_provider import LiteLLMGenerationProvider
from packages.application.ports.generation_provider import (
    GenerationTimeoutError,
    GenerationUnavailableError,
)
from packages.config.settings import Settings


def _make_settings(**overrides: object) -> Settings:
    fields: dict[str, object] = {
        "_env_file": None,
        "POSTGRES_PASSWORD": SecretStr("x"),
        "MINIO_ROOT_PASSWORD": SecretStr("x"),
        "JWT_SECRET": SecretStr("x"),
        "JWT_ISSUER": "rag-platform-tests",
        "JWT_AUDIENCE": "rag-platform-tests-api",
        "LITELLM_MAX_RETRIES": 2,
    }
    fields.update(overrides)
    return Settings(**fields)  # type: ignore[arg-type]


def _chat_response(
    *, content: str = "resposta qualquer", prompt_tokens: int = 10, completion_tokens: int = 5
) -> dict[str, object]:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def _provider(
    settings: Settings, handler: Callable[[httpx.Request], httpx.Response]
) -> LiteLLMGenerationProvider:
    return LiteLLMGenerationProvider(settings, transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    # Os testes de retry esgotam tentativas de propósito — sem isso, o
    # backoff exponencial real deixaria a suíte lenta.
    async def _instant_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(litellm_generation_module.asyncio, "sleep", _instant_sleep)


async def test_generate_sends_the_configured_alias_and_prompt_as_a_single_user_message() -> None:
    captured: dict[str, bytes] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read()
        return httpx.Response(200, json=_chat_response())

    provider = _provider(_make_settings(), _handler)

    await provider.generate(prompt="qual é a arquitetura do sistema?")

    payload = json.loads(captured["body"])
    assert payload["model"] == "generation-model-alias"
    assert payload["messages"] == [{"role": "user", "content": "qual é a arquitetura do sistema?"}]


async def test_generate_returns_content_and_token_usage_from_the_gateway() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_chat_response(
                content="a arquitetura é hexagonal", prompt_tokens=42, completion_tokens=8
            ),
        )

    provider = _provider(_make_settings(), _handler)

    result = await provider.generate(prompt="qual é a arquitetura do sistema?")

    assert result.content == "a arquitetura é hexagonal"
    assert result.prompt_tokens == 42
    assert result.completion_tokens == 8
    assert result.total_tokens == 50
    assert result.used_fallback is False


async def test_generate_retries_a_transient_server_error_and_then_succeeds() -> None:
    attempts = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, json=_chat_response())

    provider = _provider(_make_settings(), _handler)

    result = await provider.generate(prompt="q")

    assert attempts["n"] == 2
    assert result.content == "resposta qualquer"


async def test_generate_raises_unavailable_after_exhausting_retries_on_server_error() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    provider = _provider(_make_settings(LITELLM_MAX_RETRIES=2), _handler)

    with pytest.raises(GenerationUnavailableError):
        await provider.generate(prompt="q")


async def test_generate_raises_timeout_error_after_exhausting_retries() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    provider = _provider(_make_settings(LITELLM_MAX_RETRIES=1), _handler)

    with pytest.raises(GenerationTimeoutError):
        await provider.generate(prompt="q")


async def test_generate_raises_unavailable_after_exhausting_retries_on_connection_error() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = _provider(_make_settings(LITELLM_MAX_RETRIES=1), _handler)

    with pytest.raises(GenerationUnavailableError):
        await provider.generate(prompt="q")


async def test_generate_raises_immediately_on_client_error_without_retrying() -> None:
    attempts = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(422)

    provider = _provider(_make_settings(LITELLM_MAX_RETRIES=3), _handler)

    with pytest.raises(GenerationUnavailableError):
        await provider.generate(prompt="q")

    assert attempts["n"] == 1


async def test_generate_raises_unavailable_on_malformed_response_body() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    provider = _provider(_make_settings(), _handler)

    with pytest.raises(GenerationUnavailableError):
        await provider.generate(prompt="q")


async def test_generate_raises_unavailable_when_content_is_not_a_string() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        body = _chat_response()
        body["choices"][0]["message"]["content"] = None  # type: ignore[index]
        return httpx.Response(200, json=body)

    provider = _provider(_make_settings(), _handler)

    with pytest.raises(GenerationUnavailableError):
        await provider.generate(prompt="q")


async def test_generate_sends_authorization_header_when_api_key_is_configured() -> None:
    captured: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization", "")
        return httpx.Response(200, json=_chat_response())

    settings = _make_settings(LITELLM_API_KEY=SecretStr("secret-token"))
    provider = _provider(settings, _handler)

    await provider.generate(prompt="q")

    assert captured["authorization"] == "Bearer secret-token"


async def test_generate_records_a_metric_with_usage_and_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_response(prompt_tokens=12, completion_tokens=3))

    fake_record = MagicMock()
    monkeypatch.setattr(litellm_generation_module, "record_generation_call", fake_record)
    provider = _provider(_make_settings(), _handler)

    await provider.generate(prompt="q")

    fake_record.assert_called_once()
    assert fake_record.call_args.kwargs["used_fallback"] is False
    assert fake_record.call_args.kwargs["prompt_tokens"] == 12
    assert fake_record.call_args.kwargs["completion_tokens"] == 3
    assert fake_record.call_args.kwargs["duration_seconds"] >= 0.0


# --- Fallback configurável -------------------------------------------------


async def test_generate_does_not_try_fallback_when_disabled() -> None:
    attempts = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(500)

    provider = _provider(
        _make_settings(LITELLM_MAX_RETRIES=1, GENERATION_FALLBACK_ENABLED=False), _handler
    )

    with pytest.raises(GenerationUnavailableError):
        await provider.generate(prompt="q")

    # 2 tentativas no alias principal (1 inicial + 1 retry) e nenhuma no
    # de fallback.
    assert attempts["n"] == 2


async def test_generate_falls_back_to_the_fallback_alias_when_the_primary_exhausts_retries() -> (
    None
):
    requests: list[dict[str, object]] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read())
        requests.append(payload)
        if payload["model"] == "generation-model-alias":
            return httpx.Response(500)
        return httpx.Response(200, json=_chat_response(content="resposta do fallback"))

    provider = _provider(
        _make_settings(LITELLM_MAX_RETRIES=1, GENERATION_FALLBACK_ENABLED=True), _handler
    )

    result = await provider.generate(prompt="q")

    assert result.content == "resposta do fallback"
    assert result.used_fallback is True
    # 2 tentativas no principal (esgotadas) + 1 no fallback (sucesso de primeira).
    assert [payload["model"] for payload in requests] == [
        "generation-model-alias",
        "generation-model-alias",
        "generation-fallback-model-alias",
    ]


async def test_generate_raises_the_fallback_error_when_both_aliases_exhaust_retries() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read())
        if payload["model"] == "generation-model-alias":
            return httpx.Response(500)
        raise httpx.ReadTimeout("timed out", request=request)

    provider = _provider(
        _make_settings(LITELLM_MAX_RETRIES=0, GENERATION_FALLBACK_ENABLED=True), _handler
    )

    with pytest.raises(GenerationTimeoutError):
        await provider.generate(prompt="q")


async def test_generate_records_fallback_usage_when_the_fallback_alias_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read())
        if payload["model"] == "generation-model-alias":
            return httpx.Response(500)
        return httpx.Response(200, json=_chat_response(prompt_tokens=7, completion_tokens=2))

    fake_record = MagicMock()
    monkeypatch.setattr(litellm_generation_module, "record_generation_call", fake_record)
    provider = _provider(
        _make_settings(LITELLM_MAX_RETRIES=0, GENERATION_FALLBACK_ENABLED=True), _handler
    )

    result = await provider.generate(prompt="q")

    assert result.used_fallback is True
    fake_record.assert_called_once()
    assert fake_record.call_args.kwargs["used_fallback"] is True


async def test_generate_resolves_the_fallback_alias_only_once_across_calls() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read())
        if payload["model"] == "generation-model-alias":
            return httpx.Response(500)
        return httpx.Response(200, json=_chat_response())

    provider = _provider(
        _make_settings(LITELLM_MAX_RETRIES=0, GENERATION_FALLBACK_ENABLED=True), _handler
    )

    first = await provider.generate(prompt="q1")
    second = await provider.generate(prompt="q2")

    assert first.used_fallback is True
    assert second.used_fallback is True
