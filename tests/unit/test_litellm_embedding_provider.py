"""Testes de RAG-025: `LiteLLMEmbeddingProvider`.

Cobre os critérios de aceite da atividade: timeout, retry e erro são
tratados; o alias de modelo configurado é usado; nenhum teste chama um
serviço real — todo o transporte HTTP é substituído por um
`httpx.MockTransport` determinístico (injetado via o parâmetro
`transport` do adapter, que só existe para isso — produção nunca o
passa).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from unittest.mock import MagicMock

import httpx
import pytest
from pydantic import SecretStr

from adapters.litellm import embedding_provider as embedding_provider_module
from adapters.litellm.embedding_provider import LiteLLMEmbeddingProvider
from packages.application.ports.embedding_provider import (
    EmbeddingProviderUnavailableError,
    EmbeddingTimeoutError,
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


def _embedding_response(count: int, *, reversed_index: bool = False) -> dict[str, object]:
    indices = list(range(count))
    if reversed_index:
        indices = list(reversed(indices))
    return {
        "data": [{"embedding": [float(i), float(i) + 0.5], "index": i} for i in indices],
        "model": "embedding-model-alias",
    }


def _provider(
    settings: Settings, handler: Callable[[httpx.Request], httpx.Response]
) -> LiteLLMEmbeddingProvider:
    return LiteLLMEmbeddingProvider(settings, transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    # Os testes de retry esgotam tentativas de propósito — sem isso,
    # o backoff exponencial real deixaria a suíte lenta.
    async def _instant_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(embedding_provider_module.asyncio, "sleep", _instant_sleep)


async def test_embed_with_no_texts_never_calls_the_gateway() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("o gateway não deveria ser chamado para uma lista vazia")

    provider = _provider(_make_settings(), _handler)

    assert await provider.embed(texts=[]) == []


async def test_embed_sends_the_configured_alias_and_the_input_texts() -> None:
    captured: dict[str, bytes] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read()
        return httpx.Response(200, json=_embedding_response(2))

    provider = _provider(_make_settings(), _handler)

    embeddings = await provider.embed(texts=["a", "b"])

    payload = json.loads(captured["body"])
    assert payload["model"] == "embedding-model-alias"
    assert payload["input"] == ["a", "b"]
    assert embeddings == [[0.0, 0.5], [1.0, 1.5]]


async def test_embed_reorders_response_by_index_even_if_the_gateway_does_not() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_embedding_response(3, reversed_index=True))

    provider = _provider(_make_settings(), _handler)

    embeddings = await provider.embed(texts=["a", "b", "c"])

    assert embeddings == [[0.0, 0.5], [1.0, 1.5], [2.0, 2.5]]


async def test_embed_splits_into_batches_and_preserves_order() -> None:
    calls: list[list[str]] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        calls.append(body["input"])
        return httpx.Response(200, json=_embedding_response(len(body["input"])))

    settings = _make_settings(LITELLM_EMBEDDING_BATCH_SIZE=2)
    provider = _provider(settings, _handler)
    texts = ["t0", "t1", "t2", "t3", "t4"]

    embeddings = await provider.embed(texts=texts)

    assert calls == [["t0", "t1"], ["t2", "t3"], ["t4"]]
    assert len(embeddings) == 5


async def test_embed_retries_a_transient_server_error_and_then_succeeds() -> None:
    attempts = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, json=_embedding_response(1))

    provider = _provider(_make_settings(), _handler)

    embeddings = await provider.embed(texts=["a"])

    assert attempts["n"] == 2
    assert embeddings == [[0.0, 0.5]]


async def test_embed_raises_provider_unavailable_after_exhausting_retries_on_server_error() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    provider = _provider(_make_settings(LITELLM_MAX_RETRIES=2), _handler)

    with pytest.raises(EmbeddingProviderUnavailableError):
        await provider.embed(texts=["a"])


async def test_embed_raises_timeout_error_after_exhausting_retries() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    provider = _provider(_make_settings(LITELLM_MAX_RETRIES=1), _handler)

    with pytest.raises(EmbeddingTimeoutError):
        await provider.embed(texts=["a"])


async def test_embed_raises_provider_unavailable_after_exhausting_retries_on_connection_error() -> (
    None
):
    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = _provider(_make_settings(LITELLM_MAX_RETRIES=1), _handler)

    with pytest.raises(EmbeddingProviderUnavailableError):
        await provider.embed(texts=["a"])


async def test_embed_raises_immediately_on_client_error_without_retrying() -> None:
    attempts = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(422)

    provider = _provider(_make_settings(LITELLM_MAX_RETRIES=3), _handler)

    with pytest.raises(EmbeddingProviderUnavailableError):
        await provider.embed(texts=["a"])

    assert attempts["n"] == 1


async def test_embed_raises_provider_unavailable_on_malformed_response_body() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    provider = _provider(_make_settings(), _handler)

    with pytest.raises(EmbeddingProviderUnavailableError):
        await provider.embed(texts=["a"])


async def test_embed_raises_provider_unavailable_on_embedding_count_mismatch() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_embedding_response(1))  # 1 embedding, 2 pedidos

    provider = _provider(_make_settings(), _handler)

    with pytest.raises(EmbeddingProviderUnavailableError):
        await provider.embed(texts=["a", "b"])


async def test_embed_sends_authorization_header_when_api_key_is_configured() -> None:
    captured: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization", "")
        return httpx.Response(200, json=_embedding_response(1))

    settings = _make_settings(LITELLM_API_KEY=SecretStr("secret-token"))
    provider = _provider(settings, _handler)

    await provider.embed(texts=["a"])

    assert captured["authorization"] == "Bearer secret-token"


async def test_embed_records_a_metric_with_the_text_count_and_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_embedding_response(2))

    fake_record = MagicMock()
    monkeypatch.setattr(embedding_provider_module, "record_embedding_batch", fake_record)
    provider = _provider(_make_settings(), _handler)

    await provider.embed(texts=["a", "b"])

    fake_record.assert_called_once()
    assert fake_record.call_args.kwargs["text_count"] == 2
    assert fake_record.call_args.kwargs["duration_seconds"] >= 0.0


async def test_embed_does_not_record_a_metric_for_an_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_record = MagicMock()
    monkeypatch.setattr(embedding_provider_module, "record_embedding_batch", fake_record)
    provider = _provider(_make_settings(), lambda request: httpx.Response(200, json={"data": []}))

    await provider.embed(texts=[])

    fake_record.assert_not_called()
