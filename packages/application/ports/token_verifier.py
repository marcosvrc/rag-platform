"""Porta de verificação de tokens de acesso (RAG-050).

Casos de uso e a API dependem só desta porta, nunca de PyJWT ou de um
SDK concreto de IdP (seção 5.1 do plano) — trocar HS256 por RS256/JWKS,
ou trocar o provedor de identidade simulado local por um OIDC de
verdade, é uma questão de trocar o adapter usado, sem tocar em quem
chama `verify()`.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class TokenClaims:
    """Claims validadas de um token de acesso.

    Só os campos que a aplicação efetivamente usa — não o payload JWT
    bruto — para que o resto do código não fique acoplado ao formato de
    claims de um IdP específico. `tenant_id` é `None` quando o token não
    carrega essa claim (nem todo token de acesso precisa identificar um
    tenant; quem exige isso é quem chama, não esta porta).
    """

    subject: str
    tenant_id: UUID | None
    issuer: str
    expires_at: datetime


class TokenVerifierPort(ABC):
    """Valida um token de acesso (ex.: um `Authorization: Bearer <token>`)
    e devolve suas claims. Todo adapter (`adapters/token_verifier/`)
    implementa isso."""

    @abstractmethod
    def verify(self, token: str) -> TokenClaims:
        """Valida `token` e devolve suas claims.

        Levanta `packages.application.errors.AuthenticationError`
        (nunca uma exceção específica de biblioteca) se o token for
        inválido por qualquer motivo — assinatura, issuer, audience,
        expiração ou claims obrigatórias ausentes/malformadas."""
