"""Testes de RAG-051: dependências de identidade e tenant
(`apps/api/dependencies.py`).

Complementam os testes de integração HTTP em
`tests/unit/test_knowledge_base_router.py` e
`tests/unit/test_document_router.py`: aqui as funções são chamadas
diretamente (sem passar pelo FastAPI `Depends`), para isolar o
comportamento de `get_current_identity`/`get_current_tenant_id` de
qualquer router específico — inclusive o caso que nenhum dos dois
routers hoje exercita: um token válido, mas sem a claim `tenant_id`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
import pytest
from pydantic import SecretStr

from adapters.token_verifier.pyjwt_verifier import PyJWTTokenVerifier
from apps.api.dependencies import get_current_identity, get_current_tenant_id, get_token_verifier
from packages.application.errors import AuthenticationError
from packages.application.ports.token_verifier import TokenClaims
from packages.config.settings import Settings

SECRET = "test-secret-dependencies-do-not-use-elsewhere"
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
    tenant_id: str | None = "11111111-1111-1111-1111-111111111111",
    subject: str = "user-1",
) -> str:
    now = datetime.now(tz=UTC)
    payload: dict[str, object] = {
        "sub": subject,
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    if tenant_id is not None:
        payload["tenant_id"] = tenant_id
    return jwt.encode(payload, key=SECRET, algorithm="HS256")


@pytest.fixture
def token_verifier() -> PyJWTTokenVerifier:
    verifier = get_token_verifier(_make_settings())
    assert isinstance(verifier, PyJWTTokenVerifier)
    return verifier


class TestGetCurrentIdentity:
    """`get_current_identity` exige `Authorization: Bearer <token>` e
    delega a verificação a `TokenVerifierPort` — sem nenhum mecanismo
    alternativo de resolução de identidade."""

    async def test_missing_authorization_header_raises_authentication_error(
        self, token_verifier: PyJWTTokenVerifier
    ) -> None:
        with pytest.raises(AuthenticationError):
            await get_current_identity(authorization=None, token_verifier=token_verifier)

    async def test_non_bearer_scheme_raises_authentication_error(
        self, token_verifier: PyJWTTokenVerifier
    ) -> None:
        with pytest.raises(AuthenticationError):
            await get_current_identity(
                authorization="Basic dXNlcjpwYXNz", token_verifier=token_verifier
            )

    async def test_bearer_with_empty_token_raises_authentication_error(
        self, token_verifier: PyJWTTokenVerifier
    ) -> None:
        with pytest.raises(AuthenticationError):
            await get_current_identity(authorization="Bearer ", token_verifier=token_verifier)

    async def test_invalid_token_raises_authentication_error(
        self, token_verifier: PyJWTTokenVerifier
    ) -> None:
        with pytest.raises(AuthenticationError):
            await get_current_identity(
                authorization="Bearer not-a-jwt", token_verifier=token_verifier
            )

    async def test_valid_token_returns_claims(self, token_verifier: PyJWTTokenVerifier) -> None:
        token = _make_token(subject="user-42")

        identity = await get_current_identity(
            authorization=f"Bearer {token}", token_verifier=token_verifier
        )

        assert isinstance(identity, TokenClaims)
        assert identity.subject == "user-42"
        assert identity.tenant_id == UUID("11111111-1111-1111-1111-111111111111")


class TestGetCurrentTenantId:
    """`get_current_tenant_id` torna `tenant_id` obrigatório — a claim é
    opcional em `TokenClaims` porque a porta não assume que todo token
    de acesso identifica um tenant (ver `token_verifier.py`)."""

    async def test_token_without_tenant_id_claim_raises_authentication_error(self) -> None:
        identity = TokenClaims(
            subject="user-1",
            tenant_id=None,
            issuer=ISSUER,
            expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        )

        with pytest.raises(AuthenticationError):
            await get_current_tenant_id(identity=identity)

    async def test_token_with_tenant_id_claim_resolves_it(self) -> None:
        tenant_id = uuid4()
        identity = TokenClaims(
            subject="user-1",
            tenant_id=tenant_id,
            issuer=ISSUER,
            expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        )

        resolved = await get_current_tenant_id(identity=identity)

        assert resolved == tenant_id
