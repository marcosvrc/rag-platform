"""Testes de RAG-043: validação de groundedness e citações."""

from __future__ import annotations

from uuid import UUID, uuid4

from packages.application.queries.retrieval import RetrievedEvidence
from packages.domain.entities.chunk import Chunk
from packages.generation.groundedness import (
    GroundednessOutcome,
    GroundednessResult,
    enforce_groundedness,
    extract_cited_chunk_ids,
    validate_groundedness,
)

_NO_EVIDENCE_RESPONSE = "Não há evidência suficiente no contexto para responder a esta pergunta."


def _evidence(*, chunk_id: UUID | None = None, position: int = 0) -> RetrievedEvidence:
    chunk = Chunk(
        id=chunk_id or uuid4(),
        tenant_id=uuid4(),
        knowledge_base_id=uuid4(),
        version_id=uuid4(),
        content="conteúdo qualquer",
        token_count=10,
    )
    return RetrievedEvidence(chunk=chunk, retrieval_score=1.0, rerank_score=None, position=position)


# --- extract_cited_chunk_ids -----------------------------------------------


def test_extract_cited_chunk_ids_reconhece_uuids_entre_colchetes() -> None:
    chunk_id = uuid4()
    answer = f"A arquitetura é hexagonal [{chunk_id}]."

    assert extract_cited_chunk_ids(answer) == frozenset({chunk_id})


def test_extract_cited_chunk_ids_ignora_colchetes_que_nao_sao_uuid() -> None:
    answer = "Isso é uma [nota] qualquer, sem nenhuma citação de verdade."

    assert extract_cited_chunk_ids(answer) == frozenset()


def test_extract_cited_chunk_ids_deduplica_a_mesma_citacao_repetida() -> None:
    chunk_id = uuid4()
    answer = f"Primeira afirmação [{chunk_id}]. Segunda afirmação, mesmo chunk [{chunk_id}]."

    assert extract_cited_chunk_ids(answer) == frozenset({chunk_id})


def test_extract_cited_chunk_ids_reconhece_multiplas_citacoes_distintas() -> None:
    id_a, id_b = uuid4(), uuid4()
    answer = f"Um fato [{id_a}] e outro fato [{id_b}]."

    assert extract_cited_chunk_ids(answer) == frozenset({id_a, id_b})


def test_extract_cited_chunk_ids_sem_nenhum_colchete_devolve_vazio() -> None:
    assert extract_cited_chunk_ids("resposta sem nenhuma citação") == frozenset()


# --- validate_groundedness ---------------------------------------------------


def test_validate_groundedness_resposta_com_citacao_valida_e_valida() -> None:
    evidence = _evidence()
    answer = f"A resposta é X [{evidence.chunk.id}]."

    result = validate_groundedness(
        answer, included_evidence=[evidence], no_evidence_response=_NO_EVIDENCE_RESPONSE
    )

    assert result == GroundednessResult(
        is_valid=True,
        cited_chunk_ids=frozenset({evidence.chunk.id}),
        invalid_citations=frozenset(),
    )


def test_validate_groundedness_citacao_que_nao_corresponde_a_evidencia_incluida_e_invalida() -> (
    None
):
    evidence = _evidence()
    hallucinated_id = uuid4()
    answer = f"A resposta é X [{hallucinated_id}]."

    result = validate_groundedness(
        answer, included_evidence=[evidence], no_evidence_response=_NO_EVIDENCE_RESPONSE
    )

    assert result.is_valid is False
    assert result.invalid_citations == frozenset({hallucinated_id})


def test_validate_groundedness_sem_nenhuma_citacao_e_invalida() -> None:
    evidence = _evidence()
    answer = "A resposta é X, sem citar nada."

    result = validate_groundedness(
        answer, included_evidence=[evidence], no_evidence_response=_NO_EVIDENCE_RESPONSE
    )

    assert result.is_valid is False
    assert result.cited_chunk_ids == frozenset()
    assert result.invalid_citations == frozenset()


def test_validate_groundedness_mistura_de_citacao_valida_e_invalida_e_invalida() -> None:
    evidence = _evidence()
    hallucinated_id = uuid4()
    answer = f"Um fato real [{evidence.chunk.id}] e um inventado [{hallucinated_id}]."

    result = validate_groundedness(
        answer, included_evidence=[evidence], no_evidence_response=_NO_EVIDENCE_RESPONSE
    )

    assert result.is_valid is False
    assert result.cited_chunk_ids == frozenset({evidence.chunk.id, hallucinated_id})
    assert result.invalid_citations == frozenset({hallucinated_id})


def test_validate_groundedness_no_evidence_response_literal_e_sempre_valida() -> None:
    result = validate_groundedness(
        _NO_EVIDENCE_RESPONSE, included_evidence=[], no_evidence_response=_NO_EVIDENCE_RESPONSE
    )

    assert result == GroundednessResult(
        is_valid=True, cited_chunk_ids=frozenset(), invalid_citations=frozenset()
    )


def test_validate_groundedness_no_evidence_response_e_valida_mesmo_com_evidencia_incluida() -> None:
    evidence = _evidence()

    result = validate_groundedness(
        _NO_EVIDENCE_RESPONSE,
        included_evidence=[evidence],
        no_evidence_response=_NO_EVIDENCE_RESPONSE,
    )

    assert result.is_valid is True


def test_validate_groundedness_no_evidence_response_ignora_espacos_nas_pontas() -> None:
    result = validate_groundedness(
        f"  {_NO_EVIDENCE_RESPONSE}  \n",
        included_evidence=[],
        no_evidence_response=_NO_EVIDENCE_RESPONSE,
    )

    assert result.is_valid is True


def test_validate_groundedness_citacao_malformada_conta_como_ausencia_de_citacao() -> None:
    evidence = _evidence()
    answer = "A resposta é X [não-é-um-uuid]."

    result = validate_groundedness(
        answer, included_evidence=[evidence], no_evidence_response=_NO_EVIDENCE_RESPONSE
    )

    assert result.is_valid is False
    assert result.cited_chunk_ids == frozenset()


def test_validate_groundedness_sem_nenhuma_evidencia_incluida_e_citacao_presente_e_invalida() -> (
    None
):
    hallucinated_id = uuid4()
    answer = f"A resposta é X [{hallucinated_id}]."

    result = validate_groundedness(
        answer, included_evidence=[], no_evidence_response=_NO_EVIDENCE_RESPONSE
    )

    assert result.is_valid is False
    assert result.invalid_citations == frozenset({hallucinated_id})


# --- enforce_groundedness ----------------------------------------------------


def test_enforce_groundedness_resposta_valida_e_devolvida_sem_alteracao() -> None:
    evidence = _evidence()
    answer = f"A resposta é X [{evidence.chunk.id}]."

    outcome = enforce_groundedness(
        answer, included_evidence=[evidence], no_evidence_response=_NO_EVIDENCE_RESPONSE
    )

    assert outcome == GroundednessOutcome(
        content=answer,
        fallback_applied=False,
        cited_chunk_ids=frozenset({evidence.chunk.id}),
        invalid_citations=frozenset(),
    )


def test_enforce_groundedness_resposta_invalida_usa_fallback_seguro() -> None:
    evidence = _evidence()
    hallucinated_id = uuid4()
    answer = f"A resposta é X [{hallucinated_id}]."

    outcome = enforce_groundedness(
        answer, included_evidence=[evidence], no_evidence_response=_NO_EVIDENCE_RESPONSE
    )

    assert outcome.content == _NO_EVIDENCE_RESPONSE
    assert outcome.fallback_applied is True
    assert outcome.invalid_citations == frozenset({hallucinated_id})


def test_enforce_groundedness_sem_citacao_nenhuma_usa_fallback_seguro() -> None:
    evidence = _evidence()
    answer = "Uma afirmação qualquer sem nenhuma citação."

    outcome = enforce_groundedness(
        answer, included_evidence=[evidence], no_evidence_response=_NO_EVIDENCE_RESPONSE
    )

    assert outcome.content == _NO_EVIDENCE_RESPONSE
    assert outcome.fallback_applied is True


def test_enforce_groundedness_no_evidence_response_nunca_aciona_fallback() -> None:
    outcome = enforce_groundedness(
        _NO_EVIDENCE_RESPONSE, included_evidence=[], no_evidence_response=_NO_EVIDENCE_RESPONSE
    )

    assert outcome.content == _NO_EVIDENCE_RESPONSE
    assert outcome.fallback_applied is False
