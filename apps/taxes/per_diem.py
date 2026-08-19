from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db.models import Q

from apps.ledger.models import Event

from .models import PerDiemCalculation, TaxRule

ZERO = Decimal("0.00")


@dataclass(frozen=True)
class ActivityFacts:
    start_at: datetime
    end_at: datetime
    country_code: str
    provided_meals: dict[date, set[str]]
    three_month_limit_applies: bool | None
    three_month_review_required: bool = False
    provided_meal_copayments: dict[date, dict[str, Decimal]] = field(default_factory=dict)
    employer_reimbursement: Decimal = ZERO


@dataclass(frozen=True)
class PerDiemResult:
    daily_amounts: dict[date, Decimal]
    total: Decimal
    complete: bool
    missing_facts: tuple[str, ...]
    rule_codes: tuple[str, ...]
    derivation_hash: str


def _active_rule(rule_type: str, jurisdiction: str, on_date: date) -> TaxRule | None:
    return (
        TaxRule.objects.filter(
            rule_type=rule_type,
            jurisdiction=jurisdiction,
            effective_from__lte=on_date,
        )
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=on_date))
        .order_by("-effective_from", "code")
        .first()
    )


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def derive_per_diem(facts: ActivityFacts) -> PerDiemResult:
    if facts.end_at < facts.start_at:
        return PerDiemResult({}, ZERO, False, ("end before start",), (), "")

    missing: list[str] = []
    if facts.three_month_review_required and facts.three_month_limit_applies is None:
        missing.append("three-month rule decision required")

    rule_codes: list[str] = []
    if facts.three_month_limit_applies is True or facts.three_month_review_required:
        limit_rule = _active_rule("three_month_limit", facts.country_code, facts.start_at.date())
        if limit_rule is None:
            missing.append(f"three-month rule for {facts.country_code}")
        else:
            rule_codes.append(limit_rule.code)

    daily: dict[date, Decimal] = {}
    activity_days = _days(facts.start_at.date(), facts.end_at.date())
    for index, day in enumerate(activity_days):
        per_diem_rule = _active_rule("meal_per_diem", facts.country_code, day)
        reduction_rule = _active_rule("meal_reduction", facts.country_code, day)
        if per_diem_rule is None:
            daily[day] = ZERO
            missing.append(f"per-diem rule for {facts.country_code} on {day.isoformat()}")
            continue
        if per_diem_rule.code not in rule_codes:
            rule_codes.append(per_diem_rule.code)
        full = Decimal(per_diem_rule.values["full_day"])
        partial = Decimal(per_diem_rule.values["partial_day"])
        minimum_hours = Decimal(str(per_diem_rule.values["minimum_hours"]))

        if len(activity_days) == 1:
            duration_seconds = Decimal(str((facts.end_at - facts.start_at).total_seconds()))
            duration_hours = duration_seconds / Decimal("3600")
            amount = partial if duration_hours > minimum_hours else ZERO
        elif index == 0 or index == len(activity_days) - 1:
            amount = partial
        else:
            amount = full

        if facts.three_month_limit_applies is True:
            amount = ZERO
        meals = facts.provided_meals.get(day, set())
        if meals and amount > ZERO:
            if reduction_rule is None:
                missing.append(f"meal-reduction rule for {facts.country_code} on {day.isoformat()}")
            else:
                if reduction_rule.code not in rule_codes:
                    rule_codes.append(reduction_rule.code)
                copayments = facts.provided_meal_copayments.get(day, {})
                reduction = sum(
                    (
                        max(
                            ZERO,
                            Decimal(reduction_rule.values[meal])
                            - copayments.get(meal, ZERO),
                        )
                        for meal in meals
                    ),
                    start=ZERO,
                )
                amount = max(ZERO, amount - reduction)
        daily[day] = amount.quantize(Decimal("0.01"))

    remaining_reimbursement = max(ZERO, facts.employer_reimbursement)
    for day in activity_days:
        offset = min(daily.get(day, ZERO), remaining_reimbursement)
        daily[day] = daily.get(day, ZERO) - offset
        remaining_reimbursement -= offset
    total = sum(daily.values(), start=ZERO).quantize(Decimal("0.01"))
    canonical = {
        "start_at": facts.start_at.isoformat(),
        "end_at": facts.end_at.isoformat(),
        "country_code": facts.country_code,
        "provided_meals": {
            day.isoformat(): sorted(meals) for day, meals in sorted(facts.provided_meals.items())
        },
        "provided_meal_copayments": {
            day.isoformat(): {meal: str(value) for meal, value in sorted(values.items())}
            for day, values in sorted(facts.provided_meal_copayments.items())
        },
        "employer_reimbursement": str(facts.employer_reimbursement),
        "three_month_limit_applies": facts.three_month_limit_applies,
        "daily_amounts": {day.isoformat(): str(amount) for day, amount in daily.items()},
        "rule_codes": rule_codes,
    }
    derivation_hash = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return PerDiemResult(
        daily_amounts=daily,
        total=total,
        complete=not missing,
        missing_facts=tuple(dict.fromkeys(missing)),
        rule_codes=tuple(rule_codes),
        derivation_hash=derivation_hash,
    )


def calculate_and_store(activity_event: Event) -> PerDiemCalculation:
    revision = activity_event.current_revision
    if activity_event.event_type != "external_activity" or revision is None:
        raise ValueError("Per-diem calculation requires an external activity event")
    snapshot = revision.snapshot
    meals = {
        date.fromisoformat(day): set(values)
        for day, values in snapshot.get("provided_meals", {}).items()
    }
    copayments = {
        date.fromisoformat(day): {meal: Decimal(str(value)) for meal, value in values.items()}
        for day, values in snapshot.get("provided_meal_copayments", {}).items()
    }
    facts = ActivityFacts(
        start_at=datetime.fromisoformat(snapshot["start_at"]),
        end_at=datetime.fromisoformat(snapshot["end_at"]),
        country_code=snapshot["country_code"],
        provided_meals=meals,
        three_month_limit_applies=snapshot.get("three_month_limit_applies"),
        three_month_review_required=snapshot.get("three_month_limit_applies") is None,
        provided_meal_copayments=copayments,
        employer_reimbursement=Decimal(
            str(snapshot.get("employer_per_diem_reimbursement", "0"))
        ),
    )
    result = derive_per_diem(facts)
    daily_amounts = {day.isoformat(): str(amount) for day, amount in result.daily_amounts.items()}
    derivation_hash = hashlib.sha256(
        f"{activity_event.pk}|{revision.pk}|{result.derivation_hash}".encode()
    ).hexdigest()
    existing = PerDiemCalculation.objects.filter(derivation_hash=derivation_hash).first()
    if existing is not None:
        return existing
    return PerDiemCalculation.objects.create(
        activity_event=activity_event,
        input_revision=revision,
        rule_codes=list(result.rule_codes),
        daily_amounts=daily_amounts,
        total=result.total,
        complete=result.complete,
        missing_facts=list(result.missing_facts),
        derivation_hash=derivation_hash,
    )
