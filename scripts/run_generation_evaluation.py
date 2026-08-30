"""Script de avaliação de geração (RAG-062, seção 14/21 do plano):
indexa o corpus de referência, roda `evaluate_generation` contra o
dataset dourado (RAG-060) — que por sua vez roda `answer_query`
(RAG-044) e julga cada resposta com o modelo-juiz — e grava relatórios
JSON/Markdown, falhando (código de saída 1) quando faithfulness/answer
relevancy ficam abaixo de limiares configuráveis.

## Por que este script (e não um teste unitário) usa modelos reais

`packages.evaluation.generation_evaluation` é testado em
`tests/unit/test_generation_evaluation.py` com portas fake em memória,
um provedor de geração determinístico e um avaliador determinístico
próprios do teste — isso prova que a ORQUESTRAÇÃO está correta, mas não
diz nada sobre a QUALIDADE real da geração (seção 21 do plano fixa
metas concretas: "Faithfulness inicial igual ou superior a 0,85").
Por isso este script sempre usa os adapters LiteLLM reais (embeddings,
geração e avaliação), exigindo um gateway alcançável — nunca roda como
parte de `pytest tests/unit` (seção 15 do plano: "chamadas reais ficam
em workflow manual ou agendado com orçamento limitado"). Rodar isso de
verdade acontece no quality gate de CI (RAG-073,
`.github/workflows/rag-quality-gate.yml`, com `--max-cases` reduzindo o
orçamento de chamadas reais) ou por execução manual.

## Corpus de referência

Mesmo README.md do script de avaliação de retrieval
(`scripts/run_retrieval_evaluation.py`, RAG-061) — mas aqui os chunks
são persistidos através de `InMemoryDocumentRepository` (não só
indexados em `VectorSearchPort`/`LexicalSearchPort`): `answer_query`
(RAG-044) resolve `document_id`/`document_name` de cada citação via
`DocumentRepositoryPort.get_documents_by_chunk_ids`, então o documento
de origem precisa existir de verdade nessa porta, não só o chunk bruto.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import UUID, uuid4

from adapters.document_repository.in_memory import InMemoryDocumentRepository
from adapters.knowledge_base_repository.in_memory import InMemoryKnowledgeBaseRepository
from adapters.lexical_search.in_memory import InMemoryLexicalSearch
from adapters.litellm.embedding_provider import LiteLLMEmbeddingProvider
from adapters.litellm.generation_evaluator import LiteLLMGenerationEvaluator
from adapters.litellm.generation_provider import LiteLLMGenerationProvider
from adapters.query_repository.in_memory import InMemoryQueryRepository
from adapters.reranker.litellm import LiteLLMReranker
from adapters.reranker.passthrough import PassthroughReranker
from adapters.vector_search.in_memory import InMemoryVectorSearch
from packages.application.ports.embedding_provider import EmbeddingProviderPort
from packages.application.ports.reranker import RerankerPort
from packages.config.models import (
    get_default_generation_evaluator_model,
    get_default_generation_fallback_model,
    get_default_generation_model,
)
from packages.config.settings import Settings, get_settings
from packages.domain.entities.chunk import Chunk
from packages.evaluation import generation_report
from packages.evaluation.generation_evaluation import check_thresholds, evaluate_generation
from packages.evaluation.golden_dataset import load_golden_dataset
from packages.generation.prompts import get_default_answer_prompt
from packages.ingestion.chunking import chunk_document

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CORPUS_PATH = _REPO_ROOT / "README.md"
_DEFAULT_OUTPUT_DIR = _REPO_ROOT / "reports" / "generation-evaluation"

#: Seção 21 do plano ("Requisitos de desempenho iniciais") — defaults
#: só deste script; `generation_evaluation.check_thresholds` não
#: conhece nenhum valor de negócio hardcoded, só recebe limiares de
#: quem chama. O plano não fixa uma meta de answer relevancy — reusa o
#: mesmo default de faithfulness (0,85) até uma meta própria existir.
_DEFAULT_MINIMUM_FAITHFULNESS = 0.85
_DEFAULT_MINIMUM_ANSWER_RELEVANCY = 0.85
_DEFAULT_TOP_K = 5


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Avalia faithfulness/answer relevancy da geração (RAG-062)."
    )
    parser.add_argument("--dataset-id", default="golden")
    parser.add_argument("--dataset-version", default="v1")
    parser.add_argument("--corpus-path", type=Path, default=_DEFAULT_CORPUS_PATH)
    parser.add_argument("--output-dir", type=Path, default=_DEFAULT_OUTPUT_DIR)
    parser.add_argument("--top-k", type=int, default=_DEFAULT_TOP_K)
    parser.add_argument("--retrieval-minimum-score", type=float, default=0.0)
    parser.add_argument("--context-token-budget", type=int, default=3000)
    parser.add_argument("--minimum-faithfulness", type=float, default=_DEFAULT_MINIMUM_FAITHFULNESS)
    parser.add_argument(
        "--minimum-answer-relevancy", type=float, default=_DEFAULT_MINIMUM_ANSWER_RELEVANCY
    )
    parser.add_argument(
        "--max-cases",
        type=int,
        default=None,
        help=(
            "Avalia só os N primeiros casos respondíveis do dataset, não o dataset inteiro "
            "(RAG-073: 'avaliação reduzida' do quality gate de CI). Default: sem limite."
        ),
    )
    parser.add_argument(
        "--reranker",
        choices=("passthrough", "litellm"),
        default="passthrough",
        help=(
            "'litellm' exige um gateway LiteLLM de reranking alcançável; 'passthrough' "
            "(default) avalia sem reranking."
        ),
    )
    parser.add_argument(
        "--generation-fallback-enabled",
        action="store_true",
        help="Habilita o alias de geração de contingência (mesmo padrão de produção, RAG-042).",
    )
    return parser.parse_args(argv)


async def _index_corpus(
    *,
    corpus_path: Path,
    tenant_id: UUID,
    knowledge_base_id: UUID,
    embedding_provider: EmbeddingProviderPort,
    document_repository: InMemoryDocumentRepository,
    vector_search: InMemoryVectorSearch,
    lexical_search: InMemoryLexicalSearch,
) -> int:
    """Divide `corpus_path` em chunks (RAG-024), persiste o documento
    de origem (`InMemoryDocumentRepository`, para que `answer_query`
    resolva citações) e indexa os chunks nas portas de busca em
    memória. Devolve quantos chunks foram indexados."""
    markdown = corpus_path.read_text(encoding="utf-8")
    drafts = chunk_document(markdown, title=corpus_path.name, origin=corpus_path.name)
    if not drafts:
        raise ValueError(f"{corpus_path}: nenhum chunk gerado (documento vazio?).")

    upload = await document_repository.create_document(
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base_id,
        name=corpus_path.name,
        mime_type="text/markdown",
        checksum=uuid4().hex,
        object_key=f"evaluation/{corpus_path.name}",
        idempotency_key=None,
    )

    embeddings = await embedding_provider.embed(texts=[draft.content for draft in drafts])
    chunks = [
        Chunk(
            id=uuid4(),
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base_id,
            version_id=upload.version.id,
            content=draft.content,
            token_count=draft.token_count,
            page=draft.page,
            section=draft.section,
            metadata=draft.metadata,
            embedding=embedding,
        )
        for draft, embedding in zip(drafts, embeddings, strict=True)
    ]

    await document_repository.persist_chunks_and_activate_version(
        document_id=upload.document.id,
        version_id=upload.version.id,
        extracted_object_key=f"evaluation/{corpus_path.name}.extracted.txt",
        chunks=chunks,
    )
    for chunk in chunks:
        vector_search.index_chunk(chunk)
        lexical_search.index_chunk(chunk)
    return len(chunks)


def _build_reranker(settings: Settings, *, kind: str) -> RerankerPort:
    if kind == "litellm":
        return LiteLLMReranker(settings)
    return PassthroughReranker()


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    dataset = load_golden_dataset(args.dataset_id, args.dataset_version)

    tenant_id = uuid4()
    knowledge_base_repository = InMemoryKnowledgeBaseRepository()
    knowledge_base = await knowledge_base_repository.create(
        tenant_id=tenant_id, name="generation-evaluation", description=None, config={}
    )
    document_repository = InMemoryDocumentRepository()
    query_repository = InMemoryQueryRepository()
    vector_search = InMemoryVectorSearch()
    lexical_search = InMemoryLexicalSearch()
    embedding_provider = LiteLLMEmbeddingProvider(settings)

    indexed_count = await _index_corpus(
        corpus_path=args.corpus_path,
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base.id,
        embedding_provider=embedding_provider,
        document_repository=document_repository,
        vector_search=vector_search,
        lexical_search=lexical_search,
    )
    print(f"Corpus indexado: {indexed_count} chunk(s) de {args.corpus_path}.")

    generation_provider = LiteLLMGenerationProvider(settings)
    generation_evaluator = LiteLLMGenerationEvaluator(settings)
    generation_model_alias = get_default_generation_model().alias
    generation_fallback_alias = (
        get_default_generation_fallback_model().alias if args.generation_fallback_enabled else None
    )
    evaluator_model_alias = get_default_generation_evaluator_model().alias

    report = await evaluate_generation(
        dataset=dataset,
        knowledge_base_repository=knowledge_base_repository,
        document_repository=document_repository,
        query_repository=query_repository,
        embedding_provider=embedding_provider,
        vector_search=vector_search,
        lexical_search=lexical_search,
        reranker=_build_reranker(settings, kind=args.reranker),
        reranker_enabled=args.reranker == "litellm",
        generation_provider=generation_provider,
        generation_model_alias=generation_model_alias,
        generation_fallback_alias=generation_fallback_alias,
        prompt_template=get_default_answer_prompt(),
        generation_evaluator=generation_evaluator,
        evaluator_model_alias=evaluator_model_alias,
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base.id,
        top_k=args.top_k,
        retrieval_minimum_score=args.retrieval_minimum_score,
        context_token_budget=args.context_token_budget,
        max_cases=args.max_cases,
    )

    threshold_check = check_thresholds(
        report,
        minimum_faithfulness=args.minimum_faithfulness,
        minimum_answer_relevancy=args.minimum_answer_relevancy,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "generation-evaluation.json"
    markdown_path = args.output_dir / "generation-evaluation.md"
    json_path.write_text(generation_report.render_json(report), encoding="utf-8")
    markdown_path.write_text(
        generation_report.render_markdown(report, threshold_check=threshold_check),
        encoding="utf-8",
    )

    print(
        f"Faithfulness: {report.faithfulness:.4f} | Answer relevancy: {report.answer_relevancy:.4f}"
    )
    print(f"Relatórios gravados em {json_path} e {markdown_path}.")

    if not threshold_check.passed:
        print("FALHOU nos limiares configurados:", file=sys.stderr)
        for violation in threshold_check.violations:
            print(f"  - {violation}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
