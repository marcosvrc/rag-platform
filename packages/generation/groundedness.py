"""Validação de groundedness e citações (RAG-043, seção 12 do plano,
passo "validar formato e citações").

**Escopo desta atividade, deliberadamente restrito ao que o critério de
aceite pede** ("toda citação corresponde a chunk recuperado; resposta
inválida usa fallback seguro"):

- Valida CITAÇÕES, não AFIRMAÇÕES: checa se cada `[chunk_id]` que
  aparece na resposta corresponde a um chunk que de fato foi incluído
  no contexto (`ContextBuildResult.included_evidence`, RAG-041) — nunca
  tenta verificar se o texto ao redor da citação está de fato
  sustentado pelo conteúdo do chunk citado. Esse nível de verificação
  (faithfulness, claim a claim) é avaliação, não validação em tempo
  real de produção — fica para RAG-062 (avaliação de geração, com um
  LLM-juiz e o dataset dourado, RAG-060), que tem a infraestrutura
  certa para esse julgamento caro; aqui o critério é mecânico e barato
  o bastante para rodar em toda consulta.
- "detectar resposta sem suporte" é interpretado no nível mais grosso
  possível que o critério de aceite sustenta: uma resposta com ZERO
  citações válidas (nenhum `[chunk_id]` reconhecível, ou nenhum que
  corresponda a evidência incluída) é "sem suporte" — não há tentativa
  de decidir se PARTE da resposta está sem suporte e parte está.
- NÃO decide se deve sequer chamar o modelo (passo 9, "aplicar limiar
  mínimo") — isso já aconteceu antes de RAG-042 ser invocado, é decisão
  de quem monta o endpoint `query` (RAG-044). Esta atividade só valida
  o que o modelo já respondeu (passo 12, depois do passo 11).

**Citação reconhecida só como UUID exato**: `Chunk.id` é sempre um
UUID (RAG-006) e `citation_instruction` (`config/prompts/answer.v1.yaml`,
RAG-040) pede citação "no formato [chunk_id]" — então um texto entre
colchetes que não é um UUID válido não é tratado como citação
inválida, é simplesmente ignorado (pode ser uma nota entre colchetes
que não tem nada a ver com citação). Na prática isso não abre uma
brecha: uma citação malformada (UUID errado, ID truncado, formato
inventado) nunca casa com nenhum chunk incluído, então a resposta cai
no mesmo caminho de "zero citações válidas" — sem suporte, mesmo
resultado de segurança.

**Fallback seguro reaproveita `no_evidence_response`** (RAG-040,
`config/prompts/answer.v1.yaml`) em vez de inventar uma segunda
mensagem de "não foi possível responder": o usuário final já vê essa
frase sempre que a evidência recuperada não é suficiente (passo 9); uma
resposta que o modelo gerou mas que falhou a validação de groundedness
é, do ponto de vista de quem pergunta, exatamente a mesma situação —
"não há uma resposta confiável para dar". Dois sintomas diferentes
(threshold de recuperação vs. citação inválida), uma única mensagem
para quem usa o sistema.

**Caso especial: a própria `no_evidence_response`, palavra por
palavra, sempre é válida** — mesmo com zero citações e mesmo que
`included_evidence` não esteja vazio (o modelo pode legitimamente
decidir que o contexto que recebeu não sustenta uma resposta, ainda
que tenha caído dentro do orçamento de tokens). Sem esse caso especial,
o comportamento correto e esperado do modelo (seguir a instrução do
prompt) seria marcado como "resposta inválida" só por não ter citação
— o que é tecnicamente inofensivo (o fallback devolveria o mesmo
texto), mas contaminaria qualquer métrica/auditoria de groundedness
com falsos positivos."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from packages.application.queries.retrieval import RetrievedEvidence

#: `citation_instruction` (RAG-040) pede exatamente "[chunk_id]" — um
#: grupo entre colchetes sem colchetes aninhados. Qualquer conteúdo
#: entre colchetes que não seja um UUID válido é ignorado (ver
#: docstring do módulo).
_BRACKET_PATTERN = re.compile(r"\[([^\[\]]+)\]")


@dataclass(frozen=True, slots=True)
class GroundednessResult:
    """Resultado da análise de groundedness de uma resposta, sem
    aplicar nenhum fallback — só o diagnóstico. `cited_chunk_ids` são
    todas as citações reconhecidas (UUIDs válidos entre colchetes,
    únicos); `invalid_citations` é o subconjunto delas que não
    corresponde a nenhum chunk em `included_evidence` (citação
    inventada)."""

    is_valid: bool
    cited_chunk_ids: frozenset[UUID]
    invalid_citations: frozenset[UUID]


@dataclass(frozen=True, slots=True)
class GroundednessOutcome:
    """Resultado de `enforce_groundedness`: `content` é o texto final
    — a resposta original quando válida, ou `no_evidence_response`
    quando o fallback seguro foi aplicado. Quem chama (RAG-044) deve
    sempre usar `content`, nunca a resposta original do modelo
    diretamente, e pode usar `fallback_applied`/`invalid_citations`
    para auditoria/observabilidade sem precisar rodar a validação de
    novo."""

    content: str
    fallback_applied: bool
    cited_chunk_ids: frozenset[UUID]
    invalid_citations: frozenset[UUID]


def extract_cited_chunk_ids(answer: str) -> frozenset[UUID]:
    """Extrai todos os UUIDs válidos entre colchetes em `answer`.
    Conteúdo entre colchetes que não é um UUID (nota, colchete de
    markdown, o que for) é ignorado silenciosamente — não é uma
    citação."""
    found: set[UUID] = set()
    for match in _BRACKET_PATTERN.finditer(answer):
        try:
            found.add(UUID(match.group(1).strip()))
        except ValueError:
            continue
    return frozenset(found)


def validate_groundedness(
    answer: str,
    *,
    included_evidence: Sequence[RetrievedEvidence],
    no_evidence_response: str,
) -> GroundednessResult:
    """Analisa `answer` sem aplicar nenhum fallback.

    `answer` idêntica (ignorando espaços nas pontas) a
    `no_evidence_response` é sempre válida, mesmo sem nenhuma citação —
    ver docstring do módulo. Caso contrário, válida exige pelo menos
    uma citação reconhecida E nenhuma citação que não corresponda a
    `included_evidence`."""
    if answer.strip() == no_evidence_response.strip():
        return GroundednessResult(
            is_valid=True, cited_chunk_ids=frozenset(), invalid_citations=frozenset()
        )

    cited = extract_cited_chunk_ids(answer)
    valid_chunk_ids = frozenset(item.chunk.id for item in included_evidence)
    invalid = cited - valid_chunk_ids

    return GroundednessResult(
        is_valid=bool(cited) and not invalid,
        cited_chunk_ids=cited,
        invalid_citations=invalid,
    )


def enforce_groundedness(
    answer: str,
    *,
    included_evidence: Sequence[RetrievedEvidence],
    no_evidence_response: str,
) -> GroundednessOutcome:
    """Valida `answer` e já devolve o texto final a ser persistido/
    retornado — `answer` sem alteração quando válida, ou
    `no_evidence_response` (o fallback seguro, critério de aceite)
    quando inválida. Nunca levanta exceção: um problema de groundedness
    é um resultado esperado do fluxo, não uma falha."""
    result = validate_groundedness(
        answer, included_evidence=included_evidence, no_evidence_response=no_evidence_response
    )
    return GroundednessOutcome(
        content=answer if result.is_valid else no_evidence_response,
        fallback_applied=not result.is_valid,
        cited_chunk_ids=result.cited_chunk_ids,
        invalid_citations=result.invalid_citations,
    )
