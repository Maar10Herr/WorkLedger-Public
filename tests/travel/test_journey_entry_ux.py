"""Journey-entry acceptance tests: decision tree, transport progressive
disclosure, rail-pass preselection, sticky/incomplete save, and journey-linked
external activity refinement.

These tests pin user-facing vocabulary and stable test hooks without exercising
browser behaviour (Playwright covers that). They must stay green without API
credentials.
"""

from __future__ import annotations

import re
from datetime import date

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.services import configure_pin
from apps.ledger.models import Event
from apps.travel.models import Location, LocationType, RailPass, TransportMode

pytestmark = pytest.mark.django_db


def logged_in_client() -> Client:
    configure_pin("123456")
    client = Client()
    response = client.post(reverse("accounts:login"), {"pin": "123456"})
    assert response.status_code == 302
    return client


def _base_locations() -> tuple[Location, Location]:
    home = Location.objects.create(
        name="Home",
        location_type=LocationType.RESIDENCE,
        is_default_residence=True,
        locality="Berlin",
        station_name="Berlin Hbf",
    )
    office = Location.objects.create(
        name="Office",
        location_type=LocationType.FIRST_WORKPLACE,
        is_favourite=True,
        station_name="Hamburg Hbf",
    )
    return home, office


def _journey_url() -> str:
    return reverse("travel:journey_entry")


def test_journey_context_groups_destinations_and_shows_inferred_origin() -> None:
    _base_locations()
    client_site = Location.objects.create(
        name="Client", location_type=LocationType.CLIENT_SITE
    )
    Location.objects.create(name="Gym", location_type=LocationType.OTHER_EXTERNAL)
    client = logged_in_client()

    # Before any journey exists the inferred origin is the default residence,
    # shown visibly with locality and a change affordance.
    content = client.get(_journey_url()).content.decode()
    assert 'data-origin-summary' in content
    assert 'data-origin-change' in content
    origin_section = re.search(r'data-origin-summary[^>]*>(.*?)</div>', content, re.S)
    assert origin_section is not None
    assert "from:" in origin_section.group(1)
    assert "home" in origin_section.group(1).lower()
    assert "berlin" in origin_section.group(1).lower()
    assert 'value="Berlin Hbf"' in content

    # Make Client a recent destination via a saved journey, then re-check
    # favourite / recent / other grouping with recent deduplicated.
    response = client.post(
        _journey_url(),
        {"destination": str(client_site.pk), "transport_mode": "bicycle"},
    )
    assert response.status_code == 201
    content = client.get(_journey_url()).content.decode()
    favourites = re.search(
        r'data-destination-group="favourites"[^>]*>(.*?)</section>', content, re.S
    )
    assert favourites is not None
    assert "office" in favourites.group(1).lower()
    assert "gym" not in favourites.group(1).lower()
    recent = re.search(r'data-destination-group="recent"[^>]*>(.*?)</section>', content, re.S)
    assert recent is not None
    assert "client" in recent.group(1).lower()
    assert "gym" not in recent.group(1).lower()
    other_group = re.search(
        r'data-destination-group="other"[^>]*>(.*?)</section>', content, re.S
    )
    assert other_group is not None
    assert "gym" in other_group.group(1).lower()
    assert "client" not in other_group.group(1).lower()

    # Station prefills come from saved locations.
    assert 'name="origin_station"' in content
    assert 'data-station="Hamburg Hbf"' in content


def test_journey_accepts_explicit_origin_and_falls_back_to_inference() -> None:
    home, office = _base_locations()
    client = logged_in_client()

    # First POST: no origin posted, no recent journey → inferred residence.
    inferred = client.post(
        _journey_url(),
        {"destination": str(office.pk), "transport_mode": "bicycle"},
    )
    assert inferred.status_code == 201
    first = Event.objects.filter(event_type="journey").order_by("created_at").first()
    assert first is not None and first.current_revision is not None
    assert first.current_revision.snapshot["origin_id"] == str(home.pk)

    # Second POST: explicit origin overrides inference.
    explicit = client.post(
        _journey_url(),
        {"destination": str(office.pk), "transport_mode": "bicycle", "origin": str(office.pk)},
    )
    assert explicit.status_code == 201
    second = Event.objects.filter(event_type="journey").order_by("-created_at").first()
    assert second is not None and second.current_revision is not None
    assert second.current_revision.snapshot["origin_id"] == str(office.pk)


def test_journey_transport_grid_and_train_progressive_disclosure() -> None:
    _base_locations()
    content = logged_in_client().get(_journey_url()).content.decode()

    # Button/radio grid with a clear selected state, every mode reachable.
    assert 'data-transport-grid' in content
    assert content.count("data-transport-option") >= 7
    for value, _label in TransportMode.choices:
        assert f'value="{value}"' in content

    # Default train section only asks for train-relevant facts.
    default = re.search(r'data-train-default[^>]*>(.*?)</section>', content, re.S)
    assert default is not None
    assert 'name="origin_station"' in default.group(1)
    assert 'name="destination_station"' in default.group(1)
    assert 'name="scheduled_departure"' in default.group(1)
    assert reverse("travel:train_lookup") in default.group(1)

    # Manual train details live only behind the toggle: category, number,
    # arrival, and operator appear solely inside the manual disclosure.
    manual_details = re.search(r'<details[^>]*data-train-manual>.*?</details>', content, re.S)
    assert manual_details is not None
    assert 'data-train-toggle' in content
    manual = manual_details.group(0)
    assert "train_category" in manual
    assert "train_number" in manual
    assert "scheduled_arrival" in manual
    assert "train_operator" in manual
    default_without_manual = default.group(1).replace(manual, "")
    assert "train_category" not in default_without_manual
    assert "train_number" not in default_without_manual
    assert "scheduled_arrival" not in default_without_manual

    assert 'data-train-result' in content
    assert 'data-selected-train' in content
    assert 'data-cost-section' in content
    assert 'data-cost-toggle' in content


def test_rail_pass_preselected_when_single_active_pass() -> None:
    _, office = _base_locations()
    RailPass.objects.create(
        name="BahnCard 100",
        pass_type="bahncard_100",  # noqa: S106 -- rail-pass product type
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
    )

    content = logged_in_client().get(_journey_url()).content.decode()
    summary = re.search(r'data-rail-pass-summary[^>]*>(.*?)</div>', content, re.S)
    assert summary is not None
    assert "bahncard 100" in summary.group(1).lower()
    assert "covered" in summary.group(1).lower()
    assert 'data-pass-change' in content
    assert 'name="rail_pass"' in content

    # Server-side auto-cover: a train POST without an explicit pass is covered
    # when exactly one active pass exists.
    response = logged_in_client().post(
        _journey_url(),
        {
            "destination": str(office.pk),
            "transport_mode": "train",
            "origin_station": "Berlin Hbf",
            "destination_station": "Hamburg Hbf",
            "scheduled_departure": "2026-08-04T07:00",
        },
    )
    assert response.status_code == 201
    event = Event.objects.get(event_type="journey")
    assert event.current_revision is not None
    snapshot = event.current_revision.snapshot
    assert snapshot["covered_by_pass"] is True
    assert snapshot["rail_pass_name"] == "BahnCard 100"  # noqa: S105 -- rail-pass product name
    assert snapshot["incremental_ticket_cost"] == "0.00"


def test_ambiguous_rail_passes_require_an_explicit_choice() -> None:
    _, office = _base_locations()
    RailPass.objects.create(
        name="BahnCard 100", pass_type="bahncard_100",  # noqa: S106
        valid_from=date(2026, 1, 1), valid_to=date(2026, 12, 31),
    )
    RailPass.objects.create(
        name="Deutschlandticket", pass_type="deutschlandticket",  # noqa: S106
        valid_from=date(2026, 1, 1), valid_to=date(2026, 12, 31),
    )

    response = logged_in_client().post(
        _journey_url(),
        {
            "destination": str(office.pk),
            "transport_mode": "train",
            "origin_station": "Berlin Hbf",
            "destination_station": "Hamburg Hbf",
            "scheduled_departure": "2026-08-04T07:00",
        },
    )
    assert response.status_code == 201
    event = Event.objects.get(event_type="journey")
    assert event.current_revision is not None
    assert event.current_revision.snapshot["covered_by_pass"] is False


def test_incomplete_journey_post_still_creates_incomplete_event() -> None:
    _base_locations()
    response = logged_in_client().post(
        _journey_url(), {"transport_mode": "bicycle"}
    )

    assert response.status_code == 201
    event = Event.objects.get(event_type="journey")
    assert event.current_revision is not None
    assert event.current_revision.complete is False
    assert "destination_id" not in event.current_revision.snapshot
    assert "destination" in response.content.decode()


def test_journey_record_for_toggles_and_dual_track_note() -> None:
    _base_locations()
    content = logged_in_client().get(_journey_url()).content.decode()
    assert 'data-record-for' in content
    assert 'data-track-tax' in content
    assert 'data-track-employer' in content
    assert 'data-dual-track-note' in content
    assert "one event, two outputs" in content
    assert 'name="tax_relevant"' in content
    assert 'name="employer_reimbursable"' in content

    client = logged_in_client()
    both = client.post(
        _journey_url(),
        {
            "destination": str(Location.objects.get(name="Office").pk),
            "transport_mode": "bicycle",
            "track_fields_present": "1",
            "tax_relevant": "on",
            "employer_reimbursable": "on",
        },
    )
    assert both.status_code == 201
    event = Event.objects.filter(event_type="journey").order_by("-created_at").first()
    assert event is not None
    assert event.tax_relevant is True
    assert event.employer_reimbursable is True

    tax_only = client.post(
        _journey_url(),
        {
            "destination": str(Location.objects.get(name="Office").pk),
            "transport_mode": "bicycle",
            "track_fields_present": "1",
            "employer_reimbursable": "on",
        },
    )
    assert tax_only.status_code == 201
    event = Event.objects.filter(event_type="journey").order_by("-created_at").first()
    assert event is not None
    assert event.tax_relevant is False
    assert event.employer_reimbursable is True


def test_journey_sticky_submit_and_save_incomplete_hooks() -> None:
    _base_locations()
    content = logged_in_client().get(_journey_url()).content.decode()
    assert 'data-sticky-submit' in content
    assert 'data-save-incomplete' in content
    # The primary action never leads with save-without-destination.
    primary_position = content.find("save journey")
    incomplete_position = content.find("save incomplete")
    assert primary_position != -1 and incomplete_position != -1


def test_linked_activity_get_prefills_journey_destination_and_times() -> None:
    _base_locations()
    client_site = Location.objects.create(
        name="Client", location_type=LocationType.CLIENT_SITE
    )
    client = logged_in_client()
    journey = client.post(
        _journey_url(),
        {
            "destination": str(client_site.pk),
            "transport_mode": "bicycle",
            "effective_at": "2026-08-04T07:30",
        },
    )
    assert journey.status_code == 201
    journey_event = Event.objects.get(event_type="journey")

    response = client.get(
        reverse("travel:external_activity_entry"), {"journey": str(journey_event.pk)}
    )
    content = response.content.decode()
    assert response.status_code == 200
    assert 'data-activity-journey-link' in content
    assert 'data-activity-prefill-summary' in content
    assert f'name="journey_legs" value="{journey_event.pk}" checked' in content
    assert f'value="{client_site.pk}" selected' in content
    assert 'name="start_at" value="2026-08-04T07:30"' in content
    assert 'name="end_at" value="2026-08-04T16:30"' in content


def test_activity_post_without_destination_or_return_saves_incomplete() -> None:
    _base_locations()
    response = logged_in_client().post(
        reverse("travel:external_activity_entry"),
        {
            "activity_type": "client_visit",
            "start_at": "2026-08-04T08:00",
            "country_code": "DE",
            "tax_relevant": "on",
            # Browser-equivalent still-ongoing POST: the named checkbox is on
            # and the return input is disabled while ongoing, so the server
            # must ignore any end_at the hidden field still submits.
            "still_ongoing": "on",
            "end_at": "2026-08-04T18:00",
        },
    )

    assert response.status_code == 201
    event = Event.objects.get(event_type="external_activity")
    assert event.current_revision is not None
    snapshot = event.current_revision.snapshot
    assert event.current_revision.complete is False
    assert "destination_id" not in snapshot
    # Still-ongoing activity must not fabricate end=start: no end_at at all.
    assert "end_at" not in snapshot
    assert snapshot["still_ongoing"] is True
    content = response.content.decode()
    assert "destination" in content
    assert "return time" in content


def test_activity_progressive_disclosures_and_non_blocking_tax_facts() -> None:
    _base_locations()
    content = logged_in_client().get(
        reverse("travel:external_activity_entry")
    ).content.decode()

    # Meal copayments appear only after the corresponding meal is selected.
    meals = re.search(r'data-activity-meals[^>]*>(.*?)</details>', content, re.S)
    assert meals is not None
    assert 'name="breakfast"' in meals.group(1)
    assert 'name="breakfast_copayment"' in meals.group(1)
    assert 'x-show="meals.breakfast"' in content

    # Still-ongoing option replaces a mandatory return time.
    assert "still ongoing" in content

    # Three-month decision lives in advanced tax facts and is not required.
    advanced = re.search(r'data-technical-details[^>]*>(.*?)</details>', content, re.S)
    assert advanced is not None
    assert 'name="three_month_limit"' in advanced.group(1)
    assert "required" not in advanced.group(1)
    assert 'name="country_code"' in advanced.group(1)

    # Progressive disclosure sections for employer payment and work context.
    assert "employer per-diem reimbursement" in content
    assert 'name="client"' in content
    assert 'name="purpose"' in content


def test_all_required_ux_hooks_present_on_both_forms() -> None:
    _base_locations()
    journey = logged_in_client().get(_journey_url()).content.decode()
    for hook in (
        "data-origin-summary",
        "data-origin-change",
        "data-time-summary",
        "data-time-change",
        "data-transport-grid",
        "data-transport-option",
        "data-train-default",
        "data-train-manual",
        "data-train-toggle",
        "data-train-result",
        "data-selected-train",
        "data-rail-pass-summary",
        "data-cost-section",
        "data-cost-toggle",
        "data-record-for",
        "data-track-tax",
        "data-track-employer",
        "data-dual-track-note",
        "data-sticky-submit",
        "data-save-incomplete",
    ):
        assert hook in journey, f"journey missing {hook}"

    activity = logged_in_client().get(
        reverse("travel:external_activity_entry")
    ).content.decode()
    for hook in (
        "data-activity-journey-link",
        "data-activity-prefill-summary",
        "data-technical-details",
        "data-record-for",
        "data-track-tax",
        "data-track-employer",
        "data-dual-track-note",
    ):
        assert hook in activity, f"activity missing {hook}"
