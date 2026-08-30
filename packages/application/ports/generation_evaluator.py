"""Porta de avaliação de geração via LLM-juiz (RAG-062, seção 14/21 do
plano): mede faithfulness (a resposta é sustentada pelo contexto
recuperado, sem alegação inventada) e answer relevancy (a resposta
realmente responde à pergunta feita) de uma resposta já gerada
(`QueryAnswer`, RAG-044).

Mesmo racional de "Ragas ou DeepEval atrás de interface" (seção 5 do
plano, decisões arquiteturais): o QUE esta porta expõe (duas notas
0.0-1.0, sem opinião sobre qual framework ou modelo as calcula) nunca
vaza qual biblioteca está por trás — mesma disciplina de
`EmbeddingProviderPort`/`GenerationProviderPort`. A implementação real
(`adapters/litellm/generation_evaluator.py`) usa um modelo-juiz via o
mesmo gateway LiteLLM (RAG-025/030/033/042), não uma biblioteca de
avaliação de terceiros — este projeto já evita dependências pesadas
sempre que um adapter fino resolve (mesmo racional documentado em
`packages/ingestion/chunking.py` sobre não baixar o vocabulário do
`tiktoken`); nada impede uma implementação futura atrás desta MESMA
porta usar Ragas/DeepEval de verdade, sem mudar nenhum caso de uso.

**"modelo avaliador configurável"** (critério de aceite): o modelo-juiz
é resolvido por um alias PRÓPRIO (`config/models/generation-evaluator.
v1.yaml`, `packages.config.models.get_default_generation_evaluator_model`)
— deliberadamente não reaproveita o alias de geração de resposta
(RAG-042): o modelo que avalia uma resposta não precisa (e tipicamente
não deve) ser o mesmo que a gerou, para reduzir viés de
autoavaliação."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass


class GenerationEvaluatorError(Exception):
    """Categoria base: a chamada ao modelo-juiz falhou depois de
    esgotar as tentativas de retry configuradas."""

    def __init__(self, *, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class GenerationEvaluatorTimeoutError(GenerationEvaluatorError):
    """O gateway não respondeu dentro do timeout configurado, em todas
    as tentativas."""


class GenerationEvaluatorUnavailableError(GenerationEvaluatorError):
    """O gateway respondeu com um erro (HTTP >= 500, erro de conexão,
    resposta malformada, ou uma resposta bem formada mas fora do
    formato/faixa esperados — ver `adapters/litellm/
    generation_evaluator.py` para o parsing) em todas as tentativas."""


@dataclass(frozen=True)
class GenerationEvaluationResult:
    """Resultado de uma avaliação bem-sucedida.

    `faithfulness`/`answer_relevancy` são sempre um float em [0.0, 1.0]
    — 1.0 é o melhor score possível em cada dimensão. `prompt_tokens`/
    `completion_tokens`/`total_tokens` são os mesmos três contadores de
    `GenerationResult` (RAG-042) — "custos registrados" (critério de
    aceite): este projeto não tem (nem inventa aqui) uma tabela de
    preço por modelo, então tokens consumidos é o mesmo proxy de custo
    já estabelecido em toda chamada de LLM do projeto (ver
    `packages.observability.metrics.record_generation_call`)."""

    faithfulness: float
    answer_relevancy: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class GenerationEvaluatorPort(ABC):
    """Avalia uma resposta já gerada contra a pergunta que a originou e
    o contexto que ela deveria ter usado."""

    @abstractmethod
    async def evaluate(
        self, *, question: str, answer: str, context: Sequence[str]
    ) -> GenerationEvaluationResult:
        """`context` é a lista de conteúdos de chunk que de fato
        entraram no prompt de geração (`QueryAnswer.
        context_chunk_contents`, RAG-044/RAG-062) — nunca o texto
        completo do contexto já concatenado (`ContextBuildResult.
        context_text`, RAG-041): quem implementa decide como apresentar
        múltiplos trechos ao modelo-juiz.

        Implementações devem tratar timeout e erro do gateway com
        retry (mesma disciplina de `GenerationProviderPort`); depois de
        esgotar as tentativas, levantam
        `GenerationEvaluatorTimeoutError` ou
        `GenerationEvaluatorUnavailableError`."""
