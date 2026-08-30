"""Adapter LiteLLM de `GenerationProviderPort` (RAG-042).

Fala com o gateway LiteLLM (mesmo gateway de RAG-025/030/033 — seção 5
do plano: "AI Gateway: LiteLLM") via HTTP, na API de chat completion
compatível com OpenAI que o LiteLLM expõe em modo proxy
(`POST {base_url}/chat/completions`, `{"model": alias, "messages":
[{"role": "user", "content": prompt}]}` -> `{"choices": [{"message":
{"content": str}}], "usage": {"prompt_tokens": int, "completion_tokens":
int, "total_tokens": int}}`) — mesmo racional de
`adapters/litellm/embedding_provider.py` sobre por que `httpx` direto
em vez da SDK Python `litellm` (evita duplicar o papel do gateway).

`prompt` chega como uma única string já pronta (`PromptTemplate.render()`,
RAG-040) — este adapter não conhece a estrutura interna do prompt, só
encaixa o texto inteiro como o conteúdo de UMA mensagem "user"; não há
mensagem de sistema separada porque `PromptTemplate.render()` já
concatena o texto de sistema no início da própria string (ver docstring
de `packages/generation/prompts.py`).

## Retry e fallback

Timeout por tentativa e retry com backoff exponencial reaproveitam as
MESMAS configurações do gateway de embeddings/reranker
(`Settings.litellm_*`) — é o mesmo proxy, só um alias/endpoint
diferente. Mesma classificação de erro: HTTP 4xx nunca é retentado
(payload inválido, alias inexistente); HTTP >= 500/erro de conexão/
corpo malformado esgotam as tentativas antes de levantar
`GenerationUnavailableError`/`GenerationTimeoutError`.

"fallback configurável" (critério de aceite, ver docstring da porta
para o racional completo de por que não é o mesmo padrão de
`rerank_safely`): quando `Settings.generation_fallback_enabled` está
ligado, esgotar as tentativas no alias PRINCIPAL não levanta a exceção
na hora — em vez disso, o mesmo laço de tentativas roda de novo contra
o alias de FALLBACK (`get_default_generation_fallback_model`); só se
esse segundo laço também esgotar é que a exceção (do fallback) é
levantada. Quando o flag está desligado, esgotar o alias principal já
levanta a exceção — nenhuma segunda chamada é feita, e o arquivo de
configuração do fallback nem precisa existir.

Registra uma métrica de consumo (RAG-042,
`packages.observability.metrics.record_generation_call`) com os tokens
de prompt/resposta e qual alias respondeu — nunca o texto do prompt nem
o da resposta, que nunca viram atributo de métrica (mesma disciplina de
RAG-053)."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from packages.application.ports.generation_provider import (
    GenerationError,
    GenerationProviderPort,
    GenerationResult,
    GenerationTimeoutError,
    GenerationUnavailableError,
)
from packages.config.models import (
    get_default_generation_fallback_model,
    get_default_generation_model,
)
from packages.config.settings import Settings
from packages.observability.metrics import record_generation_call

_CHAT_COMPLETIONS_PATH = "/chat/completions"
_RETRY_BACKOFF_BASE_SECONDS = 0.5


class LiteLLMGenerationProvider(GenerationProviderPort):
    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._settings = settings
        self._alias = get_default_generation_model().alias
        # Só resolvido sob demanda (não no `__init__`) para que o
        # arquivo de configuração do fallback não precise existir
        # quando `generation_fallback_enabled` está desligado (mesmo
        # espírito de `PassthroughReranker` não exigir nenhuma
        # configuração de modelo, RAG-033).
        self._fallback_alias: str | None = None
        # `transport` só é passado em teste (httpx.MockTransport) — em
        # produção fica None e o httpx usa o transporte HTTP real.
        self._transport = transport

    async def generate(self, *, prompt: str) -> GenerationResult:
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
            try:
                content, usage = await self._generate_with_retry(
                    client, prompt=prompt, alias=self._alias
                )
                used_fallback = False
            except GenerationError:
                if not self._settings.generation_fallback_enabled:
                    raise
                if self._fallback_alias is None:
                    self._fallback_alias = get_default_generation_fallback_model().alias
                content, usage = await self._generate_with_retry(
                    client, prompt=prompt, alias=self._fallback_alias
                )
                used_fallback = True

        prompt_tokens, completion_tokens, total_tokens = usage
        record_generation_call(
            used_fallback=used_fallback,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            duration_seconds=time.monotonic() - started_at,
        )
        return GenerationResult(
            content=content,
            used_fallback=used_fallback,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )

    async def _generate_with_retry(
        self, client: httpx.AsyncClient, *, prompt: str, alias: str
    ) -> tuple[str, tuple[int, int, int]]:
        last_error: GenerationError | None = None
        max_attempts = self._settings.litellm_max_retries + 1

        for attempt in range(1, max_attempts + 1):
            try:
                response = await client.post(
                    _CHAT_COMPLETIONS_PATH,
                    json={
                        "model": alias,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
            except httpx.TimeoutException as exc:
                last_error = GenerationTimeoutError(detail=str(exc))
            except httpx.HTTPError as exc:
                last_error = GenerationUnavailableError(detail=str(exc))
            else:
                if response.status_code >= 500:
                    last_error = GenerationUnavailableError(
                        detail=f"gateway retornou HTTP {response.status_code}."
                    )
                elif response.status_code >= 400:
                    raise GenerationUnavailableError(
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
    def _parse_response(response: httpx.Response) -> tuple[str, tuple[int, int, int]]:
        try:
            body: dict[str, Any] = response.json()
            content = body["choices"][0]["message"]["content"]
            usage = body["usage"]
            prompt_tokens = int(usage["prompt_tokens"])
            completion_tokens = int(usage["completion_tokens"])
            total_tokens = int(usage["total_tokens"])
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            raise GenerationUnavailableError(
                detail=f"resposta do gateway em formato inesperado: {exc}"
            ) from exc
        if not isinstance(content, str):
            raise GenerationUnavailableError(
                detail="resposta do gateway em formato inesperado: conteúdo não é texto."
            )
        return content, (prompt_tokens, completion_tokens, total_tokens)
