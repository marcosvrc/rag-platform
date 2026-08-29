"""Emite um JWT de desenvolvimento local, assinado com `JWT_SECRET` (RAG-050).

**Só para uso local.** Não é um provedor de identidade real — não há
OIDC, não há rotação de chave, é literalmente o mesmo segredo compartilhado
configurado em `.env`/`.env.example`. Nunca use este script (nem o
`JWT_SECRET` que ele lê) contra um ambiente de development ou production
de verdade (seção 13 do plano: "em modo local, provedor de identidade
simulado e explicitamente identificado como não produtivo").

Uso:

    python scripts/mint_local_dev_token.py --subject dev-user \\
        --tenant-id 11111111-1111-1111-1111-111111111111

O token gerado pode ser usado como `Authorization: Bearer <token>`
contra a API local. Requer que `JWT_SECRET`/`JWT_ISSUER`/`JWT_AUDIENCE`
estejam configurados (ver `.env`); falha com uma mensagem clara se
`JWT_ALGORITHM` não for HS* (algoritmo assimétrico não tem sentido para
um segredo local mintado por este script).
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt

from packages.config.settings import ConfigurationError, get_settings


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--subject", default="local-dev-user", help="Claim 'sub' do token (padrão: local-dev-user)."
    )
    parser.add_argument(
        "--tenant-id",
        type=str,
        default=None,
        help="Claim 'tenant_id' (UUID) — omitida se não fornecida.",
    )
    parser.add_argument(
        "--ttl-seconds",
        type=int,
        default=3600,
        help="Validade do token em segundos a partir de agora (padrão: 3600).",
    )
    return parser.parse_args(argv)


def mint_token(
    *, subject: str, tenant_id: str | None, ttl_seconds: int, now: datetime | None = None
) -> str:
    settings = get_settings()

    if not settings.jwt_algorithm.startswith("HS"):
        raise ConfigurationError(
            "Este script só emite tokens com um algoritmo HS* (segredo "
            f"compartilhado local); JWT_ALGORITHM está '{settings.jwt_algorithm}'."
        )
    if settings.jwt_secret is None:
        raise ConfigurationError("JWT_SECRET não configurado — defina-o em .env para o modo local.")

    if tenant_id is not None:
        # Falha cedo com uma mensagem clara em vez de deixar o servidor
        # rejeitar o token depois por uma claim malformada.
        UUID(tenant_id)

    issued_at = now or datetime.now(tz=UTC)
    payload: dict[str, object] = {
        "sub": subject,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": int(issued_at.timestamp()),
        "exp": int((issued_at + timedelta(seconds=ttl_seconds)).timestamp()),
    }
    if tenant_id is not None:
        payload["tenant_id"] = tenant_id

    return jwt.encode(
        payload, key=settings.jwt_secret.get_secret_value(), algorithm=settings.jwt_algorithm
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        token = mint_token(
            subject=args.subject, tenant_id=args.tenant_id, ttl_seconds=args.ttl_seconds
        )
    except ConfigurationError as exc:
        print(f"Erro de configuração: {exc}", file=sys.stderr)
        return 1

    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
