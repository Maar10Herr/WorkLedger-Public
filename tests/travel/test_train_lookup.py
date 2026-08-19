from datetime import UTC, datetime

from apps.travel.train_lookup import (
    TrainChoice,
    TrainLookupUnavailable,
    get_train_choices,
    train_choice_snapshot,
)


class FailingAdapter:
    def lookup(
        self, origin_station: str, destination_station: str, departure_at: datetime
    ) -> list[TrainChoice]:
        raise TrainLookupUnavailable("API unavailable")


class StaticAdapter:
    def lookup(
        self, origin_station: str, destination_station: str, departure_at: datetime
    ) -> list[TrainChoice]:
        return [
            TrainChoice(
                category="ICE",
                number="78",
                operator="DB Fernverkehr AG",
                origin_station=origin_station,
                destination_station=destination_station,
                scheduled_departure=datetime(2026, 8, 4, 7, 0, tzinfo=UTC),
                scheduled_arrival=datetime(2026, 8, 4, 8, 5, tzinfo=UTC),
                actual_departure=None,
                actual_arrival=None,
                source_id="trip-78",
                retrieved_at=datetime(2026, 8, 4, 6, 55, tzinfo=UTC),
                raw_snapshot={"id": "trip-78", "category": "ICE", "number": "78"},
            )
        ]


def test_api_failure_returns_manual_fallback_without_raising() -> None:
    result = get_train_choices(
        adapter=FailingAdapter(),
        origin_station="Berlin Hbf",
        destination_station="Hamburg Hbf",
        departure_at=datetime(2026, 8, 4, 7, 0, tzinfo=UTC),
    )

    assert result.choices == ()
    assert result.manual_available is True
    assert result.error == "Train lookup is unavailable; enter the train manually."


def test_selected_train_snapshot_contains_source_hash_and_times() -> None:
    result = get_train_choices(
        adapter=StaticAdapter(),
        origin_station="Berlin Hbf",
        destination_station="Hamburg Hbf",
        departure_at=datetime(2026, 8, 4, 7, 0, tzinfo=UTC),
    )

    snapshot = train_choice_snapshot(result.choices[0])

    assert snapshot["train_category"] == "ICE"
    assert snapshot["train_number"] == "78"
    assert snapshot["scheduled_arrival"] == "2026-08-04T08:05:00+00:00"
    assert snapshot["manual_train_entry"] is False
    assert len(snapshot["train_source_sha256"]) == 64
