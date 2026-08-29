"""Testes de RAG-050: verificação de JWT (`PyJWTTokenVerifier`).

Cobre os critérios de aceite da atividade: assinatura, issuer, audience
e expiração são validados, e um token inválido por qualquer um desses
motivos é rejeitado (sempre como `AuthenticationError`, nunca uma
exceção de PyJWT vazando para fora do adapter).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
import pytest
from pydantic import SecretStr

from adapters.token_verifier.pyjwt_verifier import PyJWTTokenVerifier
from packages.application.errors import AuthenticationError
from packages.application.ports.token_verifier import TokenClaims
from packages.config.settings import ConfigurationError, Settings

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


def _make_token(
    *,
    secret: str = SECRET,
    algorithm: str = "HS256",
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    subject: str = "user-1",
    tenant_id: str | None = "11111111-1111-1111-1111-111111111111",
    issued_at: datetime | None = None,
    expires_in: timedelta = timedelta(minutes=5),
    extra_claims: dict[str, object] | None = None,
    omit_claims: tuple[str, ...] = (),
) -> str:
    now = issued_at or datetime.now(tz=UTC)
    payload: dict[str, object] = {
        "sub": subject,
        "iss": issuer,
        "aud": audience,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_in).timestamp()),
    }
    if tenant_id is not None:
        payload["tenant_id"] = tenant_id
    if extra_claims:
        payload.update(extra_claims)
    for claim in omit_claims:
        payload.pop(claim, None)
    return jwt.encode(payload, key=secret, algorithm=algorithm)


def test_verify_accepts_valid_token_and_extracts_claims() -> None:
    verifier = PyJWTTokenVerifier(_make_settings())
    token = _make_token()

    claims = verifier.verify(token)

    assert isinstance(claims, TokenClaims)
    assert claims.subject == "user-1"
    assert claims.tenant_id == UUID("11111111-1111-1111-1111-111111111111")
    assert claims.issuer == ISSUER
    assert claims.expires_at.tzinfo is not None


def test_verify_accepts_token_without_tenant_id_claim() -> None:
    verifier = PyJWTTokenVerifier(_make_settings())
    token = _make_token(tenant_id=None)

    claims = verifier.verify(token)

    assert claims.tenant_id is None


def test_verify_rejects_token_signed_with_wrong_secret() -> None:
    verifier = PyJWTTokenVerifier(_make_settings())
    token = _make_token(secret="a-completely-different-secret")

    with pytest.raises(AuthenticationError):
        verifier.verify(token)


def test_verify_rejects_expired_token() -> None:
    verifier = PyJWTTokenVerifier(_make_settings())
    token = _make_token(
        issued_at=datetime.now(tz=UTC) - timedelta(hours=1),
        expires_in=timedelta(minutes=1),
    )

    with pytest.raises(AuthenticationError):
        verifier.verify(token)


def test_verify_respects_leeway_for_clock_skew() -> None:
    # Expirou há 3s; leeway padrão (10s) deve tolerar isso.
    verifier = PyJWTTokenVerifier(_make_settings())
    token = _make_token(
        issued_at=datetime.now(tz=UTC) - timedelta(minutes=5, seconds=3),
        expires_in=timedelta(minutes=5),
    )

    claims = verifier.verify(token)

    assert claims.subject == "user-1"


def test_verify_rejects_wrong_issuer() -> None:
    verifier = PyJWTTokenVerifier(_make_settings())
    token = _make_token(issuer="someone-else")

    with pytest.raises(AuthenticationError):
        verifier.verify(token)


def test_verify_rejects_wrong_audience() -> None:
    verifier = PyJWTTokenVerifier(_make_settings())
    token = _make_token(audience="someone-elses-api")

    with pytest.raises(AuthenticationError):
        verifier.verify(token)


def test_verify_rejects_malformed_token_string() -> None:
    verifier = PyJWTTokenVerifier(_make_settings())

    with pytest.raises(AuthenticationError):
        verifier.verify("not-a-jwt-at-all")


@pytest.mark.parametrize("missing_claim", ["sub", "iss", "aud", "iat", "exp"])
def test_verify_rejects_token_missing_a_required_claim(missing_claim: str) -> None:
    verifier = PyJWTTokenVerifier(_make_settings())
    token = _make_token(omit_claims=(missing_claim,))

    with pytest.raises(AuthenticationError):
        verifier.verify(token)


def test_verify_rejects_tenant_id_claim_that_is_not_a_uuid() -> None:
    verifier = PyJWTTokenVerifier(_make_settings())
    token = _make_token(tenant_id=None, extra_claims={"tenant_id": "not-a-uuid"})

    with pytest.raises(AuthenticationError):
        verifier.verify(token)


def test_verify_rejects_token_signed_with_a_different_algorithm() -> None:
    # PyJWT já recusa isso (algoritmo do header != algorithms permitidos),
    # mas provamos explicitamente porque é a defesa contra o ataque clássico
    # de "alg confusion" (ex.: token dizendo alg=none).
    verifier = PyJWTTokenVerifier(_make_settings())
    token = jwt.encode(
        {
            "sub": "user-1",
            "iss": ISSUER,
            "aud": AUDIENCE,
            "iat": int(datetime.now(tz=UTC).timestamp()),
            "exp": int((datetime.now(tz=UTC) + timedelta(minutes=5)).timestamp()),
        },
        key=SECRET,
        algorithm="HS512",
    )

    with pytest.raises(AuthenticationError):
        verifier.verify(token)


def test_constructor_raises_configuration_error_for_unsupported_algorithm() -> None:
    with pytest.raises(ConfigurationError):
        PyJWTTokenVerifier(_make_settings(JWT_ALGORITHM="none"))


def test_constructor_raises_configuration_error_when_hs_algorithm_has_no_secret() -> None:
    with pytest.raises(ConfigurationError):
        PyJWTTokenVerifier(_make_settings(JWT_SECRET=None))


def test_constructor_raises_configuration_error_when_rs256_lacks_public_key() -> None:
    with pytest.raises(ConfigurationError):
        PyJWTTokenVerifier(_make_settings(JWT_ALGORITHM="RS256", JWT_SECRET=None))


def test_constructor_accepts_rs256_with_a_public_key_configured() -> None:
    # Não decodifica nada com uma chave assimétrica de verdade aqui (isso
    # exigiria gerar um par de chaves RSA/EC só para o teste) — só prova
    # que a construção usa `jwt_public_key`, não `jwt_secret`, quando o
    # algoritmo é assimétrico.
    verifier = PyJWTTokenVerifier(
        _make_settings(JWT_ALGORITHM="RS256", JWT_SECRET=None, JWT_PUBLIC_KEY="fake-public-key-pem")
    )

    assert verifier._key == "fake-public-key-pem"
    assert verifier._algorithm == "RS256"
