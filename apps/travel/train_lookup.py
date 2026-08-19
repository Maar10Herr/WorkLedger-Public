from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from db_timetables import DBApiError, TimetablesClient

DB_TIMEZONE = ZoneInfo("Europe/Berlin")


class TrainLookupUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TrainChoice:
    category: str
    number: str
    operator: str
    origin_station: str
    destination_station: str
    scheduled_departure: datetime
    scheduled_arrival: datetime | None
    actual_departure: datetime | None
    actual_arrival: datetime | None
    source_id: str
    retrieved_at: datetime
    raw_snapshot: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TrainLookupResult:
    choices: tuple[TrainChoice, ...]
    manual_available: bool
    error: str = ""


class TrainLookupAdapter(Protocol):
    def lookup(
        self, origin_station: str, destination_station: str, departure_at: datetime
    ) -> list[TrainChoice]: ...


class OfficialDbTimetablesAdapter:
    """Credentialed adapter for DB's official Timetables API (IRIS)."""

    def __init__(self, client_id: str, api_key: str, timeout: int = 8) -> None:
        self.client = TimetablesClient(client_id, api_key, timeout=timeout)

    @classmethod
    def from_environment(cls) -> OfficialDbTimetablesAdapter:
        client_id = os.environ.get("DB_TIMETABLES_CLIENT_ID", "")
        api_key = os.environ.get("DB_TIMETABLES_API_KEY", "")
        if not client_id or not api_key:
            raise TrainLookupUnavailable("DB Timetables API credentials are not configured")
        return cls(client_id, api_key)

    def lookup(
        self, origin_station: str, destination_station: str, departure_at: datetime
    ) -> list[TrainChoice]:
        try:
            origin = self._station(origin_station)
            destination = self._station(destination_station)
            local_departure = _as_db_time(departure_at)
            timetable = self.client.get_timetable_with_changes(
                origin.eva, local_departure, local_departure.hour
            )
            retrieved_at = datetime.now(UTC)
            choices: list[TrainChoice] = []
            for stop in timetable.stops:
                departure = stop.departure
                line = stop.train_line
                if departure is None or departure.planned_time is None or line is None:
                    continue
                scheduled_departure = _aware_db_time(departure.planned_time)
                assert scheduled_departure is not None
                if abs((scheduled_departure - local_departure).total_seconds()) > 2 * 60 * 60:
                    continue
                path = departure.changed_path or departure.planned_path
                endpoint = departure.changed_distant_endpoint or departure.planned_distant_endpoint
                searchable = [*path, endpoint]
                if not any(_same_station(item, destination.name) for item in searchable):
                    continue
                scheduled_arrival = self._scheduled_arrival(
                    destination.eva,
                    origin.name,
                    line.category,
                    line.number,
                    scheduled_departure,
                )
                raw = {
                    "stop_id": stop.id,
                    "origin_eva": origin.eva,
                    "destination_eva": destination.eva,
                    "category": line.category,
                    "number": line.number,
                    "operator": line.owner,
                    "planned_departure": scheduled_departure.isoformat(),
                    "actual_departure": _iso_or_none(departure.changed_time),
                    "planned_path": departure.planned_path,
                    "changed_path": departure.changed_path,
                }
                choices.append(
                    TrainChoice(
                        category=line.category,
                        number=line.number,
                        operator=line.owner,
                        origin_station=origin.name,
                        destination_station=destination.name,
                        scheduled_departure=scheduled_departure,
                        scheduled_arrival=scheduled_arrival,
                        actual_departure=_aware_db_time(departure.changed_time),
                        actual_arrival=None,
                        source_id=stop.id,
                        retrieved_at=retrieved_at,
                        raw_snapshot=raw,
                    )
                )
            return sorted(choices, key=lambda choice: choice.scheduled_departure)[:8]
        except DBApiError as exc:
            raise TrainLookupUnavailable("Official DB Timetables API request failed") from exc

    def _station(self, name: str) -> Any:
        stations = self.client.get_station(name)
        if not stations:
            raise TrainLookupUnavailable(f"Station was not found: {name}")
        normalized = _normalized_station(name)
        return next(
            (station for station in stations if _normalized_station(station.name) == normalized),
            stations[0],
        )

    def _scheduled_arrival(
        self,
        destination_eva: str,
        origin_name: str,
        category: str,
        number: str,
        departure: datetime,
    ) -> datetime | None:
        for offset in range(9):
            candidate_hour = departure + timedelta(hours=offset)
            timetable = self.client.get_plan(
                destination_eva, candidate_hour, candidate_hour.hour
            )
            for stop in timetable.stops:
                arrival = stop.arrival
                line = stop.train_line
                if arrival is None or arrival.planned_time is None or line is None:
                    continue
                if line.category != category or line.number != number:
                    continue
                arrival_time = _aware_db_time(arrival.planned_time)
                if arrival_time is None or arrival_time < departure:
                    continue
                if any(_same_station(item, origin_name) for item in arrival.planned_path):
                    return arrival_time
        return None


def get_train_choices(
    *,
    adapter: TrainLookupAdapter,
    origin_station: str,
    destination_station: str,
    departure_at: datetime,
) -> TrainLookupResult:
    try:
        choices = adapter.lookup(origin_station, destination_station, departure_at)
    except TrainLookupUnavailable:
        return TrainLookupResult(
            (), True, "Train lookup is unavailable; enter the train manually."
        )
    return TrainLookupResult(tuple(choices), True)


def train_choice_snapshot(choice: TrainChoice) -> dict[str, Any]:
    canonical_source = json.dumps(
        choice.raw_snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return {
        "train_category": choice.category,
        "train_number": choice.number,
        "train_operator": choice.operator,
        "origin_station": choice.origin_station,
        "destination_station": choice.destination_station,
        "scheduled_departure": choice.scheduled_departure.isoformat(),
        "scheduled_arrival": choice.scheduled_arrival.isoformat()
        if choice.scheduled_arrival
        else None,
        "actual_departure": choice.actual_departure.isoformat()
        if choice.actual_departure
        else None,
        "actual_arrival": choice.actual_arrival.isoformat() if choice.actual_arrival else None,
        "train_source_id": choice.source_id,
        "train_source_retrieved_at": choice.retrieved_at.isoformat(),
        "train_source_snapshot": choice.raw_snapshot,
        "train_source_sha256": hashlib.sha256(canonical_source).hexdigest(),
        "manual_train_entry": False,
    }


def _normalized_station(value: str) -> str:
    return " ".join(value.casefold().replace("hauptbahnhof", "hbf").split())


def _same_station(left: str, right: str) -> bool:
    left_normalized = _normalized_station(left)
    right_normalized = _normalized_station(right)
    return left_normalized == right_normalized or right_normalized in left_normalized


def _as_db_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=DB_TIMEZONE)
    return value.astimezone(DB_TIMEZONE)


def _aware_db_time(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=DB_TIMEZONE)
    return value.astimezone(DB_TIMEZONE)


def _iso_or_none(value: datetime | None) -> str | None:
    aware = _aware_db_time(value)
    return aware.isoformat() if aware else None
