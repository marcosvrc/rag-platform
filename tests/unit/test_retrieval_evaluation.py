"""Testes de RAG-061: orquestração de avaliação de retrieval
(`packages/evaluation/retrieval_evaluation.py`) — usa as mesmas portas
fake em memória de todo o resto do projeto (`InMemoryVectorSearch`,
`InMemoryLexicalSearch`, `InMemoryKnowledgeBaseRepository`) e um
provedor de embeddings determinístico próprio deste teste (vetores
"one-hot" por tópico, ortogonais entre si) — dá controle total sobre
qual chunk cada pergunta recupera, sem depender de nenhum modelo real
nem de correspondência lexical (`InMemoryLexicalSearch` fica vazio em
todo teste deste módulo: só a busca vetorial participa da fusão RRF)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from adapters.knowledge_base_repository.in_memory import InMemoryKnowledgeBaseRepository
from adapters.lexical_search.in_memory import InMemoryLexicalSearch
from adapters.reranker.passthrough import PassthroughReranker
from adapters.vector_search.in_memory import InMemoryVectorSearch
from packages.application.ports.embedding_provider import EmbeddingProviderPort
from packages.domain.entities.chunk import Chunk
from packages.evaluation.golden_dataset import ExpectedEvidence, GoldenCase, GoldenDataset
from packages.evaluation.retrieval_evaluation import (
    EmptyEvaluationError,
    RetrievalEvaluationReport,
    ThresholdCheck,
    check_thresholds,
    evaluate_retrieval,
)

TENANT_ID = uuid4()

#: Quantos tópicos "perfeitos" (uma pergunta, um chunk, correspondência
#: exata) usar para completar o dataset até o mínimo de 30 casos
#: exigido pelo schema (RAG-060), sem afetar de forma imprevisível a
#: métrica agregada sob teste (todo caso de preenchimento sempre tem
#: recall_at_k=1.0 e reciprocal_rank=1.0, então a média é fácil de
#: calcular à mão).
_FILLER_TOPIC_COUNT = 29
_VECTOR_DIMENSIONS = _FILLER_TOPIC_COUNT + 1  # +1 dimensão nunca usada por nenhum chunk


def _one_hot(index: int) -> list[float]:
    vector = [0.0] * _VECTOR_DIMENSIONS
    vector[index] = 1.0
    return vector


class _FakeEmbeddingProvider(EmbeddingProviderPort):
    """Devolve sempre o mesmo vetor para o mesmo texto — um dicionário
    fixo, montado pelo teste, nunca um cálculo real."""

    def __init__(self, vector_by_text: dict[str, list[float]]) -> None:
        self._vector_by_text = vector_by_text

    async def embed(self, *, texts: list[str]) -> list[list[float]]:
        return [self._vector_by_text[text] for text in texts]


def _filler_content(index: int) -> str:
    return f"conteúdo do tópico {index} com marcador-{index}."


def _chunk(
    *, tenant_id: UUID, knowledge_base_id: UUID, content: str, embedding: list[float]
) -> Chunk:
    return Chunk(
        id=uuid4(),
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base_id,
        version_id=uuid4(),
        content=content,
        token_count=10,
        embedding=embedding,
    )


@pytest.fixture
def knowledge_base_repository() -> InMemoryKnowledgeBaseRepository:
    return InMemoryKnowledgeBaseRepository()


@pytest.fixture
def vector_search() -> InMemoryVectorSearch:
    return InMemoryVectorSearch()


@pytest.fixture
def lexical_search() -> InMemoryLexicalSearch:
    return InMemoryLexicalSearch()


async def _seed_knowledge_base(repository: InMemoryKnowledgeBaseRepository) -> UUID:
    knowledge_base = await repository.create(
        tenant_id=TENANT_ID, name="avaliação", description=None, config={}
    )
    return knowledge_base.id


def _build_dataset(
    *, special_cases: list[GoldenCase]
) -> tuple[GoldenDataset, dict[str, list[float]]]:
    """Monta um `GoldenDataset` válido (mínimo de 30 casos, ao menos um
    sem resposta) a partir de `special_cases` (o que o teste realmente
    quer verificar) mais casos de preenchimento "perfeitos"."""
    vector_by_text: dict[str, list[float]] = {}
    filler_cases: list[GoldenCase] = []

    for index in range(_FILLER_TOPIC_COUNT):
        marker = f"marcador-{index}"
        question = f"pergunta de preenchimento sobre {marker}"
        vector_by_text[question] = _one_hot(index)
        vector_by_text[_filler_content(index)] = _one_hot(index)
        filler_cases.append(
            GoldenCase(
                id=f"filler-{index}",
                question=question,
                expected_answer=f"resposta do tópico {index}.",
                expected_evidence=(ExpectedEvidence(document_id="doc", content_contains=marker),),
            )
        )

    unanswerable_case = GoldenCase(id="sem-resposta", question="pergunta fora do escopo")

    dataset = GoldenDataset(
        id="teste",
        version="v1",
        cases=tuple([*special_cases, *filler_cases, unanswerable_case]),
    )
    return dataset, vector_by_text


def _index_filler_chunks(
    vector_search: InMemoryVectorSearch,
    vector_by_text: dict[str, list[float]],
    *,
    tenant_id: UUID,
    knowledge_base_id: UUID,
) -> None:
    for index in range(_FILLER_TOPIC_COUNT):
        content = _filler_content(index)
        vector_search.index_chunk(
            _chunk(
                tenant_id=tenant_id,
                knowledge_base_id=knowledge_base_id,
                content=content,
                embedding=vector_by_text[content],
            )
        )


class TestEvaluateRetrieval:
    async def test_raises_for_a_dataset_with_no_answerable_case(
        self,
        knowledge_base_repository: InMemoryKnowledgeBaseRepository,
        vector_search: InMemoryVectorSearch,
        lexical_search: InMemoryLexicalSearch,
    ) -> None:
        knowledge_base_id = await _seed_knowledge_base(knowledge_base_repository)
        only_unanswerable = GoldenDataset(
            id="teste",
            version="v1",
            cases=tuple(
                GoldenCase(id=f"sem-resposta-{i}", question=f"pergunta {i}") for i in range(30)
            ),
        )

        with pytest.raises(EmptyEvaluationError):
            await evaluate_retrieval(
                dataset=only_unanswerable,
                knowledge_base_repository=knowledge_base_repository,
                embedding_provider=_FakeEmbeddingProvider({}),
                vector_search=vector_search,
                lexical_search=lexical_search,
                reranker=PassthroughReranker(),
                reranker_enabled=False,
                tenant_id=TENANT_ID,
                knowledge_base_id=knowledge_base_id,
                top_k=5,
            )

    async def test_perfect_match_case_gets_full_recall_and_reciprocal_rank(
        self,
        knowledge_base_repository: InMemoryKnowledgeBaseRepository,
        vector_search: InMemoryVectorSearch,
        lexical_search: InMemoryLexicalSearch,
    ) -> None:
        knowledge_base_id = await _seed_knowledge_base(knowledge_base_repository)
        dataset, vector_by_text = _build_dataset(special_cases=[])
        _index_filler_chunks(
            vector_search, vector_by_text, tenant_id=TENANT_ID, knowledge_base_id=knowledge_base_id
        )

        report = await evaluate_retrieval(
            dataset=dataset,
            knowledge_base_repository=knowledge_base_repository,
            embedding_provider=_FakeEmbeddingProvider(vector_by_text),
            vector_search=vector_search,
            lexical_search=lexical_search,
            reranker=PassthroughReranker(),
            reranker_enabled=False,
            tenant_id=TENANT_ID,
            knowledge_base_id=knowledge_base_id,
            top_k=5,
        )

        assert isinstance(report, RetrievalEvaluationReport)
        assert report.dataset_id == "teste"
        assert report.dataset_version == "v1"
        assert report.k == 5
        assert report.evaluated_case_count == _FILLER_TOPIC_COUNT
        assert report.recall_at_k == pytest.approx(1.0)
        assert report.mrr == pytest.approx(1.0)
        assert all(case.recall_at_k == pytest.approx(1.0) for case in report.case_results)
        assert all(case.reciprocal_rank == pytest.approx(1.0) for case in report.case_results)

    async def test_partial_recall_case_is_reflected_in_its_own_result_and_the_aggregate(
        self,
        knowledge_base_repository: InMemoryKnowledgeBaseRepository,
        vector_search: InMemoryVectorSearch,
        lexical_search: InMemoryLexicalSearch,
    ) -> None:
        knowledge_base_id = await _seed_knowledge_base(knowledge_base_repository)
        # Reusa o vetor do tópico 0 para uma pergunta com DUAS
        # evidências esperadas, das quais só uma existe no corpus
        # indexado.
        special_question = "pergunta especial com recall parcial"
        special_case = GoldenCase(
            id="parcial",
            question=special_question,
            expected_answer="resposta parcial.",
            expected_evidence=(
                ExpectedEvidence(document_id="doc", content_contains="marcador-0"),
                ExpectedEvidence(document_id="doc", content_contains="evidência-inexistente"),
            ),
        )
        dataset, vector_by_text = _build_dataset(special_cases=[special_case])
        vector_by_text[special_question] = _one_hot(0)
        _index_filler_chunks(
            vector_search, vector_by_text, tenant_id=TENANT_ID, knowledge_base_id=knowledge_base_id
        )

        report = await evaluate_retrieval(
            dataset=dataset,
            knowledge_base_repository=knowledge_base_repository,
            embedding_provider=_FakeEmbeddingProvider(vector_by_text),
            vector_search=vector_search,
            lexical_search=lexical_search,
            reranker=PassthroughReranker(),
            reranker_enabled=False,
            tenant_id=TENANT_ID,
            knowledge_base_id=knowledge_base_id,
            top_k=5,
        )

        assert report.evaluated_case_count == _FILLER_TOPIC_COUNT + 1
        special_result = next(case for case in report.case_results if case.case_id == "parcial")
        assert special_result.expected_evidence_count == 2
        assert special_result.found_evidence_count == 1
        assert special_result.recall_at_k == pytest.approx(0.5)
        assert special_result.reciprocal_rank == pytest.approx(1.0)

        expected_mean_recall = (_FILLER_TOPIC_COUNT * 1.0 + 0.5) / (_FILLER_TOPIC_COUNT + 1)
        assert report.recall_at_k == pytest.approx(expected_mean_recall)

    async def test_case_with_no_matching_chunk_gets_zero_recall_and_zero_reciprocal_rank(
        self,
        knowledge_base_repository: InMemoryKnowledgeBaseRepository,
        vector_search: InMemoryVectorSearch,
        lexical_search: InMemoryLexicalSearch,
    ) -> None:
        knowledge_base_id = await _seed_knowledge_base(knowledge_base_repository)
        special_question = "pergunta sem nenhum chunk correspondente"
        special_case = GoldenCase(
            id="sem-match",
            question=special_question,
            expected_answer="resposta inalcançável.",
            expected_evidence=(
                ExpectedEvidence(document_id="doc", content_contains="nunca-aparece-no-corpus"),
            ),
        )
        dataset, vector_by_text = _build_dataset(special_cases=[special_case])
        # Dimensão nunca usada por nenhum chunk indexado — similaridade
        # de cosseno zero contra todo o corpus.
        vector_by_text[special_question] = _one_hot(_FILLER_TOPIC_COUNT)
        _index_filler_chunks(
            vector_search, vector_by_text, tenant_id=TENANT_ID, knowledge_base_id=knowledge_base_id
        )

        report = await evaluate_retrieval(
            dataset=dataset,
            knowledge_base_repository=knowledge_base_repository,
            embedding_provider=_FakeEmbeddingProvider(vector_by_text),
            vector_search=vector_search,
            lexical_search=lexical_search,
            reranker=PassthroughReranker(),
            reranker_enabled=False,
            tenant_id=TENANT_ID,
            knowledge_base_id=knowledge_base_id,
            top_k=5,
        )

        special_result = next(case for case in report.case_results if case.case_id == "sem-match")
        assert special_result.found_evidence_count == 0
        assert special_result.recall_at_k == pytest.approx(0.0)
        assert special_result.reciprocal_rank == pytest.approx(0.0)
        assert special_result.retrieved_chunk_count == 5


def _report(*, recall_at_k: float, mrr: float) -> RetrievalEvaluationReport:
    return RetrievalEvaluationReport(
        dataset_id="teste",
        dataset_version="v1",
        k=5,
        generated_at=datetime.now(UTC),
        recall_at_k=recall_at_k,
        mrr=mrr,
        case_results=(),
    )


class TestCheckThresholds:
    def test_passes_when_both_metrics_meet_the_threshold(self) -> None:
        result = check_thresholds(
            _report(recall_at_k=0.9, mrr=0.8), minimum_recall_at_k=0.8, minimum_mrr=0.7
        )
        assert result == ThresholdCheck(passed=True, violations=())

    def test_fails_and_reports_a_recall_violation(self) -> None:
        result = check_thresholds(
            _report(recall_at_k=0.5, mrr=0.8), minimum_recall_at_k=0.8, minimum_mrr=0.7
        )
        assert result.passed is False
        assert len(result.violations) == 1
        assert "Recall@5" in result.violations[0]

    def test_fails_and_reports_an_mrr_violation(self) -> None:
        result = check_thresholds(
            _report(recall_at_k=0.9, mrr=0.5), minimum_recall_at_k=0.8, minimum_mrr=0.7
        )
        assert result.passed is False
        assert len(result.violations) == 1
        assert "MRR" in result.violations[0]

    def test_fails_and_reports_both_violations(self) -> None:
        result = check_thresholds(
            _report(recall_at_k=0.1, mrr=0.1), minimum_recall_at_k=0.8, minimum_mrr=0.7
        )
        assert result.passed is False
        assert len(result.violations) == 2

    def test_exactly_at_the_threshold_passes(self) -> None:
        result = check_thresholds(
            _report(recall_at_k=0.8, mrr=0.7), minimum_recall_at_k=0.8, minimum_mrr=0.7
        )
        assert result.passed is True
