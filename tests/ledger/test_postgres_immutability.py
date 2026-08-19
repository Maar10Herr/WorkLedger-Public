from datetime import UTC, datetime

import pytest
from django.db import DatabaseError, connection

from apps.ledger.models import Event, EventRevision
from apps.ledger.services import create_event, revise_event

pytestmark = [pytest.mark.django_db(transaction=True)]


def test_postgresql_rejects_direct_revision_update() -> None:
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL constraint-trigger acceptance test")
    event = create_event(
        event_type="note",
        effective_at=datetime(2026, 8, 4, 8, 0, tzinfo=UTC),
        snapshot={"text": "original"},
        complete=True,
    )
    assert event.current_revision_id is not None

    with pytest.raises(DatabaseError):
        EventRevision.objects.filter(pk=event.current_revision_id).update(comment="tampered")


def test_postgresql_rejects_track_flag_change_without_revision() -> None:
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL event-trigger acceptance test")
    event = create_event(
        event_type="note",
        effective_at=datetime(2026, 8, 4, 8, 0, tzinfo=UTC),
        snapshot={"text": "original"},
        complete=True,
    )
    with pytest.raises(DatabaseError):
        Event.objects.filter(pk=event.pk).update(tax_relevant=True)


def test_postgresql_rejects_current_revision_rollback() -> None:
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL event-trigger acceptance test")
    event = create_event(
        event_type="note",
        effective_at=datetime(2026, 8, 4, 8, 0, tzinfo=UTC),
        snapshot={"text": "original"},
        complete=True,
    )
    original_id = event.current_revision_id
    revise_event(
        event=event,
        effective_at=datetime(2026, 8, 4, 8, 0, tzinfo=UTC),
        snapshot={"text": "corrected"},
        complete=True,
    )
    with pytest.raises(DatabaseError):
        Event.objects.filter(pk=event.pk).update(current_revision_id=original_id)
