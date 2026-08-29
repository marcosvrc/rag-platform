"""Testes do carregador de prompts versionados (RAG-040)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from packages.generation import prompts as prompts_module
from packages.generation.prompts import (
    PromptNotFoundError,
    PromptTemplate,
    get_default_answer_prompt,
    load_prompt,
)


def test_load_prompt_answer_v1_tem_id_e_versao_corretos() -> None:
    prompt = load_prompt("answer", "v1")

    assert isinstance(prompt, PromptTemplate)
    assert prompt.id == "answer"
    assert prompt.version == "v1"


def test_load_prompt_answer_v1_todos_os_campos_sao_nao_vazios() -> None:
    prompt = load_prompt("answer", "v1")

    for field in (
        prompt.system_template,
        prompt.untrusted_context_notice,
        prompt.citation_instruction,
        prompt.no_evidence_response,
    ):
        assert field.strip() != ""


def test_load_prompt_answer_v1_exige_citacoes() -> None:
    prompt = load_prompt("answer", "v1")

    assert "cita" in prompt.citation_instruction.lower()
    assert "chunk_id" in prompt.citation_instruction


def test_load_prompt_answer_v1_declara_contexto_como_dado_nao_confiavel() -> None:
    # Requisito da seção 13 do plano: conteúdo recuperado é dado, nunca
    # instrução — qualquer instrução embutida no contexto deve ser
    # ignorada, não obedecida.
    prompt = load_prompt("answer", "v1")

    notice = prompt.untrusted_context_notice.lower()
    assert "ignorad" in notice
    assert "dado" in notice or "instru" in notice


def test_load_prompt_answer_v1_define_resposta_sem_evidencia() -> None:
    prompt = load_prompt("answer", "v1")

    assert "evidência" in prompt.no_evidence_response.lower() or (
        "evidencia" in prompt.no_evidence_response.lower()
    )


def test_load_prompt_versao_inexistente_levanta_prompt_not_found_error() -> None:
    with pytest.raises(PromptNotFoundError):
        load_prompt("answer", "v999")


def test_load_prompt_id_inexistente_levanta_prompt_not_found_error() -> None:
    with pytest.raises(PromptNotFoundError):
        load_prompt("nao-existe", "v1")


def test_load_prompt_com_campos_id_version_divergentes_do_nome_do_arquivo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # id/version dentro do YAML devem corresponder ao nome do arquivo —
    # protege contra copiar um arquivo de versão e esquecer de atualizar
    # os campos internos.
    (tmp_path / "answer.v2.yaml").write_text(
        "id: answer\n"
        "version: v1\n"
        "system_template: x\n"
        "untrusted_context_notice: x\n"
        "citation_instruction: x\n"
        "no_evidence_response: x\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(prompts_module, "_PROMPTS_DIR", tmp_path)
    load_prompt.cache_clear()

    try:
        with pytest.raises(ValueError, match="não correspondem ao nome do arquivo"):
            load_prompt("answer", "v2")
    finally:
        load_prompt.cache_clear()


def test_load_prompt_e_cacheado_por_id_e_versao() -> None:
    a = load_prompt("answer", "v1")
    b = load_prompt("answer", "v1")

    assert a is b


def test_get_default_answer_prompt_retorna_answer_v1() -> None:
    prompt = get_default_answer_prompt()

    assert prompt.id == "answer"
    assert prompt.version == "v1"


def test_render_monta_prompt_com_contexto_e_pergunta() -> None:
    prompt = load_prompt("answer", "v1")
    context = "[chunk-1] Paris é a capital da França."
    question = "Qual é a capital da França?"

    rendered = prompt.render(context=context, question=question)

    assert context in rendered
    assert question in rendered
    assert prompt.citation_instruction.strip() in rendered
    assert prompt.untrusted_context_notice.strip() in rendered
    assert prompt.system_template.strip() in rendered


def test_prompt_template_e_imutavel() -> None:
    prompt = load_prompt("answer", "v1")

    with pytest.raises(ValidationError):
        prompt.id = "outro"
