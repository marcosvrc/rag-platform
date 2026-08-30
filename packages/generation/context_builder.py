"""Context builder (RAG-041): monta o texto de CONTEXTO do prompt de
resposta (RAG-040) selecionando, dentro de um orçamento de tokens,
quais evidências recuperadas (RAG-034, já rankeadas por RRF/reranking)
entram na chamada ao modelo (RAG-042).

Escopo desta atividade, deliberadamente restrito ao que a seção 12
(passo 10) do plano pede — "montar contexto dentro do orçamento de
tokens":

- NÃO aplica o "limiar mínimo" do passo 9 nem decide quando responder
  "não há evidência suficiente" (seção 12.1) — isso cabe a quem monta
  o endpoint `query` (RAG-043/044), que tem o contexto completo (score
  mínimo aceitável, se deve ou não sequer chamar o modelo). Aqui, não
  haver nenhuma evidência que caiba no orçamento é uma saída legítima
  e silenciosa: `ContextBuildResult.context_text` fica vazio.
- NÃO chama nenhum modelo nem decide o orçamento de tokens padrão de
  produção — isso depende de qual modelo RAG-042 escolher (a janela de
  contexto varia por modelo); `token_budget` é sempre um parâmetro
  explícito de quem chama, nunca um valor global assumido aqui.
- NÃO trunca o conteúdo de um chunk para caber no orçamento — um chunk
  é incluído inteiro ou não é incluído. Cortar um chunk no meio
  produziria uma citação `[chunk_id]` referenciando um texto que o
  chunk recuperado, de verdade, não contém por inteiro — exatamente o
  problema que RAG-043 ("toda citação corresponde a chunk recuperado")
  existe para prevenir.
"""

from __future__ import annotations

from dataclasses import dataclass

from packages.application.queries.retrieval import RetrievedEvidence

#: Valor só para quem ainda não tem uma decisão de produção (testes,
#: chamadas exploratórias). RAG-042 decide o valor real a partir da
#: janela de contexto do modelo por trás do alias de geração escolhido
#: — este módulo nunca assume esse número sozinho, mesma postura de
#: `adapters/reranker/litellm.py` não escolher o modelo real por trás
#: do alias de reranking (RAG-033).
DEFAULT_TOKEN_BUDGET = 3000


@dataclass(frozen=True)
class ContextBuildResult:
    """Resultado da montagem de contexto: o texto pronto para
    `PromptTemplate.render(context=...)` (RAG-040) e as evidências que
    de fato entraram nele, na mesma ordem do texto. RAG-043 usa
    `included_evidence` para validar que toda citação da resposta
    corresponde a um chunk realmente incluído aqui — uma citação para
    um `chunk_id` fora desta lista é uma citação inventada."""

    context_text: str
    included_evidence: tuple[RetrievedEvidence, ...]
    total_token_count: int


def build_context(
    evidence: list[RetrievedEvidence],
    *,
    token_budget: int,
) -> ContextBuildResult:
    """Seleciona, da melhor posição para a pior (`position` crescente
    — `0` é o melhor candidato), quais evidências cabem em
    `token_budget` tokens, pulando conteúdo duplicado, e monta o texto
    de contexto.

    Para cada evidência, nessa ordem de prioridade, duas regras de
    exclusão:

    1. conteúdo (`chunk.content`) idêntico ao de uma evidência já
       incluída → descartada (critério de aceite "evita duplicações
       excessivas"). Comparação exata, não uma heurística de
       similaridade: dois chunks quase idênticos mas não uma cópia
       textual exata um do outro continuam sendo considerados
       evidências distintas — deduplicação por similaridade ficaria
       fora do escopo desta atividade (o critério de aceite não pede
       essa sofisticação, e RRF, RAG-032, já deduplica o mesmo
       `chunk_id` aparecendo nos dois rankings de entrada).
    2. incluí-la estouraria `token_budget` → descartada, mas a seleção
       CONTINUA para a próxima evidência: uma evidência grande demais
       não impede uma evidência menor e pior posicionada de caber no
       orçamento depois dela.

    `evidence` não precisa chegar pré-ordenada por `position` — este
    módulo nunca assume ordem de quem chama, mesma postura defensiva de
    `retrieve_evidence` (`packages/application/queries/retrieval.py`)
    reclampando `top_k` mesmo já validado pelo contrato.
    """
    ordered = sorted(evidence, key=lambda item: item.position)

    included: list[RetrievedEvidence] = []
    seen_content: set[str] = set()
    total_tokens = 0

    for item in ordered:
        if item.chunk.content in seen_content:
            continue
        if total_tokens + item.chunk.token_count > token_budget:
            continue
        included.append(item)
        seen_content.add(item.chunk.content)
        total_tokens += item.chunk.token_count

    context_text = "\n\n".join(
        f"[{item.chunk.id}] {item.chunk.content.strip()}" for item in included
    )

    return ContextBuildResult(
        context_text=context_text,
        included_evidence=tuple(included),
        total_token_count=total_tokens,
    )
