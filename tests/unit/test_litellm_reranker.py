"""Testes de RAG-033: `LiteLLMReranker`.

Mesmo racional de `tests/unit/test_litellm_embedding_provider.py`:
timeout, retry e erro são tratados; o alias de modelo configurado é
usado; nenhum teste chama um serviço real — todo o transporte HTTP é
substituído por um `httpx.MockTransport` determinístico."""

from __future__ import annotations

import json
from collections.abc import Callable
from unittest.mock import MagicMock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from adapters.reranker import litellm as litellm_reranker_module
from adapters.reranker.litellm import LiteLLMReranker
from packages.application.ports.lexical_search import ScoredChunk
from packages.application.ports.reranker import (
    RerankerTimeoutError,
    RerankerUnavailableError,
)
from packages.config.settings import Settings
from packages.domain.entities.chunk import Chunk


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


def _chunk(content: str = "conteúdo qualquer") -> Chunk:
    return Chunk(
        id=uuid4(),
        tenant_id=uuid4(),
        knowledge_base_id=uuid4(),
        version_id=uuid4(),
        content=content,
        token_count=1,
        page=None,
        section=None,
        metadata={},
        embedding=None,
    )


def _candidates(*contents: str) -> list[ScoredChunk]:
    return [ScoredChunk(chunk=_chunk(text), score=1.0) for text in contents]


def _rerank_response(*pairs: tuple[int, float]) -> dict[str, object]:
    return {"results": [{"index": index, "relevance_score": score} for index, score in pairs]}


def _reranker(
    settings: Settings, handler: Callable[[httpx.Request], httpx.Response]
) -> LiteLLMReranker:
    return LiteLLMReranker(settings, transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    # Os testes de retry esgotam tentativas de propósito — sem isso, o
    # backoff exponencial real deixaria a suíte lenta.
    async def _instant_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(litellm_reranker_module.asyncio, "sleep", _instant_sleep)


async def test_rerank_with_no_candidates_never_calls_the_gateway() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("o gateway não deveria ser chamado sem candidatos")

    reranker = _reranker(_make_settings(), _handler)

    assert await reranker.rerank(query="q", candidates=[], top_n=10) == []


async def test_rerank_sends_the_configured_alias_query_and_documents() -> None:
    captured: dict[str, bytes] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read()
        return httpx.Response(200, json=_rerank_response((0, 0.9), (1, 0.1)))

    reranker = _reranker(_make_settings(), _handler)
    candidates = _candidates("doc a", "doc b")

    await reranker.rerank(query="minha pergunta", candidates=candidates, top_n=10)

    payload = json.loads(captured["body"])
    assert payload["model"] == "reranker-model-alias"
    assert payload["query"] == "minha pergunta"
    assert payload["documents"] == ["doc a", "doc b"]
    assert payload["top_n"] == 10


async def test_rerank_reorders_candidates_by_relevance_score_descending() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        # O candidato de índice 1 ("doc b") é o mais relevante.
        return httpx.Response(200, json=_rerank_response((0, 0.2), (1, 0.8)))

    reranker = _reranker(_make_settings(), _handler)
    candidates = _candidates("doc a", "doc b")

    result = await reranker.rerank(query="q", candidates=candidates, top_n=10)

    assert [scored.chunk.content for scored in result] == ["doc b", "doc a"]


async def test_rerank_truncates_to_top_n() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_rerank_response((0, 0.5), (1, 0.9), (2, 0.1)))

    reranker = _reranker(_make_settings(), _handler)
    candidates = _candidates("a", "b", "c")

    result = await reranker.rerank(query="q", candidates=candidates, top_n=2)

    assert [scored.chunk.content for scored in result] == ["b", "a"]


async def test_rerank_retries_a_transient_server_error_and_then_succeeds() -> None:
    attempts = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, json=_rerank_response((0, 1.0)))

    reranker = _reranker(_make_settings(), _handler)

    result = await reranker.rerank(query="q", candidates=_candidates("a"), top_n=10)

    assert attempts["n"] == 2
    assert [scored.chunk.content for scored in result] == ["a"]


async def test_rerank_raises_unavailable_after_exhausting_retries_on_server_error() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    reranker = _reranker(_make_settings(LITELLM_MAX_RETRIES=2), _handler)

    with pytest.raises(RerankerUnavailableError):
        await reranker.rerank(query="q", candidates=_candidates("a"), top_n=10)


async def test_rerank_raises_timeout_error_after_exhausting_retries() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    reranker = _reranker(_make_settings(LITELLM_MAX_RETRIES=1), _handler)

    with pytest.raises(RerankerTimeoutError):
        await reranker.rerank(query="q", candidates=_candidates("a"), top_n=10)


async def test_rerank_raises_unavailable_after_exhausting_retries_on_connection_error() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    reranker = _reranker(_make_settings(LITELLM_MAX_RETRIES=1), _handler)

    with pytest.raises(RerankerUnavailableError):
        await reranker.rerank(query="q", candidates=_candidates("a"), top_n=10)


async def test_rerank_raises_immediately_on_client_error_without_retrying() -> None:
    attempts = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(422)

    reranker = _reranker(_make_settings(LITELLM_MAX_RETRIES=3), _handler)

    with pytest.raises(RerankerUnavailableError):
        await reranker.rerank(query="q", candidates=_candidates("a"), top_n=10)

    assert attempts["n"] == 1


async def test_rerank_raises_unavailable_on_malformed_response_body() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    reranker = _reranker(_make_settings(), _handler)

    with pytest.raises(RerankerUnavailableError):
        await reranker.rerank(query="q", candidates=_candidates("a"), top_n=10)


async def test_rerank_sends_authorization_header_when_api_key_is_configured() -> None:
    captured: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization", "")
        return httpx.Response(200, json=_rerank_response((0, 1.0)))

    settings = _make_settings(LITELLM_API_KEY=SecretStr("secret-token"))
    reranker = _reranker(settings, _handler)

    await reranker.rerank(query="q", candidates=_candidates("a"), top_n=10)

    assert captured["authorization"] == "Bearer secret-token"


async def test_rerank_records_a_metric_with_the_call_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_rerank_response((0, 1.0)))

    fake_record = MagicMock()
    monkeypatch.setattr(litellm_reranker_module, "record_reranker_call", fake_record)
    reranker = _reranker(_make_settings(), _handler)

    await reranker.rerank(query="q", candidates=_candidates("a"), top_n=10)

    fake_record.assert_called_once()
    assert fake_record.call_args.kwargs["duration_seconds"] >= 0.0


async def test_rerank_does_not_record_a_metric_for_no_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_record = MagicMock()
    monkeypatch.setattr(litellm_reranker_module, "record_reranker_call", fake_record)
    reranker = _reranker(_make_settings(), lambda request: httpx.Response(200, json={}))

    await reranker.rerank(query="q", candidates=[], top_n=10)

    fake_record.assert_not_called()
