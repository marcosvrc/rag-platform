"""Adapter LiteLLM para geração de embeddings em lote (RAG-025).

Fala com o gateway LiteLLM (seção 5 do plano: "AI Gateway: LiteLLM")
via HTTP, na API de embeddings compatível com OpenAI que o LiteLLM
expõe em modo proxy (`POST {base_url}/embeddings`) — não a SDK Python
`litellm`. A SDK rotearia para provedores diretamente dentro do
processo da aplicação, duplicando o papel do gateway (que o diagrama
de arquitetura, seção 3, já modela como um serviço separado: "IW -->
GW[LiteLLM]") e reintroduzindo o mesmo tipo de dependência pesada já
evitada em RAG-023/024 — o próprio propósito de um "AI gateway" é a
aplicação só falar HTTP com ele, que já é o que `httpx` (dependência já
existente do projeto) resolve.

## Provisionamento do gateway

Este adapter só implementa o cliente — a atividade original (RAG-025)
deliberadamente não provisionou um proxy LiteLLM real, porque isso
exigiria escolher um modelo/provedor de embeddings de verdade, uma
decisão de produto melhor tomada explicitamente do que assumida
sozinha. Essa decisão foi tomada no RAG-030 (Qwen3-Embedding-0.6B via
Ollama): `docker-compose.yml` já sobe o proxy real (serviço `litellm`)
e `Settings.litellm_base_url` (default `http://localhost:4000`) aponta
para ele. Ver README, seções RAG-025 e RAG-030, para os detalhes.

## Retry e timeout

Timeout por tentativa (`Settings.litellm_timeout_seconds`) e retry com
backoff exponencial (`Settings.litellm_max_retries` tentativas
adicionais) são desta camada — `httpx` não faz retry sozinho. Depois de
esgotar as tentativas: `EmbeddingTimeoutError` se a última falha foi
timeout, `EmbeddingProviderUnavailableError` para qualquer outro erro do
gateway (HTTP >= 500, erro de conexão, corpo malformado). Um HTTP 4xx
(erro do cliente — payload inválido, alias inexistente) não é tratado
como transitório: levanta `EmbeddingProviderUnavailableError` na
primeira tentativa, sem consumir retries (retry não corrige uma
requisição malformada).

Desde o RAG-053, `embed()` também registra uma métrica de consumo
(`packages.observability.metrics.record_embedding_batch`) com a
contagem total de textos e a duração de toda a chamada (todos os
lotes HTTP juntos, não lote a lote) — só quando `texts` não é vazio,
já que uma lista vazia nem chega a chamar o gateway.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from packages.application.ports.embedding_provider import (
    EmbeddingProviderError,
    EmbeddingProviderPort,
    EmbeddingProviderUnavailableError,
    EmbeddingTimeoutError,
)
from packages.config.models import get_default_embedding_model
from packages.config.settings import Settings
from packages.observability.metrics import record_embedding_batch

_EMBEDDINGS_PATH = "/embeddings"
_RETRY_BACKOFF_BASE_SECONDS = 0.5


class LiteLLMEmbeddingProvider(EmbeddingProviderPort):
    """Gera embeddings via gateway LiteLLM, em lotes de até
    `Settings.litellm_embedding_batch_size` textos por requisição HTTP
    (passo 11 do fluxo de indexação: "gerar embeddings em lotes")."""

    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._settings = settings
        self._alias = get_default_embedding_model().alias
        # `transport` só é passado em teste (httpx.MockTransport) — em
        # produção fica None e o httpx usa o transporte HTTP real.
        self._transport = transport

    async def embed(self, *, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        started_at = time.monotonic()
        batch_size = self._settings.litellm_embedding_batch_size
        headers = {}
        if self._settings.litellm_api_key is not None:
            headers["Authorization"] = f"Bearer {self._settings.litellm_api_key.get_secret_value()}"

        embeddings: list[list[float]] = []
        async with httpx.AsyncClient(
            base_url=self._settings.litellm_base_url,
            headers=headers,
            timeout=self._settings.litellm_timeout_seconds,
            transport=self._transport,
        ) as client:
            for start in range(0, len(texts), batch_size):
                batch = texts[start : start + batch_size]
                embeddings.extend(await self._embed_batch(client, batch))

        record_embedding_batch(
            text_count=len(texts), duration_seconds=time.monotonic() - started_at
        )
        return embeddings

    async def _embed_batch(self, client: httpx.AsyncClient, batch: list[str]) -> list[list[float]]:
        last_error: EmbeddingProviderError | None = None
        max_attempts = self._settings.litellm_max_retries + 1

        for attempt in range(1, max_attempts + 1):
            try:
                response = await client.post(
                    _EMBEDDINGS_PATH, json={"model": self._alias, "input": batch}
                )
            except httpx.TimeoutException as exc:
                last_error = EmbeddingTimeoutError(detail=str(exc))
            except httpx.HTTPError as exc:
                last_error = EmbeddingProviderUnavailableError(detail=str(exc))
            else:
                if response.status_code >= 500:
                    last_error = EmbeddingProviderUnavailableError(
                        detail=f"gateway retornou HTTP {response.status_code}."
                    )
                elif response.status_code >= 400:
                    raise EmbeddingProviderUnavailableError(
                        detail=f"gateway rejeitou a requisição (HTTP {response.status_code})."
                    )
                else:
                    return self._parse_embeddings(response, expected=len(batch))

            if attempt < max_attempts:
                await asyncio.sleep(_RETRY_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)))

        if last_error is None:
            raise RuntimeError("estado inesperado: loop de retry terminou sem erro nem sucesso.")
        raise last_error

    @staticmethod
    def _parse_embeddings(response: httpx.Response, *, expected: int) -> list[list[float]]:
        try:
            body: dict[str, Any] = response.json()
            items = sorted(body["data"], key=lambda item: item.get("index", 0))
            embeddings = [item["embedding"] for item in items]
        except (ValueError, KeyError, TypeError) as exc:
            raise EmbeddingProviderUnavailableError(
                detail=f"resposta do gateway em formato inesperado: {exc}"
            ) from exc
        if len(embeddings) != expected:
            raise EmbeddingProviderUnavailableError(
                detail=f"gateway devolveu {len(embeddings)} embeddings para {expected} textos."
            )
        return embeddings
