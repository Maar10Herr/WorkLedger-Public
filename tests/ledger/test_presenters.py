# ruff: noqa: RUF002  (docstrings quote the mandated en-dash time ranges)
"""Pure presenter contract for `apps/ledger/presenters.py`.

Typed, deterministic summaries/badges/missing actions derived from the
current snapshot — never stored, no display strings in the schema. These
tests exercise the presenter directly (no HTTP, no browser) so the exact
mandated summary strings are pinned as pure behaviour.

Mandate examples:
  journey train  -> "08:03 · berlin → hamburg · ice 78"
  journey car    -> "17:41 · berlin → hamburg · private car" (route order)
  wfh            -> "07:41 · work from home · berlin"
  expense        -> "table · €249 · office furniture"
  receipt        -> "receipt · unlinked · uploaded 07:56"
  activity       -> "client visit · 09:30–18:20 · per diem incomplete"
  fallback       -> "note · 10:00" (safe, never a raw identifier)
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.ledger.models import Event
from apps.ledger.presenters import FIELD_LABELS, DateGroup, EventSummary, MissingAction, present
from apps.ledger.services import create_event, revise_event

pytestmark = pytest.mark.django_db

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _at(hour: int, minute: int = 0) -> datetime:
    """A deterministic Europe/Berlin local clock time (settings TIME_ZONE)."""
    return timezone.make_aware(datetime(2026, 8, 4, hour, minute))


def _event(
    event_type: str,
    snapshot: dict[str, object],
    *,
    effective_at: datetime | None = None,
    complete: bool = True,
    tax_relevant: bool = False,
    employer_reimbursable: bool = False,
) -> Event:
    return create_event(
        event_type=event_type,
        effective_at=effective_at or _at(8, 3),
        snapshot=snapshot,
        complete=complete,
        tax_relevant=tax_relevant,
        employer_reimbursable=employer_reimbursable,
    )


# ---------------------------------------------------------------------------
# Primary summary strings (the mandated matrix)
# ---------------------------------------------------------------------------


def test_journey_train_summary_is_semantic() -> None:
    event = _event(
        "journey",
        {
            "transport_mode": "train",
            "origin_name": "Berlin",
            "destination_name": "Hamburg",
            "train_category": "ICE",
            "train_number": "78",
        },
        effective_at=_at(8, 3),
    )
    summary = present(event)
    assert isinstance(summary, EventSummary)
    assert summary.title == "08:03 · berlin → hamburg · ice 78"


def test_journey_car_summary_uses_transport_label_and_route_order() -> None:
    event = _event(
        "journey",
        {
            "transport_mode": "private_car",
            "origin_name": "Hamburg",
            "destination_name": "Berlin",
        },
        effective_at=_at(17, 41),
    )
    assert present(event).title == "17:41 · hamburg → berlin · private car"


def test_wfh_summary_includes_location() -> None:
    event = _event(
        "work_from_home",
        {"residence_name": "Berlin"},
        effective_at=_at(7, 41),
    )
    assert present(event).title == "07:41 · work from home · berlin"


def test_wfh_summary_without_residence_is_still_safe() -> None:
    event = _event("work_from_home", {}, complete=False)
    assert present(event).title == "08:03 · work from home"


def test_expense_summary_category_and_amount() -> None:
    event = _event(
        "expense",
        {
            "description": "table",
            "amount": "249.00",
            "category_name": "Office furniture",
            "currency": "EUR",
        },
    )
    assert present(event).title == "table · €249 · office furniture"


def test_expense_summary_without_description_falls_back_to_category() -> None:
    event = _event("expense", {"amount": "19.80", "category_name": "Taxi"})
    assert present(event).title == "expense · €19.80 · taxi"


def test_receipt_summary_unlinked() -> None:
    event = _event(
        "receipt_only",
        {"reconciliation_status": "unmatched"},
        effective_at=_at(7, 56),
    )
    assert present(event).title == "receipt · unlinked · uploaded 07:56"


def test_receipt_summary_linked() -> None:
    event = _event("receipt_only", {"reconciliation_status": "matched"})
    assert present(event).title == "receipt · linked · uploaded 08:03"


def test_activity_summary_incomplete_per_diem() -> None:
    event = _event(
        "external_activity",
        {
            "activity_type": "client_visit",
            "start_at": _at(9, 30).isoformat(),
            "end_at": _at(18, 20).isoformat(),
            "country_code": "DE",
            "three_month_limit_applies": None,
        },
    )
    assert present(event).title == "client visit · 09:30\u201318:20 · per diem incomplete"


def test_activity_summary_still_ongoing() -> None:
    event = _event(
        "external_activity",
        {
            "activity_type": "client_visit",
            "start_at": _at(9, 30).isoformat(),
            "still_ongoing": True,
        },
    )
    assert present(event).title == "client visit · 09:30\u2013ongoing · per diem incomplete"


def test_fallback_summary_is_safe_and_deterministic() -> None:
    event = _event("note", {"note": "something"}, effective_at=_at(10, 0))
    assert present(event).title == "note · 10:00"


def test_metadata_and_date_group_are_human() -> None:
    event = _event("work_from_home", {"residence_name": "Berlin"})
    summary = present(event)
    assert summary.meta == "tuesday, 4 august · 08:03"
    assert isinstance(summary.date_group, DateGroup)
    assert summary.date_group.key == "2026-08-04"
    assert summary.date_group.label == "tuesday, 4 august"


# ---------------------------------------------------------------------------
# Never expose raw identifiers in ordinary summaries
# ---------------------------------------------------------------------------


def test_summary_never_contains_uuid() -> None:
    cases: list[tuple[str, dict[str, object]]] = [
        ("journey", {"transport_mode": "train", "origin_name": "Berlin",
                     "destination_name": "Hamburg", "train_category": "ICE",
                     "train_number": "78", "origin_id": "11111111-1111-4111-8111-111111111111"}),
        ("work_from_home", {"residence_name": "Berlin",
                            "residence_id": "22222222-2222-4222-8222-222222222222"}),
        ("expense", {"description": "table", "amount": "249.00",
                     "category_name": "Office furniture", "category": "office_furniture"}),
        ("receipt_only", {"reconciliation_status": "unmatched"}),
        (
            "external_activity",
            {"activity_type": "client_visit", "start_at": _at(9, 30).isoformat()},
        ),
        ("note", {"note": "hello"}),
        ("attachment_upload", {"original_filename": "scan.png"}),
    ]
    for event_type, snapshot in cases:
        event = _event(event_type, snapshot, complete=event_type != "note")
        summary = present(event)
        joined = " ".join(
            [summary.title, summary.meta, *[badge.label for badge in summary.badges]]
        )
        assert UUID_RE.search(joined) is None, f"{event_type} leaked an identifier into {joined!r}"
        assert UUID_RE.search(str(summary.date_group.label)) is None


def test_summary_uses_user_vocabulary_not_db_terminology() -> None:
    event = _event(
        "expense",
        {"description": "table", "amount": "249.00", "category_name": "Office furniture"},
    )
    joined = f"{present(event).title} {present(event).meta}".casefold()
    for forbidden in ("event_type", "snapshot", "revision", "location id", "category code", "uuid"):
        assert forbidden not in joined


# ---------------------------------------------------------------------------
# Badges — useful set only
# ---------------------------------------------------------------------------


def test_badges_dual_track() -> None:
    event = _event(
        "expense",
        {"description": "table", "amount": "249.00", "category_name": "Office furniture"},
        tax_relevant=True,
        employer_reimbursable=True,
    )
    keys = [badge.key for badge in present(event).badges]
    assert "tax" in keys
    assert "employer" in keys

    plain = _event("expense", {"description": "table", "amount": "249.00"})
    keys = [badge.key for badge in present(plain).badges]
    assert "tax" not in keys
    assert "employer" not in keys


def test_badges_incomplete_and_amended() -> None:
    incomplete = _event("journey", {"transport_mode": "train"}, complete=False)
    assert [badge.key for badge in present(incomplete).badges] == ["incomplete"]

    event = _event("journey", {"transport_mode": "train", "destination_name": "Hamburg"})
    assert "amended" not in [badge.key for badge in present(event).badges]
    assert event.current_revision is not None
    revise_event(
        event=event,
        effective_at=_at(9, 0),
        snapshot={**event.current_revision.snapshot, "note": "amended"},
        complete=True,
        comment="Correction",
    )
    assert "amended" in [badge.key for badge in present(event).badges]


def test_badges_reimbursed_and_receipt_missing() -> None:
    from apps.expenses.models import Expense, ExpenseCategory

    category = ExpenseCategory.objects.get(code="meal_actual")
    event = _event(
        "expense", {"description": "lunch", "amount": "12.00", "category_name": "Meal actual"}
    )
    Expense.objects.create(
        event=event,
        category=category,
        reimbursement_status=Expense.ReimbursementStatus.REIMBURSED,
        reimbursed_amount=Decimal("12.00"),
    )
    keys = [badge.key for badge in present(event).badges]
    assert "reimbursed" in keys
    assert "receipt missing" in [badge.label for badge in present(event).badges]

    attached = _event(
        "expense",
        {"description": "lunch", "amount": "12.00", "category_name": "Meal actual",
         "documentation_status": "attached"},
    )
    labels = [badge.label for badge in present(attached).badges]
    assert "receipt missing" not in labels


def test_badge_labels_are_the_six_useful_labels() -> None:
    labels = {badge.label for badge in present(
        _event("expense", {"description": "x", "amount": "1.00"}, complete=False,
               tax_relevant=True, employer_reimbursable=True)
    ).badges}
    assert labels <= {"incomplete", "tax", "employer", "reimbursed", "receipt missing", "amended"}


# ---------------------------------------------------------------------------
# Missing actions — presenter-derived groups with a direct fix route
# ---------------------------------------------------------------------------


def test_missing_actions_map_to_ui_labels() -> None:
    journey = _event("journey", {"transport_mode": "train"}, complete=False)
    assert [action.label for action in present(journey).missing_actions] == ["add destination"]

    expense = _event(
        "expense", {"description": "table", "category_name": "Office furniture"}, complete=False
    )
    assert [action.label for action in present(expense).missing_actions] == ["add amount"]

    receipt = _event("receipt_only", {"reconciliation_status": "unmatched"}, complete=False)
    assert [action.label for action in present(receipt).missing_actions] == ["link receipt"]

    activity = _event(
        "external_activity",
        {
            "activity_type": "client_visit",
            "start_at": _at(9, 30).isoformat(),
            "still_ongoing": True,
        },
        complete=False,
    )
    assert [action.label for action in present(activity).missing_actions] == [
        "complete per-diem times"
    ]

    car = _event(
        "journey",
        {
            "transport_mode": "private_car",
            "origin_id": "33333333-3333-4333-8333-333333333333",
            "origin_name": "Berlin",
            "destination_id": "44444444-4444-4444-8444-444444444444",
            "destination_name": "Hamburg",
        },
        complete=False,
    )
    assert [action.label for action in present(car).missing_actions] == ["review tax route"]


def test_complete_events_have_no_missing_actions() -> None:
    journey = _event(
        "journey",
        {"transport_mode": "train", "origin_name": "Berlin", "destination_name": "Hamburg",
         "train_category": "ICE", "train_number": "78"},
    )
    assert present(journey).missing_actions == ()


def test_missing_actions_are_typed_with_fix_route() -> None:
    journey = _event("journey", {"transport_mode": "train"}, complete=False)
    (action,) = present(journey).missing_actions
    assert isinstance(action, MissingAction)
    assert action.key == "add_destination"
    assert action.label == "add destination"
    assert action.fix_url_name == "ledger:event_detail"


# ---------------------------------------------------------------------------
# Human field / revision labels
# ---------------------------------------------------------------------------


def test_field_labels_are_human() -> None:
    assert FIELD_LABELS["train_number"] == "train number"
    assert FIELD_LABELS["amount_personally_paid_eur"] == "personally paid (eur)"
    assert FIELD_LABELS["invoice_or_receipt_date"] == "receipt date"
    assert FIELD_LABELS["business_reason"] == "business reason"
    assert FIELD_LABELS["covered_by_pass"] == "covered by rail pass"  # noqa: S105


def test_raw_identifier_keys_have_technical_labels() -> None:
    # Raw ids/hashes/provider payloads are labelled as such so they can only
    # surface inside the collapsed technical section, never as ordinary facts.
    assert FIELD_LABELS["destination_id"].startswith("technical")
    assert FIELD_LABELS["train_source_sha256"].startswith("technical")


def test_format_summary_returns_the_title() -> None:
    from apps.ledger.presenters import format_summary

    event = _event("work_from_home", {"residence_name": "Berlin"}, effective_at=_at(7, 41))
    assert format_summary(event) == "07:41 · work from home · berlin"


def test_present_uses_local_time_for_hhmm() -> None:
    # The clock in summaries is the settings-local clock (Europe/Berlin, UTC+2
    # in August): a 12:03 UTC event renders as 14:03. The summary stays a pure
    # string — no datetime objects leak into the payload.
    event = _event(
        "work_from_home",
        {"residence_name": "Berlin"},
        effective_at=datetime(2026, 8, 4, 12, 3, tzinfo=UTC),
    )
    summary = present(event)
    assert summary.title == "14:03 · work from home · berlin"
    assert isinstance(summary.title, str)
    assert isinstance(summary.meta, str)
