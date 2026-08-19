from datetime import UTC, datetime

import pytest
from django.core.exceptions import ValidationError
from django.db import DatabaseError, connection

from apps.ledger.models import EventRevision
from apps.ledger.services import create_event, revise_event, verify_audit_chain

pytestmark = pytest.mark.django_db


def test_incomplete_event_is_saved_as_first_immutable_revision() -> None:
    event = create_event(
        event_type="journey",
        effective_at=datetime(2026, 8, 4, 8, 0, tzinfo=UTC),
        snapshot={"transport_mode": "train"},
        complete=False,
    )

    revision = event.current_revision
    assert revision is not None
    assert revision.revision_number == 1
    assert revision.parent_revision is None
    assert revision.complete is False
    assert revision.snapshot["transport_mode"] == "train"
    assert len(revision.audit_hash) == 64
    assert verify_audit_chain().valid is True


def test_edit_creates_revision_and_preserves_original() -> None:
    event = create_event(
        event_type="journey",
        effective_at=datetime(2026, 8, 4, 8, 0, tzinfo=UTC),
        snapshot={"transport_mode": "train"},
        complete=False,
    )
    original = event.current_revision

    updated = revise_event(
        event=event,
        effective_at=datetime(2026, 8, 4, 8, 0, tzinfo=UTC),
        snapshot={"transport_mode": "train", "train_number": "ICE 78"},
        complete=True,
        comment="Added train after arrival",
    )

    assert original is not None
    original.refresh_from_db()
    assert EventRevision.objects.filter(event=event).count() == 2
    assert original.snapshot == {
        "transport_mode": "train",
        "tax_relevant": False,
        "employer_reimbursable": False,
    }
    assert updated.parent_revision == original
    assert updated.revision_number == 2
    assert updated.previous_audit_hash == original.audit_hash
    assert event.current_revision == updated


def test_revision_cannot_be_updated_or_deleted_through_model() -> None:
    event = create_event(
        event_type="note",
        effective_at=datetime(2026, 8, 4, 8, 0, tzinfo=UTC),
        snapshot={"text": "original"},
        complete=True,
    )
    revision = event.current_revision
    assert revision is not None

    revision.comment = "mutation"
    with pytest.raises(ValidationError):
        revision.save()
    with pytest.raises(ValidationError):
        revision.delete()


def test_tamper_detection_reports_changed_revision() -> None:
    event = create_event(
        event_type="note",
        effective_at=datetime(2026, 8, 4, 8, 0, tzinfo=UTC),
        snapshot={"text": "original"},
        complete=True,
    )
    revision = event.current_revision
    assert revision is not None
    if connection.vendor == "postgresql":
        with pytest.raises(DatabaseError):
            EventRevision.objects.filter(pk=revision.pk).update(snapshot={"text": "tampered"})
        return
    EventRevision.objects.filter(pk=revision.pk).update(snapshot={"text": "tampered"})

    result = verify_audit_chain()

    assert result.valid is False
    assert result.broken_revision_id == revision.pk
