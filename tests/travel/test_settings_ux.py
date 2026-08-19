"""Settings presentation split into an index plus six data-section subpages.

Pins the new presentation contract without schema changes:

- ``/settings/`` stays the legacy entry point (index of six section cards) and
  its existing POST actions keep working, redirecting to the right subpage.
- Each data page lists existing records first and only then offers the
  add-new action inside a disclosure.
- Coordinates and route provider fields are advanced/technical only.
- Security exposes a safe status readout plus a PIN-change flow that reuses
  the existing Argon2id services (current-PIN verification, validation,
  lockout) without weakening login.
- Defaults is read-only: it shows the currency/timezone/output defaults that
  already live in configuration — nothing is persisted for display.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Owner
from apps.accounts.services import authenticate_pin, configure_pin
from apps.taxes.journey import record_route_distance
from apps.taxes.models import RouteDistance
from apps.travel.models import Employer, Location, LocationType, RailPass

pytestmark = pytest.mark.django_db

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def logged_in_client() -> Client:
    configure_pin("123456")
    client = Client()
    response = client.post(reverse("accounts:login"), {"pin": "123456"})
    assert response.status_code == 302
    return client


def _settings_url() -> str:
    return reverse("travel:settings")


def _locations() -> list[Location]:
    home = Location.objects.create(
        name="Home",
        location_type=LocationType.RESIDENCE,
        address="Home Street 1",
        locality="Berlin",
        station_name="Berlin Hbf",
        is_default_residence=True,
        is_favourite=True,
        latitude=Decimal("49.006900"),
        longitude=Decimal("8.403700"),
    )
    office = Location.objects.create(
        name="Office",
        location_type=LocationType.FIRST_WORKPLACE,
        locality="Hamburg",
        station_name="Hamburg Hbf",
        latitude=Decimal("50.110900"),
        longitude=Decimal("8.682100"),
    )
    return [home, office]


def _routes() -> list[RouteDistance]:
    home, office = _locations()
    confirmed = record_route_distance(
        origin=home,
        destination=office,
        mode="driving",
        distance_km=Decimal("141.00"),
        source="manual",
        manual_override=True,
        override_comment="Standard commute",
        confirmed=True,
    )
    candidate = record_route_distance(
        origin=office,
        destination=home,
        mode="driving",
        distance_km=Decimal("142.50"),
        source="openrouteservice",
        source_url="https://api.openrouteservice.org/v2/directions/driving-car/geojson",
        provider_input={"coordinates": [[8.4, 50.1], [8.7, 49.0]]},
        provider_response={"distance_metres": 142500},
        raw_response_hash="f" * 64,
        returned_metres=142500,
        confirmed=False,
    )
    return [confirmed, candidate]


# --------------------------------------------------------------------------
# Index: legacy /settings/ entry point with six data-section cards
# --------------------------------------------------------------------------


def test_settings_index_shows_six_section_cards() -> None:
    client = logged_in_client()

    response = client.get(_settings_url())

    assert response.status_code == 200
    body = response.content.decode()
    assert "<h1" in body and "settings" in body
    expected = {
        "locations": "travel:settings_locations",
        "employer": "travel:settings_employer",
        "rail_passes": "travel:settings_rail_passes",
        "routes": "travel:settings_routes",
        "security": "travel:settings_security",
        "defaults": "travel:settings_defaults",
    }
    for section, url_name in expected.items():
        assert f'data-settings-section="{section}"' in body
        assert f'href="{reverse(url_name)}"' in body


def test_all_six_settings_subpages_render() -> None:
    client = logged_in_client()

    for url_name in (
        "travel:settings_locations",
        "travel:settings_employer",
        "travel:settings_rail_passes",
        "travel:settings_routes",
        "travel:settings_security",
        "travel:settings_defaults",
    ):
        response = client.get(reverse(url_name))
        assert response.status_code == 200, url_name
        body = response.content.decode()
        # Every subpage shares the common page-header pattern and a back link
        # to the settings index.
        assert 'href="' + _settings_url() + '"' in body, url_name


# --------------------------------------------------------------------------
# Locations: records first, add-new in a disclosure, coordinates advanced
# --------------------------------------------------------------------------


def test_locations_page_lists_existing_records_before_add_disclosure() -> None:
    _locations()
    client = logged_in_client()

    response = client.get(reverse("travel:settings_locations"))

    body = response.content.decode()
    list_pos = body.index('data-locations-list')
    add_pos = body.index('data-add-location')
    assert list_pos < add_pos
    assert "Home" in body and "Office" in body
    # Ordinary location rows never show the raw UUID.
    assert UUID_RE.search(body) is None


def test_locations_page_shows_primary_fields_and_default_favourite_badges() -> None:
    home, _office = _locations()
    client = logged_in_client()

    response = client.get(reverse("travel:settings_locations"))

    body = response.content.decode()
    assert "Home" in body
    assert "Residence" in body  # type label, not the code
    assert "Berlin" in body  # city
    assert "Berlin Hbf" in body  # station
    assert "default" in body and "favourite" in body
    assert str(home.pk) not in body


def test_locations_coordinates_only_under_technical_details() -> None:
    _locations()
    client = logged_in_client()

    response = client.get(reverse("travel:settings_locations"))

    body = response.content.decode()
    # Coordinates never appear in the ordinary list markup; each record's
    # technical disclosure exists and contains the coordinates.
    assert "data-location-technical" in body
    technical_start = body.index("data-location-technical")
    assert "49.006900" not in body[:technical_start]
    assert "49.006900" in body[technical_start:]
    assert "8.403700" in body[technical_start:]


def test_locations_add_form_hides_coordinates_under_advanced_disclosure() -> None:
    client = logged_in_client()

    response = client.get(reverse("travel:settings_locations"))

    body = response.content.decode()
    form_start = body.index('name="action" value="location"')
    advanced_pos = body.index("data-advanced-fields")
    assert advanced_pos > form_start
    # Latitude input exists but lives inside the advanced disclosure.
    lat_pos = body.index('name="latitude"', form_start)
    assert lat_pos > advanced_pos


def test_locations_subpage_post_creates_location_and_redirects() -> None:
    client = logged_in_client()

    response = client.post(
        reverse("travel:settings_locations"),
        {
            "action": "location",
            "name": "Client Munich",
            "location_type": LocationType.CLIENT_SITE,
            "address": "Example 1",
            "country_code": "de",
            "locality": "Munich",
            "station_name": "München Hbf",
            "is_favourite": "on",
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("travel:settings_locations")
    created = Location.objects.get(name="Client Munich")
    assert created.location_type == LocationType.CLIENT_SITE
    assert created.locality == "Munich"
    assert created.country_code == "DE"
    assert created.is_favourite is True
    assert created.is_default_residence is False


def test_locations_post_default_residence_swaps_previous_default() -> None:
    _locations()
    client = logged_in_client()

    response = client.post(
        reverse("travel:settings_locations"),
        {
            "action": "location",
            "name": "New Home",
            "location_type": LocationType.RESIDENCE,
            "is_default_residence": "on",
        },
    )

    assert response.status_code == 302
    assert Location.objects.filter(is_default_residence=True).count() == 1
    assert Location.objects.get(is_default_residence=True).name == "New Home"


# --------------------------------------------------------------------------
# Employer: readable rows
# --------------------------------------------------------------------------


def test_employer_page_readable_rows_and_add_disclosure() -> None:
    Employer.objects.create(name="First Employer", is_active=False)
    Employer.objects.create(name="Active Employer", is_active=True)
    client = logged_in_client()

    response = client.get(reverse("travel:settings_employer"))

    body = response.content.decode()
    assert 'data-employers-list' in body
    assert 'data-employer-record' in body
    list_pos = body.index("data-employers-list")
    add_pos = body.index("data-add-employer")
    assert list_pos < add_pos
    assert "First Employer" in body
    assert "Active Employer" in body
    assert "active" in body
    assert UUID_RE.search(body) is None


def test_employer_subpage_post_sets_active_employer() -> None:
    Employer.objects.create(name="Previous", is_active=True)
    client = logged_in_client()

    response = client.post(
        reverse("travel:settings_employer"),
        {"action": "employer", "name": "New Employer"},
    )

    assert response.status_code == 302
    assert Employer.objects.filter(is_active=True).count() == 1
    active = Employer.objects.get(is_active=True)
    assert active.name == "New Employer"


# --------------------------------------------------------------------------
# Rail passes: readable rows
# --------------------------------------------------------------------------


def test_rail_passes_page_readable_rows_and_add_disclosure() -> None:
    kind = "local"
    RailPass.objects.create(
        name="Deutschlandticket",
        pass_type=kind,
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        purchase_cost=Decimal("49.00"),
    )
    client = logged_in_client()

    response = client.get(reverse("travel:settings_rail_passes"))

    body = response.content.decode()
    assert "data-rail-passes-list" in body
    list_pos = body.index("data-rail-passes-list")
    add_pos = body.index("data-add-rail-pass")
    assert list_pos < add_pos
    assert "Deutschlandticket" in body
    assert "2026-01-01" in body and "2026-12-31" in body
    assert "49.00" in body
    assert UUID_RE.search(body) is None


def test_rail_passes_subpage_post_creates_pass() -> None:
    client = logged_in_client()

    response = client.post(
        reverse("travel:settings_rail_passes"),
        {
            "action": "rail_pass",
            "name": "BahnCard 100",
            "pass_type": "unlimited",
            "valid_from": "2026-01-01",
            "valid_to": "2026-12-31",
        },
    )

    assert response.status_code == 302
    rail_pass = RailPass.objects.get(name="BahnCard 100")
    assert rail_pass.valid_from == date(2026, 1, 1)
    assert rail_pass.valid_to == date(2026, 12, 31)


# --------------------------------------------------------------------------
# Commuting routes: lookup/manual confirmation, provider fields technical
# --------------------------------------------------------------------------


def test_routes_page_lists_confirmed_routes_readably() -> None:
    _routes()
    client = logged_in_client()

    response = client.get(reverse("travel:settings_routes"))

    body = response.content.decode()
    assert "data-routes-list" in body
    assert "data-route-record" in body
    # Origin → destination and kilometres are the ordinary rows.
    assert "Home" in body and "Office" in body
    assert "141.00" in body
    # Provider payloads/hashes never appear in ordinary markup (they may
    # only live inside the technical disclosure). Hidden form values and
    # select option ids are functional, not display, and are not asserted.
    technical_start = body.index("data-technical-details")
    ordinary = body[:technical_start]
    assert "openrouteservice" not in ordinary
    assert "coordinates" not in ordinary
    assert "f" * 64 not in ordinary


def test_routes_page_offers_lookup_and_manual_confirmation() -> None:
    _routes()
    client = logged_in_client()

    response = client.get(reverse("travel:settings_routes"))

    body = response.content.decode()
    assert "data-route-lookup" in body  # provider lookup form
    assert "data-route-manual" in body  # manual confirmation form
    assert "data-route-candidate" in body  # unconfirmed candidate review


def test_routes_page_provider_fields_only_under_technical_details() -> None:
    _routes()
    client = logged_in_client()

    response = client.get(reverse("travel:settings_routes"))

    body = response.content.decode()
    assert "data-technical-details" in body
    technical_start = body.index("data-technical-details")
    # The provider source URL appears only inside the technical disclosure.
    assert "openrouteservice" not in body[:technical_start]


def test_routes_manual_post_creates_confirmed_route() -> None:
    home, office = _locations()
    client = logged_in_client()

    response = client.post(
        reverse("travel:settings_routes"),
        {
            "action": "route_manual",
            "origin": str(home.pk),
            "destination": str(office.pk),
            "distance_km": "150.50",
            "route_comment": "Measured manually",
        },
    )

    assert response.status_code == 302
    route = RouteDistance.objects.get(confirmed=True, manual_override=True)
    assert route.origin == home and route.destination == office
    assert route.distance_km == Decimal("150.50")
    assert route.override_comment == "Measured manually"


def test_routes_lookup_post_creates_unconfirmed_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, office = _locations()

    def fake_lookup(origin: Location, destination: Location) -> RouteDistance:
        return record_route_distance(
            origin=origin,
            destination=destination,
            mode="driving",
            distance_km=Decimal("149.99"),
            source="openrouteservice",
            source_url="https://api.openrouteservice.org/v2/directions/driving-car/geojson",
            provider_input={"coordinates": [[8.4, 49.0], [8.7, 50.1]]},
            provider_response={"distance_metres": 149990},
            raw_response_hash="a" * 64,
            returned_metres=149990,
            confirmed=False,
        )

    monkeypatch.setattr("apps.travel.views.fetch_shortest_road_route", fake_lookup)
    client = logged_in_client()

    response = client.post(
        reverse("travel:settings_routes"),
        {"action": "route_lookup", "origin": str(home.pk), "destination": str(office.pk)},
    )

    assert response.status_code == 302
    candidate = RouteDistance.objects.get(confirmed=False)
    assert candidate.origin == home and candidate.destination == office
    assert candidate.source == "openrouteservice"


def test_routes_confirm_post_confirms_candidate() -> None:
    _routes()
    candidate = RouteDistance.objects.get(confirmed=False)
    client = logged_in_client()

    response = client.post(
        reverse("travel:settings_routes"),
        {"action": "route_confirm", "candidate": str(candidate.pk), "route_comment": "Looks right"},
    )

    assert response.status_code == 302
    assert RouteDistance.objects.filter(confirmed=True).count() == 2


# --------------------------------------------------------------------------
# Security: safe status + PIN change without weakening lockout/Argon2id
# --------------------------------------------------------------------------


def test_security_page_shows_status_lock_and_navigation() -> None:
    configure_pin("123456")
    client = logged_in_client()

    response = client.get(reverse("travel:settings_security"))

    body = response.content.decode()
    assert "data-security-page" in body
    assert "data-pin-configured" in body  # PIN is set
    assert "Argon2id" in body or "argon2" in body
    assert "data-lockout-state" in body
    assert 'action="' + reverse("accounts:logout") + '"' in body  # lock form
    assert "data-pin-change" in body  # change-PIN flow present


def test_security_page_reports_lockout_state_honestly() -> None:
    client = logged_in_client()
    owner = Owner.objects.get()
    owner.failed_attempts = 3
    owner.next_attempt_at = timezone.now() + timedelta(seconds=60)
    owner.save(update_fields=["failed_attempts", "next_attempt_at", "updated_at"])

    response = client.get(reverse("travel:settings_security"))

    body = response.content.decode()
    assert "data-lockout-state" in body
    # The page must not claim the owner is unlocked while a lockout is active.
    assert "temporarily locked" in body
    assert "3" in body  # failed attempts surfaced


def test_security_pin_change_requires_current_pin() -> None:
    from django.contrib.auth.hashers import check_password

    client = logged_in_client()

    response = client.post(
        reverse("travel:settings_security"),
        {
            "action": "change_pin",
            "current_pin": "999999",
            "new_pin": "654321",
            "confirmation": "654321",
        },
    )

    assert response.status_code == 400
    # The stored hash still verifies the original PIN (a failed change only
    # applied the normal failed-attempt lockout, never the new PIN).
    assert check_password("123456", Owner.objects.get().pin_hash) is True
    assert check_password("654321", Owner.objects.get().pin_hash) is False


def test_security_pin_change_requires_matching_confirmation() -> None:
    configure_pin("123456")
    client = logged_in_client()

    response = client.post(
        reverse("travel:settings_security"),
        {
            "action": "change_pin",
            "current_pin": "123456",
            "new_pin": "654321",
            "confirmation": "654322",
        },
    )

    assert response.status_code == 400
    assert authenticate_pin("123456").authenticated is True
    assert authenticate_pin("654321").authenticated is False


def test_security_pin_change_validates_new_pin_format() -> None:
    configure_pin("123456")
    client = logged_in_client()

    response = client.post(
        reverse("travel:settings_security"),
        {
            "action": "change_pin",
            "current_pin": "123456",
            "new_pin": "12x",
            "confirmation": "12x",
        },
    )

    assert response.status_code == 400
    assert authenticate_pin("123456").authenticated is True


def test_security_pin_change_preserves_argon2id() -> None:
    from django.contrib.auth.hashers import identify_hasher

    configure_pin("123456")
    client = logged_in_client()

    response = client.post(
        reverse("travel:settings_security"),
        {
            "action": "change_pin",
            "current_pin": "123456",
            "new_pin": "654321",
            "confirmation": "654321",
        },
    )

    assert response.status_code == 200
    owner = Owner.objects.get()
    assert identify_hasher(owner.pin_hash).algorithm == "argon2"
    assert "$argon2id$" in owner.pin_hash
    # Session stays authenticated after a successful change.
    assert client.get(_settings_url()).status_code == 200
    assert authenticate_pin("654321").authenticated is True
    assert authenticate_pin("123456").authenticated is False


def test_security_pin_change_respects_active_lockout() -> None:
    from django.contrib.auth.hashers import check_password

    client = logged_in_client()
    owner = Owner.objects.get()
    owner.failed_attempts = 5
    owner.next_attempt_at = timezone.now() + timedelta(seconds=300)
    owner.save(update_fields=["failed_attempts", "next_attempt_at", "updated_at"])

    response = client.post(
        reverse("travel:settings_security"),
        {
            "action": "change_pin",
            "current_pin": "123456",
            "new_pin": "654321",
            "confirmation": "654321",
        },
    )

    assert response.status_code == 400
    # The lockout blocks the current-PIN verification, so the PIN is unchanged.
    assert check_password("123456", Owner.objects.get().pin_hash) is True
    assert check_password("654321", Owner.objects.get().pin_hash) is False


# --------------------------------------------------------------------------
# Defaults: read-only view of existing configuration
# --------------------------------------------------------------------------


def test_defaults_page_shows_currency_timezone_and_output_defaults() -> None:
    client = logged_in_client()

    response = client.get(reverse("travel:settings_defaults"))

    body = response.content.decode()
    assert "data-defaults-page" in body
    assert "Europe/Berlin" in body  # settings.TIME_ZONE
    assert "EUR" in body  # entry/claim currency default
    assert "Excel" in body or "xlsx" in body  # default export format
    assert "data-default-row" in body


def test_defaults_page_is_read_only() -> None:
    client = logged_in_client()

    response = client.get(reverse("travel:settings_defaults"))

    body = response.content.decode()
    # No form, no CSRF token, nothing to POST.
    assert "<form" not in body
    assert "csrfmiddlewaretoken" not in body


# --------------------------------------------------------------------------
# Legacy /settings/ POST workflows keep working from the index
# --------------------------------------------------------------------------


def test_legacy_settings_post_location_redirects_to_locations_page() -> None:
    client = logged_in_client()

    response = client.post(
        _settings_url(),
        {
            "action": "location",
            "name": "Legacy Location",
            "location_type": LocationType.OTHER_EXTERNAL,
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("travel:settings_locations")
    assert Location.objects.filter(name="Legacy Location").exists()


def test_legacy_settings_post_employer_redirects_to_employer_page() -> None:
    client = logged_in_client()

    response = client.post(_settings_url(), {"action": "employer", "name": "Legacy Employer"})

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("travel:settings_employer")
    assert Employer.objects.get(is_active=True).name == "Legacy Employer"


def test_legacy_settings_post_rail_pass_redirects_to_passes_page() -> None:
    client = logged_in_client()

    response = client.post(
        _settings_url(),
        {
            "action": "rail_pass",
            "name": "Legacy Pass",
            "pass_type": "other",
            "valid_from": "2026-06-01",
            "valid_to": "2026-06-30",
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("travel:settings_rail_passes")
    assert RailPass.objects.filter(name="Legacy Pass").exists()


def test_legacy_settings_post_route_manual_redirects_to_routes_page() -> None:
    home, office = _locations()
    client = logged_in_client()

    response = client.post(
        _settings_url(),
        {
            "action": "route_manual",
            "origin": str(home.pk),
            "destination": str(office.pk),
            "distance_km": "140.00",
            "route_comment": "Legacy manual route",
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("travel:settings_routes")
    assert RouteDistance.objects.filter(confirmed=True).exists()


def test_legacy_settings_post_route_lookup_redirects_to_routes_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, office = _locations()

    def fake_lookup(origin: Location, destination: Location) -> RouteDistance:
        return record_route_distance(
            origin=origin,
            destination=destination,
            mode="driving",
            distance_km=Decimal("138.00"),
            source="openrouteservice",
            source_url="https://api.openrouteservice.org/v2/directions/driving-car/geojson",
            provider_input={"coordinates": [[8.4, 49.0], [8.7, 50.1]]},
            provider_response={"distance_metres": 138000},
            raw_response_hash="b" * 64,
            returned_metres=138000,
            confirmed=False,
        )

    monkeypatch.setattr("apps.travel.views.fetch_shortest_road_route", fake_lookup)
    client = logged_in_client()

    response = client.post(
        _settings_url(),
        {"action": "route_lookup", "origin": str(home.pk), "destination": str(office.pk)},
    )

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("travel:settings_routes")
    assert RouteDistance.objects.filter(confirmed=False).exists()


def test_settings_pages_require_login() -> None:
    client = Client()

    for url_name in (
        "travel:settings",
        "travel:settings_locations",
        "travel:settings_employer",
        "travel:settings_rail_passes",
        "travel:settings_routes",
        "travel:settings_security",
        "travel:settings_defaults",
    ):
        response = client.get(reverse(url_name))
        assert response.status_code == 302, url_name
        assert response.headers["Location"].startswith(reverse("accounts:login"))
