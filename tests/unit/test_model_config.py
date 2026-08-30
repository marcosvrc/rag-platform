"""Testes do carregador de aliases de modelo versionados (RAG-025)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from packages.config import models as models_module
from packages.config.models import (
    ModelConfig,
    ModelConfigNotFoundError,
    get_default_embedding_model,
    get_default_reranker_model,
    load_model_config,
)


def test_load_model_config_embedding_v1_tem_id_e_versao_corretos() -> None:
    config = load_model_config("embedding", "v1")

    assert isinstance(config, ModelConfig)
    assert config.id == "embedding"
    assert config.version == "v1"
    assert config.alias.strip() != ""


def test_load_model_config_versao_inexistente_levanta_not_found_error() -> None:
    with pytest.raises(ModelConfigNotFoundError):
        load_model_config("embedding", "v999")


def test_load_model_config_id_inexistente_levanta_not_found_error() -> None:
    with pytest.raises(ModelConfigNotFoundError):
        load_model_config("nao-existe", "v1")


def test_load_model_config_com_campos_id_version_divergentes_do_nome_do_arquivo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "embedding.v2.yaml").write_text(
        "id: embedding\nversion: v1\nalias: x\n", encoding="utf-8"
    )
    monkeypatch.setattr(models_module, "_MODELS_DIR", tmp_path)
    load_model_config.cache_clear()

    try:
        with pytest.raises(ValueError, match="não correspondem ao nome do arquivo"):
            load_model_config("embedding", "v2")
    finally:
        load_model_config.cache_clear()


def test_load_model_config_e_cacheado_por_id_e_versao() -> None:
    a = load_model_config("embedding", "v1")
    b = load_model_config("embedding", "v1")

    assert a is b


def test_get_default_embedding_model_retorna_embedding_v1() -> None:
    config = get_default_embedding_model()

    assert config.id == "embedding"
    assert config.version == "v1"


def test_load_model_config_reranker_v1_tem_id_e_versao_corretos() -> None:
    config = load_model_config("reranker", "v1")

    assert isinstance(config, ModelConfig)
    assert config.id == "reranker"
    assert config.version == "v1"
    assert config.alias.strip() != ""


def test_get_default_reranker_model_retorna_reranker_v1() -> None:
    config = get_default_reranker_model()

    assert config.id == "reranker"
    assert config.version == "v1"


def test_model_config_e_imutavel() -> None:
    config = load_model_config("embedding", "v1")

    with pytest.raises(ValidationError):
        config.alias = "outro"
