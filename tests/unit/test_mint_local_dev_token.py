"""Testes de RAG-050: `scripts/mint_local_dev_token.py`.

O script só serve para o modo local (segredo compartilhado) — cobre o
critério de aceite "modo local é isolado e documentado": o token que ele
gera é aceito pelo mesmo `PyJWTTokenVerifier` usado pela API, e o script
recusa rodar com um algoritmo assimétrico (não faz sentido "mintar" um
token com uma chave privada que este projeto não gerencia).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import SecretStr

from adapters.token_verifier.pyjwt_verifier import PyJWTTokenVerifier
from packages.config.settings import ConfigurationError, Settings
from scripts.mint_local_dev_token import main, mint_token

SECRET = "test-secret-do-not-use-elsewhere"
ISSUER = "rag-platform-tests"
AUDIENCE = "rag-platform-tests-api"


def _make_settings(**overrides: object) -> Settings:
    fields: dict[str, object] = {
        "_env_file": None,
        "POSTGRES_PASSWORD": SecretStr("x"),
        "MINIO_ROOT_PASSWORD": SecretStr("x"),
        "JWT_SECRET": SecretStr(SECRET),
        "JWT_ISSUER": ISSUER,
        "JWT_AUDIENCE": AUDIENCE,
    }
    fields.update(overrides)
    return Settings(**fields)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from packages.config import settings as settings_module

    settings_module.get_settings.cache_clear()
    monkeypatch.setenv("POSTGRES_PASSWORD", "x")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "x")
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setenv("JWT_ISSUER", ISSUER)
    monkeypatch.setenv("JWT_AUDIENCE", AUDIENCE)
    yield
    settings_module.get_settings.cache_clear()


def test_mint_token_is_accepted_by_the_real_verifier() -> None:
    token = mint_token(
        subject="dev-user",
        tenant_id="11111111-1111-1111-1111-111111111111",
        ttl_seconds=3600,
    )

    verifier = PyJWTTokenVerifier(_make_settings())
    claims = verifier.verify(token)

    assert claims.subject == "dev-user"
    assert claims.tenant_id == UUID("11111111-1111-1111-1111-111111111111")


def test_mint_token_without_tenant_id_omits_the_claim() -> None:
    token = mint_token(subject="dev-user", tenant_id=None, ttl_seconds=3600)

    verifier = PyJWTTokenVerifier(_make_settings())
    claims = verifier.verify(token)

    assert claims.tenant_id is None


def test_mint_token_rejects_invalid_tenant_id() -> None:
    with pytest.raises(ValueError):
        mint_token(subject="dev-user", tenant_id="not-a-uuid", ttl_seconds=3600)


def test_mint_token_refuses_asymmetric_algorithm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_ALGORITHM", "RS256")
    monkeypatch.setenv("JWT_PUBLIC_KEY", "irrelevant-for-this-test")
    from packages.config import settings as settings_module

    settings_module.get_settings.cache_clear()

    with pytest.raises(ConfigurationError):
        mint_token(subject="dev-user", tenant_id=None, ttl_seconds=3600)


def test_main_prints_token_and_returns_zero(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["--subject", "dev-user"])

    assert exit_code == 0
    printed = capsys.readouterr().out.strip()
    assert printed.count(".") == 2  # header.payload.signature


def test_main_returns_one_and_prints_error_on_configuration_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """`monkeypatch.chdir(tmp_path)` isola este teste de um `.env` real
    no diretório de trabalho do repositório (ex.: o que RAG-080 pede
    para criar antes de `make e2e`, RAG-004/settings.py:
    `env_file=".env"`, caminho relativo). Sem isso, `get_settings()`
    recuperaria `JWT_SECRET` do arquivo mesmo com a variável de
    ambiente removida por `monkeypatch.delenv` — o teste passaria a
    falhar sempre que um `.env` de verdade existisse no repositório,
    apesar de nenhuma mudança de comportamento real ter ocorrido."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    from packages.config import settings as settings_module

    settings_module.get_settings.cache_clear()

    exit_code = main(["--subject", "dev-user"])

    assert exit_code == 1
    assert "Erro de configuração" in capsys.readouterr().err


def test_mint_token_expires_at_the_requested_ttl() -> None:
    now = datetime.now(tz=UTC).replace(microsecond=0)
    token = mint_token(subject="dev-user", tenant_id=None, ttl_seconds=60, now=now)

    verifier = PyJWTTokenVerifier(_make_settings())
    claims = verifier.verify(token)

    assert claims.expires_at == now + timedelta(seconds=60)
