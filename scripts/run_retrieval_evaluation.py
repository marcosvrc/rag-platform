"""Script de avaliação de retrieval (RAG-061, seção 14/21 do plano):
indexa o corpus de referência, roda `evaluate_retrieval` contra o
dataset dourado (RAG-060) e grava relatórios JSON/Markdown, falhando
(código de saída 1) quando Recall@K/MRR ficam abaixo de limiares
configuráveis.

## Por que este script (e não um teste unitário) usa embeddings reais

`packages.evaluation.retrieval_evaluation` é testado em
`tests/unit/test_retrieval_evaluation.py` com portas fake em memória e
um provedor de embeddings determinístico — isso prova que a
ORQUESTRAÇÃO está correta (agrega Recall@K/MRR certo para um cenário
conhecido), mas não diz nada sobre a QUALIDADE real do retrieval (o
critério de aceite da RAG-061 é "execução reproduzível", e a seção 21
do plano fixa metas concretas: "Recall@5 inicial igual ou superior a
0,80", "MRR inicial igual ou superior a 0,70" — números que só um
embedding de verdade pode produzir). Por isso este script sempre usa
`LiteLLMEmbeddingProvider`, exigindo um gateway LiteLLM alcançável
(mesma configuração de produção, `packages.config.settings.Settings`) —
nunca roda como parte de `pytest tests/unit` (seção 15 do plano:
"Provedores de LLM devem ser simulados no CI comum. Chamadas reais
ficam em workflow manual ou agendado com orçamento limitado"). Rodar
isso de verdade acontece no quality gate de CI (RAG-073,
`.github/workflows/rag-quality-gate.yml`, com `--max-cases` reduzindo o
orçamento de chamadas reais) ou por execução manual.

## Corpus de referência

Reusa o mesmo README.md contra o qual o dataset dourado (RAG-060) foi
curado (ver comentário em `datasets/golden/golden.v1.yaml`) — chunkado
com a mesma configuração determinística de produção (RAG-024,
`chunk_document` com os defaults da seção 11.1), sem persistir nada em
Postgres/MinIO: os chunks resultantes só populam as portas de busca em
memória (`InMemoryVectorSearch`/`InMemoryLexicalSearch`) desta execução.

## Reranking

Desativado por padrão (`--reranker passthrough`) — avalia a fusão RRF
vetorial+lexical (RAG-030/031/032) sem depender de um segundo gateway
de rede; passe `--reranker litellm` para incluir reranking real
(RAG-033) e refletir a configuração completa de produção quando
`Settings.reranker_enabled` estiver ligado.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import UUID, uuid4

from adapters.knowledge_base_repository.in_memory import InMemoryKnowledgeBaseRepository
from adapters.lexical_search.in_memory import InMemoryLexicalSearch
from adapters.litellm.embedding_provider import LiteLLMEmbeddingProvider
from adapters.reranker.litellm import LiteLLMReranker
from adapters.reranker.passthrough import PassthroughReranker
from adapters.vector_search.in_memory import InMemoryVectorSearch
from packages.application.ports.embedding_provider import EmbeddingProviderPort
from packages.application.ports.reranker import RerankerPort
from packages.config.settings import Settings, get_settings
from packages.domain.entities.chunk import Chunk
from packages.evaluation import retrieval_report
from packages.evaluation.golden_dataset import load_golden_dataset
from packages.evaluation.retrieval_evaluation import check_thresholds, evaluate_retrieval
from packages.ingestion.chunking import chunk_document

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CORPUS_PATH = _REPO_ROOT / "README.md"
_DEFAULT_OUTPUT_DIR = _REPO_ROOT / "reports" / "retrieval-evaluation"

#: Seção 21 do plano ("Requisitos de desempenho iniciais") — defaults
#: só deste script; `retrieval_evaluation.check_thresholds` não conhece
#: nenhum valor de negócio hardcoded, só recebe limiares de quem chama.
_DEFAULT_MINIMUM_RECALL_AT_K = 0.80
_DEFAULT_MINIMUM_MRR = 0.70
_DEFAULT_TOP_K = 5


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Avalia Recall@K/MRR do retrieval contra o dataset dourado (RAG-061)."
    )
    parser.add_argument("--dataset-id", default="golden")
    parser.add_argument("--dataset-version", default="v1")
    parser.add_argument("--corpus-path", type=Path, default=_DEFAULT_CORPUS_PATH)
    parser.add_argument("--output-dir", type=Path, default=_DEFAULT_OUTPUT_DIR)
    parser.add_argument("--top-k", type=int, default=_DEFAULT_TOP_K)
    parser.add_argument("--minimum-recall-at-k", type=float, default=_DEFAULT_MINIMUM_RECALL_AT_K)
    parser.add_argument("--minimum-mrr", type=float, default=_DEFAULT_MINIMUM_MRR)
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
            "(default) avalia só a fusão RRF vetorial+lexical, sem reranking."
        ),
    )
    return parser.parse_args(argv)


async def _index_corpus(
    *,
    corpus_path: Path,
    tenant_id: UUID,
    knowledge_base_id: UUID,
    embedding_provider: EmbeddingProviderPort,
    vector_search: InMemoryVectorSearch,
    lexical_search: InMemoryLexicalSearch,
) -> int:
    """Divide `corpus_path` em chunks (RAG-024) e os indexa nas portas
    em memória. Devolve quantos chunks foram indexados; levanta
    `ValueError` se o corpus não gerar nenhum chunk (documento vazio)."""
    markdown = corpus_path.read_text(encoding="utf-8")
    drafts = chunk_document(markdown, title=corpus_path.name, origin=corpus_path.name)
    if not drafts:
        raise ValueError(f"{corpus_path}: nenhum chunk gerado (documento vazio?).")

    embeddings = await embedding_provider.embed(texts=[draft.content for draft in drafts])
    version_id = uuid4()
    for draft, embedding in zip(drafts, embeddings, strict=True):
        chunk = Chunk(
            id=uuid4(),
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base_id,
            version_id=version_id,
            content=draft.content,
            token_count=draft.token_count,
            page=draft.page,
            section=draft.section,
            metadata=draft.metadata,
            embedding=embedding,
        )
        vector_search.index_chunk(chunk)
        lexical_search.index_chunk(chunk)
    return len(drafts)


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
        tenant_id=tenant_id, name="retrieval-evaluation", description=None, config={}
    )

    vector_search = InMemoryVectorSearch()
    lexical_search = InMemoryLexicalSearch()
    embedding_provider = LiteLLMEmbeddingProvider(settings)

    indexed_count = await _index_corpus(
        corpus_path=args.corpus_path,
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base.id,
        embedding_provider=embedding_provider,
        vector_search=vector_search,
        lexical_search=lexical_search,
    )
    print(f"Corpus indexado: {indexed_count} chunk(s) de {args.corpus_path}.")

    reranker_enabled = args.reranker == "litellm"
    report = await evaluate_retrieval(
        dataset=dataset,
        knowledge_base_repository=knowledge_base_repository,
        embedding_provider=embedding_provider,
        vector_search=vector_search,
        lexical_search=lexical_search,
        reranker=_build_reranker(settings, kind=args.reranker),
        reranker_enabled=reranker_enabled,
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base.id,
        top_k=args.top_k,
        max_cases=args.max_cases,
    )

    threshold_check = check_thresholds(
        report, minimum_recall_at_k=args.minimum_recall_at_k, minimum_mrr=args.minimum_mrr
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "retrieval-evaluation.json"
    markdown_path = args.output_dir / "retrieval-evaluation.md"
    json_path.write_text(retrieval_report.render_json(report), encoding="utf-8")
    markdown_path.write_text(
        retrieval_report.render_markdown(report, threshold_check=threshold_check),
        encoding="utf-8",
    )

    print(f"Recall@{report.k}: {report.recall_at_k:.4f} | MRR: {report.mrr:.4f}")
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
