from datetime import UTC, datetime

import pytest

from apps.travel.models import Location, LocationType, TransportMode
from apps.travel.services import create_journey, repeat_journey, reverse_journey

pytestmark = pytest.mark.django_db


def test_journey_infers_origin_from_default_residence() -> None:
    home = Location.objects.create(
        name="Home", location_type=LocationType.RESIDENCE, is_default_residence=True
    )
    office = Location.objects.create(name="Office", location_type=LocationType.FIRST_WORKPLACE)

    event = create_journey(
        destination=office,
        transport_mode=TransportMode.TRAIN,
        effective_at=datetime(2026, 8, 4, 7, 0, tzinfo=UTC),
    )

    assert event.current_revision is not None
    assert event.current_revision.snapshot["origin_id"] == str(home.pk)
    assert event.current_revision.snapshot["destination_type"] == LocationType.FIRST_WORKPLACE


def test_reverse_journey_creates_separate_return_event() -> None:
    home = Location.objects.create(
        name="Home", location_type=LocationType.RESIDENCE, is_default_residence=True
    )
    office = Location.objects.create(name="Office", location_type=LocationType.FIRST_WORKPLACE)
    outbound = create_journey(
        destination=office,
        transport_mode=TransportMode.BICYCLE,
        effective_at=datetime(2026, 8, 4, 7, 0, tzinfo=UTC),
    )

    returned = reverse_journey(
        outbound,
        effective_at=datetime(2026, 8, 4, 17, 0, tzinfo=UTC),
    )

    assert returned.pk != outbound.pk
    assert returned.current_revision is not None
    assert returned.current_revision.snapshot["origin_id"] == str(office.pk)
    assert returned.current_revision.snapshot["destination_id"] == str(home.pk)
    assert returned.current_revision.snapshot["transport_mode"] == TransportMode.BICYCLE


def test_repeat_recent_journey_preserves_route_and_mode() -> None:
    home = Location.objects.create(
        name="Home", location_type=LocationType.RESIDENCE, is_default_residence=True
    )
    office = Location.objects.create(name="Office", location_type=LocationType.FIRST_WORKPLACE)
    original = create_journey(
        destination=office,
        transport_mode=TransportMode.TRAIN,
        effective_at=datetime(2026, 8, 4, 7, 0, tzinfo=UTC),
        facts={"train_number": "ICE 78", "manual_train_entry": True},
    )

    repeated = repeat_journey(
        original,
        effective_at=datetime(2026, 8, 5, 7, 0, tzinfo=UTC),
    )

    assert repeated.pk != original.pk
    assert repeated.current_revision is not None
    assert repeated.current_revision.snapshot["origin_id"] == str(home.pk)
    assert repeated.current_revision.snapshot["destination_id"] == str(office.pk)
    assert repeated.current_revision.snapshot["train_number"] == "ICE 78"
