"""Estado da base de conhecimento (RAG-010).

Espelha o padrão de exclusão lógica citado no plano ("excluir
logicamente bases", épico de gestão de bases).
"""

from enum import StrEnum


class KnowledgeBaseStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DELETED = "DELETED"
