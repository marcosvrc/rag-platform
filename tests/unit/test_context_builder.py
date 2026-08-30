"""Testes do context builder (RAG-041)."""

from __future__ import annotations

from uuid import UUID, uuid4

from packages.application.queries.retrieval import RetrievedEvidence
from packages.domain.entities.chunk import Chunk
from packages.generation.context_builder import ContextBuildResult, build_context
from packages.generation.prompts import get_default_answer_prompt


def _evidence(
    *,
    content: str = "conteúdo",
    token_count: int = 10,
    position: int,
    chunk_id: UUID | None = None,
    retrieval_score: float = 1.0,
    rerank_score: float | None = None,
) -> RetrievedEvidence:
    chunk = Chunk(
        id=chunk_id or uuid4(),
        tenant_id=uuid4(),
        knowledge_base_id=uuid4(),
        version_id=uuid4(),
        content=content,
        token_count=token_count,
    )
    return RetrievedEvidence(
        chunk=chunk,
        retrieval_score=retrieval_score,
        rerank_score=rerank_score,
        position=position,
    )


def test_build_context_com_lista_vazia_devolve_resultado_vazio() -> None:
    result = build_context([], token_budget=1000)

    assert result == ContextBuildResult(context_text="", included_evidence=(), total_token_count=0)


def test_build_context_inclui_evidencia_que_cabe_no_orcamento() -> None:
    evidence = _evidence(content="banana prata", token_count=10, position=0)

    result = build_context([evidence], token_budget=100)

    assert result.included_evidence == (evidence,)
    assert result.total_token_count == 10
    assert f"[{evidence.chunk.id}]" in result.context_text
    assert "banana prata" in result.context_text


def test_build_context_respeita_o_limite_de_tokens() -> None:
    cabe = _evidence(content="cabe", token_count=60, position=0)
    nao_cabe = _evidence(content="não cabe", token_count=60, position=1)

    result = build_context([cabe, nao_cabe], token_budget=100)

    assert result.included_evidence == (cabe,)
    assert result.total_token_count == 60
    assert "não cabe" not in result.context_text


def test_build_context_evidencia_grande_demais_nao_bloqueia_as_menores_depois_dela() -> None:
    grande_demais = _evidence(content="grande demais", token_count=200, position=0)
    cabe = _evidence(content="cabe", token_count=10, position=1)

    result = build_context([grande_demais, cabe], token_budget=100)

    assert result.included_evidence == (cabe,)
    assert result.total_token_count == 10


def test_build_context_orcamento_zero_nao_inclui_nada() -> None:
    evidence = _evidence(content="qualquer coisa", token_count=1, position=0)

    result = build_context([evidence], token_budget=0)

    assert result == ContextBuildResult(context_text="", included_evidence=(), total_token_count=0)


def test_build_context_evita_conteudo_duplicado() -> None:
    original = _evidence(content="conteúdo repetido", token_count=10, position=0)
    duplicata = _evidence(content="conteúdo repetido", token_count=10, position=1)
    diferente = _evidence(content="conteúdo diferente", token_count=10, position=2)

    result = build_context([original, duplicata, diferente], token_budget=1000)

    assert result.included_evidence == (original, diferente)
    assert result.total_token_count == 20
    assert result.context_text.count("conteúdo repetido") == 1


def test_build_context_processa_em_ordem_de_position_independente_da_ordem_de_entrada() -> None:
    melhor = _evidence(content="melhor", token_count=60, position=0)
    pior = _evidence(content="pior", token_count=60, position=1)

    # Lista de entrada fora de ordem: o pior candidato vem primeiro.
    result = build_context([pior, melhor], token_budget=100)

    # Só há orçamento para um dos dois — deve ser o de melhor position,
    # não o que apareceu primeiro na lista de entrada.
    assert result.included_evidence == (melhor,)


def test_build_context_preserva_multiplas_evidencias_na_ordem_de_position() -> None:
    primeira = _evidence(content="primeira", token_count=10, position=0)
    segunda = _evidence(content="segunda", token_count=10, position=1)

    result = build_context([segunda, primeira], token_budget=1000)

    assert result.included_evidence == (primeira, segunda)
    assert result.context_text.index("primeira") < result.context_text.index("segunda")


def test_build_context_formato_de_citacao_e_compativel_com_o_prompt_de_resposta() -> None:
    """Integração com RAG-040: o formato `[chunk_id]` que este módulo
    produz é exatamente o que `citation_instruction` (config/prompts/
    answer.v1.yaml) pede ao modelo para citar — um teste de regressão
    contra os dois módulos divergindo silenciosamente."""
    evidence = _evidence(content="banana prata", token_count=10, position=0)
    result = build_context([evidence], token_budget=100)
    prompt = get_default_answer_prompt()

    rendered = prompt.render(context=result.context_text, question="O que é banana prata?")

    assert f"[{evidence.chunk.id}]" in rendered
    assert "banana prata" in rendered
