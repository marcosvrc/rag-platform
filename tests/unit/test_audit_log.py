"""Testes de RAG-054: `InMemoryAuditLog` (contrato de `AuditLogPort`)
e `record_audit_event_safely` (nunca deixa uma falha de auditoria
derrubar a ação principal)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from adapters.audit_log.in_memory import InMemoryAuditLog
from packages.application.ports.audit_log import AuditLogPort, record_audit_event_safely

TENANT_A = uuid4()


@pytest.fixture
def audit_log() -> InMemoryAuditLog:
    return InMemoryAuditLog()


async def test_record_stores_all_required_fields(audit_log: InMemoryAuditLog) -> None:
    resource_id = uuid4()

    event = await audit_log.record(
        tenant_id=TENANT_A,
        actor="user-123",
        action="knowledge_base.create",
        resource_type="knowledge_base",
        resource_id=resource_id,
    )

    assert event.tenant_id == TENANT_A
    assert event.actor == "user-123"
    assert event.action == "knowledge_base.create"
    assert event.resource_type == "knowledge_base"
    assert event.resource_id == resource_id
    assert audit_log.events == [event]


async def test_record_sets_occurred_at_to_now_in_utc(audit_log: InMemoryAuditLog) -> None:
    before = datetime.now(UTC)

    event = await audit_log.record(
        tenant_id=TENANT_A,
        actor="user-123",
        action="document.upload",
        resource_type="document",
        resource_id=uuid4(),
    )

    after = datetime.now(UTC)
    assert event.occurred_at.tzinfo is not None
    assert before <= event.occurred_at <= after


async def test_record_appends_without_overwriting_previous_events(
    audit_log: InMemoryAuditLog,
) -> None:
    first = await audit_log.record(
        tenant_id=TENANT_A,
        actor="user-123",
        action="knowledge_base.create",
        resource_type="knowledge_base",
        resource_id=uuid4(),
    )
    second = await audit_log.record(
        tenant_id=TENANT_A,
        actor="user-123",
        action="knowledge_base.update",
        resource_type="knowledge_base",
        resource_id=uuid4(),
    )

    assert audit_log.events == [first, second]
    assert first.id != second.id


async def test_record_audit_event_safely_delegates_to_record(
    audit_log: InMemoryAuditLog,
) -> None:
    resource_id = uuid4()

    await record_audit_event_safely(
        audit_log,
        tenant_id=TENANT_A,
        actor="user-123",
        action="document.reindex",
        resource_type="document",
        resource_id=resource_id,
    )

    assert len(audit_log.events) == 1
    assert audit_log.events[0].action == "document.reindex"
    assert audit_log.events[0].resource_id == resource_id


class _AlwaysFailingAuditLog(AuditLogPort):
    async def record(self, **_kwargs: object) -> None:  # type: ignore[override]
        raise RuntimeError("banco de auditoria indisponível")


async def test_record_audit_event_safely_never_propagates_a_recording_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """O critério real deste teste: a chamada abaixo não levanta —
    uma ação administrativa já concluída nunca deve virar um erro 500
    só porque o registro de auditoria falhou."""
    with caplog.at_level(logging.ERROR):
        await record_audit_event_safely(
            _AlwaysFailingAuditLog(),
            tenant_id=TENANT_A,
            actor="user-123",
            action="knowledge_base.delete",
            resource_type="knowledge_base",
            resource_id=uuid4(),
        )

    assert any(record.levelno >= logging.ERROR for record in caplog.records)
