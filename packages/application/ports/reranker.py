"""Porta de reranking de candidatos (RAG-033, seção 11 do plano, passo
"reranquear candidatos (opcional)").

Domínio e casos de uso não importam LiteLLM (nem qualquer cliente
HTTP) diretamente (seção 5.1 do plano) — só esta interface; a
implementação real (`adapters/reranker/litellm.py`) fala com o mesmo
gateway LiteLLM de RAG-025/030 (`POST {base_url}/rerank`, no formato
Cohere que o LiteLLM segue para qualquer provedor por trás — Cohere,
Voyage, ou um reranker self-hospedado via Text Embeddings Inference).

**"pode ser desativado"** (critério de aceite): a configuração é QUAL
ADAPTER é injetado, nunca um `if` dentro de um adapter só — RAG-034 (o
endpoint retrieve, que ainda não existe) escolhe entre
`LiteLLMReranker` (reranking real) e `PassthroughReranker` (devolve os
candidatos na mesma ordem, verbatim) a partir de
`Settings.reranker_enabled`. Os dois implementam a mesma
`RerankerPort` — quem chama nunca sabe qual dos dois está por trás.

**"timeout usa ranking anterior"** (critério de aceite): reranking é
uma melhoria de qualidade sobre um ranking que já é bom o suficiente
(RRF, RAG-032) — nunca deve quebrar o fluxo de consulta inteiro. Por
isso `rerank_safely()` (mesmo padrão de `record_audit_event_safely`,
RAG-054) envolve a chamada e devolve os candidatos ORIGINAIS (truncados
a `top_n`, na ordem de entrada) em qualquer falha de `RerankerPort`
(timeout ou qualquer outro erro do gateway) — nunca deixa uma falha de
reranking virar uma falha de consulta.

**"registra latência sem registrar texto sensível"** (critério de
aceite): `adapters/reranker/litellm.py` mede e registra a duração da
chamada (RAG-053, `packages.observability.metrics`) — nunca o texto
dos chunks nem a query, que nunca viram atributo de métrica nem de
log."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Sequence

from packages.application.ports.lexical_search import ScoredChunk

_logger = logging.getLogger(__name__)


class RerankerError(Exception):
    """Categoria base: a chamada ao reranker falhou depois de esgotar
    as tentativas de retry configuradas."""

    def __init__(self, *, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class RerankerTimeoutError(RerankerError):
    """O gateway não respondeu dentro do timeout configurado, em todas
    as tentativas (incluindo os retries)."""


class RerankerUnavailableError(RerankerError):
    """O gateway respondeu com um erro (HTTP >= 500, erro de conexão,
    resposta malformada) em todas as tentativas."""


class RerankerPort(ABC):
    """Reordena `candidates` (já rankeados por RAG-032/fusão RRF) por
    relevância de verdade em relação a `query`, via um cross-encoder —
    mais caro e mais preciso que o ranking por similaridade/RRF que já
    os trouxe até aqui."""

    @abstractmethod
    async def rerank(
        self, *, query: str, candidates: Sequence[ScoredChunk], top_n: int
    ) -> list[ScoredChunk]:
        """Devolve no máximo `top_n` de `candidates`, na nova ordem de
        relevância. `candidates` vazio devolve lista vazia sem chamar
        o gateway. Levanta `RerankerTimeoutError`/`RerankerUnavailableError`
        depois de esgotar as tentativas — quem chama em produção deve
        usar `rerank_safely()`, não esta porta diretamente, a menos que
        queira tratar a falha de outro jeito."""


async def rerank_safely(
    reranker: RerankerPort, *, query: str, candidates: Sequence[ScoredChunk], top_n: int
) -> list[ScoredChunk]:
    """Envolve `reranker.rerank(...)`, devolvendo os candidatos
    ORIGINAIS (truncados a `top_n`, na ordem de entrada — o "ranking
    anterior" do critério de aceite) em qualquer falha de
    `RerankerError`. Nunca propaga a exceção — reranking é uma melhoria
    de qualidade, nunca um passo que pode derrubar uma consulta."""
    try:
        return await reranker.rerank(query=query, candidates=candidates, top_n=top_n)
    except RerankerError:
        _logger.exception("Reranking falhou; usando o ranking anterior (RRF) sem reordenar.")
        return list(candidates[:top_n])
