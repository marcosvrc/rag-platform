"""Testes de RAG-062: orquestração de avaliação de geração
(`packages/evaluation/generation_evaluation.py`) — usa as mesmas portas
fake em memória de todo o resto do projeto (mesmo padrão de
`test_query_command.py`, RAG-044) mais um avaliador determinístico
próprio deste teste (devolve scores pré-configurados, um por chamada,
na ordem em que `evaluate_generation` processa os casos)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from adapters.document_repository.in_memory import InMemoryDocumentRepository
from adapters.knowledge_base_repository.in_memory import InMemoryKnowledgeBaseRepository
from adapters.lexical_search.in_memory import InMemoryLexicalSearch
from adapters.query_repository.in_memory import InMemoryQueryRepository
from adapters.reranker.passthrough import PassthroughReranker
from adapters.vector_search.in_memory import InMemoryVectorSearch
from packages.application.ports.embedding_provider import EmbeddingProviderPort
from packages.application.ports.generation_evaluator import (
    GenerationEvaluationResult,
    GenerationEvaluatorPort,
)
from packages.application.ports.generation_provider import GenerationProviderPort, GenerationResult
from packages.domain.entities.chunk import Chunk
from packages.evaluation.generation_evaluation import (
    EmptyEvaluationError,
    GenerationEvaluationReport,
    ThresholdCheck,
    _mean,
    check_thresholds,
    evaluate_generation,
)
from packages.evaluation.golden_dataset import ExpectedEvidence, GoldenCase, GoldenDataset
from packages.generation.prompts import get_default_answer_prompt

TENANT_ID = uuid4()
_CORPUS_CONTENT = (
    "A arquitetura do sistema é hexagonal, com marcador-1 e marcador-2 no mesmo trecho."
)
_CHUNK_ID_PATTERN = re.compile(r"\[([0-9a-fA-F-]{36})\]")
_GENERATION_ALIAS = "generation-model-alias"
_EVALUATOR_ALIAS = "generation-evaluator-model-alias"

#: Quantos casos de preenchimento SEM RESPOSTA usar para completar o
#: dataset até o mínimo de 30 casos exigido pelo schema (RAG-060) —
#: `evaluate_generation` nem chega a olhar para eles (só considera
#: casos com `expected_evidence`), então não precisam de nenhum chunk
#: real por trás, ao contrário dos casos de preenchimento de
#: `test_retrieval_evaluation.py` (RAG-061), que TINHAM que ser
#: respondíveis para contar na métrica.
_UNANSWERABLE_FILLER_COUNT = 28


class _FakeEmbeddingProvider(EmbeddingProviderPort):
    """Vetor constante para todo texto — só existe um chunk no corpus
    deste teste, então não há necessidade de distinguir embeddings por
    tópico (compare com o truque "one-hot" de
    `test_retrieval_evaluation.py`, desnecessário aqui)."""

    async def embed(self, *, texts: list[str]) -> list[list[float]]:
        return [[1.0] for _ in texts]


class _EchoingGenerationProvider(GenerationProviderPort):
    """Simula um modelo que "lê" o contexto: devolve uma resposta que
    cita o primeiro `chunk_id` encontrado no prompt recebido — produz
    uma resposta sempre `grounded=True` sem precisar saber de antemão
    qual chunk foi recuperado."""

    async def generate(self, *, prompt: str) -> GenerationResult:
        match = _CHUNK_ID_PATTERN.search(prompt)
        chunk_id = match.group(1) if match else "00000000-0000-0000-0000-000000000000"
        return GenerationResult(
            content=f"A arquitetura é hexagonal [{chunk_id}].",
            used_fallback=False,
            prompt_tokens=50,
            completion_tokens=10,
            total_tokens=60,
        )


class _FakeGenerationEvaluator(GenerationEvaluatorPort):
    """Devolve um score pré-configurado por chamada, na ordem
    recebida — nunca calcula nada de verdade."""

    def __init__(self, scores: list[tuple[float, float, int, int]]) -> None:
        self._scores = list(scores)
        self.received: list[tuple[str, str, tuple[str, ...]]] = []

    async def evaluate(
        self, *, question: str, answer: str, context: Sequence[str]
    ) -> GenerationEvaluationResult:
        self.received.append((question, answer, tuple(context)))
        faithfulness, answer_relevancy, prompt_tokens, completion_tokens = self._scores.pop(0)
        return GenerationEvaluationResult(
            faithfulness=faithfulness,
            answer_relevancy=answer_relevancy,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )


def _build_dataset() -> GoldenDataset:
    case_1 = GoldenCase(
        id="c1",
        question="pergunta sobre o marcador um",
        expected_answer="resposta do marcador um.",
        expected_evidence=(ExpectedEvidence(document_id="doc", content_contains="marcador-1"),),
    )
    case_2 = GoldenCase(
        id="c2",
        question="pergunta sobre o marcador dois",
        expected_answer="resposta do marcador dois.",
        expected_evidence=(ExpectedEvidence(document_id="doc", content_contains="marcador-2"),),
    )
    filler_cases = [
        GoldenCase(id=f"filler-{i}", question=f"pergunta de preenchimento {i}")
        for i in range(_UNANSWERABLE_FILLER_COUNT)
    ]
    return GoldenDataset(id="teste", version="v1", cases=tuple([case_1, case_2, *filler_cases]))


@pytest.fixture
def knowledge_base_repository() -> InMemoryKnowledgeBaseRepository:
    return InMemoryKnowledgeBaseRepository()


@pytest.fixture
def document_repository() -> InMemoryDocumentRepository:
    return InMemoryDocumentRepository()


@pytest.fixture
def query_repository() -> InMemoryQueryRepository:
    return InMemoryQueryRepository()


@pytest.fixture
def vector_search() -> InMemoryVectorSearch:
    return InMemoryVectorSearch()


@pytest.fixture
def lexical_search() -> InMemoryLexicalSearch:
    return InMemoryLexicalSearch()


async def _seed_knowledge_base_and_corpus(
    *,
    knowledge_base_repository: InMemoryKnowledgeBaseRepository,
    document_repository: InMemoryDocumentRepository,
    vector_search: InMemoryVectorSearch,
) -> UUID:
    knowledge_base = await knowledge_base_repository.create(
        tenant_id=TENANT_ID, name="avaliação", description=None, config={}
    )
    upload = await document_repository.create_document(
        tenant_id=TENANT_ID,
        knowledge_base_id=knowledge_base.id,
        name="doc.md",
        mime_type="text/markdown",
        checksum=uuid4().hex,
        object_key="doc.md",
        idempotency_key=None,
    )
    chunk = Chunk(
        id=uuid4(),
        tenant_id=TENANT_ID,
        knowledge_base_id=knowledge_base.id,
        version_id=upload.version.id,
        content=_CORPUS_CONTENT,
        token_count=15,
        embedding=[1.0],
    )
    await document_repository.persist_chunks_and_activate_version(
        document_id=upload.document.id,
        version_id=upload.version.id,
        extracted_object_key="doc.md.extracted.txt",
        chunks=[chunk],
    )
    vector_search.index_chunk(chunk)
    return knowledge_base.id


class TestEvaluateGeneration:
    async def test_raises_for_a_dataset_with_no_answerable_case(
        self,
        knowledge_base_repository: InMemoryKnowledgeBaseRepository,
        document_repository: InMemoryDocumentRepository,
        query_repository: InMemoryQueryRepository,
        vector_search: InMemoryVectorSearch,
        lexical_search: InMemoryLexicalSearch,
    ) -> None:
        knowledge_base_id = await _seed_knowledge_base_and_corpus(
            knowledge_base_repository=knowledge_base_repository,
            document_repository=document_repository,
            vector_search=vector_search,
        )
        only_unanswerable = GoldenDataset(
            id="teste",
            version="v1",
            cases=tuple(GoldenCase(id=f"f-{i}", question=f"pergunta {i}") for i in range(30)),
        )

        with pytest.raises(EmptyEvaluationError):
            await evaluate_generation(
                dataset=only_unanswerable,
                knowledge_base_repository=knowledge_base_repository,
                document_repository=document_repository,
                query_repository=query_repository,
                embedding_provider=_FakeEmbeddingProvider(),
                vector_search=vector_search,
                lexical_search=lexical_search,
                reranker=PassthroughReranker(),
                reranker_enabled=False,
                generation_provider=_EchoingGenerationProvider(),
                generation_model_alias=_GENERATION_ALIAS,
                generation_fallback_alias=None,
                prompt_template=get_default_answer_prompt(),
                generation_evaluator=_FakeGenerationEvaluator([]),
                evaluator_model_alias=_EVALUATOR_ALIAS,
                tenant_id=TENANT_ID,
                knowledge_base_id=knowledge_base_id,
                top_k=5,
                retrieval_minimum_score=0.0,
                context_token_budget=3000,
            )

    async def test_runs_answer_query_and_judges_each_answerable_case(
        self,
        knowledge_base_repository: InMemoryKnowledgeBaseRepository,
        document_repository: InMemoryDocumentRepository,
        query_repository: InMemoryQueryRepository,
        vector_search: InMemoryVectorSearch,
        lexical_search: InMemoryLexicalSearch,
    ) -> None:
        knowledge_base_id = await _seed_knowledge_base_and_corpus(
            knowledge_base_repository=knowledge_base_repository,
            document_repository=document_repository,
            vector_search=vector_search,
        )
        prompt_template = get_default_answer_prompt()
        evaluator = _FakeGenerationEvaluator([(0.9, 0.8, 20, 5), (0.7, 0.6, 30, 7)])

        report = await evaluate_generation(
            dataset=_build_dataset(),
            knowledge_base_repository=knowledge_base_repository,
            document_repository=document_repository,
            query_repository=query_repository,
            embedding_provider=_FakeEmbeddingProvider(),
            vector_search=vector_search,
            lexical_search=lexical_search,
            reranker=PassthroughReranker(),
            reranker_enabled=False,
            generation_provider=_EchoingGenerationProvider(),
            generation_model_alias=_GENERATION_ALIAS,
            generation_fallback_alias=None,
            prompt_template=prompt_template,
            generation_evaluator=evaluator,
            evaluator_model_alias=_EVALUATOR_ALIAS,
            tenant_id=TENANT_ID,
            knowledge_base_id=knowledge_base_id,
            top_k=5,
            retrieval_minimum_score=0.0,
            context_token_budget=3000,
        )

        assert isinstance(report, GenerationEvaluationReport)
        assert report.dataset_id == "teste"
        assert report.dataset_version == "v1"
        assert report.generation_model_alias == _GENERATION_ALIAS
        assert report.prompt_id == prompt_template.id
        assert report.prompt_version == prompt_template.version
        assert report.evaluator_model_alias == _EVALUATOR_ALIAS
        assert report.evaluated_case_count == 2

        assert [case.case_id for case in report.case_results] == ["c1", "c2"]
        assert all(case.grounded for case in report.case_results)
        assert report.case_results[0].faithfulness == pytest.approx(0.9)
        assert report.case_results[0].answer_relevancy == pytest.approx(0.8)
        assert report.case_results[1].faithfulness == pytest.approx(0.7)
        assert report.case_results[1].answer_relevancy == pytest.approx(0.6)

        assert report.faithfulness == pytest.approx((0.9 + 0.7) / 2)
        assert report.answer_relevancy == pytest.approx((0.8 + 0.6) / 2)
        assert report.total_prompt_tokens == 50
        assert report.total_completion_tokens == 12

        # O avaliador recebeu a pergunta original e o contexto que de
        # fato entrou no prompt de geração (RAG-044/062).
        first_question, first_answer, first_context = evaluator.received[0]
        assert first_question == "pergunta sobre o marcador um"
        assert first_answer.startswith("A arquitetura é hexagonal")
        assert first_context == (_CORPUS_CONTENT,)

    async def test_max_cases_limits_how_many_answerable_cases_are_processed(
        self,
        knowledge_base_repository: InMemoryKnowledgeBaseRepository,
        document_repository: InMemoryDocumentRepository,
        query_repository: InMemoryQueryRepository,
        vector_search: InMemoryVectorSearch,
        lexical_search: InMemoryLexicalSearch,
    ) -> None:
        knowledge_base_id = await _seed_knowledge_base_and_corpus(
            knowledge_base_repository=knowledge_base_repository,
            document_repository=document_repository,
            vector_search=vector_search,
        )
        # Só um score configurado: se max_cases não limitasse a "c2"
        # (a segunda pergunta respondível), o avaliador fake estouraria
        # ao tentar dar `pop(0)` numa lista vazia.
        evaluator = _FakeGenerationEvaluator([(0.9, 0.8, 20, 5)])

        report = await evaluate_generation(
            dataset=_build_dataset(),
            knowledge_base_repository=knowledge_base_repository,
            document_repository=document_repository,
            query_repository=query_repository,
            embedding_provider=_FakeEmbeddingProvider(),
            vector_search=vector_search,
            lexical_search=lexical_search,
            reranker=PassthroughReranker(),
            reranker_enabled=False,
            generation_provider=_EchoingGenerationProvider(),
            generation_model_alias=_GENERATION_ALIAS,
            generation_fallback_alias=None,
            prompt_template=get_default_answer_prompt(),
            generation_evaluator=evaluator,
            evaluator_model_alias=_EVALUATOR_ALIAS,
            tenant_id=TENANT_ID,
            knowledge_base_id=knowledge_base_id,
            top_k=5,
            retrieval_minimum_score=0.0,
            context_token_budget=3000,
            max_cases=1,
        )

        assert report.evaluated_case_count == 1
        assert [case.case_id for case in report.case_results] == ["c1"]


def _report(*, faithfulness: float, answer_relevancy: float) -> GenerationEvaluationReport:
    return GenerationEvaluationReport(
        dataset_id="teste",
        dataset_version="v1",
        generation_model_alias=_GENERATION_ALIAS,
        prompt_id="answer",
        prompt_version="v1",
        evaluator_model_alias=_EVALUATOR_ALIAS,
        generated_at=datetime.now(UTC),
        faithfulness=faithfulness,
        answer_relevancy=answer_relevancy,
        total_prompt_tokens=0,
        total_completion_tokens=0,
        case_results=(),
    )


class TestMean:
    def test_raises_for_an_empty_sequence(self) -> None:
        with pytest.raises(ValueError, match="values não pode ser vazio"):
            _mean([])

    def test_computes_the_arithmetic_mean(self) -> None:
        assert _mean([1.0, 0.5, 0.0]) == pytest.approx(0.5)


class TestCheckThresholds:
    def test_passes_when_both_metrics_meet_the_threshold(self) -> None:
        result = check_thresholds(
            _report(faithfulness=0.9, answer_relevancy=0.9),
            minimum_faithfulness=0.85,
            minimum_answer_relevancy=0.85,
        )
        assert result == ThresholdCheck(passed=True, violations=())

    def test_fails_and_reports_a_faithfulness_violation(self) -> None:
        result = check_thresholds(
            _report(faithfulness=0.5, answer_relevancy=0.9),
            minimum_faithfulness=0.85,
            minimum_answer_relevancy=0.85,
        )
        assert result.passed is False
        assert len(result.violations) == 1
        assert "Faithfulness" in result.violations[0]

    def test_fails_and_reports_an_answer_relevancy_violation(self) -> None:
        result = check_thresholds(
            _report(faithfulness=0.9, answer_relevancy=0.5),
            minimum_faithfulness=0.85,
            minimum_answer_relevancy=0.85,
        )
        assert result.passed is False
        assert len(result.violations) == 1
        assert "Answer relevancy" in result.violations[0]

    def test_fails_and_reports_both_violations(self) -> None:
        result = check_thresholds(
            _report(faithfulness=0.1, answer_relevancy=0.1),
            minimum_faithfulness=0.85,
            minimum_answer_relevancy=0.85,
        )
        assert result.passed is False
        assert len(result.violations) == 2

    def test_exactly_at_the_threshold_passes(self) -> None:
        result = check_thresholds(
            _report(faithfulness=0.85, answer_relevancy=0.85),
            minimum_faithfulness=0.85,
            minimum_answer_relevancy=0.85,
        )
        assert result.passed is True
