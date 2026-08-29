"""Testes de RAG-012: `InMemoryKnowledgeBaseRepository`.

Cobrem o contrato de `KnowledgeBaseRepositoryPort` diretamente: unicidade
de nome ACTIVE por tenant, isolamento por tenant, exclusão lógica e
paginação por cursor.
"""

from uuid import uuid4

import pytest

from adapters.knowledge_base_repository.in_memory import InMemoryKnowledgeBaseRepository
from packages.application.ports.knowledge_base_repository import KnowledgeBaseNameConflictError
from packages.domain.enums.knowledge_base_status import KnowledgeBaseStatus

TENANT_A = uuid4()
TENANT_B = uuid4()


@pytest.fixture
def repository() -> InMemoryKnowledgeBaseRepository:
    return InMemoryKnowledgeBaseRepository()


async def test_create_returns_active_knowledge_base(
    repository: InMemoryKnowledgeBaseRepository,
) -> None:
    kb = await repository.create(
        tenant_id=TENANT_A, name="Manuais", description="desc", config={"chunk_size": 512}
    )
    assert kb.tenant_id == TENANT_A
    assert kb.name == "Manuais"
    assert kb.status == KnowledgeBaseStatus.ACTIVE
    assert kb.created_at == kb.updated_at


async def test_create_rejects_duplicate_name_for_same_tenant(
    repository: InMemoryKnowledgeBaseRepository,
) -> None:
    await repository.create(tenant_id=TENANT_A, name="Manuais", description=None, config={})
    with pytest.raises(KnowledgeBaseNameConflictError):
        await repository.create(tenant_id=TENANT_A, name="Manuais", description=None, config={})


async def test_create_allows_same_name_for_different_tenants(
    repository: InMemoryKnowledgeBaseRepository,
) -> None:
    await repository.create(tenant_id=TENANT_A, name="Manuais", description=None, config={})
    kb_b = await repository.create(tenant_id=TENANT_B, name="Manuais", description=None, config={})
    assert kb_b.tenant_id == TENANT_B


async def test_get_by_id_returns_none_for_unknown_id(
    repository: InMemoryKnowledgeBaseRepository,
) -> None:
    assert await repository.get_by_id(tenant_id=TENANT_A, knowledge_base_id=uuid4()) is None


async def test_get_by_id_returns_none_for_other_tenant(
    repository: InMemoryKnowledgeBaseRepository,
) -> None:
    kb = await repository.create(tenant_id=TENANT_A, name="Manuais", description=None, config={})
    assert await repository.get_by_id(tenant_id=TENANT_B, knowledge_base_id=kb.id) is None


async def test_list_by_tenant_only_returns_that_tenant(
    repository: InMemoryKnowledgeBaseRepository,
) -> None:
    await repository.create(tenant_id=TENANT_A, name="A1", description=None, config={})
    await repository.create(tenant_id=TENANT_B, name="B1", description=None, config={})
    page = await repository.list_by_tenant(tenant_id=TENANT_A, limit=10, cursor=None)
    assert [kb.name for kb in page.items] == ["A1"]
    assert page.next_cursor is None


async def test_list_by_tenant_paginates_with_cursor(
    repository: InMemoryKnowledgeBaseRepository,
) -> None:
    for i in range(5):
        await repository.create(tenant_id=TENANT_A, name=f"kb-{i}", description=None, config={})

    first_page = await repository.list_by_tenant(tenant_id=TENANT_A, limit=2, cursor=None)
    assert [kb.name for kb in first_page.items] == ["kb-0", "kb-1"]
    assert first_page.next_cursor is not None

    second_page = await repository.list_by_tenant(
        tenant_id=TENANT_A, limit=2, cursor=first_page.next_cursor
    )
    assert [kb.name for kb in second_page.items] == ["kb-2", "kb-3"]
    assert second_page.next_cursor is not None

    third_page = await repository.list_by_tenant(
        tenant_id=TENANT_A, limit=2, cursor=second_page.next_cursor
    )
    assert [kb.name for kb in third_page.items] == ["kb-4"]
    assert third_page.next_cursor is None


async def test_update_changes_only_given_fields(
    repository: InMemoryKnowledgeBaseRepository,
) -> None:
    kb = await repository.create(
        tenant_id=TENANT_A, name="Manuais", description="antiga", config={"a": 1}
    )
    updated = await repository.update(
        tenant_id=TENANT_A, knowledge_base_id=kb.id, fields={"description": "nova"}
    )
    assert updated is not None
    assert updated.name == "Manuais"
    assert updated.description == "nova"
    assert updated.config == {"a": 1}
    assert updated.updated_at >= kb.updated_at


async def test_update_can_clear_description(repository: InMemoryKnowledgeBaseRepository) -> None:
    kb = await repository.create(
        tenant_id=TENANT_A, name="Manuais", description="antiga", config={}
    )
    updated = await repository.update(
        tenant_id=TENANT_A, knowledge_base_id=kb.id, fields={"description": None}
    )
    assert updated is not None
    assert updated.description is None


async def test_update_rejects_rename_to_existing_name(
    repository: InMemoryKnowledgeBaseRepository,
) -> None:
    await repository.create(tenant_id=TENANT_A, name="Manuais", description=None, config={})
    kb2 = await repository.create(tenant_id=TENANT_A, name="Políticas", description=None, config={})
    with pytest.raises(KnowledgeBaseNameConflictError):
        await repository.update(
            tenant_id=TENANT_A, knowledge_base_id=kb2.id, fields={"name": "Manuais"}
        )


async def test_update_allows_keeping_own_name(repository: InMemoryKnowledgeBaseRepository) -> None:
    kb = await repository.create(tenant_id=TENANT_A, name="Manuais", description=None, config={})
    updated = await repository.update(
        tenant_id=TENANT_A, knowledge_base_id=kb.id, fields={"name": "Manuais"}
    )
    assert updated is not None
    assert updated.name == "Manuais"


async def test_update_returns_none_for_other_tenant(
    repository: InMemoryKnowledgeBaseRepository,
) -> None:
    kb = await repository.create(tenant_id=TENANT_A, name="Manuais", description=None, config={})
    result = await repository.update(
        tenant_id=TENANT_B, knowledge_base_id=kb.id, fields={"name": "Outro"}
    )
    assert result is None


async def test_soft_delete_hides_from_get_and_list(
    repository: InMemoryKnowledgeBaseRepository,
) -> None:
    kb = await repository.create(tenant_id=TENANT_A, name="Manuais", description=None, config={})
    assert await repository.soft_delete(tenant_id=TENANT_A, knowledge_base_id=kb.id) is True
    assert await repository.get_by_id(tenant_id=TENANT_A, knowledge_base_id=kb.id) is None
    page = await repository.list_by_tenant(tenant_id=TENANT_A, limit=10, cursor=None)
    assert page.items == []


async def test_soft_delete_is_reported_false_when_already_gone(
    repository: InMemoryKnowledgeBaseRepository,
) -> None:
    kb = await repository.create(tenant_id=TENANT_A, name="Manuais", description=None, config={})
    assert await repository.soft_delete(tenant_id=TENANT_A, knowledge_base_id=kb.id) is True
    assert await repository.soft_delete(tenant_id=TENANT_A, knowledge_base_id=kb.id) is False


async def test_soft_delete_returns_false_for_other_tenant(
    repository: InMemoryKnowledgeBaseRepository,
) -> None:
    kb = await repository.create(tenant_id=TENANT_A, name="Manuais", description=None, config={})
    assert await repository.soft_delete(tenant_id=TENANT_B, knowledge_base_id=kb.id) is False
    # A base continua ativa para o dono de verdade.
    assert await repository.get_by_id(tenant_id=TENANT_A, knowledge_base_id=kb.id) is not None


async def test_create_after_soft_delete_reuses_the_name(
    repository: InMemoryKnowledgeBaseRepository,
) -> None:
    kb = await repository.create(tenant_id=TENANT_A, name="Manuais", description=None, config={})
    await repository.soft_delete(tenant_id=TENANT_A, knowledge_base_id=kb.id)
    recreated = await repository.create(
        tenant_id=TENANT_A, name="Manuais", description=None, config={}
    )
    assert recreated.id != kb.id
    assert recreated.status == KnowledgeBaseStatus.ACTIVE


async def test_get_by_id_unscoped_returns_active_knowledge_base_without_tenant_filter(
    repository: InMemoryKnowledgeBaseRepository,
) -> None:
    kb = await repository.create(tenant_id=TENANT_A, name="Manuais", description=None, config={})

    found = await repository.get_by_id_unscoped(knowledge_base_id=kb.id)

    assert found is not None
    assert found.id == kb.id


async def test_get_by_id_unscoped_returns_none_for_unknown_id(
    repository: InMemoryKnowledgeBaseRepository,
) -> None:
    assert await repository.get_by_id_unscoped(knowledge_base_id=uuid4()) is None


async def test_get_by_id_unscoped_returns_deleted_knowledge_bases_too(
    repository: InMemoryKnowledgeBaseRepository,
) -> None:
    kb = await repository.create(tenant_id=TENANT_A, name="Manuais", description=None, config={})
    await repository.soft_delete(tenant_id=TENANT_A, knowledge_base_id=kb.id)

    found = await repository.get_by_id_unscoped(knowledge_base_id=kb.id)

    assert found is not None
    assert found.status == KnowledgeBaseStatus.DELETED
