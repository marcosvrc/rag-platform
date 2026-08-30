"""Testes do schema do dataset dourado de avaliação (RAG-060)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from packages.evaluation import golden_dataset as golden_dataset_module
from packages.evaluation.golden_dataset import (
    MINIMUM_CASE_COUNT,
    ExpectedEvidence,
    GoldenCase,
    GoldenDataset,
    GoldenDatasetNotFoundError,
    get_default_golden_dataset,
    load_golden_dataset,
)


def _evidence(content_contains: str = "algum conteúdo") -> ExpectedEvidence:
    return ExpectedEvidence(document_id="doc-1", content_contains=content_contains)


def _answerable_case(case_id: str) -> GoldenCase:
    return GoldenCase(
        id=case_id,
        question=f"Pergunta {case_id}?",
        expected_answer="Uma resposta.",
        expected_evidence=(_evidence(),),
    )


def _unanswerable_case(case_id: str) -> GoldenCase:
    return GoldenCase(id=case_id, question=f"Pergunta {case_id}?")


def _minimum_valid_case_set() -> list[GoldenCase]:
    # MINIMUM_CASE_COUNT respondíveis + 1 sem resposta — o menor
    # conjunto que satisfaz os dois critérios de aceite ao mesmo tempo
    # (>= MINIMUM_CASE_COUNT casos, pelo menos um sem resposta).
    return [_answerable_case(f"c-{i}") for i in range(MINIMUM_CASE_COUNT)] + [
        _unanswerable_case("c-sem-resposta")
    ]


# --- carregamento do dataset real (golden/v1) -------------------------------


def test_load_golden_dataset_v1_tem_id_e_versao_corretos() -> None:
    dataset = load_golden_dataset("golden", "v1")

    assert isinstance(dataset, GoldenDataset)
    assert dataset.id == "golden"
    assert dataset.version == "v1"


def test_load_golden_dataset_v1_tem_pelo_menos_o_minimo_de_casos() -> None:
    dataset = load_golden_dataset("golden", "v1")

    assert len(dataset.cases) >= MINIMUM_CASE_COUNT


def test_load_golden_dataset_v1_inclui_pergunta_sem_resposta() -> None:
    dataset = load_golden_dataset("golden", "v1")

    sem_resposta = [case for case in dataset.cases if case.expected_answer is None]
    assert len(sem_resposta) >= 1
    for case in sem_resposta:
        assert case.expected_evidence == ()


def test_load_golden_dataset_v1_ids_de_caso_sao_unicos() -> None:
    dataset = load_golden_dataset("golden", "v1")

    ids = [case.id for case in dataset.cases]
    assert len(ids) == len(set(ids))


def test_load_golden_dataset_v1_casos_respondiveis_tem_evidencia() -> None:
    dataset = load_golden_dataset("golden", "v1")

    for case in dataset.cases:
        if case.expected_answer is not None:
            assert len(case.expected_evidence) >= 1


def test_load_golden_dataset_versao_inexistente_levanta_not_found() -> None:
    with pytest.raises(GoldenDatasetNotFoundError):
        load_golden_dataset("golden", "v999")


def test_load_golden_dataset_id_inexistente_levanta_not_found() -> None:
    with pytest.raises(GoldenDatasetNotFoundError):
        load_golden_dataset("nao-existe", "v1")


def test_load_golden_dataset_com_campos_id_version_divergentes_do_nome_do_arquivo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yaml

    # Precisa ser um dataset estruturalmente válido (>= MINIMUM_CASE_COUNT
    # casos + 1 sem resposta) para que o erro de propósito deste teste
    # (id/version internos divergentes do nome do arquivo) seja o único
    # motivo da falha — um dataset inválido por outro motivo (poucos
    # casos, por exemplo) falharia antes de chegar nessa checagem.
    valid_cases = _minimum_valid_case_set()
    raw = {
        "id": "golden",
        "version": "v1",
        "cases": [
            {
                "id": case.id,
                "question": case.question,
                "expected_answer": case.expected_answer,
                "expected_evidence": [
                    {
                        "document_id": ev.document_id,
                        "section": ev.section,
                        "content_contains": ev.content_contains,
                    }
                    for ev in case.expected_evidence
                ],
            }
            for case in valid_cases
        ],
    }
    (tmp_path / "golden.v2.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    monkeypatch.setattr(golden_dataset_module, "_DATASETS_DIR", tmp_path)
    load_golden_dataset.cache_clear()

    try:
        with pytest.raises(ValueError, match="não correspondem ao nome do arquivo"):
            load_golden_dataset("golden", "v2")
    finally:
        load_golden_dataset.cache_clear()


def test_load_golden_dataset_e_cacheado_por_id_e_versao() -> None:
    a = load_golden_dataset("golden", "v1")
    b = load_golden_dataset("golden", "v1")

    assert a is b


def test_get_default_golden_dataset_retorna_golden_v1() -> None:
    dataset = get_default_golden_dataset()

    assert dataset.id == "golden"
    assert dataset.version == "v1"


# --- invariantes de GoldenCase -----------------------------------------------


def test_golden_case_pergunta_sem_resposta_nao_pode_ter_evidencia() -> None:
    with pytest.raises(ValidationError, match="não pode ter expected_evidence"):
        GoldenCase(
            id="c-1",
            question="Pergunta?",
            expected_answer=None,
            expected_evidence=(_evidence(),),
        )


def test_golden_case_pergunta_com_resposta_precisa_de_evidencia() -> None:
    with pytest.raises(ValidationError, match="precisa de ao"):
        GoldenCase(id="c-1", question="Pergunta?", expected_answer="Uma resposta.")


def test_golden_case_e_imutavel() -> None:
    case = _unanswerable_case("c-1")

    with pytest.raises(ValidationError):
        case.question = "Outra pergunta?"


def test_golden_case_rejeita_campo_desconhecido() -> None:
    with pytest.raises(ValidationError):
        GoldenCase.model_validate({"id": "c-1", "question": "Pergunta?", "campo_desconhecido": "x"})


def test_expected_evidence_rejeita_campo_desconhecido() -> None:
    with pytest.raises(ValidationError):
        ExpectedEvidence.model_validate({"document_id": "doc-1", "campo_desconhecido": "x"})


# --- invariantes de GoldenDataset --------------------------------------------


def test_golden_dataset_exige_minimo_de_casos() -> None:
    # MINIMUM_CASE_COUNT - 2 respondíveis + 1 sem resposta = MINIMUM_CASE_COUNT - 1
    # no total — abaixo do mínimo de propósito (adicionar só 1 a menos
    # que o mínimo de respondíveis chegaria de volta ao total mínimo,
    # por causa do caso sem resposta extra).
    cases = [_answerable_case(f"c-{i}") for i in range(MINIMUM_CASE_COUNT - 2)] + [
        _unanswerable_case("c-sem-resposta")
    ]

    with pytest.raises(ValidationError, match="mínimo exigido"):
        GoldenDataset(id="golden", version="v1", cases=tuple(cases))


def test_golden_dataset_exige_pelo_menos_uma_pergunta_sem_resposta() -> None:
    cases = [_answerable_case(f"c-{i}") for i in range(MINIMUM_CASE_COUNT + 1)]

    with pytest.raises(ValidationError, match="nenhuma pergunta sem resposta"):
        GoldenDataset(id="golden", version="v1", cases=tuple(cases))


def test_golden_dataset_rejeita_ids_de_caso_duplicados() -> None:
    cases = _minimum_valid_case_set()
    cases[1] = _answerable_case(cases[0].id)  # duplica o id do primeiro caso

    with pytest.raises(ValidationError, match="duplicado"):
        GoldenDataset(id="golden", version="v1", cases=tuple(cases))


def test_golden_dataset_aceita_o_conjunto_minimo_valido() -> None:
    cases = _minimum_valid_case_set()

    dataset = GoldenDataset(id="golden", version="v1", cases=tuple(cases))

    assert len(dataset.cases) == MINIMUM_CASE_COUNT + 1


def test_golden_dataset_e_imutavel() -> None:
    dataset = GoldenDataset(id="golden", version="v1", cases=tuple(_minimum_valid_case_set()))

    with pytest.raises(ValidationError):
        dataset.version = "v2"


def test_golden_dataset_rejeita_campo_desconhecido() -> None:
    with pytest.raises(ValidationError):
        GoldenDataset.model_validate({"id": "golden", "version": "v1", "cases": [], "extra": 1})
