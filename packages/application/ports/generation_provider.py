"""Porta de geração de resposta via chat completion (RAG-042, seção 12
do plano, passo "gerar resposta com LLM").

Domínio e casos de uso não importam LiteLLM (nem qualquer cliente
HTTP) diretamente (seção 5.1 do plano) — só esta interface; a
implementação real (`adapters/litellm/generation_provider.py`) fala
com o mesmo gateway LiteLLM de RAG-025/030/033
(`POST {base_url}/chat/completions`, no formato compatível com OpenAI
que o LiteLLM expõe em modo proxy para qualquer provedor por trás),
seguindo o mesmo racional de "por que httpx direto, não a SDK `litellm`"
já documentado em `adapters/litellm/embedding_provider.py`.

**Prompt já pronto, não uma lista de mensagens**: `generate(prompt=...)`
recebe uma única string — o texto que `PromptTemplate.render()`
(RAG-040) já monta (sistema + aviso de conteúdo não confiável +
contexto + instrução de citação + pergunta, tudo concatenado). Esta
porta não conhece `PromptTemplate` nem inventa uma divisão paralela em
mensagens de sistema/usuário — quem monta o prompt (o caso de uso que
RAG-044 vai escrever) decide a composição; esta porta só entrega esse
texto como o conteúdo de uma única mensagem "user" ao modelo (ver
docstring do adapter para o detalhe do payload).

**"usa alias"** (critério de aceite): mesmo padrão de RAG-025/033 —
`packages.config.models.get_default_generation_model().alias`, nunca
um nome de modelo hardcoded aqui ou no adapter.

**"aplica timeout e fallback configurável"** (critério de aceite): o
timeout e o retry reaproveitam as MESMAS configurações do gateway já
usadas por embeddings/reranker (`Settings.litellm_*`) — é o mesmo
proxy, só um alias/endpoint diferente. O "fallback configurável" NÃO
é o mesmo padrão de `rerank_safely()` (RAG-033): lá, a falha do
reranker tem um estado anterior óbvio para reverter (o ranking
RRF que já existia antes de tentar reranquear). Aqui não há resposta
"anterior" nenhuma para uma pergunta que ainda não foi respondida —
"falhar de volta para nada" não é uma opção. Em vez disso, o fallback é
um SEGUNDO alias de modelo (`config/models/generation-fallback.v1.yaml`,
carregado por `get_default_generation_fallback_model()`), ligado por
`Settings.generation_fallback_enabled` (default `False`, mesmo padrão
liga/desliga de `Settings.reranker_enabled`): quando ligado, o adapter
tenta o alias principal até esgotar `litellm_max_retries`, e só então
tenta o alias de fallback (com o mesmo orçamento de tentativas) antes
de desistir; quando desligado, esgotar as tentativas do alias principal
já levanta o erro — nenhuma segunda chamada é feita.

**"registra uso"** (critério de aceite): `GenerationResult` devolve os
três contadores de token que o gateway já reporta (`prompt_tokens`,
`completion_tokens`, `total_tokens`) e qual dos dois aliases respondeu
(`used_fallback`) — RAG-044 persiste isso em `QueryLog.token_usage`
(a coluna já existe desde RAG-010). O adapter também emite uma métrica
de consumo (`packages.observability.metrics.record_generation_call`),
mesmo ponto de instrumentação de `record_embedding_batch`/
`record_reranker_call`."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class GenerationError(Exception):
    """Categoria base: a chamada de geração falhou depois de esgotar as
    tentativas de retry configuradas (no alias principal e, se
    `Settings.generation_fallback_enabled`, também no de fallback)."""

    def __init__(self, *, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class GenerationTimeoutError(GenerationError):
    """O gateway não respondeu dentro do timeout configurado, em todas
    as tentativas (incluindo os retries e, se aplicável, o fallback)."""


class GenerationUnavailableError(GenerationError):
    """O gateway respondeu com um erro (HTTP >= 500, erro de conexão,
    resposta malformada) em todas as tentativas (incluindo o fallback,
    se aplicável)."""


@dataclass(frozen=True)
class GenerationResult:
    """Resultado de uma chamada de geração bem-sucedida.

    `used_fallback` indica se foi o alias de fallback que respondeu
    (só possível quando `Settings.generation_fallback_enabled` é
    verdadeiro) — RAG-044/observabilidade usam para saber se a
    resposta veio do modelo principal ou do modelo de contingência."""

    content: str
    used_fallback: bool
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class GenerationProviderPort(ABC):
    """Gera uma resposta em texto livre a partir de um prompt já
    renderizado (RAG-040/041), via chat completion."""

    @abstractmethod
    async def generate(self, *, prompt: str) -> GenerationResult:
        """Envia `prompt` como o conteúdo de uma única mensagem "user"
        ao modelo por trás do alias de geração configurado.

        Implementações devem tratar timeout e erro do gateway com
        retry (mesma disciplina de `EmbeddingProviderPort`/
        `RerankerPort`); depois de esgotar as tentativas — e o
        fallback, se configurado — levantam `GenerationTimeoutError` ou
        `GenerationUnavailableError`."""
