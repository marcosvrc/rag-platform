"""Adapter LiteLLM de `RerankerPort` (RAG-033).

Fala com o gateway LiteLLM (mesmo gateway de RAG-025/030 — seção 5 do
plano: "AI Gateway: LiteLLM") via HTTP, no formato de request/response
do Cohere Rerank v2 que o LiteLLM segue para qualquer provedor por
trás (`POST {base_url}/rerank`, `{"model": alias, "query": ...,
"documents": [...], "top_n": ...}` -> `{"results": [{"index":
int, "relevance_score": float}, ...]}`) — mesmo racional de
`adapters/litellm/embedding_provider.py` sobre por que `httpx` direto
em vez da SDK Python `litellm` (evita duplicar o papel do gateway).

Timeout por tentativa e retry com backoff exponencial reaproveitam as
MESMAS configurações do gateway de embeddings (`Settings.litellm_*`) —
é o mesmo proxy LiteLLM, só um alias/endpoint diferente; não faz
sentido um timeout/retry separado só para reranking. Mesma
classificação de erro: HTTP 4xx nunca é retentado (payload inválido,
alias inexistente); HTTP >= 500/erro de conexão/corpo malformado
esgotam as tentativas antes de levantar `RerankerUnavailableError`.

Registra a duração da chamada como métrica de consumo (RAG-053,
`packages.observability.metrics.record_reranker_call`) — nunca o texto
dos chunks nem a query, que nunca viram atributo de métrica (critério
de aceite "registra latência sem registrar texto sensível")."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from typing import Any

import httpx

from packages.application.ports.lexical_search import ScoredChunk
from packages.application.ports.reranker import (
    RerankerError,
    RerankerPort,
    RerankerTimeoutError,
    RerankerUnavailableError,
)
from packages.config.models import get_default_reranker_model
from packages.config.settings import Settings
from packages.observability.metrics import record_reranker_call

_RERANK_PATH = "/rerank"
_RETRY_BACKOFF_BASE_SECONDS = 0.5


class LiteLLMReranker(RerankerPort):
    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._settings = settings
        self._alias = get_default_reranker_model().alias
        # `transport` só é passado em teste (httpx.MockTransport) — em
        # produção fica None e o httpx usa o transporte HTTP real.
        self._transport = transport

    async def rerank(
        self, *, query: str, candidates: Sequence[ScoredChunk], top_n: int
    ) -> list[ScoredChunk]:
        if not candidates:
            return []

        started_at = time.monotonic()
        headers = {}
        if self._settings.litellm_api_key is not None:
            headers["Authorization"] = f"Bearer {self._settings.litellm_api_key.get_secret_value()}"

        documents = [scored.chunk.content for scored in candidates]
        async with httpx.AsyncClient(
            base_url=self._settings.litellm_base_url,
            headers=headers,
            timeout=self._settings.litellm_timeout_seconds,
            transport=self._transport,
        ) as client:
            reranked = await self._rerank_with_retry(
                client, query=query, documents=documents, top_n=top_n
            )

        record_reranker_call(duration_seconds=time.monotonic() - started_at)
        return [candidates[index] for index, _ in reranked][:top_n]

    async def _rerank_with_retry(
        self, client: httpx.AsyncClient, *, query: str, documents: list[str], top_n: int
    ) -> list[tuple[int, float]]:
        last_error: RerankerError | None = None
        max_attempts = self._settings.litellm_max_retries + 1

        for attempt in range(1, max_attempts + 1):
            try:
                response = await client.post(
                    _RERANK_PATH,
                    json={
                        "model": self._alias,
                        "query": query,
                        "documents": documents,
                        "top_n": top_n,
                    },
                )
            except httpx.TimeoutException as exc:
                last_error = RerankerTimeoutError(detail=str(exc))
            except httpx.HTTPError as exc:
                last_error = RerankerUnavailableError(detail=str(exc))
            else:
                if response.status_code >= 500:
                    last_error = RerankerUnavailableError(
                        detail=f"gateway retornou HTTP {response.status_code}."
                    )
                elif response.status_code >= 400:
                    raise RerankerUnavailableError(
                        detail=f"gateway rejeitou a requisição (HTTP {response.status_code})."
                    )
                else:
                    return self._parse_results(response)

            if attempt < max_attempts:
                await asyncio.sleep(_RETRY_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))

        if last_error is None:
            raise RuntimeError("estado inesperado: loop de retry terminou sem erro nem sucesso.")
        raise last_error

    @staticmethod
    def _parse_results(response: httpx.Response) -> list[tuple[int, float]]:
        try:
            body: dict[str, Any] = response.json()
            results = [
                (int(item["index"]), float(item["relevance_score"])) for item in body["results"]
            ]
        except (ValueError, KeyError, TypeError) as exc:
            raise RerankerUnavailableError(
                detail=f"resposta do gateway em formato inesperado: {exc}"
            ) from exc
        # Defensivo: o Cohere/LiteLLM já devolve ordenado por
        # relevance_score decrescente, mas esta porta não depende disso
        # (mesmo racional de reordenar por `index` em
        # adapters/litellm/embedding_provider.py).
        results.sort(key=lambda pair: pair[1], reverse=True)
        return results
