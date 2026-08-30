"""Adapter LiteLLM de `GenerationEvaluatorPort` (RAG-062).

Fala com o MESMO gateway LiteLLM de RAG-025/030/033/042 (chat
completion compatível com OpenAI, `POST {base_url}/chat/completions`)
— mesma estrutura de cliente HTTP, timeout e retry de
`adapters/litellm/generation_provider.py` (sem fallback: um modelo-juiz
não tem o conceito de "segundo alias de contingência", ver docstring
da porta), com uma diferença central: o CONTEÚDO da resposta do
modelo-juiz não é devolvido como texto livre — é interpretado como um
JSON estrito `{"faithfulness": <float>, "answer_relevancy": <float>}`
(o prompt, `config/prompts/generation-judge.v1.yaml`, instrui o modelo
exatamente nisso). Um corpo de resposta bem formado mas cujo `content`
não é esse JSON (chaves ausentes, valor fora de [0.0, 1.0], texto
extra que impede o parse) é tratado como
`GenerationEvaluatorUnavailableError`, mesma disciplina de
"resposta em formato inesperado" de `_parse_embeddings`/`_parse_response`
nos outros dois adapters LiteLLM."""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Sequence
from typing import Any

import httpx

from packages.application.ports.generation_evaluator import (
    GenerationEvaluationResult,
    GenerationEvaluatorError,
    GenerationEvaluatorPort,
    GenerationEvaluatorTimeoutError,
    GenerationEvaluatorUnavailableError,
)
from packages.config.models import get_default_generation_evaluator_model
from packages.config.settings import Settings
from packages.evaluation.judge_prompt import get_default_judge_prompt
from packages.observability.metrics import record_generation_evaluation_call

_CHAT_COMPLETIONS_PATH = "/chat/completions"
_RETRY_BACKOFF_BASE_SECONDS = 0.5
#: Extrai o primeiro objeto JSON de `{` a `}` balanceado mais externo —
#: alguns modelos envolvem o JSON pedido em cercas de código Markdown
#: (```json ... ```) mesmo quando instruídos a não fazer isso; este
#: adapter tolera esse envoltório em vez de falhar por causa dele.
_JSON_OBJECT_PATTERN = re.compile(r"\{.*\}", re.DOTALL)


class LiteLLMGenerationEvaluator(GenerationEvaluatorPort):
    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._settings = settings
        self._alias = get_default_generation_evaluator_model().alias
        self._prompt = get_default_judge_prompt()
        # `transport` só é passado em teste (httpx.MockTransport) — em
        # produção fica None e o httpx usa o transporte HTTP real.
        self._transport = transport

    async def evaluate(
        self, *, question: str, answer: str, context: Sequence[str]
    ) -> GenerationEvaluationResult:
        prompt = self._prompt.render(question=question, answer=answer, context=context)
        started_at = time.monotonic()
        headers = {}
        if self._settings.litellm_api_key is not None:
            headers["Authorization"] = f"Bearer {self._settings.litellm_api_key.get_secret_value()}"

        async with httpx.AsyncClient(
            base_url=self._settings.litellm_base_url,
            headers=headers,
            timeout=self._settings.litellm_timeout_seconds,
            transport=self._transport,
        ) as client:
            faithfulness, answer_relevancy, usage = await self._evaluate_with_retry(
                client, prompt=prompt
            )

        prompt_tokens, completion_tokens, total_tokens = usage
        record_generation_evaluation_call(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            duration_seconds=time.monotonic() - started_at,
        )
        return GenerationEvaluationResult(
            faithfulness=faithfulness,
            answer_relevancy=answer_relevancy,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )

    async def _evaluate_with_retry(
        self, client: httpx.AsyncClient, *, prompt: str
    ) -> tuple[float, float, tuple[int, int, int]]:
        last_error: GenerationEvaluatorError | None = None
        max_attempts = self._settings.litellm_max_retries + 1

        for attempt in range(1, max_attempts + 1):
            try:
                response = await client.post(
                    _CHAT_COMPLETIONS_PATH,
                    json={
                        "model": self._alias,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
            except httpx.TimeoutException as exc:
                last_error = GenerationEvaluatorTimeoutError(detail=str(exc))
            except httpx.HTTPError as exc:
                last_error = GenerationEvaluatorUnavailableError(detail=str(exc))
            else:
                if response.status_code >= 500:
                    last_error = GenerationEvaluatorUnavailableError(
                        detail=f"gateway retornou HTTP {response.status_code}."
                    )
                elif response.status_code >= 400:
                    raise GenerationEvaluatorUnavailableError(
                        detail=f"gateway rejeitou a requisição (HTTP {response.status_code})."
                    )
                else:
                    return self._parse_response(response)

            if attempt < max_attempts:
                await asyncio.sleep(_RETRY_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))

        if last_error is None:
            raise RuntimeError("estado inesperado: loop de retry terminou sem erro nem sucesso.")
        raise last_error

    @staticmethod
    def _parse_response(response: httpx.Response) -> tuple[float, float, tuple[int, int, int]]:
        try:
            body: dict[str, Any] = response.json()
            content = body["choices"][0]["message"]["content"]
            usage = body["usage"]
            prompt_tokens = int(usage["prompt_tokens"])
            completion_tokens = int(usage["completion_tokens"])
            total_tokens = int(usage["total_tokens"])
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            raise GenerationEvaluatorUnavailableError(
                detail=f"resposta do gateway em formato inesperado: {exc}"
            ) from exc

        if not isinstance(content, str):
            raise GenerationEvaluatorUnavailableError(
                detail="resposta do gateway em formato inesperado: conteúdo não é texto."
            )

        faithfulness, answer_relevancy = LiteLLMGenerationEvaluator._parse_scores(content)
        return faithfulness, answer_relevancy, (prompt_tokens, completion_tokens, total_tokens)

    @staticmethod
    def _parse_scores(content: str) -> tuple[float, float]:
        match = _JSON_OBJECT_PATTERN.search(content)
        if match is None:
            raise GenerationEvaluatorUnavailableError(
                detail="resposta do modelo-juiz não contém um objeto JSON."
            )
        try:
            payload = json.loads(match.group(0))
            faithfulness = float(payload["faithfulness"])
            answer_relevancy = float(payload["answer_relevancy"])
        except (ValueError, KeyError, TypeError) as exc:
            raise GenerationEvaluatorUnavailableError(
                detail=f"resposta do modelo-juiz em formato inesperado: {exc}"
            ) from exc

        for name, value in (("faithfulness", faithfulness), ("answer_relevancy", answer_relevancy)):
            if not (0.0 <= value <= 1.0):
                raise GenerationEvaluatorUnavailableError(
                    detail=f"resposta do modelo-juiz: '{name}' {value!r} fora da faixa [0.0, 1.0]."
                )

        return faithfulness, answer_relevancy
