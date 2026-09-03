"""Settings integrity regressions for residence and location handling.

Covers the country-normalization failure mode plus adjacent invariants:

- country normalization (DE/de/Germany/Deutschland → DE, invalid → inline error)
- name/length, coordinate, and default-residence validation
- transactional default-replacement and employer replacement
- rail-pass date/amount validation
- malformed route inputs never 500; provider failure preserves confirmed data
- journey add-location return flow and safe ``next`` handling
- home setup callouts
- ordinary responses never leak tracebacks/SQL/raw DB errors
"""

from __future__ import annotations

from decimal import Decimal
from typing import cast

import pytest
from django.db import connection
from django.http import HttpResponse
from django.test import Client
from django.urls import reverse

from apps.accounts.services import configure_pin
from apps.taxes.journey import record_route_distance
from apps.taxes.models import RouteDistance
from apps.taxes.route_lookup import RouteLookupUnavailable
from apps.travel.models import Employer, Location, LocationType, RailPass

pytestmark = pytest.mark.django_db

LEAK_MARKERS = (
    "Traceback",
    "django.db.utils",
    "DataError",
    "psycopg",
    "ProgrammingError",
)


def logged_in_client() -> Client:
    configure_pin("123456")
    client = Client()
    assert client.post(reverse("accounts:login"), {"pin": "123456"}).status_code == 302
    return client


@pytest.fixture(autouse=True)
def _authed_client(client: Client) -> None:
    """Every test acts as the authenticated owner (the pages under test are
    ``owner_login_required``; an unauthenticated client would only exercise
    the login redirect and never reach the stabilised code paths)."""
    configure_pin("123456")
    assert client.post(reverse("accounts:login"), {"pin": "123456"}).status_code == 302


def _post_location(client: Client, **overrides: object) -> HttpResponse:
    payload: dict[str, object] = {
        "action": "location",
        "name": "Test Place",
        "location_type": LocationType.OTHER_EXTERNAL,
        "address": "Test Street 1",
        "country_code": "DE",
        "locality": "Berlin",
        "station_name": "",
        "latitude": "",
        "longitude": "",
        "is_favourite": "",
        "is_default_residence": "",
    }
    payload.update(overrides)
    return cast(HttpResponse, client.post(reverse("travel:settings_locations"), payload))


def _create_residence(client: Client, name: str = "Old Home", **extra: object) -> None:
    response = _post_location(
        client,
        name=name,
        location_type=LocationType.RESIDENCE,
        is_default_residence="on",
        **extra,
    )
    assert response.status_code == 302, response.content.decode()


# ---------------------------------------------------------------------------
# Confirmed defect: country input must normalize, never 500
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["DE", "de", "Germany", "Deutschland"])
def test_country_variants_create_location_stored_as_de(client: Client, raw: str) -> None:
    response = _post_location(client, country_code=raw)

    assert response.status_code == 302
    created = Location.objects.get(name="Test Place")
    assert created.country_code == "DE"


@pytest.mark.parametrize("raw", ["XX", "GER", "D", "DEU", "123", "Germany Extra"])
def test_invalid_country_inline_error_and_no_row(client: Client, raw: str) -> None:
    response = _post_location(client, country_code=raw)

    assert response.status_code == 200
    assert Location.objects.filter(name="Test Place").exists() is False
    body = response.content.decode()
    assert "valid country" in body
    assert not any(marker in body for marker in LEAK_MARKERS)


def test_blank_country_allowed_for_non_residence(client: Client) -> None:
    response = _post_location(client, country_code="")

    assert response.status_code == 302
    assert Location.objects.get(name="Test Place").country_code == ""


# ---------------------------------------------------------------------------
# Name / coordinate validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["", "   ", "x" * 201])
def test_blank_and_overlong_names_inline_error(client: Client, name: str) -> None:
    response = _post_location(client, name=name)

    assert response.status_code == 200
    assert Location.objects.filter(name=name).exists() is False
    body = response.content.decode()
    assert "error" in body.lower()
    assert not any(marker in body for marker in LEAK_MARKERS)


@pytest.mark.parametrize(
    ("lat", "lng"),
    [
        ("abc", ""),
        ("", "abc"),
        ("91", "0"),
        ("-91", "0"),
        ("0", "181"),
        ("0", "-181"),
        ("1e999", "0"),
        ("NaN", "0"),
    ],
)
def test_malformed_and_out_of_range_coords_inline_error(
    client: Client, lat: str, lng: str
) -> None:
    response = _post_location(client, latitude=lat, longitude=lng)

    assert response.status_code == 200
    assert Location.objects.filter(name="Test Place").exists() is False
    body = response.content.decode()
    assert not any(marker in body for marker in LEAK_MARKERS)


def test_blank_coordinates_become_none(client: Client) -> None:
    response = _post_location(
        client, latitude="49.006900", longitude="8.403700", country_code=""
    )
    assert response.status_code == 302

    response = _post_location(client, name="No Coords", latitude="", longitude="")
    assert response.status_code == 302
    created = Location.objects.get(name="No Coords")
    assert created.latitude is None and created.longitude is None


# ---------------------------------------------------------------------------
# Default residence invariants
# ---------------------------------------------------------------------------


def test_first_residence_becomes_default(client: Client) -> None:
    response = _post_location(
        client, name="First Home", location_type=LocationType.RESIDENCE
    )

    assert response.status_code == 302
    assert Location.objects.filter(is_default_residence=True).count() == 1
    assert Location.objects.get(is_default_residence=True).name == "First Home"


def test_non_residence_cannot_be_default(client: Client) -> None:
    response = _post_location(
        client,
        name="Client Site",
        location_type=LocationType.CLIENT_SITE,
        is_default_residence="on",
    )

    assert response.status_code == 200
    assert Location.objects.filter(is_default_residence=True).count() == 0
    assert Location.objects.filter(name="Client Site").exists() is False
    body = response.content.decode()
    assert "Only a residence" in body


def test_failed_default_replacement_preserves_old_default(client: Client) -> None:
    _create_residence(client)

    response = _post_location(
        client,
        name="Broken Home",
        location_type=LocationType.RESIDENCE,
        is_default_residence="on",
        country_code="XX",
    )

    assert response.status_code == 200
    assert Location.objects.get(is_default_residence=True).name == "Old Home"
    assert Location.objects.filter(name="Broken Home").exists() is False


def test_default_residence_swap_updates_previous(client: Client) -> None:
    _create_residence(client)

    response = _post_location(
        client,
        name="New Home",
        location_type=LocationType.RESIDENCE,
        is_default_residence="on",
    )

    assert response.status_code == 302
    assert Location.objects.filter(is_default_residence=True).count() == 1
    assert Location.objects.get(is_default_residence=True).name == "New Home"


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(
    connection.vendor != "postgresql", reason="concurrency check requires PostgreSQL"
)
def test_concurrent_first_residence_leaves_single_default(client: Client) -> None:
    """Two racing first-residence creates must end with exactly one default.

    This test uses thread-local database connections. ``transaction=True`` is
    required so those connections can see the owner row created by the setup
    fixture; Django's default test transaction would otherwise keep that row
    uncommitted and make a concurrent insert wait indefinitely.

    The partial unique index is the backstop: exactly one of the two racing
    inserts wins, the other rolls back and gets a retryable inline error.
    Exactly one default must survive; the loser may see either a success
    redirect (serialised after the winner committed, swap path) or a 200
    with the retry message (unique-index conflict). A 500 is never allowed.
    """
    from concurrent.futures import ThreadPoolExecutor

    def attempt(name: str) -> int:
        c = logged_in_client()
        response = _post_location(
            c, name=name, location_type=LocationType.RESIDENCE, is_default_residence="on"
        )
        return response.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(attempt, ["Racing Home A", "Racing Home B"]))

    assert all(code in (200, 302) for code in codes), codes
    assert Location.objects.filter(is_default_residence=True).count() == 1


# ---------------------------------------------------------------------------
# Employer: atomic replacement, failed validation preserves active
# ---------------------------------------------------------------------------


def test_employer_replacement_atomic_and_rollback(client: Client) -> None:
    Employer.objects.create(name="Previous", is_active=True)

    # Valid replacement succeeds and is the only active employer.
    response = client.post(
        reverse("travel:settings_employer"), {"action": "employer", "name": "New Employer"}
    )
    assert response.status_code == 302
    assert Employer.objects.filter(is_active=True).count() == 1
    assert Employer.objects.get(is_active=True).name == "New Employer"

    # Blank name fails inline; previous active employer untouched, no new row.
    response = client.post(reverse("travel:settings_employer"), {"action": "employer", "name": ""})
    assert response.status_code == 200
    assert Employer.objects.filter(is_active=True).count() == 1
    assert Employer.objects.get(is_active=True).name == "New Employer"
    body = response.content.decode()
    assert not any(marker in body for marker in LEAK_MARKERS)


# ---------------------------------------------------------------------------
# Rail pass: date order, malformed dates, overlong strings, amount
# ---------------------------------------------------------------------------


def _post_pass(client: Client, **overrides: object) -> HttpResponse:
    payload: dict[str, object] = {
        "action": "rail_pass",
        "name": "BahnCard 100",
        "pass_type": "unlimited",
        "valid_from": "2026-01-01",
        "valid_to": "2026-12-31",
        "purchase_cost": "",
    }
    payload.update(overrides)
    return cast(HttpResponse, client.post(reverse("travel:settings_rail_passes"), payload))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"valid_from": "not-a-date"}, "date"),
        ({"valid_to": "not-a-date"}, "date"),
        ({"valid_from": "2026-06-01", "valid_to": "2026-05-01"}, "after"),
        ({"purchase_cost": "-5"}, "nonnegative"),
        ({"purchase_cost": "abc"}, "number"),
        ({"name": "x" * 201}, "characters"),
    ],
)
def test_rail_pass_malformed_input_inline_error_no_row(
    client: Client, overrides: dict[str, str], message: str
) -> None:
    response = _post_pass(client, **overrides)

    assert response.status_code == 200
    assert RailPass.objects.count() == 0
    body = response.content.decode().lower()
    assert message in body
    assert not any(marker in body for marker in LEAK_MARKERS)


def test_rail_pass_valid_optional_cost(client: Client) -> None:
    response = _post_pass(client, purchase_cost="49.00")

    assert response.status_code == 302
    assert RailPass.objects.get(name="BahnCard 100").purchase_cost == Decimal("49.00")


# ---------------------------------------------------------------------------
# Routes: malformed inputs never 500, provider failure is nonfatal
# ---------------------------------------------------------------------------


def _locations_pair() -> tuple[Location, Location]:
    home = Location.objects.create(
        name="Home", location_type=LocationType.RESIDENCE, country_code="DE"
    )
    office = Location.objects.create(
        name="Office", location_type=LocationType.FIRST_WORKPLACE, country_code="DE"
    )
    return home, office


@pytest.mark.parametrize(
    ("action", "payload"),
    [
        ("route_lookup", {"origin": "not-a-uuid", "destination": "still-not"}),
        ("route_lookup", {"origin": "", "destination": ""}),
        ("route_manual", {"origin": "not-a-uuid", "destination": "not-a-uuid"}),
        (
            "route_manual",
            {
                "origin": "00000000-0000-0000-0000-000000000000",
                "destination": "00000000-0000-0000-0000-000000000001",
            },
        ),
        ("route_confirm", {"candidate": "not-a-uuid"}),
        ("route_confirm", {"candidate": "00000000-0000-0000-0000-000000000000"}),
    ],
)
def test_malformed_route_inputs_no_500(
    client: Client, action: str, payload: dict[str, str]
) -> None:
    _locations_pair()
    response = client.post(reverse("travel:settings_routes"), {"action": action, **payload})

    assert response.status_code == 200
    body = response.content.decode()
    assert not any(marker in body for marker in LEAK_MARKERS)


def test_route_origin_destination_same_location_inline_error(client: Client) -> None:
    home, _ = _locations_pair()
    response = client.post(
        reverse("travel:settings_routes"),
        {"action": "route_lookup", "origin": str(home.pk), "destination": str(home.pk)},
    )

    assert response.status_code == 200
    assert "same" in response.content.decode().lower()
    assert RouteDistance.objects.count() == 0


def test_route_manual_negative_or_huge_distance_inline_error(client: Client) -> None:
    home, office = _locations_pair()
    base = {"action": "route_manual", "origin": str(home.pk), "destination": str(office.pk)}

    for bad in ("-1", "1e999", "abc"):
        response = client.post(
            reverse("travel:settings_routes"), {**base, "distance_km": bad, "route_comment": "x"}
        )
        assert response.status_code == 200
        assert RouteDistance.objects.count() == 0


@pytest.mark.parametrize(
    "reason",
    [
        "",
        "coordinates not included",
        "äöü — déjà vu; note (manual)",
        "ordinary punctuation: commas, periods, apostrophes, and / slashes!",
        "x" * 500,
    ],
)
def test_manual_route_confirmation_accepts_ordinary_notes(
    client: Client, reason: str
) -> None:
    home, office = _locations_pair()
    response = client.post(
        reverse("travel:settings_routes"),
        {
            "action": "route_manual",
            "origin": str(home.pk),
            "destination": str(office.pk),
            "distance_km": "139",
            "route_comment": reason,
        },
    )

    assert response.status_code == 302
    route = RouteDistance.objects.get()
    assert route.origin_id == home.pk
    assert route.destination_id == office.pk
    assert route.override_comment == reason


def test_overlong_manual_route_note_is_an_inline_error_and_preserves_input(
    client: Client,
) -> None:
    home, office = _locations_pair()
    reason = "x" * 501
    response = client.post(
        reverse("travel:settings_routes"),
        {
            "action": "route_manual",
            "origin": str(home.pk),
            "destination": str(office.pk),
            "distance_km": "139",
            "route_comment": reason,
        },
    )

    body = response.content.decode()
    assert response.status_code == 200
    assert "at most 500 characters" in body
    assert reason in body
    assert f'value="{home.pk}" selected' in body
    assert f'value="{office.pk}" selected' in body
    assert RouteDistance.objects.count() == 0


def test_provider_distance_override_requires_a_reason(client: Client) -> None:
    home, office = _locations_pair()
    candidate = record_route_distance(
        origin=home,
        destination=office,
        mode="driving",
        distance_km=Decimal("139"),
        source="openrouteservice",
        confirmed=False,
    )
    response = client.post(
        reverse("travel:settings_routes"),
        {
            "action": "route_confirm",
            "candidate": str(candidate.pk),
            "distance_km": "140",
            "route_comment": "",
        },
    )

    assert response.status_code == 200
    assert "correction requires a confirmation reason" in response.content.decode()
    assert RouteDistance.objects.filter(confirmed=True).count() == 0


def test_manual_confirmation_requires_reason_when_replacing_provider_route(client: Client) -> None:
    home, office = _locations_pair()
    record_route_distance(
        origin=home,
        destination=office,
        mode="driving",
        distance_km=Decimal("139"),
        source="openrouteservice",
        confirmed=True,
    )
    response = client.post(
        reverse("travel:settings_routes"),
        {
            "action": "route_manual",
            "origin": str(home.pk),
            "destination": str(office.pk),
            "distance_km": "140",
            "route_comment": "",
        },
    )

    assert response.status_code == 200
    assert "override reason is required" in response.content.decode()
    assert RouteDistance.objects.count() == 1


def test_route_provider_failure_preserves_confirmed_data(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, office = _locations_pair()
    record_route_distance(
        origin=home,
        destination=office,
        mode="driving",
        distance_km=Decimal("141.00"),
        source="manual",
        manual_override=True,
        override_comment="Standard commute",
        confirmed=True,
    )
    before = list(RouteDistance.objects.values_list("pk", "distance_km", "confirmed"))

    def broken_lookup(origin: Location, destination: Location) -> RouteDistance:
        raise RouteLookupUnavailable("OPENROUTESERVICE_API_KEY is not configured")

    monkeypatch.setattr("apps.travel.views.fetch_shortest_road_route", broken_lookup)
    response = client.post(
        reverse("travel:settings_routes"),
        {"action": "route_lookup", "origin": str(home.pk), "destination": str(office.pk)},
    )

    assert response.status_code == 200
    assert "OPENROUTESERVICE_API_KEY" in response.content.decode()
    assert list(RouteDistance.objects.values_list("pk", "distance_km", "confirmed")) == before


# ---------------------------------------------------------------------------
# Journey add-location return flow and safe next
# ---------------------------------------------------------------------------


def test_journey_page_add_location_link_uses_safe_next(client: Client) -> None:
    response = client.get(reverse("travel:journey_entry"))

    body = response.content.decode()
    next_url = reverse("travel:journey_entry")
    assert f"{reverse('travel:settings_locations')}?next=" in body
    assert next_url in body


def test_settings_locations_next_redirects_back_to_journey(client: Client) -> None:
    journey_url = reverse("travel:journey_entry")
    response = _post_location(client, next=journey_url)

    assert response.status_code == 302
    location = Location.objects.get(name="Test Place")
    assert response.headers["Location"] == f"{journey_url}?new_location={location.pk}"


def test_settings_locations_next_external_target_ignored(client: Client) -> None:
    response = _post_location(client, next="https://evil.example/phish")

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("travel:settings_locations")


def test_journey_new_location_preselected_on_return(client: Client) -> None:
    _post_location(client, name="Brand New Office", location_type=LocationType.FIRST_WORKPLACE)
    location = Location.objects.get(name="Brand New Office")

    response = client.get(
        reverse("travel:journey_entry"), {"new_location": str(location.pk)}
    )

    body = response.content.decode()
    assert f'value="{location.pk}"' in body
    assert "checked" in body
    assert "data-initial-destination" in body


# ---------------------------------------------------------------------------
# Home setup callouts
# ---------------------------------------------------------------------------


def test_home_setup_callouts_when_missing(client: Client) -> None:
    response = client.get(reverse("home"))

    body = response.content.decode()
    assert "add your residence" in body
    assert "add your employer" in body
    assert "data-setup-callout" in body


def test_home_setup_callouts_disappear_when_configured(client: Client) -> None:
    _create_residence(client)
    client.post(reverse("travel:settings_employer"), {"action": "employer", "name": "ACME"})

    response = client.get(reverse("home"))

    body = response.content.decode()
    assert "data-setup-callout" not in body
    assert "add your residence" not in body


# ---------------------------------------------------------------------------
# Ordinary pages never leak tracebacks / SQL / raw DB errors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url_name",
    [
        "travel:settings",
        "travel:settings_locations",
        "travel:settings_employer",
        "travel:settings_rail_passes",
        "travel:settings_routes",
        "travel:settings_security",
        "travel:settings_defaults",
        "travel:journey_entry",
        "travel:external_activity_entry",
        "home",
    ],
)
def test_ordinary_pages_do_not_leak_internals(client: Client, url_name: str) -> None:
    response = client.get(reverse(url_name))

    assert response.status_code == 200
    body = response.content.decode()
    assert not any(marker in body for marker in LEAK_MARKERS)
