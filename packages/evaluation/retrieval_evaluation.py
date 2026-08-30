"""Orquestração da avaliação de retrieval (RAG-061, seção 14/21 do
plano): roda `retrieve_evidence` (RAG-034) para cada caso respondível
do dataset dourado (RAG-060) e agrega Recall@K/MRR
(`packages.evaluation.retrieval_metrics`).

Este módulo NÃO indexa nenhum corpus (chunking, embeddings, popular
`VectorSearchPort`/`LexicalSearchPort`) — recebe as portas já
populadas, mesma pressuposição de `retrieve_evidence` em si. Quem monta
o corpus de referência e escolhe QUAIS adapters (fake em memória para
testes; `LiteLLMEmbeddingProvider`/`LiteLLMReranker` reais para uma
execução que produza números que signifiquem algo, seção 21 do plano:
"Recall@5 inicial igual ou superior a 0,80") é
`scripts/run_retrieval_evaluation.py` — mesma divisão de
responsabilidade de `apps/api/routers/retrieval.py` decidir os
adapters e `retrieve_evidence` só orquestrar as portas."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from packages.application.ports.embedding_provider import EmbeddingProviderPort
from packages.application.ports.knowledge_base_repository import KnowledgeBaseRepositoryPort
from packages.application.ports.lexical_search import LexicalSearchPort
from packages.application.ports.reranker import RerankerPort
from packages.application.ports.vector_search import VectorSearchPort
from packages.application.queries.retrieval import retrieve_evidence
from packages.evaluation import retrieval_metrics
from packages.evaluation.golden_dataset import GoldenDataset


class EmptyEvaluationError(ValueError):
    """`dataset` não tem nenhum caso respondível (`expected_evidence`
    não vazio) — um relatório sem nenhum caso avaliado não tem Recall@K/
    MRR bem definidos (ver `retrieval_metrics.mean`)."""


@dataclass(frozen=True, slots=True)
class CaseRetrievalResult:
    """Resultado da avaliação de retrieval para um `GoldenCase`."""

    case_id: str
    expected_evidence_count: int
    found_evidence_count: int
    retrieved_chunk_count: int
    recall_at_k: float
    reciprocal_rank: float


@dataclass(frozen=True, slots=True)
class RetrievalEvaluationReport:
    """Resultado agregado de uma execução de avaliação de retrieval —
    o que `scripts/run_retrieval_evaluation.py` serializa em JSON/
    Markdown (critério de aceite "relatório JSON e Markdown") e contra
    o que um limiar configurável é checado (`check_thresholds`,
    critério de aceite "falha por limiar configurável")."""

    dataset_id: str
    dataset_version: str
    k: int
    generated_at: datetime
    recall_at_k: float
    mrr: float
    case_results: tuple[CaseRetrievalResult, ...] = field(default_factory=tuple)

    @property
    def evaluated_case_count(self) -> int:
        return len(self.case_results)


async def evaluate_retrieval(
    *,
    dataset: GoldenDataset,
    knowledge_base_repository: KnowledgeBaseRepositoryPort,
    embedding_provider: EmbeddingProviderPort,
    vector_search: VectorSearchPort,
    lexical_search: LexicalSearchPort,
    reranker: RerankerPort,
    reranker_enabled: bool,
    tenant_id: UUID,
    knowledge_base_id: UUID,
    top_k: int,
) -> RetrievalEvaluationReport:
    """Avalia o retrieval configurado (as portas recebidas, já
    populadas com o corpus de referência) contra `dataset`.

    Só considera os casos de `dataset.cases` com `expected_evidence`
    não vazio — uma "pergunta sem resposta" (RAG-060) não tem evidência
    esperada para medir recall/MRR contra ela; essas perguntas
    verificam a recusa de geração (RAG-062/RAG-043), não a qualidade do
    retrieval. Levanta `EmptyEvaluationError` se nenhum caso do dataset
    tiver `expected_evidence` (dataset mal formado para este propósito —
    o schema, RAG-060, já garante um mínimo de 30 casos e ao menos um
    sem resposta, mas não garante ao menos um COM resposta)."""
    answerable_cases = [case for case in dataset.cases if case.expected_evidence]
    if not answerable_cases:
        raise EmptyEvaluationError(
            f"Dataset '{dataset.id}' versão '{dataset.version}' não tem nenhum "
            "caso com expected_evidence — nada para avaliar."
        )

    case_results: list[CaseRetrievalResult] = []
    for case in answerable_cases:
        retrieved = await retrieve_evidence(
            knowledge_base_repository=knowledge_base_repository,
            embedding_provider=embedding_provider,
            vector_search=vector_search,
            lexical_search=lexical_search,
            reranker=reranker,
            reranker_enabled=reranker_enabled,
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base_id,
            query=case.question,
            top_k=top_k,
        )
        retrieved_contents = [evidence.chunk.content for evidence in retrieved]

        case_results.append(
            CaseRetrievalResult(
                case_id=case.id,
                expected_evidence_count=len(case.expected_evidence),
                found_evidence_count=retrieval_metrics.count_found_evidence(
                    case.expected_evidence, retrieved_contents
                ),
                retrieved_chunk_count=len(retrieved_contents),
                recall_at_k=retrieval_metrics.recall_at_k(
                    case.expected_evidence, retrieved_contents, k=top_k
                ),
                reciprocal_rank=retrieval_metrics.reciprocal_rank(
                    case.expected_evidence, retrieved_contents
                ),
            )
        )

    return RetrievalEvaluationReport(
        dataset_id=dataset.id,
        dataset_version=dataset.version,
        k=top_k,
        generated_at=datetime.now(UTC),
        recall_at_k=retrieval_metrics.mean([result.recall_at_k for result in case_results]),
        mrr=retrieval_metrics.mean([result.reciprocal_rank for result in case_results]),
        case_results=tuple(case_results),
    )


@dataclass(frozen=True, slots=True)
class ThresholdCheck:
    """Resultado de checar `RetrievalEvaluationReport` contra limiares
    mínimos configuráveis (critério de aceite "falha por limiar
    configurável", seção 21 do plano: "Recall@5 ... 0,80", "MRR ...
    0,70" — os limiares em si nunca são hardcoded aqui, sempre
    parâmetros de quem chama)."""

    passed: bool
    violations: tuple[str, ...] = field(default_factory=tuple)


def check_thresholds(
    report: RetrievalEvaluationReport, *, minimum_recall_at_k: float, minimum_mrr: float
) -> ThresholdCheck:
    """Verifica `report` contra os limiares mínimos informados.
    `passed=False` sempre que pelo menos uma métrica ficou abaixo do
    seu limiar — `violations` descreve qual(is), em texto pronto para
    aparecer no relatório Markdown e na saída do script de avaliação."""
    violations: list[str] = []
    if report.recall_at_k < minimum_recall_at_k:
        violations.append(
            f"Recall@{report.k} {report.recall_at_k:.4f} abaixo do limiar mínimo "
            f"{minimum_recall_at_k:.4f}."
        )
    if report.mrr < minimum_mrr:
        violations.append(f"MRR {report.mrr:.4f} abaixo do limiar mínimo {minimum_mrr:.4f}.")
    return ThresholdCheck(passed=not violations, violations=tuple(violations))
