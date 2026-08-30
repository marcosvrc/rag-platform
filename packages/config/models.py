"""Aliases de modelo versionados (RAG-025).

Cada alias é um arquivo YAML em `config/models/<id>.<version>.yaml` —
mesma convenção de `packages/generation/prompts.py` (RAG-040): uma
versão publicada é imutável (seção 8 do plano: "configurações de
prompt, retrieval e modelo devem possuir versão"), uma mudança de
modelo por trás do alias sempre cria uma versão nova, e não há "versão
atual" implícita — todo carregamento pede `id`/`version` explícitos.

O alias em si (ex.: `"embedding-model-alias"`) é só uma string opaca
para este módulo — quem resolve o alias para um provedor/modelo real é
a configuração do gateway LiteLLM (fora deste repositório; ver
`packages/config/settings.py` para a URL do gateway). Este módulo só
garante que a aplicação sempre referencia um alias por um `id`/`version`
rastreável, nunca uma string hardcoded espalhada pelo código.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

_MODELS_DIR = Path(__file__).resolve().parent.parent.parent / "config" / "models"


class ModelConfigNotFoundError(LookupError):
    """Não existe `config/models/<model_id>.<version>.yaml` para o par pedido."""

    def __init__(self, model_id: str, version: str) -> None:
        self.model_id = model_id
        self.version = version
        super().__init__(f"Configuração de modelo '{model_id}' versão '{version}' não encontrada.")


class ModelConfig(BaseModel):
    """Um alias de modelo versionado."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    alias: str = Field(min_length=1)


@lru_cache
def load_model_config(model_id: str, version: str) -> ModelConfig:
    """Carrega e valida `config/models/<model_id>.<version>.yaml`.

    Cacheado por processo, mesma justificativa de `load_prompt`: o par
    `(id, version)` é imutável por convenção, então ler o arquivo uma
    vez é seguro."""
    path = _MODELS_DIR / f"{model_id}.{version}.yaml"
    if not path.is_file():
        raise ModelConfigNotFoundError(model_id, version)

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    config = ModelConfig.model_validate(raw)

    if config.id != model_id or config.version != version:
        raise ValueError(
            f"{path}: campos 'id'/'version' ({config.id!r}/{config.version!r}) "
            f"não correspondem ao nome do arquivo ({model_id!r}/{version!r})."
        )
    return config


def get_default_embedding_model() -> ModelConfig:
    """O alias de embeddings atualmente usado pela aplicação (RAG-025).

    Único ponto que precisa mudar quando uma nova versão for adotada —
    quem gera embeddings deve chamar esta função, nunca
    `load_model_config("embedding", ...)` com uma versão hardcoded."""
    return load_model_config("embedding", "v1")


def get_default_reranker_model() -> ModelConfig:
    """O alias de reranker atualmente usado pela aplicação (RAG-033).
    Mesma convenção de `get_default_embedding_model` acima."""
    return load_model_config("reranker", "v1")


def get_default_generation_model() -> ModelConfig:
    """O alias de geração (chat completion) atualmente usado pela
    aplicação (RAG-042). Mesma convenção de `get_default_embedding_model`
    acima."""
    return load_model_config("generation", "v1")


def get_default_generation_fallback_model() -> ModelConfig:
    """O alias de geração de CONTINGÊNCIA (RAG-042) — só é resolvido por
    quem chama quando `Settings.generation_fallback_enabled` está
    ligado; ver docstring de `packages/application/ports/
    generation_provider.py` para o racional completo do fallback."""
    return load_model_config("generation-fallback", "v1")
