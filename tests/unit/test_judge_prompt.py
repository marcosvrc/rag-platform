"""Testes do carregador de prompt de avaliação (RAG-062), mesmo padrão
de `test_prompts.py` (RAG-040)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from packages.evaluation import judge_prompt as judge_prompt_module
from packages.evaluation.judge_prompt import (
    JudgePromptNotFoundError,
    JudgePromptTemplate,
    get_default_judge_prompt,
    load_judge_prompt,
)


def test_load_judge_prompt_generation_judge_v1_tem_id_e_versao_corretos() -> None:
    prompt = load_judge_prompt("generation-judge", "v1")

    assert isinstance(prompt, JudgePromptTemplate)
    assert prompt.id == "generation-judge"
    assert prompt.version == "v1"


def test_load_judge_prompt_generation_judge_v1_todos_os_campos_sao_nao_vazios() -> None:
    prompt = load_judge_prompt("generation-judge", "v1")

    assert prompt.system_template.strip() != ""
    assert prompt.instructions.strip() != ""


def test_load_judge_prompt_generation_judge_v1_pede_as_duas_dimensoes() -> None:
    prompt = load_judge_prompt("generation-judge", "v1")

    assert "faithfulness" in prompt.instructions
    assert "answer_relevancy" in prompt.instructions


def test_load_judge_prompt_versao_inexistente_levanta_not_found_error() -> None:
    with pytest.raises(JudgePromptNotFoundError):
        load_judge_prompt("generation-judge", "v999")


def test_load_judge_prompt_id_inexistente_levanta_not_found_error() -> None:
    with pytest.raises(JudgePromptNotFoundError):
        load_judge_prompt("nao-existe", "v1")


def test_load_judge_prompt_com_campos_divergentes_do_nome_do_arquivo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "generation-judge.v2.yaml").write_text(
        "id: generation-judge\nversion: v1\nsystem_template: x\ninstructions: x\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(judge_prompt_module, "_PROMPTS_DIR", tmp_path)
    load_judge_prompt.cache_clear()

    try:
        with pytest.raises(ValueError, match="não correspondem ao nome do arquivo"):
            load_judge_prompt("generation-judge", "v2")
    finally:
        load_judge_prompt.cache_clear()


def test_load_judge_prompt_e_cacheado_por_id_e_versao() -> None:
    a = load_judge_prompt("generation-judge", "v1")
    b = load_judge_prompt("generation-judge", "v1")

    assert a is b


def test_get_default_judge_prompt_retorna_generation_judge_v1() -> None:
    prompt = get_default_judge_prompt()

    assert prompt.id == "generation-judge"
    assert prompt.version == "v1"


def test_judge_prompt_template_e_imutavel() -> None:
    prompt = load_judge_prompt("generation-judge", "v1")

    with pytest.raises(ValidationError):
        prompt.id = "outro"


class TestRender:
    def test_includes_question_answer_and_context(self) -> None:
        prompt = load_judge_prompt("generation-judge", "v1")

        rendered = prompt.render(
            question="qual é a capital da França?",
            answer="é Paris.",
            context=["[chunk-1] Paris é a capital da França."],
        )

        assert "qual é a capital da França?" in rendered
        assert "é Paris." in rendered
        assert "Paris é a capital da França." in rendered
        assert prompt.system_template.strip() in rendered
        assert prompt.instructions.strip() in rendered

    def test_labels_multiple_context_snippets_in_order(self) -> None:
        prompt = load_judge_prompt("generation-judge", "v1")

        rendered = prompt.render(question="q", answer="a", context=["primeiro", "segundo"])

        assert rendered.index("primeiro") < rendered.index("segundo")
        assert "[trecho 1]" in rendered
        assert "[trecho 2]" in rendered

    def test_empty_context_produces_an_explicit_notice(self) -> None:
        prompt = load_judge_prompt("generation-judge", "v1")

        rendered = prompt.render(question="q", answer="a", context=[])

        assert "nenhum trecho de contexto foi fornecido" in rendered
