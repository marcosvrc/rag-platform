"""Testes de RAG-071: validação de `security/exceptions.yml`.

Cobrem os critérios de aceite "exceções têm prazo e justificativa":
  * arquivo ausente ou vazio é válido (nenhuma exceção registrada);
  * entrada sem `justification` ou sem `expires` é rejeitada;
  * `expires` em formato inválido é rejeitado;
  * exceção com `expires` no passado é rejeitada (vencida);
  * exceção com `expires` no futuro é aceita.
"""

import datetime as dt
from typing import Any

from scripts.check_security_exceptions import validate

TODAY = dt.date(2026, 8, 29)


def test_empty_data_has_no_errors() -> None:
    assert validate({}, TODAY) == []


def test_tool_with_no_entries_has_no_errors() -> None:
    data: dict[str, Any] = {"gitleaks": [], "bandit": None}
    assert validate(data, TODAY) == []


def test_entry_missing_justification_is_rejected() -> None:
    data = {"bandit": [{"id": "B101", "expires": "2027-01-01"}]}
    errors = validate(data, TODAY)
    assert len(errors) == 1
    assert "justification" in errors[0]


def test_entry_missing_expires_is_rejected() -> None:
    data = {"bandit": [{"id": "B101", "justification": "motivo"}]}
    errors = validate(data, TODAY)
    assert len(errors) == 1
    assert "expires" in errors[0]


def test_entry_with_invalid_expires_format_is_rejected() -> None:
    data = {"pip_audit": [{"id": "GHSA-xxxx", "justification": "motivo", "expires": "31/12/2026"}]}
    errors = validate(data, TODAY)
    assert len(errors) == 1
    assert "inválido" in errors[0]


def test_expired_entry_is_rejected() -> None:
    data = {"hadolint": [{"rule": "DL3008", "justification": "motivo", "expires": "2026-01-01"}]}
    errors = validate(data, TODAY)
    assert len(errors) == 1
    assert "expirada" in errors[0]


def test_future_expires_entry_is_accepted() -> None:
    data = {
        "gitleaks": [
            {
                "rule": "generic-api-key",
                "justification": "motivo",
                "expires": "2027-01-01",
            }
        ]
    }
    assert validate(data, TODAY) == []


def test_entry_expiring_today_is_accepted() -> None:
    data = {"bandit": [{"id": "B101", "justification": "motivo", "expires": "2026-08-29"}]}
    assert validate(data, TODAY) == []


def test_multiple_errors_are_all_reported() -> None:
    data = {
        "bandit": [{"id": "B101"}],
        "pip_audit": [{"id": "GHSA-x", "justification": "m", "expires": "2020-01-01"}],
    }
    errors = validate(data, TODAY)
    assert len(errors) == 3  # falta justification + falta expires + expirada
