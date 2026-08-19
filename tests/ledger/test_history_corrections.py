import re
from datetime import UTC, datetime

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.services import configure_pin
from apps.ledger.services import create_event

pytestmark = pytest.mark.django_db


def logged_in_client() -> Client:
    configure_pin("123456")
    client = Client()
    client.post(reverse("accounts:login"), {"pin": "123456"})
    return client


def test_history_shows_semantic_summary_and_tracks_without_raw_ids() -> None:
    create_event(
        event_type="note",
        effective_at=timezone.make_aware(datetime(2026, 8, 4, 10, 0)),
        snapshot={"note": "Missing evidence"},
        complete=False,
        tax_relevant=True,
        employer_reimbursable=True,
    )

    response = logged_in_client().get(reverse("ledger:history"))

    content = response.content.decode()
    assert response.status_code == 200
    # The presenter summary replaces the raw identifier on the card; the UUID
    # survives only in the detail URL (an attribute, never card text).
    cards = re.findall(r'<a[^>]*data-event-card="true"[^>]*>(.*?)</a>', content, re.S)
    assert len(cards) == 1
    card = cards[0].casefold()
    assert "note · 10:00" in card
    assert "incomplete" in card
    assert "tax" in card
    assert "employer" in card
    assert 'data-badge-incomplete="true"' in card
    assert 'data-badge-tax="true"' in card
    assert 'data-badge-employer="true"' in card
    assert re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", card) is None


def test_correction_creates_new_revision_and_preserves_original() -> None:
    event = create_event(
        event_type="note",
        effective_at=datetime(2026, 8, 4, 10, 0, tzinfo=UTC),
        snapshot={"note": "orignal"},
        complete=True,
    )
    original = event.current_revision
    assert original is not None

    response = logged_in_client().post(
        reverse("ledger:correct_event", kwargs={"event_id": event.pk}),
        {
            "effective_at": "2026-08-04T10:00",
            "field_note": "corrected",
            "complete": "on",
            "correction_comment": "Fixed typo",
            "tax_relevant": "on",
        },
    )

    event.refresh_from_db()
    original.refresh_from_db()
    assert response.status_code == 302
    assert event.revisions.count() == 2
    assert original.snapshot == {
        "note": "orignal",
        "tax_relevant": False,
        "employer_reimbursable": False,
    }
    assert event.current_revision is not None
    assert event.current_revision.parent_revision == original
    assert event.current_revision.snapshot["note"] == "corrected"
    assert event.current_revision.comment == "Fixed typo"
    assert event.tax_relevant is True


def test_correction_preserves_numeric_snapshot_types() -> None:
    event = create_event(
        event_type="note",
        effective_at=datetime(2026, 8, 4, 10, 0, tzinfo=UTC),
        snapshot={"days": 2, "distance_km": 12.5, "amount": "100.00"},
        complete=True,
    )
    response = logged_in_client().post(
        reverse("ledger:correct_event", kwargs={"event_id": event.pk}),
        {
            "effective_at": "2026-08-04T10:00",
            "field_days": "3",
            "field_distance_km": "13.75",
            "field_amount": "999.00",
            "complete": "on",
            "correction_comment": "Corrected quantities",
        },
    )

    event.refresh_from_db()
    assert response.status_code == 302
    assert event.current_revision is not None
    assert event.current_revision.snapshot["days"] == 3
    assert isinstance(event.current_revision.snapshot["days"], int)
    assert event.current_revision.snapshot["distance_km"] == 13.75
    assert isinstance(event.current_revision.snapshot["distance_km"], float)
    assert event.current_revision.snapshot["amount"] == "100.00"
