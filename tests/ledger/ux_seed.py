"""Deterministic demo events shared by the history/detail/unresolved
tests. Fixed Europe/Berlin clock times (settings TIME_ZONE) so presenter
strings and date-group labels are exact. Not collected by pytest."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.utils import timezone

from apps.expenses.models import ExpenseCategory
from apps.ledger.models import Event
from apps.ledger.services import create_event, revise_event
from apps.travel.models import Location, LocationType


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return timezone.make_aware(datetime(2026, 8, day, hour, minute))


def _create(
    event_type: str,
    *,
    effective_at: datetime,
    snapshot: dict[str, Any],
    complete: bool = True,
    tax_relevant: bool = False,
    employer_reimbursable: bool = False,
) -> Event:
    return create_event(
        event_type=event_type,
        effective_at=effective_at,
        snapshot=snapshot,
        complete=complete,
        tax_relevant=tax_relevant,
        employer_reimbursable=employer_reimbursable,
    )


def seed_demo_events() -> dict[str, Any]:
    """The shared demo dataset (see docs/UX_REFINEMENT_PLAN.md §5)."""
    home = Location.objects.create(
        name="Berlin",
        location_type=LocationType.RESIDENCE,
        is_default_residence=True,
        locality="Berlin",
    )
    office = Location.objects.create(
        name="Hamburg",
        location_type=LocationType.FIRST_WORKPLACE,
        locality="Hamburg",
    )
    ExpenseCategory.objects.get_or_create(
        code="furniture", defaults={"name": "Desk equipment"}
    )
    journey = _create(
        "journey",
        effective_at=at(4, 8, 3),
        snapshot={
            "transport_mode": "train",
            "origin_id": str(home.pk),
            "origin_name": "Berlin",
            "destination_id": str(office.pk),
            "destination_name": "Hamburg",
            "train_category": "ICE",
            "train_number": "78",
        },
        tax_relevant=True,
        employer_reimbursable=True,
    )
    wfh = _create(
        "work_from_home",
        effective_at=at(4, 7, 41),
        snapshot={"residence_id": str(home.pk), "residence_name": "Berlin"},
    )
    expense = _create(
        "expense",
        effective_at=at(4, 10, 0),
        snapshot={
            "description": "table",
            "amount": "249.00",
            "currency": "EUR",
            "category": "furniture",
            "category_name": "Desk equipment",
            "documentation_status": "attached",
        },
    )
    receipt = _create(
        "receipt_only",
        effective_at=at(4, 7, 56),
        snapshot={
            "reconciliation_status": "unmatched",
            "original_filename": "scan.png",
        },
    )
    activity = _create(
        "external_activity",
        effective_at=at(4, 9, 30),
        snapshot={
            "activity_type": "client_visit",
            "start_at": at(4, 9, 30).isoformat(),
            "end_at": at(4, 18, 20).isoformat(),
            "country_code": "DE",
            "three_month_limit_applies": None,
            "destination_id": str(office.pk),
            "destination_name": "Hamburg",
            "departure_context": "Berlin",
            "return_context": "Berlin",
        },
        complete=False,
    )
    earlier = _create(
        "journey",
        effective_at=at(3, 17, 41),
        snapshot={
            "transport_mode": "private_car",
            "origin_id": str(office.pk),
            "origin_name": "Hamburg",
            "destination_id": str(home.pk),
            "destination_name": "Berlin",
        },
        employer_reimbursable=True,
    )
    # Amended: a second revision on the WFH event.
    wfh_current = wfh.current_revision
    assert wfh_current is not None
    revise_event(
        event=wfh,
        effective_at=at(4, 7, 41),
        snapshot={**wfh_current.snapshot, "note": "added later"},
        complete=True,
        comment="Added a note",
    )
    return {
        "home": home,
        "office": office,
        "journey": journey,
        "wfh": wfh,
        "expense": expense,
        "receipt": receipt,
        "activity": activity,
        "earlier": earlier,
    }
