"""Prompt de resposta fundamentada, versionado (RAG-040).

Cada prompt é um arquivo YAML em `config/prompts/<id>.<version>.yaml`
— uma vez publicada, uma versão é imutável (seção 8 do plano:
"configurações de prompt, retrieval e modelo devem possuir versão"); uma
mudança de conteúdo sempre cria uma versão nova, nunca edita a
existente. Não há "versão atual" implícita: todo carregamento pede
`id` e `version` explicitamente.

Este módulo só carrega e valida a estrutura do prompt — não seleciona
evidências nem monta o contexto dentro de um orçamento de tokens (isso
é `packages/generation`/RAG-041, o context builder) e não chama nenhum
modelo (RAG-042). `render()` aqui é só a montagem textual fixa (system
+ aviso de conteúdo não confiável + contexto + instrução de citação).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "config" / "prompts"


class PromptNotFoundError(LookupError):
    """Não existe `config/prompts/<id>.<version>.yaml` para o par pedido."""

    def __init__(self, prompt_id: str, version: str) -> None:
        self.prompt_id = prompt_id
        self.version = version
        super().__init__(f"Prompt '{prompt_id}' versão '{version}' não encontrado.")


class PromptTemplate(BaseModel):
    """Um prompt de resposta versionado e suas partes fixas.

    Todos os campos de texto são obrigatórios e não podem ser vazios —
    são exatamente os requisitos de aceite da atividade (seção 13 do
    plano): tratar o contexto como dado não confiável, exigir citação,
    e definir o que responder quando não há evidência suficiente.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    system_template: str = Field(min_length=1)
    untrusted_context_notice: str = Field(min_length=1)
    citation_instruction: str = Field(min_length=1)
    no_evidence_response: str = Field(min_length=1)

    def render(self, *, context: str, question: str) -> str:
        """Monta o prompt completo para uma pergunta e um contexto já
        selecionado.

        Só concatena as partes fixas — não trunca nem prioriza
        evidências por orçamento de tokens (RAG-041) e não decide
        sozinho quando usar `no_evidence_response` (isso depende do
        limiar de recuperação, RAG-043/seção 12.1); quem chama decide
        se há evidência suficiente para sequer montar este prompt.
        """
        return (
            f"{self.system_template.strip()}\n\n"
            f"{self.untrusted_context_notice.strip()}\n\n"
            f"CONTEXTO:\n{context.strip()}\n\n"
            f"{self.citation_instruction.strip()}\n\n"
            f"PERGUNTA:\n{question.strip()}"
        )


@lru_cache
def load_prompt(prompt_id: str, version: str) -> PromptTemplate:
    """Carrega e valida `config/prompts/<prompt_id>.<version>.yaml`.

    Cacheado por processo (mesmo par `(id, version)` sempre devolve a
    mesma instância) — o arquivo é lido do disco só uma vez, e como uma
    versão publicada é imutável por convenção, isso é seguro.
    """
    path = _PROMPTS_DIR / f"{prompt_id}.{version}.yaml"
    if not path.is_file():
        raise PromptNotFoundError(prompt_id, version)

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    template = PromptTemplate.model_validate(raw)

    if template.id != prompt_id or template.version != version:
        raise ValueError(
            f"{path}: campos 'id'/'version' ({template.id!r}/{template.version!r}) "
            f"não correspondem ao nome do arquivo ({prompt_id!r}/{version!r})."
        )
    return template


def get_default_answer_prompt() -> PromptTemplate:
    """A versão do prompt de resposta atualmente usada pela aplicação.

    Único ponto que precisa mudar quando uma nova versão for adotada —
    RAG-041/042 devem chamar esta função, nunca `load_prompt("answer",
    ...)` com uma versão hardcoded em outro lugar.
    """
    return load_prompt("answer", "v1")
