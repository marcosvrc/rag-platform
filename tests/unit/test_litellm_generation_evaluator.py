"""Testes de RAG-062: `LiteLLMGenerationEvaluator`.

Mesmo racional de `test_litellm_generation_provider.py`: timeout, retry
e erro são tratados; o alias do modelo-juiz é usado; nenhum teste chama
um serviço real — todo o transporte HTTP é substituído por um
`httpx.MockTransport` determinístico. Testes adicionais cobrem o
parsing do JSON de scores (o que diferencia este adapter do de
geração de resposta)."""

from __future__ import annotations

import json
from collections.abc import Callable
from unittest.mock import MagicMock

import httpx
import pytest
from pydantic import SecretStr

from adapters.litellm import generation_evaluator as litellm_evaluator_module
from adapters.litellm.generation_evaluator import LiteLLMGenerationEvaluator
from packages.application.ports.generation_evaluator import (
    GenerationEvaluatorTimeoutError,
    GenerationEvaluatorUnavailableError,
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
    *,
    content: str = '{"faithfulness": 0.9, "answer_relevancy": 0.8}',
    prompt_tokens: int = 20,
    completion_tokens: int = 10,
) -> dict[str, object]:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def _evaluator(
    settings: Settings, handler: Callable[[httpx.Request], httpx.Response]
) -> LiteLLMGenerationEvaluator:
    return LiteLLMGenerationEvaluator(settings, transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _instant_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(litellm_evaluator_module.asyncio, "sleep", _instant_sleep)


async def test_evaluate_sends_the_configured_alias_with_question_answer_and_context() -> None:
    captured: dict[str, bytes] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read()
        return httpx.Response(200, json=_chat_response())

    evaluator = _evaluator(_make_settings(), _handler)

    await evaluator.evaluate(
        question="qual é a arquitetura?", answer="é hexagonal.", context=["trecho sobre hexagonal"]
    )

    payload = json.loads(captured["body"])
    assert payload["model"] == "generation-evaluator-model-alias"
    message = payload["messages"][0]["content"]
    assert "qual é a arquitetura?" in message
    assert "é hexagonal." in message
    assert "trecho sobre hexagonal" in message


async def test_evaluate_returns_scores_and_token_usage_from_the_gateway() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_chat_response(
                content='{"faithfulness": 1.0, "answer_relevancy": 0.5}',
                prompt_tokens=40,
                completion_tokens=6,
            ),
        )

    evaluator = _evaluator(_make_settings(), _handler)

    result = await evaluator.evaluate(question="q", answer="a", context=["c"])

    assert result.faithfulness == 1.0
    assert result.answer_relevancy == 0.5
    assert result.prompt_tokens == 40
    assert result.completion_tokens == 6
    assert result.total_tokens == 46


async def test_evaluate_tolerates_markdown_code_fences_around_the_json() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_chat_response(
                content='```json\n{"faithfulness": 0.7, "answer_relevancy": 0.6}\n```'
            ),
        )

    evaluator = _evaluator(_make_settings(), _handler)

    result = await evaluator.evaluate(question="q", answer="a", context=[])

    assert result.faithfulness == 0.7
    assert result.answer_relevancy == 0.6


async def test_evaluate_with_empty_context_still_sends_a_valid_prompt() -> None:
    captured: dict[str, bytes] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read()
        return httpx.Response(200, json=_chat_response())

    evaluator = _evaluator(_make_settings(), _handler)

    await evaluator.evaluate(question="q", answer="sem evidência suficiente.", context=[])

    payload = json.loads(captured["body"])
    assert "nenhum trecho de contexto" in payload["messages"][0]["content"]


async def test_evaluate_retries_a_transient_server_error_and_then_succeeds() -> None:
    attempts = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, json=_chat_response())

    evaluator = _evaluator(_make_settings(), _handler)

    result = await evaluator.evaluate(question="q", answer="a", context=[])

    assert attempts["n"] == 2
    assert result.faithfulness == 0.9


async def test_evaluate_raises_unavailable_after_exhausting_retries_on_server_error() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    evaluator = _evaluator(_make_settings(LITELLM_MAX_RETRIES=1), _handler)

    with pytest.raises(GenerationEvaluatorUnavailableError):
        await evaluator.evaluate(question="q", answer="a", context=[])


async def test_evaluate_raises_timeout_error_after_exhausting_retries() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    evaluator = _evaluator(_make_settings(LITELLM_MAX_RETRIES=1), _handler)

    with pytest.raises(GenerationEvaluatorTimeoutError):
        await evaluator.evaluate(question="q", answer="a", context=[])


async def test_evaluate_raises_unavailable_after_exhausting_retries_on_connection_error() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    evaluator = _evaluator(_make_settings(LITELLM_MAX_RETRIES=1), _handler)

    with pytest.raises(GenerationEvaluatorUnavailableError):
        await evaluator.evaluate(question="q", answer="a", context=[])


async def test_evaluate_raises_immediately_on_client_error_without_retrying() -> None:
    attempts = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(422)

    evaluator = _evaluator(_make_settings(LITELLM_MAX_RETRIES=3), _handler)

    with pytest.raises(GenerationEvaluatorUnavailableError):
        await evaluator.evaluate(question="q", answer="a", context=[])

    assert attempts["n"] == 1


async def test_evaluate_raises_unavailable_on_malformed_response_body() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    evaluator = _evaluator(_make_settings(), _handler)

    with pytest.raises(GenerationEvaluatorUnavailableError):
        await evaluator.evaluate(question="q", answer="a", context=[])


async def test_evaluate_raises_unavailable_when_content_is_not_a_string() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        body = _chat_response()
        body["choices"][0]["message"]["content"] = None  # type: ignore[index]
        return httpx.Response(200, json=body)

    evaluator = _evaluator(_make_settings(), _handler)

    with pytest.raises(GenerationEvaluatorUnavailableError):
        await evaluator.evaluate(question="q", answer="a", context=[])


async def test_evaluate_raises_unavailable_when_content_has_no_json_object() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_response(content="não consigo avaliar isso."))

    evaluator = _evaluator(_make_settings(), _handler)

    with pytest.raises(GenerationEvaluatorUnavailableError):
        await evaluator.evaluate(question="q", answer="a", context=[])


async def test_evaluate_raises_unavailable_when_a_score_key_is_missing() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_response(content='{"faithfulness": 0.9}'))

    evaluator = _evaluator(_make_settings(), _handler)

    with pytest.raises(GenerationEvaluatorUnavailableError):
        await evaluator.evaluate(question="q", answer="a", context=[])


async def test_evaluate_raises_unavailable_when_a_score_is_out_of_range() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_chat_response(content='{"faithfulness": 1.5, "answer_relevancy": 0.5}')
        )

    evaluator = _evaluator(_make_settings(), _handler)

    with pytest.raises(GenerationEvaluatorUnavailableError):
        await evaluator.evaluate(question="q", answer="a", context=[])


async def test_evaluate_sends_authorization_header_when_api_key_is_configured() -> None:
    captured: dict[str, str] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization", "")
        return httpx.Response(200, json=_chat_response())

    settings = _make_settings(LITELLM_API_KEY=SecretStr("secret-token"))
    evaluator = _evaluator(settings, _handler)

    await evaluator.evaluate(question="q", answer="a", context=[])

    assert captured["authorization"] == "Bearer secret-token"


async def test_evaluate_records_a_metric_with_usage_and_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_response(prompt_tokens=15, completion_tokens=4))

    fake_record = MagicMock()
    monkeypatch.setattr(litellm_evaluator_module, "record_generation_evaluation_call", fake_record)
    evaluator = _evaluator(_make_settings(), _handler)

    await evaluator.evaluate(question="q", answer="a", context=[])

    fake_record.assert_called_once()
    assert fake_record.call_args.kwargs["prompt_tokens"] == 15
    assert fake_record.call_args.kwargs["completion_tokens"] == 4
    assert fake_record.call_args.kwargs["duration_seconds"] >= 0.0
