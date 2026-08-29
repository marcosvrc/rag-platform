"""Fusão RRF (Reciprocal Rank Fusion) de rankings de busca (RAG-032).

Combina o ranking de `VectorSearchPort` (RAG-030) com o de
`LexicalSearchPort` (RAG-031) num único ranking — a etapa entre as
duas buscas e o reranker (RAG-033). RRF usa só a POSIÇÃO de cada chunk
em cada ranking, nunca a magnitude do `score` — por isso funciona
mesmo as duas buscas usando escalas de score completamente diferentes
(`ts_rank` da busca lexical, similaridade de cosseno da busca
vetorial; ver docstring de `VectorSearchPort`), sem precisar
normalizar nada entre elas.

Fórmula clássica (Cormack et al., 2009): para um chunk que aparece na
posição `rank` (1-indexado) de um ranking, sua contribuição é
`weight / (k + rank)`; a contribuição de um ranking em que o chunk NÃO
aparece é zero (não é um erro, só ausência de evidência daquela
busca). O score final de cada chunk é a soma dessas contribuições
somada sobre os dois rankings — um chunk que aparece bem posicionado
nos dois rankings sempre supera um que aparece bem posicionado em só
um, o comportamento que motiva usar RRF em vez de pegar só um dos
dois.

`k=60` é o valor padrão da literatura original e o mais usado em
produção (suaviza a diferença entre posições altas do ranking); pesos
(`vector_weight`/`lexical_weight`) deixam favorecer uma busca sobre a
outra sem editar código — RAG-034 (endpoint retrieve) é quem decide
esses valores em produção, este módulo só aceita o que for passado.

Deduplicação: um chunk que aparece nos dois rankings de entrada
aparece UMA VEZ na saída, com o score combinado (critério de aceite
"duplicidades são removidas") — nunca duas entradas para o mesmo
`chunk.id`. Desempate determinístico por `chunk.id` (mesma convenção
de RAG-030/RAG-031) quando o score combinado empata."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from packages.application.ports.lexical_search import ScoredChunk

_DEFAULT_RRF_K = 60


def reciprocal_rank_fusion(
    *,
    vector_results: Sequence[ScoredChunk],
    lexical_results: Sequence[ScoredChunk],
    vector_weight: float = 1.0,
    lexical_weight: float = 1.0,
    k: int = _DEFAULT_RRF_K,
    limit: int | None = None,
) -> list[ScoredChunk]:
    """Funde `vector_results` e `lexical_results` (cada um já ordenado
    por relevância decrescente da sua própria busca) num único ranking
    por RRF.

    `vector_weight`/`lexical_weight` multiplicam a contribuição de cada
    ranking antes de somar (default 1.0 — nenhum favorecido). `k`
    controla o quanto a fórmula suaviza diferenças de posição (default
    60, o valor clássico). `limit`, se informado, trunca o resultado
    final aos `limit` chunks de maior score combinado; `None` (default)
    devolve todos os chunks que aparecem em pelo menos um dos dois
    rankings de entrada.

    Um `k` não positivo levanta `ValueError` (a fórmula `1 / (k + rank)`
    não faz sentido com `k <= 0` quando `rank` pode ser 1 e `k` for,
    por exemplo, `-1`, gerando denominador zero ou score negativo)."""
    if k <= 0:
        raise ValueError("k precisa ser positivo.")

    combined_scores: dict[UUID, float] = {}
    chunk_by_id: dict[UUID, ScoredChunk] = {}

    for results, weight in ((vector_results, vector_weight), (lexical_results, lexical_weight)):
        for rank, scored in enumerate(results, start=1):
            chunk_id = scored.chunk.id
            combined_scores[chunk_id] = combined_scores.get(chunk_id, 0.0) + weight / (k + rank)
            chunk_by_id.setdefault(chunk_id, scored)

    fused = [
        ScoredChunk(chunk=chunk_by_id[chunk_id].chunk, score=score)
        for chunk_id, score in combined_scores.items()
    ]
    fused.sort(key=lambda scored: (-scored.score, str(scored.chunk.id)))

    return fused[:limit] if limit is not None else fused
