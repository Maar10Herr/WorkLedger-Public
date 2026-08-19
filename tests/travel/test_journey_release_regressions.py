"""Journey workflow regressions for release-critical behavior.

These tests pin corrected behavior without API credentials or a browser
(Playwright covers real browser behavior). JavaScript behavior is pinned
statically against the shipped sources.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from django.core import signing
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.services import configure_pin
from apps.ledger.models import Event
from apps.taxes.models import PerDiemCalculation, RouteDistance
from apps.travel.models import Location, LocationType, RailPass
from apps.travel.train_lookup import TrainChoice, train_choice_snapshot

pytestmark = pytest.mark.django_db

JS_DIR = Path(__file__).resolve().parents[2] / "static" / "js"
UI_JS = (JS_DIR / "workledger-ui.js").read_text()
DRAFTS_JS = (JS_DIR / "drafts.js").read_text()


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


def _activity_url() -> str:
    return reverse("travel:external_activity_entry")


def _signed_choice_token() -> str:
    choice = TrainChoice(
        category="ICE",
        number="78",
        operator="DB Fernverkehr AG",
        origin_station="Berlin Hbf",
        destination_station="Hamburg Hbf",
        scheduled_departure=datetime(2026, 8, 4, 7, 0, tzinfo=UTC),
        scheduled_arrival=datetime(2026, 8, 4, 8, 5, tzinfo=UTC),
        actual_departure=None,
        actual_arrival=None,
        source_id="trip-78",
        retrieved_at=datetime(2026, 8, 4, 6, 55, tzinfo=UTC),
        raw_snapshot={"id": "trip-78"},
    )
    return signing.dumps(
        train_choice_snapshot(choice), salt="workledger.train-choice", compress=True
    )


# --------------------------------------------------------------------------
# M1 — still-ongoing activity: named checkbox, end_at omitted (no end=start),
# return time marked missing, no per-diem until an actual return exists.
# --------------------------------------------------------------------------


def test_still_ongoing_checkbox_is_named_and_disables_return_input() -> None:
    _base_locations()
    content = logged_in_client().get(_activity_url()).content.decode()
    ongoing = re.search(r'data-activity-still-ongoing[^>]*>(.*?)</label>', content, re.S)
    assert ongoing is not None
    assert 'name="still_ongoing"' in ongoing.group(1)
    assert 'x-model="stillOngoing"' in ongoing.group(1)
    # While ongoing the return input is disabled so the browser omits end_at.
    assert ':disabled="stillOngoing"' in content


def test_still_ongoing_post_ignores_end_at_and_marks_return_missing() -> None:
    _base_locations()
    response = logged_in_client().post(
        _activity_url(),
        {
            "activity_type": "client_visit",
            "start_at": "2026-08-04T08:00",
            "country_code": "DE",
            "tax_relevant": "on",
            "still_ongoing": "on",
            # Browser-equivalent: the hidden return input may still submit a
            # value; the server must ignore it while the activity is ongoing.
            "end_at": "2026-08-04T18:00",
        },
    )

    assert response.status_code == 201
    event = Event.objects.get(event_type="external_activity")
    assert event.current_revision is not None
    snapshot = event.current_revision.snapshot
    assert event.current_revision.complete is False
    assert "end_at" not in snapshot
    assert snapshot["still_ongoing"] is True
    content = response.content.decode()
    assert "destination" in content
    assert "return time" in content


def test_still_ongoing_post_does_not_derive_per_diem() -> None:
    _base_locations()
    client = logged_in_client()
    client.post(
        _activity_url(),
        {
            "activity_type": "client_visit",
            "start_at": "2026-08-04T08:00",
            "country_code": "DE",
            "still_ongoing": "on",
        },
    )
    assert PerDiemCalculation.objects.count() == 0

    # Control: a completed activity still derives and stores the allowance.
    destination = Location.objects.create(
        name="Client", location_type=LocationType.CLIENT_SITE
    )
    client.post(
        _activity_url(),
        {
            "activity_type": "client_visit",
            "start_at": "2026-08-04T08:00",
            "end_at": "2026-08-04T18:00",
            "country_code": "DE",
            "three_month_limit": "no",
            "destination": str(destination.pk),
            "departure_context": "Home",
            "return_context": "Home",
        },
    )
    assert PerDiemCalculation.objects.count() == 1


# --------------------------------------------------------------------------
# M2 — manual train data must override / clear a stale signed choice token.
# --------------------------------------------------------------------------


def test_signed_train_token_still_wins_without_manual_fields() -> None:
    _, office = _base_locations()
    token = _signed_choice_token()
    response = logged_in_client().post(
        _journey_url(),
        {
            "destination": str(office.pk),
            "transport_mode": "train",
            "origin_station": "Berlin Hbf",
            "destination_station": "Hamburg Hbf",
            "scheduled_departure": "2026-08-04T07:00",
            "train_choice_token": token,
        },
    )
    assert response.status_code == 201
    event = Event.objects.get(event_type="journey")
    assert event.current_revision is not None
    snapshot = event.current_revision.snapshot
    assert snapshot["train_category"] == "ICE"
    assert snapshot["train_number"] == "78"
    assert snapshot["manual_train_entry"] is False
    assert "train_source_sha256" in snapshot


def test_manual_train_fields_override_signed_token() -> None:
    _, office = _base_locations()
    token = _signed_choice_token()
    response = logged_in_client().post(
        _journey_url(),
        {
            "destination": str(office.pk),
            "transport_mode": "train",
            "origin_station": "Berlin Hbf",
            "destination_station": "Hamburg Hbf",
            "scheduled_departure": "2026-08-04T07:00",
            "train_choice_token": token,
            "train_category": "RE",
            "train_number": "99",
            "scheduled_arrival": "2026-08-04T09:00",
            "train_operator": "DB Regio",
        },
    )
    assert response.status_code == 201
    event = Event.objects.get(event_type="journey")
    assert event.current_revision is not None
    snapshot = event.current_revision.snapshot
    assert snapshot["train_category"] == "RE"
    assert snapshot["train_number"] == "99"
    assert snapshot["train_operator"] == "DB Regio"
    assert snapshot["manual_train_entry"] is True
    assert "train_source_sha256" not in snapshot


def test_changed_route_stations_clear_stale_token() -> None:
    _, office = _base_locations()
    token = _signed_choice_token()
    response = logged_in_client().post(
        _journey_url(),
        {
            "destination": str(office.pk),
            "transport_mode": "train",
            "origin_station": "Berlin Hbf",
            "destination_station": "Mannheim Hbf",
            "scheduled_departure": "2026-08-04T07:00",
            "train_choice_token": token,
        },
    )
    assert response.status_code == 201
    event = Event.objects.get(event_type="journey")
    assert event.current_revision is not None
    snapshot = event.current_revision.snapshot
    assert snapshot["destination_station"] == "Mannheim Hbf"
    assert snapshot["manual_train_entry"] is True
    assert "train_source_sha256" not in snapshot


# --------------------------------------------------------------------------
# M3 — compact effective time renders the real now_local HH:MM and the date
# label updates when the user changes the picker.
# --------------------------------------------------------------------------


def test_effective_time_summary_renders_server_now() -> None:
    _base_locations()
    content = logged_in_client().get(_journey_url()).content.decode()
    match = re.search(r'data-time-value[^>]*>(\d{2}:\d{2})<', content)
    assert match is not None
    rendered = datetime.strptime(match.group(1), "%H:%M")
    now = timezone.localtime().replace(second=0, microsecond=0)
    delta = abs((now.hour * 60 + now.minute) - (rendered.hour * 60 + rendered.minute))
    assert delta <= 2


def test_effective_time_date_label_is_bound_and_updates_on_change() -> None:
    _base_locations()
    content = logged_in_client().get(_journey_url()).content.decode()
    assert 'data-date-label' in content
    assert 'x-text="dateLabel"' in content
    assert 'onTimeChange($event.target)' in content
    assert "onTimeChange" in UI_JS
    assert "dateLabel" in UI_JS
    # The change handler recomputes both the clock time and the day label.
    assert re.search(r"onTimeChange\s*\(input\)", UI_JS) is not None
    assert "dateLabel" in UI_JS.split("onTimeChange")[1][:400]


# --------------------------------------------------------------------------
# L1 — malformed UUIDs must never 500: safely ignored / marked incomplete.
# --------------------------------------------------------------------------


def test_malformed_destination_uuid_saves_incomplete() -> None:
    _base_locations()
    response = logged_in_client().post(
        _journey_url(), {"destination": "not-a-uuid", "transport_mode": "bicycle"}
    )
    assert response.status_code == 201
    event = Event.objects.get(event_type="journey")
    assert event.current_revision is not None
    assert event.current_revision.complete is False
    assert "destination" in response.content.decode()


def test_malformed_origin_uuid_falls_back_to_inference() -> None:
    home, office = _base_locations()
    response = logged_in_client().post(
        _journey_url(),
        {"destination": str(office.pk), "origin": "garbage", "transport_mode": "bicycle"},
    )
    assert response.status_code == 201
    event = Event.objects.get(event_type="journey")
    assert event.current_revision is not None
    assert event.current_revision.snapshot["origin_id"] == str(home.pk)


def test_malformed_rail_pass_uuid_is_not_covered() -> None:
    _, office = _base_locations()
    response = logged_in_client().post(
        _journey_url(),
        {
            "destination": str(office.pk),
            "transport_mode": "train",
            "origin_station": "Berlin Hbf",
            "destination_station": "Hamburg Hbf",
            "scheduled_departure": "2026-08-04T07:00",
            "rail_pass": "not-a-uuid",
        },
    )
    assert response.status_code == 201
    event = Event.objects.get(event_type="journey")
    assert event.current_revision is not None
    assert event.current_revision.snapshot["covered_by_pass"] is False


def test_malformed_journey_query_param_is_ignored() -> None:
    _base_locations()
    response = logged_in_client().get(_activity_url(), {"journey": "bogus"})
    assert response.status_code == 200
    content = response.content.decode()
    assert 'data-activity-prefill-summary' in content
    # No leg may be preselected and no destination prefilled.
    link_section = re.search(r'data-activity-journey-link.*?</section>', content, re.S)
    assert link_section is not None
    assert 'checked' not in link_section.group(0)


def test_malformed_journey_leg_ids_are_ignored() -> None:
    _base_locations()
    response = logged_in_client().post(
        _activity_url(),
        {
            "activity_type": "client_visit",
            "start_at": "2026-08-04T08:00",
            "country_code": "DE",
            "journey_legs": ["junk", "also-junk"],
        },
    )
    assert response.status_code == 201
    event = Event.objects.get(event_type="external_activity")
    assert event.current_revision is not None
    assert event.current_revision.snapshot["journey_leg_ids"] == []


def test_malformed_activity_destination_uuid_saves_incomplete() -> None:
    _base_locations()
    response = logged_in_client().post(
        _activity_url(),
        {
            "activity_type": "client_visit",
            "start_at": "2026-08-04T08:00",
            "country_code": "DE",
            "destination": "not-a-uuid",
        },
    )
    assert response.status_code == 201
    event = Event.objects.get(event_type="external_activity")
    assert event.current_revision is not None
    assert event.current_revision.complete is False
    assert "destination" in response.content.decode()


# --------------------------------------------------------------------------
# L2 — an explicit origin change also updates the origin station input
# (data-station attributes + Alpine-bound originStation state).
# --------------------------------------------------------------------------


def test_origin_choice_carries_station_and_updates_bound_input() -> None:
    _base_locations()
    content = logged_in_client().get(_journey_url()).content.decode()
    # The auto (inferred) option and every explicit origin carry a station.
    assert 'data-station="Berlin Hbf"' in content
    assert 'data-station="Hamburg Hbf"' in content
    # The train origin-station input is Alpine-bound so a selection updates it.
    assert 'x-model="originStation"' in content
    assert 'name="origin_station"' in content
    assert "onOriginChange" in UI_JS
    assert 'input.getAttribute("data-station")' in UI_JS
    assert "this.originStation =" in UI_JS


# --------------------------------------------------------------------------
# L3 — draft restore must dispatch input/change events so Alpine-bound state
# (and @change handlers) see the restored values.
# --------------------------------------------------------------------------


def test_draft_restore_dispatches_input_and_change_events() -> None:
    assert "new Event(\"input\", { bubbles: true })" in DRAFTS_JS
    assert "new Event(\"change\", { bubbles: true })" in DRAFTS_JS
    assert "field.dispatchEvent" in DRAFTS_JS
    # The dispatch happens inside the restore loop, after values are set.
    restore_tail = DRAFTS_JS.split("else field.value = value")[1]
    assert "dispatchEvent" in restore_tail


def test_draft_forms_still_annotated_after_restore_change() -> None:
    _base_locations()
    client = logged_in_client()
    journey = client.get(_journey_url()).content.decode()
    assert 'data-draft-key="journey-entry"' in journey
    activity = client.get(_activity_url()).content.decode()
    assert 'data-draft-key="external-activity"' in activity


# --------------------------------------------------------------------------
# L4 — data-covered binds dynamically; dead route_source context removed.
# --------------------------------------------------------------------------


def test_rail_pass_summary_data_covered_binds_dynamically() -> None:
    _base_locations()
    RailPass.objects.create(
        name="BahnCard 100",
        pass_type="bahncard_100",  # noqa: S106 -- rail-pass product type
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
    )
    content = logged_in_client().get(_journey_url()).content.decode()
    assert ':data-covered="coveredByPass ? \'true\' : \'false\'"' in content
    assert 'x-text="coveredByPass ? \'covered\' : \'not covered\'"' in content


def test_journey_context_options_carry_no_route_source() -> None:
    from apps.travel.views import _journey_context

    _base_locations()
    context = _journey_context()
    for option in (
        *context["favourite_options"],
        *context["recent_options"],
        *context["other_options"],
    ):
        assert "route_source" not in option
        assert "route_km" in option


# --------------------------------------------------------------------------
# L5 — car route summaries filter confirmed driving routes at the DB layer.
# --------------------------------------------------------------------------


def test_car_route_summaries_use_only_confirmed_driving_routes() -> None:
    from apps.travel.views import _car_route_summaries

    home, office = _base_locations()
    gym = Location.objects.create(name="Gym", location_type=LocationType.OTHER_EXTERNAL)

    def make_route(
        destination: Location, mode: str, confirmed: bool, version: int, km: str
    ) -> RouteDistance:
        return RouteDistance.objects.create(
            origin=home,
            destination=destination,
            mode=mode,
            distance_km=Decimal(km),
            version=version,
            source="manual",
            calculation_date=date(2026, 8, 1),
            confirmed=confirmed,
        )

    make_route(office, "driving", True, 1, "12.50")
    make_route(office, "driving", False, 2, "99.99")  # newer but unconfirmed
    make_route(gym, "walking", True, 1, "3.00")  # confirmed but not driving
    make_route(gym, "driving", True, 1, "9.40")

    summaries = _car_route_summaries(home)
    assert summaries[str(office.pk)]["distance_km"] == "12.50"
    assert summaries[str(gym.pk)]["distance_km"] == "9.40"
