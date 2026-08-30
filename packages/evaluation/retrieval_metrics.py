"""Métricas de avaliação de retrieval (RAG-061, seção 14/21 do plano):
Recall@K e MRR (Mean Reciprocal Rank).

Funções puras, sem I/O e sem depender de nenhuma porta hexagonal — só
comparam texto recuperado contra `ExpectedEvidence.content_contains`
(RAG-060). Quem orquestra a recuperação de verdade (embeddings, busca
vetorial/lexical, RRF, reranking) é
`packages.evaluation.retrieval_evaluation.evaluate_retrieval`, que
reusa `retrieve_evidence` (RAG-034) e chama estas funções só com o
texto já recuperado.

Uma evidência esperada (`ExpectedEvidence`) é considerada "encontrada"
quando `content_contains` aparece como substring em pelo menos um dos
conteúdos recuperados — mesmo critério já usado para verificar a
consistência do próprio dataset dourado contra o README (RAG-060):
correspondência por conteúdo, nunca por `chunk_id` (um dataset
versionado no repositório não pode depender de um UUID gerado só na
indexação)."""

from __future__ import annotations

from collections.abc import Sequence

from packages.evaluation.golden_dataset import ExpectedEvidence


def _is_found(evidence: ExpectedEvidence, retrieved_contents: Sequence[str]) -> bool:
    return any(evidence.content_contains in content for content in retrieved_contents)


def count_found_evidence(
    expected_evidence: Sequence[ExpectedEvidence], retrieved_contents: Sequence[str]
) -> int:
    """Quantos itens de `expected_evidence` aparecem em pelo menos um
    conteúdo de `retrieved_contents` — a contagem por trás de
    `recall_at_k`, exposta separadamente porque o relatório
    (`CaseRetrievalResult`) também guarda esse número bruto, não só a
    fração."""
    return sum(1 for evidence in expected_evidence if _is_found(evidence, retrieved_contents))


def recall_at_k(
    expected_evidence: Sequence[ExpectedEvidence], retrieved_contents: Sequence[str], *, k: int
) -> float:
    """Fração de `expected_evidence` encontrada dentro dos `k`
    primeiros `retrieved_contents` (na ordem em que já vêm — a ordem de
    posição do ranking final, não uma ordem que esta função decida).

    `expected_evidence` vazio levanta `ValueError`: um caso sem nenhuma
    evidência esperada não tem "recall" definido (divisão por zero) —
    quem chama (`evaluate_retrieval`) já filtra os casos sem
    `expected_evidence` (perguntas sem resposta, RAG-060) antes de
    calcular esta métrica, então chegar aqui com uma lista vazia é um
    erro de uso, não um caso legítimo a tratar como `0.0`."""
    if not expected_evidence:
        raise ValueError("expected_evidence não pode ser vazio para calcular recall@k.")
    truncated = retrieved_contents[:k]
    return count_found_evidence(expected_evidence, truncated) / len(expected_evidence)


def reciprocal_rank(
    expected_evidence: Sequence[ExpectedEvidence], retrieved_contents: Sequence[str]
) -> float:
    """`1 / rank` da primeira posição (1-indexada) de
    `retrieved_contents` que contém pelo menos um item de
    `expected_evidence`; `0.0` se nenhuma posição contém.

    Sobre todo `retrieved_contents` recebido, não truncado por um `k`
    separado — o `top_k` que limita a profundidade já foi decidido por
    quem chamou `retrieve_evidence` (RAG-034); MRR mede o quão cedo,
    dentro do que foi de fato recuperado, a primeira evidência
    relevante aparece."""
    for rank, content in enumerate(retrieved_contents, start=1):
        if any(evidence.content_contains in content for evidence in expected_evidence):
            return 1.0 / rank
    return 0.0


def mean(values: Sequence[float]) -> float:
    """Média aritmética simples — usada para agregar `recall_at_k`/
    `reciprocal_rank` por caso em `RetrievalEvaluationReport.recall_at_k`/
    `.mrr` (RAG-061). `values` vazio levanta `ValueError`: mesma postura
    de `recall_at_k` — um relatório sem nenhum caso avaliado é um erro
    de uso (dataset sem caso respondível), não uma média `0.0`
    silenciosa."""
    if not values:
        raise ValueError("values não pode ser vazio para calcular a média.")
    return sum(values) / len(values)
