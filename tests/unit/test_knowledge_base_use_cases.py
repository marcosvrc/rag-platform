"""Testes de RAG-012: camada de aplicação (commands/queries) de bases de
conhecimento.

Cobrem a tradução de falhas do repositório para os erros de aplicação de
RAG-013 e a validação de regras de negócio (campos não anuláveis,
isolamento por tenant tratado como 404).
"""

from uuid import uuid4

import pytest

from adapters.knowledge_base_repository.in_memory import InMemoryKnowledgeBaseRepository
from packages.application.commands.knowledge_base import (
    create_knowledge_base,
    delete_knowledge_base,
    update_knowledge_base,
)
from packages.application.errors import ConflictError, NotFoundError, UnprocessableEntityError
from packages.application.queries.knowledge_base import get_knowledge_base, list_knowledge_bases

TENANT_A = uuid4()
TENANT_B = uuid4()


@pytest.fixture
def repository() -> InMemoryKnowledgeBaseRepository:
    return InMemoryKnowledgeBaseRepository()


async def test_create_knowledge_base_raises_conflict_on_duplicate_name(
    repository: InMemoryKnowledgeBaseRepository,
) -> None:
    await create_knowledge_base(
        repository, tenant_id=TENANT_A, name="Manuais", description=None, config={}
    )
    with pytest.raises(ConflictError):
        await create_knowledge_base(
            repository, tenant_id=TENANT_A, name="Manuais", description=None, config={}
        )


async def test_get_knowledge_base_raises_not_found_for_unknown_id(
    repository: InMemoryKnowledgeBaseRepository,
) -> None:
    with pytest.raises(NotFoundError):
        await get_knowledge_base(repository, tenant_id=TENANT_A, knowledge_base_id=uuid4())


async def test_get_knowledge_base_raises_not_found_for_other_tenant(
    repository: InMemoryKnowledgeBaseRepository,
) -> None:
    kb = await create_knowledge_base(
        repository, tenant_id=TENANT_A, name="Manuais", description=None, config={}
    )
    with pytest.raises(NotFoundError):
        await get_knowledge_base(repository, tenant_id=TENANT_B, knowledge_base_id=kb.id)


async def test_list_knowledge_bases_bounds_limit_to_max_page_size(
    repository: InMemoryKnowledgeBaseRepository,
) -> None:
    for i in range(3):
        await create_knowledge_base(
            repository, tenant_id=TENANT_A, name=f"kb-{i}", description=None, config={}
        )
    page = await list_knowledge_bases(repository, tenant_id=TENANT_A, cursor=None, limit=10_000)
    assert len(page.items) == 3


async def test_update_knowledge_base_rejects_unknown_field(
    repository: InMemoryKnowledgeBaseRepository,
) -> None:
    kb = await create_knowledge_base(
        repository, tenant_id=TENANT_A, name="Manuais", description=None, config={}
    )
    with pytest.raises(ValueError, match="status"):
        await update_knowledge_base(
            repository,
            tenant_id=TENANT_A,
            knowledge_base_id=kb.id,
            fields={"status": "DELETED"},
        )


async def test_update_knowledge_base_rejects_null_name(
    repository: InMemoryKnowledgeBaseRepository,
) -> None:
    kb = await create_knowledge_base(
        repository, tenant_id=TENANT_A, name="Manuais", description=None, config={}
    )
    with pytest.raises(UnprocessableEntityError):
        await update_knowledge_base(
            repository, tenant_id=TENANT_A, knowledge_base_id=kb.id, fields={"name": None}
        )


async def test_update_knowledge_base_rejects_null_config(
    repository: InMemoryKnowledgeBaseRepository,
) -> None:
    kb = await create_knowledge_base(
        repository, tenant_id=TENANT_A, name="Manuais", description=None, config={}
    )
    with pytest.raises(UnprocessableEntityError):
        await update_knowledge_base(
            repository, tenant_id=TENANT_A, knowledge_base_id=kb.id, fields={"config": None}
        )


async def test_update_knowledge_base_allows_clearing_description(
    repository: InMemoryKnowledgeBaseRepository,
) -> None:
    kb = await create_knowledge_base(
        repository, tenant_id=TENANT_A, name="Manuais", description="algo", config={}
    )
    updated = await update_knowledge_base(
        repository, tenant_id=TENANT_A, knowledge_base_id=kb.id, fields={"description": None}
    )
    assert updated.description is None


async def test_update_knowledge_base_raises_not_found_for_other_tenant(
    repository: InMemoryKnowledgeBaseRepository,
) -> None:
    kb = await create_knowledge_base(
        repository, tenant_id=TENANT_A, name="Manuais", description=None, config={}
    )
    with pytest.raises(NotFoundError):
        await update_knowledge_base(
            repository,
            tenant_id=TENANT_B,
            knowledge_base_id=kb.id,
            fields={"name": "Outro"},
        )


async def test_update_knowledge_base_raises_conflict_on_rename_collision(
    repository: InMemoryKnowledgeBaseRepository,
) -> None:
    await create_knowledge_base(
        repository, tenant_id=TENANT_A, name="Manuais", description=None, config={}
    )
    kb2 = await create_knowledge_base(
        repository, tenant_id=TENANT_A, name="Políticas", description=None, config={}
    )
    with pytest.raises(ConflictError):
        await update_knowledge_base(
            repository, tenant_id=TENANT_A, knowledge_base_id=kb2.id, fields={"name": "Manuais"}
        )


async def test_delete_knowledge_base_raises_not_found_for_unknown_id(
    repository: InMemoryKnowledgeBaseRepository,
) -> None:
    with pytest.raises(NotFoundError):
        await delete_knowledge_base(repository, tenant_id=TENANT_A, knowledge_base_id=uuid4())


async def test_delete_knowledge_base_raises_not_found_on_second_delete(
    repository: InMemoryKnowledgeBaseRepository,
) -> None:
    kb = await create_knowledge_base(
        repository, tenant_id=TENANT_A, name="Manuais", description=None, config={}
    )
    await delete_knowledge_base(repository, tenant_id=TENANT_A, knowledge_base_id=kb.id)
    with pytest.raises(NotFoundError):
        await delete_knowledge_base(repository, tenant_id=TENANT_A, knowledge_base_id=kb.id)


async def test_delete_knowledge_base_raises_not_found_for_other_tenant(
    repository: InMemoryKnowledgeBaseRepository,
) -> None:
    kb = await create_knowledge_base(
        repository, tenant_id=TENANT_A, name="Manuais", description=None, config={}
    )
    with pytest.raises(NotFoundError):
        await delete_knowledge_base(repository, tenant_id=TENANT_B, knowledge_base_id=kb.id)
