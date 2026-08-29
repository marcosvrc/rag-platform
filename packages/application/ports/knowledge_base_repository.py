"""Porta do repositório de bases de conhecimento (RAG-012).

Define o contrato que qualquer adapter (Postgres real, fake em memória
para testes) precisa implementar. Todo método recebe `tenant_id`
explicitamente — nenhuma consulta pode acontecer sem esse filtro
(seção 8 do plano: "toda consulta ao banco deve conter filtro de
tenant"). Um recurso de outro tenant é tratado exatamente como
inexistente: nenhum método aqui distingue "não existe" de "existe, mas
é de outro tenant" — do ponto de vista do chamador é o mesmo caso, o
que evita vazar a existência de recursos alheios (ver
`packages/application/errors.py::NotFoundError`).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from packages.domain.entities.knowledge_base import KnowledgeBase


class KnowledgeBaseNameConflictError(Exception):
    """Já existe uma base ACTIVE com esse nome para o tenant (unique
    constraint `tenant_id` + `name`, RAG-011)."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"Já existe uma base de conhecimento chamada {name!r} para este tenant.")


@dataclass(frozen=True, slots=True)
class KnowledgeBasePage:
    """Uma página de resultados de `list_by_tenant`.

    `next_cursor` é `None` quando esta é a última página.
    """

    items: list[KnowledgeBase]
    next_cursor: str | None


class KnowledgeBaseRepositoryPort(ABC):
    """Porta hexagonal (seção 5.1 do plano): `packages/application` só
    conhece esta interface, nunca SQLAlchemy/asyncpg diretamente."""

    @abstractmethod
    async def create(
        self,
        *,
        tenant_id: UUID,
        name: str,
        description: str | None,
        config: dict[str, Any],
    ) -> KnowledgeBase:
        """Cria uma base de conhecimento com status ACTIVE.

        Levanta `KnowledgeBaseNameConflictError` se já existir uma base
        ACTIVE com o mesmo nome para o tenant.
        """

    @abstractmethod
    async def get_by_id(self, *, tenant_id: UUID, knowledge_base_id: UUID) -> KnowledgeBase | None:
        """`None` se não existir, estiver excluída (DELETED) ou
        pertencer a outro tenant."""

    @abstractmethod
    async def list_by_tenant(
        self, *, tenant_id: UUID, limit: int, cursor: str | None
    ) -> KnowledgeBasePage:
        """Lista bases ACTIVE do tenant, em ordem estável (`created_at`,
        `id`), paginada por cursor opaco (`limit` itens por página)."""

    @abstractmethod
    async def update(
        self,
        *,
        tenant_id: UUID,
        knowledge_base_id: UUID,
        fields: Mapping[str, Any],
    ) -> KnowledgeBase | None:
        """Atualiza somente as chaves presentes em `fields` (chaves
        aceitas: `name`, `description`, `config`) — uma chave ausente
        fica inalterada; a validação de valores é responsabilidade da
        camada de aplicação (`packages/application/commands`).

        Retorna `None` se a base não existir/for de outro tenant.
        Levanta `KnowledgeBaseNameConflictError` se o novo `name`
        colidir com outra base ACTIVE do mesmo tenant.
        """

    @abstractmethod
    async def soft_delete(self, *, tenant_id: UUID, knowledge_base_id: UUID) -> bool:
        """Marca a base como DELETED (exclusão lógica).

        Retorna `False` se a base não existir, já estiver excluída ou
        for de outro tenant — a camada de aplicação trata isso como
        404, exatamente como um `get_by_id` que retorna `None`.
        """
