"""Testes de RAG-004: Settings da aplicação via Pydantic Settings.

Cobrem os dois critérios de aceite da atividade:
  * startup falha com mensagem segura quando configuração obrigatória falta;
  * segredos não aparecem em logs (repr/str sempre mascarados).
"""

from collections.abc import Iterator

import pytest

from packages.config.settings import (
    ConfigurationError,
    Environment,
    get_settings,
    load_settings,
)

REQUIRED_SECRET_VARS = ("POSTGRES_PASSWORD", "MINIO_ROOT_PASSWORD")


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """Garante que get_settings() não vaze cache entre testes."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Remove do ambiente as variáveis de configuração conhecidas, para que
    cada teste controle exatamente o que está (ou não) definido — mesmo que
    a máquina de desenvolvimento tenha um `.env` real (ver README/RAG-003).
    """
    for name in (
        "ENVIRONMENT",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "REDIS_HOST",
        "REDIS_PORT",
        "MINIO_HOST",
        "MINIO_API_PORT",
        "MINIO_ROOT_USER",
        "MINIO_ROOT_PASSWORD",
        "MINIO_USE_SSL",
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_missing_required_secrets_raises_configuration_error(
    clean_env: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        load_settings(env_file=None)

    message = str(exc_info.value)
    assert "Configuração obrigatória ausente" in message
    for var in REQUIRED_SECRET_VARS:
        assert var.lower() in message.lower()


def test_configuration_error_never_leaks_a_provided_value(
    clean_env: pytest.MonkeyPatch,
) -> None:
    """Mesmo com um valor inválido fornecido, a mensagem de erro não deve
    ecoar esse valor de volta (apenas o nome do campo problemático)."""
    clean_env.setenv("POSTGRES_PORT", "not-a-port-number")
    clean_env.setenv("POSTGRES_PASSWORD", "s3cr3t-should-not-leak")
    clean_env.setenv("MINIO_ROOT_PASSWORD", "another-secret-should-not-leak")

    with pytest.raises(ConfigurationError) as exc_info:
        load_settings(env_file=None)

    message = str(exc_info.value)
    assert "s3cr3t-should-not-leak" not in message
    assert "not-a-port-number" not in message


def test_settings_loads_with_required_secrets_and_masks_them_in_repr(
    clean_env: pytest.MonkeyPatch,
) -> None:
    clean_env.setenv("POSTGRES_PASSWORD", "s3cr3t-should-not-leak")
    clean_env.setenv("MINIO_ROOT_PASSWORD", "another-secret-should-not-leak")

    settings = load_settings(env_file=None)

    assert settings.environment is Environment.LOCAL
    assert settings.postgres_password.get_secret_value() == "s3cr3t-should-not-leak"

    rendered = f"{settings!r} {settings}"
    assert "s3cr3t-should-not-leak" not in rendered
    assert "another-secret-should-not-leak" not in rendered
    assert "**********" in rendered


def test_computed_urls_use_the_real_secret_value(
    clean_env: pytest.MonkeyPatch,
) -> None:
    clean_env.setenv("POSTGRES_PASSWORD", "s3cr3t-should-not-leak")
    clean_env.setenv("MINIO_ROOT_PASSWORD", "another-secret-should-not-leak")

    settings = load_settings(env_file=None)

    assert settings.database_url == (
        "postgresql+asyncpg://rag_platform:s3cr3t-should-not-leak@localhost:5432/rag_platform"
    )
    assert settings.redis_url == "redis://localhost:6379/0"
    assert settings.minio_endpoint == "localhost:9000"
    assert settings.minio_endpoint_url == "http://localhost:9000"


def test_minio_endpoint_url_uses_https_when_ssl_is_enabled(
    clean_env: pytest.MonkeyPatch,
) -> None:
    clean_env.setenv("POSTGRES_PASSWORD", "s3cr3t-should-not-leak")
    clean_env.setenv("MINIO_ROOT_PASSWORD", "another-secret-should-not-leak")
    clean_env.setenv("MINIO_USE_SSL", "true")

    settings = load_settings(env_file=None)

    assert settings.minio_endpoint_url == "https://localhost:9000"


def test_get_settings_is_cached_per_process(
    clean_env: pytest.MonkeyPatch,
) -> None:
    clean_env.setenv("POSTGRES_PASSWORD", "s3cr3t-should-not-leak")
    clean_env.setenv("MINIO_ROOT_PASSWORD", "another-secret-should-not-leak")
    # get_settings() usa load_settings(), que por padrão também tentaria ler
    # um .env do diretório atual; como não há um no ambiente de testes,
    # basta que as variáveis de ambiente acima já sejam suficientes.

    first = get_settings()
    second = get_settings()

    assert first is second
