from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_FLOOR, Decimal

from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from apps.ledger.models import Event, EventRevision
from apps.travel.models import Location, LocationType, TransportMode

from .models import RouteDistance, TaxRule

ZERO = Decimal("0.00")


@dataclass(frozen=True)
class JourneyTaxResult:
    classification: str
    distance_km: Decimal
    amount: Decimal
    rule_code: str
    route_version: int | None
    complete: bool
    missing_facts: tuple[str, ...] = ()


def _tax_rule(rule_type: str, on_date: date) -> TaxRule | None:
    return (
        TaxRule.objects.filter(
            jurisdiction="DE", rule_type=rule_type, effective_from__lte=on_date
        )
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=on_date))
        .order_by("-effective_from", "code")
        .first()
    )


def _money(snapshot: dict[str, object], key: str) -> Decimal:
    value = snapshot.get(key)
    if value in (None, ""):
        return ZERO
    return Decimal(str(value)).quantize(Decimal("0.01"))


@transaction.atomic
def record_route_distance(
    *,
    origin: Location,
    destination: Location,
    mode: str,
    distance_km: Decimal,
    source: str,
    source_url: str = "",
    raw_response_hash: str = "",
    provider_input: dict[str, object] | None = None,
    provider_response: dict[str, object] | None = None,
    returned_metres: int | None = None,
    manual_override: bool = False,
    override_comment: str = "",
    confirmed: bool = True,
) -> RouteDistance:
    # Runtime PostgreSQL deliberately has INSERT/SELECT but not UPDATE on
    # immutable tax facts.  SELECT FOR UPDATE therefore fails before the
    # insert; serialize the version calculation with an advisory transaction
    # lock instead, then read the chain head normally.
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                [f"route-distance:{origin.pk}:{destination.pk}:{mode}"],
            )
    latest = (
        RouteDistance.objects.filter(origin=origin, destination=destination, mode=mode)
        .order_by("-version")
        .first()
    )
    return RouteDistance.objects.create(
        origin=origin,
        destination=destination,
        mode=mode,
        distance_km=distance_km.quantize(Decimal("0.01")),
        version=1 if latest is None else latest.version + 1,
        source=source,
        source_url=source_url,
        provider_input=provider_input or {},
        provider_response=provider_response or {},
        raw_response_hash=raw_response_hash,
        returned_metres=returned_metres,
        full_tax_km=int(distance_km.to_integral_value(rounding=ROUND_FLOOR)),
        calculation_date=timezone.localdate(),
        manual_override=manual_override,
        override_comment=override_comment,
        confirmed=confirmed,
    )


def _latest_route(
    origin_id: str, destination_id: str, as_of: datetime | None = None
) -> RouteDistance | None:
    routes = RouteDistance.objects.filter(mode="driving", confirmed=True)
    if as_of is not None:
        routes = routes.filter(recorded_at__lte=as_of)
    return (
        routes
        .filter(
            Q(origin_id=origin_id, destination_id=destination_id)
            | Q(origin_id=destination_id, destination_id=origin_id)
        )
        .order_by("-recorded_at", "-version")
        .first()
    )


def derive_journey_tax(
    event: Event,
    revision: EventRevision | None = None,
    as_of: datetime | None = None,
) -> JourneyTaxResult:
    selected_revision = revision or event.current_revision
    if event.event_type != "journey" or selected_revision is None:
        raise ValueError("Journey tax derivation requires a journey event")
    snapshot = selected_revision.snapshot
    on_date = selected_revision.effective_at.date()
    destination = Location.objects.filter(pk=snapshot.get("destination_id")).first()
    transport_mode = snapshot.get("transport_mode")
    if destination is None:
        return JourneyTaxResult("unknown", ZERO, ZERO, "", None, False, ("destination",))

    if destination.location_type == LocationType.FIRST_WORKPLACE:
        rule = _tax_rule("commuting_allowance", on_date)
        route = _latest_route(snapshot.get("origin_id", ""), str(destination.pk), as_of)
        missing: list[str] = []
        if rule is None:
            missing.append("commuting tax rule")
        if route is None:
            missing.append("one-way route distance")
        if missing:
            return JourneyTaxResult(
                "commuting_allowance",
                ZERO,
                ZERO,
                rule.code if rule else "",
                None,
                False,
                tuple(missing),
            )
        assert rule is not None and route is not None
        whole_km = route.distance_km.to_integral_value(rounding=ROUND_FLOOR)
        rate = Decimal(rule.values["per_distance_km"])
        duplicate = (
            Event.objects.filter(
                event_type="journey",
                current_revision__deleted=False,
                current_revision__effective_at__date=on_date,
                current_revision__snapshot__destination_id=str(destination.pk),
            )
            .exclude(pk=event.pk)
            .filter(
                Q(current_revision__effective_at__lt=selected_revision.effective_at)
                | Q(
                    current_revision__effective_at=selected_revision.effective_at,
                    pk__lt=event.pk,
                )
            )
            .exists()
        )
        gross_amount = (whole_km * rate).quantize(Decimal("0.01"))
        reimbursement = _money(snapshot, "employer_reimbursement")
        return JourneyTaxResult(
            "commuting_allowance",
            whole_km,
            ZERO if duplicate else max(ZERO, gross_amount - reimbursement),
            rule.code,
            route.version,
            True,
            ("commuting allowance already used for this workplace and day",)
            if duplicate
            else (),
        )

    if transport_mode == TransportMode.PRIVATE_CAR:
        rule = _tax_rule("business_mileage", on_date)
        raw_distance = snapshot.get("actual_kilometres")
        missing = []
        if rule is None:
            missing.append("business mileage tax rule")
        if raw_distance is None:
            missing.append("actual kilometres")
        if missing:
            return JourneyTaxResult(
                "business_mileage",
                ZERO,
                ZERO,
                rule.code if rule else "",
                None,
                False,
                tuple(missing),
            )
        assert rule is not None
        assert raw_distance is not None
        distance = Decimal(raw_distance)
        rate = Decimal(rule.values["private_car_per_km"])
        reimbursement = _money(snapshot, "employer_reimbursement")
        return JourneyTaxResult(
            "business_mileage",
            distance,
            max(ZERO, (distance * rate).quantize(Decimal("0.01")) - reimbursement),
            rule.code,
            None,
            True,
        )

    personally_paid = _money(snapshot, "personally_paid")
    reimbursement = _money(snapshot, "employer_reimbursement")
    return JourneyTaxResult(
        "actual_cost",
        ZERO,
        max(ZERO, personally_paid - reimbursement),
        "DE_ACTUAL_COST",
        None,
        True,
    )
