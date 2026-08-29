"""Estado do tenant (RAG-010).

A seção 9 do plano não detalha os valores possíveis; mantido mínimo:
um tenant está ativo ou suspenso.
"""

from enum import StrEnum


class TenantStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
