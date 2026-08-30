"""Prompt do modelo-juiz de avaliação de geração (RAG-062), mesma
convenção de versionamento imutável de `packages/generation/
prompts.py` (RAG-040): `config/prompts/<id>.<version>.yaml`; uma versão
publicada nunca é editada — uma mudança de conteúdo sempre cria uma
versão nova.

Schema deliberadamente separado de `PromptTemplate` (RAG-040): aquele
modela um prompt de RESPOSTA (aviso de conteúdo não confiável,
instrução de citação, resposta padrão sem evidência) — nenhum desses
campos faz sentido para um prompt de AVALIAÇÃO, que instrui o
modelo-juiz a devolver um JSON estrito com dois scores, nunca uma
resposta em texto livre para o usuário final."""

from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "config" / "prompts"


class JudgePromptNotFoundError(LookupError):
    """Não existe `config/prompts/<id>.<version>.yaml` para o par
    pedido."""

    def __init__(self, prompt_id: str, version: str) -> None:
        self.prompt_id = prompt_id
        self.version = version
        super().__init__(f"Prompt de avaliação '{prompt_id}' versão '{version}' não encontrado.")


class JudgePromptTemplate(BaseModel):
    """Um prompt de avaliação versionado."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    system_template: str = Field(min_length=1)
    instructions: str = Field(min_length=1)

    def render(self, *, question: str, answer: str, context: Sequence[str]) -> str:
        """Monta o prompt completo para uma pergunta, a resposta a
        avaliar, e os trechos de contexto que a originaram.

        `context` vazio (nenhum trecho, ex.: uma resposta "sem
        evidência suficiente") ainda produz um prompt válido — o
        modelo-juiz recebe um aviso explícito de que não havia
        contexto, em vez de uma seção CONTEXTO vazia e ambígua."""
        if context:
            context_block = "\n\n".join(
                f"[trecho {index}]\n{item.strip()}" for index, item in enumerate(context, start=1)
            )
        else:
            context_block = "(nenhum trecho de contexto foi fornecido a esta resposta)"

        return (
            f"{self.system_template.strip()}\n\n"
            f"{self.instructions.strip()}\n\n"
            f"PERGUNTA:\n{question.strip()}\n\n"
            f"CONTEXTO:\n{context_block}\n\n"
            f"RESPOSTA A AVALIAR:\n{answer.strip()}"
        )


@lru_cache
def load_judge_prompt(prompt_id: str, version: str) -> JudgePromptTemplate:
    """Carrega e valida `config/prompts/<prompt_id>.<version>.yaml`.

    Cacheado por processo, mesma justificativa de
    `packages.generation.prompts.load_prompt`: o par `(id, version)` é
    imutável por convenção, então ler o arquivo uma vez é seguro."""
    path = _PROMPTS_DIR / f"{prompt_id}.{version}.yaml"
    if not path.is_file():
        raise JudgePromptNotFoundError(prompt_id, version)

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    template = JudgePromptTemplate.model_validate(raw)

    if template.id != prompt_id or template.version != version:
        raise ValueError(
            f"{path}: campos 'id'/'version' ({template.id!r}/{template.version!r}) "
            f"não correspondem ao nome do arquivo ({prompt_id!r}/{version!r})."
        )
    return template


def get_default_judge_prompt() -> JudgePromptTemplate:
    """O prompt de avaliação atualmente usado (RAG-062). Único ponto
    que precisa mudar quando uma nova versão for adotada."""
    return load_judge_prompt("generation-judge", "v1")
