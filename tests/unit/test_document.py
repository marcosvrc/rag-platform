"""Testes da máquina de estados de Document (RAG-010, seção 9.1 do plano)."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from packages.domain.entities.document import Document
from packages.domain.enums.document_status import DocumentStatus
from packages.domain.exceptions.errors import InvalidStatusTransitionError

NOW = datetime.now(UTC)

VALID_TRANSITIONS = [
    (DocumentStatus.PENDING, DocumentStatus.PROCESSING),
    (DocumentStatus.PENDING, DocumentStatus.DELETED),
    (DocumentStatus.PROCESSING, DocumentStatus.INDEXED),
    (DocumentStatus.PROCESSING, DocumentStatus.FAILED),
    (DocumentStatus.PROCESSING, DocumentStatus.QUARANTINED),
    (DocumentStatus.PROCESSING, DocumentStatus.DELETED),
    (DocumentStatus.INDEXED, DocumentStatus.PROCESSING),
    (DocumentStatus.INDEXED, DocumentStatus.DELETED),
    (DocumentStatus.FAILED, DocumentStatus.DELETED),
    (DocumentStatus.QUARANTINED, DocumentStatus.DELETED),
]

INVALID_TRANSITIONS = [
    (DocumentStatus.PENDING, DocumentStatus.INDEXED),
    (DocumentStatus.PENDING, DocumentStatus.FAILED),
    (DocumentStatus.PENDING, DocumentStatus.QUARANTINED),
    (DocumentStatus.PROCESSING, DocumentStatus.PENDING),
    (DocumentStatus.INDEXED, DocumentStatus.FAILED),
    (DocumentStatus.INDEXED, DocumentStatus.QUARANTINED),
    (DocumentStatus.INDEXED, DocumentStatus.PENDING),
    (DocumentStatus.FAILED, DocumentStatus.PROCESSING),
    (DocumentStatus.FAILED, DocumentStatus.INDEXED),
    (DocumentStatus.QUARANTINED, DocumentStatus.PROCESSING),
    (DocumentStatus.QUARANTINED, DocumentStatus.INDEXED),
    (DocumentStatus.DELETED, DocumentStatus.PENDING),
    (DocumentStatus.DELETED, DocumentStatus.PROCESSING),
    (DocumentStatus.DELETED, DocumentStatus.INDEXED),
    (DocumentStatus.DELETED, DocumentStatus.FAILED),
    (DocumentStatus.DELETED, DocumentStatus.QUARANTINED),
]


def _make_document(status: DocumentStatus) -> Document:
    return Document(
        id=uuid4(),
        knowledge_base_id=uuid4(),
        name="contrato.pdf",
        mime_type="application/pdf",
        checksum="deadbeef",
        status=status,
        created_at=NOW,
    )


@pytest.mark.parametrize(("current", "target"), VALID_TRANSITIONS)
def test_valid_transitions_succeed(current: DocumentStatus, target: DocumentStatus) -> None:
    document = _make_document(current)

    updated = document.transition_to(target)

    assert updated.status is target
    # A entidade original permanece imutável.
    assert document.status is current


@pytest.mark.parametrize(("current", "target"), INVALID_TRANSITIONS)
def test_invalid_transitions_fail(current: DocumentStatus, target: DocumentStatus) -> None:
    document = _make_document(current)

    with pytest.raises(InvalidStatusTransitionError) as exc_info:
        document.transition_to(target)

    assert exc_info.value.entity == "Document"
    assert exc_info.value.current == current.value
    assert exc_info.value.attempted == target.value


def test_deleted_is_terminal() -> None:
    document = _make_document(DocumentStatus.DELETED)

    for target in DocumentStatus:
        with pytest.raises(InvalidStatusTransitionError):
            document.transition_to(target)


def test_document_is_frozen() -> None:
    document = _make_document(DocumentStatus.PENDING)

    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
        document.status = DocumentStatus.PROCESSING


def test_every_non_listed_transition_is_rejected() -> None:
    """Cobertura exaustiva: qualquer par (atual, alvo) que não esteja em
    VALID_TRANSITIONS deve falhar, incluindo auto-transições."""
    allowed = set(VALID_TRANSITIONS)
    for current in DocumentStatus:
        for target in DocumentStatus:
            if (current, target) in allowed:
                continue
            document = _make_document(current)
            with pytest.raises(InvalidStatusTransitionError):
                document.transition_to(target)
