from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from decimal import Decimal

from apps.travel.models import Location

from .journey import record_route_distance
from .models import RouteDistance

ORS_DIRECTIONS_URL = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"


class RouteLookupUnavailable(RuntimeError):
    pass


def fetch_shortest_road_route(
    origin: Location,
    destination: Location,
    *,
    api_key: str | None = None,
    timeout: float = 15.0,
) -> RouteDistance:
    if None in {origin.latitude, origin.longitude, destination.latitude, destination.longitude}:
        raise RouteLookupUnavailable("Both locations need latitude and longitude")
    assert origin.latitude is not None and origin.longitude is not None
    assert destination.latitude is not None and destination.longitude is not None
    credential = api_key or os.environ.get("OPENROUTESERVICE_API_KEY", "")
    if not credential:
        raise RouteLookupUnavailable("OPENROUTESERVICE_API_KEY is not configured")
    provider_input: dict[str, object] = {
        "coordinates": [
            [float(origin.longitude), float(origin.latitude)],
            [float(destination.longitude), float(destination.latitude)],
        ],
        "preference": "recommended",
        "instructions": False,
    }
    body = json.dumps(provider_input, sort_keys=True, separators=(",", ":")).encode()
    request = urllib.request.Request(
        ORS_DIRECTIONS_URL,
        data=body,
        headers={"Authorization": credential, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            raw = response.read()
    except (OSError, urllib.error.URLError) as exc:
        raise RouteLookupUnavailable("OpenRouteService lookup failed") from exc
    try:
        payload = json.loads(raw)
        feature = payload["features"][0]
        summary = feature["properties"]["summary"]
        returned_metres = round(float(summary["distance"]))
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RouteLookupUnavailable("OpenRouteService returned an invalid route") from exc
    compact_response: dict[str, object] = {
        "distance_metres": returned_metres,
        "duration_seconds": summary.get("duration"),
        "bbox": payload.get("bbox"),
    }
    return record_route_distance(
        origin=origin,
        destination=destination,
        mode="driving",
        distance_km=(Decimal(returned_metres) / Decimal(1000)).quantize(Decimal("0.01")),
        source="openrouteservice",
        source_url=ORS_DIRECTIONS_URL,
        provider_input=provider_input,
        provider_response=compact_response,
        raw_response_hash=hashlib.sha256(raw).hexdigest(),
        returned_metres=returned_metres,
        confirmed=False,
    )


def confirm_route(
    candidate: RouteDistance,
    *,
    override_distance_km: Decimal | None = None,
    comment: str = "",
) -> RouteDistance:
    overridden = override_distance_km is not None
    if overridden and not comment.strip():
        raise ValueError("A manual override requires a comment")
    return record_route_distance(
        origin=candidate.origin,
        destination=candidate.destination,
        mode=candidate.mode,
        distance_km=override_distance_km or candidate.distance_km,
        source="manual_override" if overridden else f"{candidate.source}_confirmed",
        source_url=candidate.source_url,
        provider_input=candidate.provider_input,
        provider_response=candidate.provider_response,
        raw_response_hash=candidate.raw_response_hash,
        returned_metres=candidate.returned_metres,
        manual_override=overridden,
        override_comment=comment.strip(),
        confirmed=True,
    )
