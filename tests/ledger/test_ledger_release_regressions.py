"""Ledger release regressions for malformed input and presentation boundaries.

- History tolerates malformed date params: Django 5.2 ``parse_date``
    raises ValueError for well-formed-but-invalid dates (2026-99-99); the
    view must treat them as absent instead of 500ing.
- The correction endpoint whitelists new snapshot keys: journey picker
    keys are accepted only on journey events; arbitrary new keys and
    journey pickers on other event types are dropped; correction of
    existing scalar/list fields and the append-only chain keep working.
- Raw per-diem rule codes leave the ordinary tax section and surface
    only under the collapsed technical details.
- The filter-sheet GET form carries ``q`` as a hidden input so applying
    filters retains the active search.
Observation: every incomplete event stays visible on the unresolved page
    without adding a sixth group — per-diem-complete activities missing a
    destination fall back under the honest add-destination group, and
    events with no honest mandated group surface as a truthful count
    instead of the "everything is complete" lie.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from ux_seed import at, seed_demo_events

from apps.accounts.services import configure_pin
from apps.ledger.services import create_event
from apps.taxes.models import PerDiemCalculation

pytestmark = pytest.mark.django_db


def logged_in_client() -> Client:
    configure_pin("123456")
    client = Client()
    response = client.post(reverse("accounts:login"), {"pin": "123456"})
    assert response.status_code == 302
    return client


def _history(client: Client, params: dict[str, str]) -> str:
    response = client.get(reverse("ledger:history"), params)
    assert response.status_code == 200
    return response.content.decode()


def _cards(content: str) -> list[str]:
    return re.findall(r'<a[^>]*data-event-card="true"[^>]*>(.*?)</a>', content, re.S)


def _sheet(content: str) -> str:
    match = re.search(
        r'<section[^>]*data-filter-sheet[^>]*>(.*?)</section>', content, re.S
    )
    assert match is not None, "filter sheet not rendered"
    return match.group(0)


def _detail(client: Client, event_id: object) -> str:
    response = client.get(reverse("ledger:event_detail", args=[event_id]))
    assert response.status_code == 200
    return response.content.decode()


def _unresolved(client: Client) -> str:
    response = client.get(reverse("ledger:unresolved"))
    assert response.status_code == 200
    return response.content.decode()


def _correct(client: Client, event_id: object, data: dict[str, str]) -> Any:
    response = client.post(
        reverse("ledger:correct_event", kwargs={"event_id": event_id}), data
    )
    assert response.status_code == 302
    return response


# ---------------------------------------------------------------------------
# F1 — history invalid date params (parse_date ValueError -> absent)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "malformed",
    ["2026-99-99", "2026-13-01", "2026-02-30", "not-a-date", "2026-08", "99-99-99"],
)
def test_history_malformed_date_params_return_200(malformed: str) -> None:
    seed_demo_events()
    client = logged_in_client()
    for param in ("start", "end"):
        response = client.get(reverse("ledger:history"), {param: malformed})
        assert response.status_code == 200, f"{param}={malformed!r} must not 500"


def test_history_malformed_dates_treated_as_absent() -> None:
    seed_demo_events()
    client = logged_in_client()
    # A malformed range is dropped entirely: all six demo events still show.
    assert len(_cards(_history(client, {"start": "2026-99-99"}))) == 6
    assert len(_cards(_history(client, {"end": "2026-99-99"}))) == 6
    # Valid dates still filter — the fix must not disable the filter.
    assert len(_cards(_history(client, {"start": "2026-08-04"}))) == 5


# ---------------------------------------------------------------------------
# F2 — correction whitelist (new keys only via journey pickers on journeys)
# ---------------------------------------------------------------------------


def test_correction_rejects_arbitrary_new_keys() -> None:
    event = create_event(
        event_type="note",
        effective_at=datetime(2026, 8, 4, 10, 0, tzinfo=UTC),
        snapshot={"note": "orignal"},
        complete=True,
    )
    response = _correct(
        logged_in_client(),
        event.pk,
        {
            "effective_at": "2026-08-04T10:00",
            "field_note": "corrected",
            "field_sneaky_key": "injected",
            "complete": "on",
            "correction_comment": "Fixed typo",
        },
    )
    assert response.status_code == 302
    event.refresh_from_db()
    assert event.revisions.count() == 2  # append-only chain intact
    assert event.current_revision is not None
    assert event.current_revision.snapshot["note"] == "corrected"
    assert "sneaky_key" not in event.current_revision.snapshot
    # The arbitrary key must never appear in ANY revision of the chain.
    assert all("sneaky_key" not in revision.snapshot for revision in event.revisions.all())


def test_correction_rejects_journey_picker_keys_on_wfh() -> None:
    event = create_event(
        event_type="work_from_home",
        effective_at=datetime(2026, 8, 4, 8, 0, tzinfo=UTC),
        snapshot={
            "residence_id": "11111111-1111-4111-8111-111111111111",
            "residence_name": "Berlin",
        },
        complete=True,
    )
    response = _correct(
        logged_in_client(),
        event.pk,
        {
            "effective_at": "2026-08-04T08:00",
            "field_destination_id": "22222222-2222-4222-8222-222222222222",
            "field_destination_name": "Hamburg",
            "field_destination_type": "first_workplace",
            "complete": "on",
            "correction_comment": "Tried to add a destination",
        },
    )
    assert response.status_code == 302
    event.refresh_from_db()
    assert event.revisions.count() == 2  # append-only chain intact
    assert event.current_revision is not None
    snapshot = event.current_revision.snapshot
    assert "destination_id" not in snapshot
    assert "destination_name" not in snapshot
    assert snapshot["residence_name"] == "Berlin"  # existing field preserved


def test_correction_preserves_existing_list_fields() -> None:
    meals = {"2026-08-04": ["lunch"]}
    event = create_event(
        event_type="external_activity",
        effective_at=datetime(2026, 8, 4, 10, 0, tzinfo=UTC),
        snapshot={"activity_type": "client_visit", "provided_meals": meals},
        complete=True,
    )
    response = _correct(
        logged_in_client(),
        event.pk,
        {
            "effective_at": "2026-08-04T10:00",
            "field_activity_type": "meeting",
            "complete": "on",
            "correction_comment": "Fixed type",
        },
    )
    assert response.status_code == 302
    event.refresh_from_db()
    assert event.revisions.count() == 2
    assert event.current_revision is not None
    snapshot = event.current_revision.snapshot
    assert snapshot["activity_type"] == "meeting"  # scalar still corrected
    assert snapshot["provided_meals"] == meals  # list value untouched


def test_correction_journey_picker_still_updates_existing_destination() -> None:
    """Guard: the whitelist must not break correcting a destination that the
    journey already has (the picker path for existing keys)."""
    seed = seed_demo_events()
    journey = seed["journey"]
    response = _correct(
        logged_in_client(),
        journey.pk,
        {
            "effective_at": "2026-08-04T08:03",
            "field_destination_id": str(seed["home"].pk),
            "field_destination_name": "Berlin",
            "field_destination_type": "residence",
            "complete": "on",
            "correction_comment": "Changed destination",
        },
    )
    assert response.status_code == 302
    journey.refresh_from_db()
    assert journey.revisions.count() == 2
    assert journey.current_revision is not None
    assert journey.current_revision.snapshot["destination_id"] == str(seed["home"].pk)
    assert journey.current_revision.snapshot["destination_name"] == "Berlin"


def test_correction_malformed_effective_at_falls_back_not_500() -> None:
    """Same parse-family hardening as F1: a malformed datetime in the
    correction POST falls back to the current revision time, never 500s."""
    event = create_event(
        event_type="note",
        effective_at=datetime(2026, 8, 4, 10, 0, tzinfo=UTC),
        snapshot={"note": "keep"},
        complete=True,
    )
    response = _correct(
        logged_in_client(),
        event.pk,
        {
            "effective_at": "2026-99-99T10:00",
            "field_note": "corrected",
            "complete": "on",
            "correction_comment": "Fix",
        },
    )
    assert response.status_code == 302
    event.refresh_from_db()
    assert event.current_revision is not None
    assert event.current_revision.effective_at.date().isoformat() == "2026-08-04"
    assert event.current_revision.snapshot["note"] == "corrected"


# ---------------------------------------------------------------------------
# F3 — raw per-diem rule codes only under collapsed technical details
# ---------------------------------------------------------------------------


def test_ordinary_tax_section_hides_raw_per_diem_rule_codes() -> None:
    seed = seed_demo_events()
    activity = seed["activity"]
    assert activity.current_revision is not None
    PerDiemCalculation.objects.create(
        activity_event=activity,
        input_revision=activity.current_revision,
        rule_codes=["P4", "M0.5", "X"],
        daily_amounts={},
        total=Decimal("12.00"),
        complete=True,
        missing_facts=[],
        derivation_hash="f" * 64,
    )
    content = _detail(logged_in_client(), activity.pk)
    ordinary = content.split("data-technical-details")[0]
    # The humanised per-diem total stays in the ordinary tax section…
    assert "meal per diem" in ordinary.casefold()
    assert "12.00" in ordinary
    # …but the raw rule codes do not.
    assert "P4" not in ordinary
    assert "M0.5" not in ordinary
    # The raw codes surface under the collapsed technical details instead.
    technical = content.split("data-technical-details")[1]
    assert "P4" in technical
    assert "M0.5" in technical


# ---------------------------------------------------------------------------
# F4 — filter-sheet GET form preserves the active search (q)
# ---------------------------------------------------------------------------


def test_filter_sheet_keeps_active_search_as_hidden_input() -> None:
    seed_demo_events()
    content = _history(logged_in_client(), {"q": "ice", "event_type": "journey"})
    sheet = _sheet(content)
    form = re.search(r'<form[^>]*class="wl-sheet__scroll"[^>]*>(.*?)</form>', sheet, re.S)
    assert form is not None
    body = form.group(1)
    assert re.search(
        r'<input[^>]*type="hidden"[^>]*name="q"[^>]*value="ice"', body
    ) is not None


def test_applying_filters_retains_search_results() -> None:
    seed_demo_events()
    client = logged_in_client()
    # The GET the sheet form produces: q plus one filter — search still applies.
    content = _history(client, {"q": "ice", "event_type": "journey"})
    cards = _cards(content)
    assert len(cards) == 1
    assert "ice 78" in cards[0].casefold()
    # The same filter without the search term shows both journeys.
    assert len(_cards(_history(client, {"event_type": "journey"}))) == 2


# ---------------------------------------------------------------------------
# Observation — every incomplete event stays visible (no sixth group)
# ---------------------------------------------------------------------------


def test_incomplete_activity_without_destination_visible_under_add_destination() -> None:
    """Honest fallback: per-diem facts complete but no destination — the
    add-destination group's label is truthful for this missing fact."""
    activity = create_event(
        event_type="external_activity",
        effective_at=at(4, 10, 0),
        snapshot={
            "activity_type": "client_visit",
            "start_at": at(4, 10, 0).isoformat(),
            "end_at": at(4, 18, 0).isoformat(),
            "country_code": "DE",
            "three_month_limit_applies": True,
            "departure_context": "Berlin",
            "return_context": "Berlin",
        },
        complete=False,
    )
    content = _unresolved(logged_in_client())
    groups = re.findall(r'data-unresolved-group="([a-z_]+)"', content)
    assert groups == ["add_destination"]
    destination_group = re.search(
        r'data-unresolved-group="add_destination".*?</section>', content, re.S
    )
    assert destination_group is not None
    assert (
        f'href="{reverse("ledger:event_detail", args=[activity.pk])}"'
        in destination_group.group(0)
    )


def test_unresolved_empty_state_is_truthful_when_incomplete_events_are_hidden() -> None:
    """A WFH entry saved without a residence/employer is incomplete but has
    no honest mandated group — the page must not claim everything is
    complete, and must surface the hidden count instead."""
    create_event(
        event_type="work_from_home",
        effective_at=at(4, 8, 0),
        snapshot={"note": "no residence configured"},
        complete=False,
    )
    content = _unresolved(logged_in_client())
    assert "everything is complete" not in content
    assert 'data-hidden-incomplete="1"' in content


def test_unresolved_hidden_count_sits_alongside_groups() -> None:
    seed_demo_events()  # contributes the per-diem-incomplete activity group
    create_event(
        event_type="work_from_home",
        effective_at=at(4, 8, 0),
        snapshot={"note": "no residence configured"},
        complete=False,
    )
    content = _unresolved(logged_in_client())
    assert 'data-unresolved-group="complete_per_diem_times"' in content
    assert 'data-hidden-incomplete="1"' in content
