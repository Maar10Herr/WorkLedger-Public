from __future__ import annotations

import json
from decimal import Decimal

import pytest

from apps.taxes.route_lookup import confirm_route, fetch_shortest_road_route
from apps.travel.models import Location, LocationType

pytestmark = pytest.mark.django_db


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def test_ors_route_snapshot_requires_confirmation_and_preserves_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = Location.objects.create(
        name="Home",
        location_type=LocationType.RESIDENCE,
        latitude=Decimal("49.006900"),
        longitude=Decimal("8.403700"),
    )
    office = Location.objects.create(
        name="Office",
        location_type=LocationType.FIRST_WORKPLACE,
        latitude=Decimal("50.110900"),
        longitude=Decimal("8.682100"),
    )
    payload = json.dumps(
        {
            "bbox": [8.4, 49.0, 8.7, 50.2],
            "features": [
                {"properties": {"summary": {"distance": 141234.4, "duration": 7200}}}
            ],
        }
    ).encode()

    def fake_urlopen(_request: object, timeout: float) -> FakeResponse:
        assert timeout == 15.0
        return FakeResponse(payload)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    candidate = fetch_shortest_road_route(home, office, api_key="test-key")

    assert candidate.confirmed is False
    assert candidate.returned_metres == 141234
    assert candidate.full_tax_km == 141
    assert candidate.raw_response_hash
    confirmed = confirm_route(
        candidate,
        override_distance_km=Decimal("139.80"),
        comment="Confirmed shorter usable road connection",
    )
    assert confirmed.confirmed is True
    assert confirmed.manual_override is True
    assert confirmed.override_comment == "Confirmed shorter usable road connection"
    assert confirmed.version == 2
