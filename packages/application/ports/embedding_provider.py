"""Porta de geração de embeddings em lote (RAG-025).

O domínio e os casos de uso não importam LiteLLM (nem qualquer cliente
HTTP) diretamente — só esta interface (seção 5.1 do plano). A
implementação concreta (`adapters/litellm/embedding_provider.py`) fala
com o gateway LiteLLM (seção 5: "AI Gateway: LiteLLM") por HTTP, na API
compatível com OpenAI que o LiteLLM expõe em modo proxy — não a SDK
Python `litellm`, que rotearia para provedores diretamente e duplicaria
o papel do gateway (ver docstring do adapter).
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class EmbeddingProviderError(Exception):
    """Categoria base: a chamada ao gateway de embeddings falhou depois
    de esgotar as tentativas de retry configuradas."""

    def __init__(self, *, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class EmbeddingTimeoutError(EmbeddingProviderError):
    """O gateway não respondeu dentro do timeout configurado, em todas
    as tentativas (incluindo os retries)."""


class EmbeddingProviderUnavailableError(EmbeddingProviderError):
    """O gateway respondeu com um erro (HTTP >= 500, erro de conexão,
    resposta malformada) em todas as tentativas."""


class EmbeddingProviderPort(ABC):
    """Gera embeddings para um lote de textos, na ordem em que foram
    enviados — `embed(texts)[i]` é o embedding de `texts[i]`."""

    @abstractmethod
    async def embed(self, *, texts: list[str]) -> list[list[float]]:
        """Gera um embedding por texto em `texts`, preservando a ordem.

        Implementações devem tratar timeout e erro do provedor com
        retry (critério de aceite do RAG-025); depois de esgotar as
        tentativas, levantam `EmbeddingTimeoutError` ou
        `EmbeddingProviderUnavailableError`. `texts` vazio devolve `[]`
        sem chamar o gateway."""
