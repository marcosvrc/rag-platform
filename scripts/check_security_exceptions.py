"""Valida `security/exceptions.yml` (RAG-071).

Cada exceção de segurança registrada precisa ter justificativa e prazo
de validade. Este script falha (código de saída 1) quando encontra uma
entrada:

- sem `justification` ou sem `expires`;
- com `expires` em formato inválido (esperado `AAAA-MM-DD`);
- com `expires` já vencido (a exceção precisa ser renovada ou o achado
  de segurança precisa ser corrigido).

Uso: ``python scripts/check_security_exceptions.py``
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from typing import Any

import yaml

EXCEPTIONS_PATH = Path(__file__).resolve().parent.parent / "security" / "exceptions.yml"
_REQUIRED_FIELDS = ("justification", "expires")


def load_exceptions(path: Path) -> dict[str, Any]:
    """Lê e faz o parse do arquivo de exceções.

    Um arquivo ausente ou vazio é tratado como "nenhuma exceção
    registrada" (não é um erro).
    """
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return raw or {}


def _iter_entries(data: dict[str, Any]) -> Any:
    for tool, entries in data.items():
        for entry in entries or []:
            yield tool, entry


def validate(data: dict[str, Any], today: dt.date) -> list[str]:
    """Retorna a lista de mensagens de erro (vazia se tudo estiver ok)."""
    errors: list[str] = []
    for tool, entry in _iter_entries(data):
        for field in _REQUIRED_FIELDS:
            if not entry.get(field):
                errors.append(f"[{tool}] entrada {entry!r} sem campo obrigatório '{field}'")

        expires_raw = entry.get("expires")
        if not expires_raw:
            continue

        try:
            expires = dt.date.fromisoformat(str(expires_raw))
        except ValueError:
            errors.append(f"[{tool}] campo 'expires' inválido (use AAAA-MM-DD): {expires_raw!r}")
            continue

        if expires < today:
            errors.append(
                f"[{tool}] exceção expirada em {expires.isoformat()} "
                f"(justificativa: {entry.get('justification')!r}) — "
                "renove o prazo ou corrija o achado"
            )
    return errors


def main() -> int:
    data = load_exceptions(EXCEPTIONS_PATH)
    errors = validate(data, dt.date.today())
    if errors:
        print("Exceções de segurança inválidas ou expiradas:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(f"{EXCEPTIONS_PATH} OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
