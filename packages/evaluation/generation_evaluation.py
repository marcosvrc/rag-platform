"""Orquestração da avaliação de geração (RAG-062, seção 14/21 do
plano): roda `answer_query` (RAG-044) para cada caso respondível do
dataset dourado (RAG-060) e julga cada resposta com
`GenerationEvaluatorPort` (faithfulness, answer relevancy).

Reusa `answer_query` sem modificação — a mesma função que o endpoint
`/query` chama — para que a avaliação meça exatamente a geração de
produção (recuperação + contexto + geração + validação de groundedness),
nunca um caminho de código paralelo só para avaliação; mesmo racional
de `packages.evaluation.retrieval_evaluation` reusar `retrieve_evidence`
(RAG-034) sem modificação.

**"resultados ligados às versões de prompt/modelo"** (critério de
aceite): `GenerationEvaluationReport` registra o alias do modelo de
GERAÇÃO usado para produzir as respostas, o (id, versão) do prompt de
resposta (RAG-040) usado para montá-las, e o alias do modelo-juiz que
as avaliou — os três valores que, juntos, determinam se um número de
faithfulness/relevancy de uma execução é comparável ao de outra (trocar
qualquer um invalida a comparação direta contra uma execução anterior).

**"custos registrados"**: `total_prompt_tokens`/`total_completion_tokens`
somam só os tokens gastos AVALIANDO (chamadas ao modelo-juiz) — os
tokens gastos GERANDO cada resposta já são contados por
`record_generation_call` (RAG-042), no ponto de entrada de produção que
`answer_query` de fato invoca; nenhum número é contado duas vezes.

`ThresholdCheck`/`_mean` são definidos aqui, não importados de
`packages.evaluation.retrieval_evaluation` (RAG-061): esta atividade
depende só de RAG-044/RAG-060 (ver seção 14/21 do plano) — RAG-061 é
uma atividade irmã, numa branch independente, sem relação de
dependência declarada com esta. Duplicar essas ~10 linhas triviais é
mais barato que acoplar duas branches que podem mesclar em qualquer
ordem."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

from packages.application.commands.query import answer_query
from packages.application.ports.document_repository import DocumentRepositoryPort
from packages.application.ports.embedding_provider import EmbeddingProviderPort
from packages.application.ports.generation_evaluator import GenerationEvaluatorPort
from packages.application.ports.generation_provider import GenerationProviderPort
from packages.application.ports.knowledge_base_repository import KnowledgeBaseRepositoryPort
from packages.application.ports.lexical_search import LexicalSearchPort
from packages.application.ports.query_repository import QueryRepositoryPort
from packages.application.ports.reranker import RerankerPort
from packages.application.ports.vector_search import VectorSearchPort
from packages.evaluation.golden_dataset import GoldenDataset
from packages.generation.prompts import PromptTemplate


def _mean(values: Sequence[float]) -> float:
    """Média aritmética simples — mesma função (por nome e
    comportamento) de `packages.evaluation.retrieval_metrics.mean`
    (RAG-061), reimplementada aqui em vez de importada: ver docstring
    do módulo sobre por que este módulo não depende da branch de
    RAG-061. `values` vazio levanta `ValueError` — um relatório sem
    nenhum caso avaliado é um erro de uso (dataset sem caso
    respondível), nunca uma média `0.0` silenciosa."""
    if not values:
        raise ValueError("values não pode ser vazio para calcular a média.")
    return sum(values) / len(values)


@dataclass(frozen=True, slots=True)
class ThresholdCheck:
    """Resultado de checar um relatório de avaliação contra limiares
    mínimos configuráveis — mesmo formato (por nome e comportamento) de
    `packages.evaluation.retrieval_evaluation.ThresholdCheck` (RAG-061),
    reimplementado aqui pela mesma razão de `_mean` acima."""

    passed: bool
    violations: tuple[str, ...] = field(default_factory=tuple)


class EmptyEvaluationError(ValueError):
    """`dataset` não tem nenhum caso respondível (`expected_evidence`
    não vazio) — um relatório sem nenhum caso avaliado não tem
    faithfulness/answer relevancy bem definidos."""


@dataclass(frozen=True, slots=True)
class CaseGenerationResult:
    """Resultado da avaliação de geração para um `GoldenCase`."""

    case_id: str
    grounded: bool
    faithfulness: float
    answer_relevancy: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True, slots=True)
class GenerationEvaluationReport:
    """Resultado agregado de uma execução de avaliação de geração — o
    que `scripts/run_generation_evaluation.py` serializa em JSON/
    Markdown e contra o que um limiar configurável é checado
    (`check_thresholds`, reusado de `retrieval_evaluation`, critério de
    aceite "falha por limiar configurável" — mesma exigência da
    RAG-061, mesma implementação)."""

    dataset_id: str
    dataset_version: str
    generation_model_alias: str
    prompt_id: str
    prompt_version: str
    evaluator_model_alias: str
    generated_at: datetime
    faithfulness: float
    answer_relevancy: float
    total_prompt_tokens: int
    total_completion_tokens: int
    case_results: tuple[CaseGenerationResult, ...] = field(default_factory=tuple)

    @property
    def evaluated_case_count(self) -> int:
        return len(self.case_results)


async def evaluate_generation(
    *,
    dataset: GoldenDataset,
    knowledge_base_repository: KnowledgeBaseRepositoryPort,
    document_repository: DocumentRepositoryPort,
    query_repository: QueryRepositoryPort,
    embedding_provider: EmbeddingProviderPort,
    vector_search: VectorSearchPort,
    lexical_search: LexicalSearchPort,
    reranker: RerankerPort,
    reranker_enabled: bool,
    generation_provider: GenerationProviderPort,
    generation_model_alias: str,
    generation_fallback_alias: str | None,
    prompt_template: PromptTemplate,
    generation_evaluator: GenerationEvaluatorPort,
    evaluator_model_alias: str,
    tenant_id: UUID,
    knowledge_base_id: UUID,
    top_k: int,
    retrieval_minimum_score: float,
    context_token_budget: int,
) -> GenerationEvaluationReport:
    """Avalia a geração configurada (as portas recebidas, já apontando
    para o corpus de referência já indexado) contra `dataset`.

    Só considera os casos de `dataset.cases` com `expected_evidence`
    não vazio — mesmo filtro de `evaluate_retrieval`
    (`packages.evaluation.retrieval_evaluation`): uma "pergunta sem
    resposta" (RAG-060) não tem o que julgar por faithfulness/relevancy
    contra uma resposta esperada, já que ela não tem uma. Levanta
    `EmptyEvaluationError` se nenhum caso do dataset for respondível."""
    answerable_cases = [case for case in dataset.cases if case.expected_evidence]
    if not answerable_cases:
        raise EmptyEvaluationError(
            f"Dataset '{dataset.id}' versão '{dataset.version}' não tem nenhum "
            "caso com expected_evidence — nada para avaliar."
        )

    case_results: list[CaseGenerationResult] = []
    for case in answerable_cases:
        answer = await answer_query(
            knowledge_base_repository=knowledge_base_repository,
            document_repository=document_repository,
            query_repository=query_repository,
            embedding_provider=embedding_provider,
            vector_search=vector_search,
            lexical_search=lexical_search,
            reranker=reranker,
            reranker_enabled=reranker_enabled,
            generation_provider=generation_provider,
            generation_model_alias=generation_model_alias,
            generation_fallback_alias=generation_fallback_alias,
            prompt_template=prompt_template,
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base_id,
            query=case.question,
            top_k=top_k,
            filters=None,
            retrieval_minimum_score=retrieval_minimum_score,
            context_token_budget=context_token_budget,
            trace_id=uuid4(),
        )

        evaluation = await generation_evaluator.evaluate(
            question=case.question,
            answer=answer.answer,
            context=answer.context_chunk_contents,
        )

        case_results.append(
            CaseGenerationResult(
                case_id=case.id,
                grounded=answer.grounded,
                faithfulness=evaluation.faithfulness,
                answer_relevancy=evaluation.answer_relevancy,
                prompt_tokens=evaluation.prompt_tokens,
                completion_tokens=evaluation.completion_tokens,
                total_tokens=evaluation.total_tokens,
            )
        )

    return GenerationEvaluationReport(
        dataset_id=dataset.id,
        dataset_version=dataset.version,
        generation_model_alias=generation_model_alias,
        prompt_id=prompt_template.id,
        prompt_version=prompt_template.version,
        evaluator_model_alias=evaluator_model_alias,
        generated_at=datetime.now(UTC),
        faithfulness=_mean([result.faithfulness for result in case_results]),
        answer_relevancy=_mean([result.answer_relevancy for result in case_results]),
        total_prompt_tokens=sum(result.prompt_tokens for result in case_results),
        total_completion_tokens=sum(result.completion_tokens for result in case_results),
        case_results=tuple(case_results),
    )


def check_thresholds(
    report: GenerationEvaluationReport,
    *,
    minimum_faithfulness: float,
    minimum_answer_relevancy: float,
) -> ThresholdCheck:
    """Verifica `report` contra os limiares mínimos informados (seção
    21 do plano: "Faithfulness inicial igual ou superior a 0,85") —
    mesmo `ThresholdCheck` de `retrieval_evaluation`, reusado aqui
    porque o formato do resultado (passou/violações em texto) não muda
    entre avaliar retrieval ou geração, só o NOME das métricas checadas."""
    violations: list[str] = []
    if report.faithfulness < minimum_faithfulness:
        violations.append(
            f"Faithfulness {report.faithfulness:.4f} abaixo do limiar mínimo "
            f"{minimum_faithfulness:.4f}."
        )
    if report.answer_relevancy < minimum_answer_relevancy:
        violations.append(
            f"Answer relevancy {report.answer_relevancy:.4f} abaixo do limiar mínimo "
            f"{minimum_answer_relevancy:.4f}."
        )
    return ThresholdCheck(passed=not violations, violations=tuple(violations))
