"""Tipo de job de indexação (RAG-010).

Distingue a indexação inicial de uma reindexação (documento que já
está INDEXED e recebe uma nova versão).
"""

from enum import StrEnum


class IndexJobType(StrEnum):
    INDEX = "INDEX"
    REINDEX = "REINDEX"
