"""Verificador de JWT via PyJWT (RAG-050).

Único lugar que sabe qual algoritmo/chave usar e que fala diretamente
com a biblioteca PyJWT — o resto da aplicação depende só de
`TokenVerifierPort`.

**Modo local**: quando não há um IdP OIDC real disponível (ambiente
`local`/`development`, ver `packages/config/settings.py`), o "provedor
de identidade" é só um segredo compartilhado (`JWT_SECRET`, algoritmo
HS256 por padrão) configurado via `.env` — sem OIDC, sem JWKS, sem
rotação de chave. Isso é intencional e explicitamente documentado
(seção 13 do plano: "em modo local, provedor de identidade simulado e
explicitamente identificado como não produtivo"). `scripts/mint_local_dev_token.py`
gera tokens válidos para esse modo. Nunca reaproveite `JWT_SECRET`/
`JWT_ISSUER` de desenvolvimento em produção — em produção, configure um
algoritmo assimétrico (`JWT_ALGORITHM=RS256` e `JWT_PUBLIC_KEY`) com a
chave pública do IdP real.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import jwt

from packages.application.errors import AuthenticationError
from packages.application.ports.token_verifier import TokenClaims, TokenVerifierPort
from packages.config.settings import ConfigurationError, Settings

_HS_ALGORITHMS = frozenset({"HS256", "HS384", "HS512"})
_ASYMMETRIC_ALGORITHMS = frozenset({"RS256", "RS384", "RS512", "ES256", "ES384", "PS256", "PS384"})
_SUPPORTED_ALGORITHMS = _HS_ALGORITHMS | _ASYMMETRIC_ALGORITHMS

# Claims que todo token precisa ter para ser aceito, além de assinatura,
# issuer, audience e expiração (já validados por `jwt.decode`).
_REQUIRED_CLAIMS = ("exp", "iat", "sub", "iss", "aud")


class PyJWTTokenVerifier(TokenVerifierPort):
    """Implementação de `TokenVerifierPort` via PyJWT.

    A chave/algoritmo/issuer/audience são fixados na construção (a
    partir de `Settings`) — cada instância verifica só um emissor. Uma
    instância é segura para reuso entre requisições (não guarda estado
    de uma verificação para a próxima).
    """

    def __init__(self, settings: Settings) -> None:
        algorithm = settings.jwt_algorithm
        if algorithm not in _SUPPORTED_ALGORITHMS:
            raise ConfigurationError(
                f"JWT_ALGORITHM '{algorithm}' não suportado. Use um de: "
                + ", ".join(sorted(_SUPPORTED_ALGORITHMS))
                + "."
            )

        if algorithm in _HS_ALGORITHMS:
            if settings.jwt_secret is None:
                raise ConfigurationError(
                    f"JWT_SECRET é obrigatório para o algoritmo '{algorithm}'."
                )
            self._key: str = settings.jwt_secret.get_secret_value()
        else:
            if not settings.jwt_public_key:
                raise ConfigurationError(
                    f"JWT_PUBLIC_KEY é obrigatório para o algoritmo '{algorithm}'."
                )
            self._key = settings.jwt_public_key

        self._algorithm = algorithm
        self._issuer = settings.jwt_issuer
        self._audience = settings.jwt_audience
        self._leeway_seconds = settings.jwt_leeway_seconds

    def verify(self, token: str) -> TokenClaims:
        try:
            payload = jwt.decode(
                token,
                key=self._key,
                algorithms=[self._algorithm],
                issuer=self._issuer,
                audience=self._audience,
                leeway=self._leeway_seconds,
                options={"require": list(_REQUIRED_CLAIMS)},
            )
        except jwt.PyJWTError as exc:
            # Detalhe genérico de propósito: não dizer *por que* o token
            # falhou (assinatura errada vs. expirado vs. issuer errado)
            # evita dar a um atacante um oráculo para calibrar tentativas.
            raise AuthenticationError(detail="Token inválido ou expirado.") from exc

        tenant_id: UUID | None = None
        tenant_id_raw = payload.get("tenant_id")
        if tenant_id_raw is not None:
            try:
                tenant_id = UUID(str(tenant_id_raw))
            except ValueError as exc:
                raise AuthenticationError(
                    detail="Claim 'tenant_id' do token é inválida: não é um UUID."
                ) from exc

        return TokenClaims(
            subject=str(payload["sub"]),
            tenant_id=tenant_id,
            issuer=str(payload["iss"]),
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        )
