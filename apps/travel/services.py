from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from django.db import transaction

from apps.ledger.models import Event
from apps.ledger.services import create_event

from .models import ExternalActivity, Location, TransportMode


@transaction.atomic
def create_external_activity(
    *,
    start_at: datetime,
    end_at: datetime | None,
    country_code: str,
    activity_type: str,
    provided_meals: dict[str, list[str]],
    three_month_limit_applies: bool | None,
    note: str = "",
    tax_relevant: bool = True,
    employer_reimbursable: bool = False,
    facts: dict[str, Any] | None = None,
    journey_legs: list[Event] | None = None,
) -> ExternalActivity:
    snapshot: dict[str, Any] = {
        "start_at": start_at.isoformat(),
        "still_ongoing": end_at is None,
        "country_code": country_code.upper(),
        "activity_type": activity_type,
        "provided_meals": provided_meals,
        "three_month_limit_applies": three_month_limit_applies,
        "note": note,
    }
    if end_at is not None:
        snapshot["end_at"] = end_at.isoformat()
    if facts:
        snapshot.update(facts)
    snapshot["journey_leg_ids"] = sorted(str(leg.pk) for leg in (journey_legs or []))
    event = create_event(
        event_type="external_activity",
        effective_at=start_at,
        snapshot=snapshot,
        complete=bool(
            end_at is not None
            and end_at >= start_at
            and country_code
            and activity_type
            and three_month_limit_applies is not None
            and facts is not None
            and facts.get("destination_id")
            and facts.get("departure_context")
            and facts.get("return_context")
        ),
        tax_relevant=tax_relevant,
        employer_reimbursable=employer_reimbursable,
    )
    activity = ExternalActivity.objects.create(event=event)
    if journey_legs:
        activity.journey_legs.set(journey_legs)
    return activity


def infer_origin() -> Location | None:
    recent = (
        Event.objects.filter(event_type="journey", current_revision__deleted=False)
        .select_related("current_revision")
        .order_by("-current_revision__effective_at", "-created_at")
        .first()
    )
    if recent is not None and recent.current_revision is not None:
        destination_id = recent.current_revision.snapshot.get("destination_id")
        if destination_id:
            destination = Location.objects.filter(pk=destination_id).first()
            if destination is not None:
                return destination
    return Location.objects.filter(is_default_residence=True).first()


def create_journey(
    *,
    destination: Location | None,
    transport_mode: str,
    effective_at: datetime,
    origin: Location | None = None,
    actual_kilometres: Decimal | None = None,
    note: str = "",
    facts: dict[str, Any] | None = None,
    tax_relevant: bool = True,
    employer_reimbursable: bool = False,
) -> Event:
    if transport_mode not in TransportMode.values:
        raise ValueError("Unsupported transport mode")
    origin = origin or infer_origin()
    snapshot: dict[str, Any] = {
        "transport_mode": transport_mode,
        "note": note,
    }
    if origin is not None:
        snapshot.update({"origin_id": str(origin.pk), "origin_name": origin.name})
    if destination is not None:
        snapshot.update(
            {
                "destination_id": str(destination.pk),
                "destination_name": destination.name,
                "destination_type": destination.location_type,
            }
        )
    if actual_kilometres is not None:
        snapshot["actual_kilometres"] = str(actual_kilometres)
    if facts:
        snapshot.update(facts)
    return create_event(
        event_type="journey",
        effective_at=effective_at,
        snapshot=snapshot,
        complete=origin is not None and destination is not None,
        tax_relevant=tax_relevant,
        employer_reimbursable=employer_reimbursable,
    )


def reverse_journey(event: Event, *, effective_at: datetime) -> Event:
    if event.event_type != "journey" or event.current_revision is None:
        raise ValueError("Only a journey with a current revision can be reversed")
    snapshot = event.current_revision.snapshot
    origin = Location.objects.filter(pk=snapshot.get("destination_id")).first()
    destination = Location.objects.filter(pk=snapshot.get("origin_id")).first()
    return create_journey(
        origin=origin,
        destination=destination,
        transport_mode=snapshot["transport_mode"],
        effective_at=effective_at,
        note=f"Reversed journey {event.pk}",
        tax_relevant=event.tax_relevant,
        employer_reimbursable=event.employer_reimbursable,
    )


def repeat_journey(event: Event, *, effective_at: datetime) -> Event:
    if event.event_type != "journey" or event.current_revision is None:
        raise ValueError("Only a journey with a current revision can be repeated")
    snapshot = event.current_revision.snapshot
    origin = Location.objects.filter(pk=snapshot.get("origin_id")).first()
    destination = Location.objects.filter(pk=snapshot.get("destination_id")).first()
    routing_fields = {
        "origin_id",
        "origin_name",
        "destination_id",
        "destination_name",
        "destination_type",
        "transport_mode",
        "note",
        "actual_kilometres",
    }
    facts = {key: value for key, value in snapshot.items() if key not in routing_fields}
    actual_kilometres = (
        Decimal(snapshot["actual_kilometres"])
        if snapshot.get("actual_kilometres") is not None
        else None
    )
    return create_journey(
        origin=origin,
        destination=destination,
        transport_mode=snapshot["transport_mode"],
        effective_at=effective_at,
        actual_kilometres=actual_kilometres,
        note=f"Repeated journey {event.pk}",
        facts=facts,
        tax_relevant=event.tax_relevant,
        employer_reimbursable=event.employer_reimbursable,
    )
