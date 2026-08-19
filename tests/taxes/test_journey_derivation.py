from datetime import UTC, datetime
from decimal import Decimal

import pytest

from apps.taxes.journey import derive_journey_tax, record_route_distance
from apps.travel.models import Location, LocationType, TransportMode
from apps.travel.services import create_journey

pytestmark = pytest.mark.django_db


def test_first_workplace_uses_one_way_distance_and_2026_commuting_rule() -> None:
    home = Location.objects.create(
        name="Home", location_type=LocationType.RESIDENCE, is_default_residence=True
    )
    office = Location.objects.create(name="Office", location_type=LocationType.FIRST_WORKPLACE)
    route = record_route_distance(
        origin=home,
        destination=office,
        mode="driving",
        distance_km=Decimal("42.90"),
        source="manual",
    )
    event = create_journey(
        origin=home,
        destination=office,
        transport_mode=TransportMode.PRIVATE_CAR,
        effective_at=datetime(2026, 8, 4, 7, 0, tzinfo=UTC),
    )

    result = derive_journey_tax(event)

    assert result.classification == "commuting_allowance"
    assert result.distance_km == Decimal("42")
    assert result.amount == Decimal("15.96")
    assert result.rule_code == "DE_COMMUTING_2026"
    assert result.route_version == route.version


def test_client_trip_private_car_uses_actual_round_trip_kilometres() -> None:
    home = Location.objects.create(
        name="Home", location_type=LocationType.RESIDENCE, is_default_residence=True
    )
    client = Location.objects.create(name="Client", location_type=LocationType.CLIENT_SITE)
    event = create_journey(
        origin=home,
        destination=client,
        transport_mode=TransportMode.PRIVATE_CAR,
        effective_at=datetime(2026, 8, 4, 7, 0, tzinfo=UTC),
        actual_kilometres=Decimal("85.80"),
    )

    result = derive_journey_tax(event)

    assert result.classification == "business_mileage"
    assert result.distance_km == Decimal("85.80")
    assert result.amount == Decimal("25.74")
    assert result.rule_code == "DE_BUSINESS_MILEAGE_2026"


def test_route_corrections_create_versions_without_overwriting() -> None:
    home = Location.objects.create(
        name="Home", location_type=LocationType.RESIDENCE, is_default_residence=True
    )
    office = Location.objects.create(name="Office", location_type=LocationType.FIRST_WORKPLACE)

    first = record_route_distance(
        origin=home,
        destination=office,
        mode="driving",
        distance_km=Decimal("42.90"),
        source="osrm",
    )
    second = record_route_distance(
        origin=home,
        destination=office,
        mode="driving",
        distance_km=Decimal("41.70"),
        source="manual correction",
    )

    first.refresh_from_db()
    assert first.version == 1
    assert first.distance_km == Decimal("42.90")
    assert second.version == 2


def test_commuting_allowance_is_not_duplicated_for_same_workplace_and_day() -> None:
    home = Location.objects.create(
        name="Home", location_type=LocationType.RESIDENCE, is_default_residence=True
    )
    office = Location.objects.create(name="Office", location_type=LocationType.FIRST_WORKPLACE)
    record_route_distance(
        origin=home,
        destination=office,
        mode="driving",
        distance_km=Decimal("10.90"),
        source="manual",
    )
    first = create_journey(
        origin=home,
        destination=office,
        transport_mode=TransportMode.TRAIN,
        effective_at=datetime(2026, 8, 4, 7, 0, tzinfo=UTC),
    )
    second = create_journey(
        origin=home,
        destination=office,
        transport_mode=TransportMode.PRIVATE_CAR,
        effective_at=datetime(2026, 8, 4, 12, 0, tzinfo=UTC),
    )

    assert derive_journey_tax(first).amount == Decimal("3.80")
    duplicate = derive_journey_tax(second)
    assert duplicate.amount == Decimal("0.00")
    assert duplicate.missing_facts == (
        "commuting allowance already used for this workplace and day",
    )
