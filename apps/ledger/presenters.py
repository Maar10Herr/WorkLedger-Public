"""Typed, deterministic presentation of ledger events.

Everything rendered to the user as a "summary" — history cards, unresolved
groups, detail sections, revision timelines — is derived here from the
current revision snapshot. No display strings are stored in the schema, and
no domain/tax logic lives in templates.

Guarantees:
- ``present()`` output never contains raw identifiers (event/revision/location
  ids, audit hashes, provider payloads); those surface only through
  ``is_technical_key`` / ``display_label`` for the collapsed technical section.
- All labels are human and deterministic (fixed weekday/month names, no
  locale dependence, no relative "today" wording).
- Unknown event types and malformed snapshots degrade to a safe fallback
  summary instead of raising or leaking internals.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from django.utils import timezone

from apps.ledger.models import Event

# ---------------------------------------------------------------------------
# Public value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Badge:
    key: str
    label: str


@dataclass(frozen=True, slots=True)
class MissingAction:
    key: str
    label: str
    fix_url_name: str


@dataclass(frozen=True, slots=True)
class DateGroup:
    key: str
    label: str


@dataclass(frozen=True, slots=True)
class EventSummary:
    title: str
    meta: str
    date_group: DateGroup
    badges: tuple[Badge, ...]
    missing_actions: tuple[MissingAction, ...]


@dataclass(frozen=True, slots=True)
class FactRow:
    label: str
    value: str


# ---------------------------------------------------------------------------
# Human vocabularies (deterministic — no locale-dependent strftime)
# ---------------------------------------------------------------------------

EVENT_TYPE_LABELS: dict[str, str] = {
    "work_from_home": "work from home",
    "journey": "journey",
    "work_location": "work location",
    "external_activity": "external activity",
    "expense": "expense",
    "receipt_only": "receipt",
    "attachment_upload": "attachment",
    "reimbursement_update": "reimbursement update",
    "note": "note",
}

TRANSPORT_LABELS: dict[str, str] = {
    "train": "train",
    "private_car": "private car",
    "employer_car": "employer car",
    "passenger": "passenger",
    "taxi": "taxi",
    "local_public_transport": "local transport",
    "bicycle": "bicycle",
    "walking": "walking",
    "plane": "plane",
    "ferry": "ferry",
    "other": "other",
}

_WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
_MONTHS = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)

# Human field labels for snapshot keys (detail facts, correction form,
# revision timeline). Raw identifiers/hashes/provider payloads are labelled
# "technical …" so they can only surface inside the collapsed technical
# section — never as ordinary facts.
FIELD_LABELS: dict[str, str] = {
    # journey / route
    "origin_name": "origin",
    "destination_name": "destination",
    "destination_type": "destination type",
    "destination_locality": "destination locality",
    "transport_mode": "transport",
    "actual_kilometres": "actual kilometres",
    "note": "note",
    "train_category": "train category",
    "train_number": "train number",
    "train_operator": "train operator",
    "origin_station": "origin station",
    "destination_station": "destination station",
    "scheduled_departure": "scheduled departure",
    "scheduled_arrival": "scheduled arrival",
    "manual_train_entry": "manual train entry",
    "covered_by_pass": "covered by rail pass",
    "rail_pass_name": "rail pass",
    "incremental_ticket_cost": "ticket cost",
    "total_fare": "total fare",
    "personally_paid": "personally paid",
    "employer_reimbursement": "employer reimbursement",
    "employer_paid": "paid by employer",
    "payer_description": "payer",
    # expense
    "description": "description",
    "category_name": "category",
    "amount": "amount",
    "currency": "currency",
    "merchant": "merchant",
    "invoice_or_receipt_date": "receipt date",
    "payment_date": "payment date",
    "payment_method": "payment method",
    "vat_amount": "vat amount",
    "original_amount": "original amount",
    "gross_amount_eur": "gross amount (eur)",
    "amount_personally_paid_eur": "personally paid (eur)",
    "employer_reimbursement_amount_eur": "employer reimbursement (eur)",
    "original_currency": "original currency",
    "exchange_rate_to_eur": "exchange rate",
    "reference": "reference",
    "supplier_address": "supplier address",
    "business_reason": "business reason",
    "professional_use_percentage": "professional use",
    "justification": "justification",
    "documentation_status": "documentation",
    # activity / per diem
    "start_at": "start",
    "end_at": "end",
    "still_ongoing": "still ongoing",
    "country_code": "country",
    "activity_type": "activity type",
    "provided_meals": "meals provided",
    "provided_meal_copayments": "meal copayments",
    "three_month_limit_applies": "three-month limit applies",
    "overnight": "overnight",
    "client": "client",
    "project": "project",
    "purpose": "purpose",
    "departure_context": "departed from",
    "return_context": "returned to",
    "employer_per_diem_reimbursement": "employer per-diem reimbursement",
    "tax_classification_note": "tax classification note",
    # home / employer / shared
    "residence_name": "residence",
    "employer_name": "employer",
    "reconciliation_status": "reconciliation status",
    "original_filename": "file name",
    "tax_relevant": "tax relevant",
    "employer_reimbursable": "employer reimbursable",
    # technical identifiers (only inside the collapsed technical section)
    "origin_id": "technical origin id",
    "destination_id": "technical destination id",
    "residence_id": "technical residence id",
    "employer_id": "technical employer id",
    "rail_pass_id": "technical rail-pass id",
    "journey_leg_ids": "technical linked journey ids",
    "reconciled_to_event_id": "technical reconciled event id",
    "category": "technical category code",
    "train_source_sha256": "technical train source hash",
    "raw_snapshot": "technical provider payload",
    "provider_input": "technical provider input",
    "provider_response": "technical provider response",
    "raw_response_hash": "technical response hash",
    "source_url": "technical source url",
    "source_id": "technical source id",
}

# Keys that are raw identifiers / hashes / provider payloads and must never
# appear as ordinary facts or editable correction fields.
_TECHNICAL_EXPLICIT = {
    "origin_id",
    "destination_id",
    "residence_id",
    "employer_id",
    "rail_pass_id",
    "journey_leg_ids",
    "reconciled_to_event_id",
    "category",
    "train_source_sha256",
    "raw_snapshot",
    "provider_input",
    "provider_response",
    "raw_response_hash",
    "source_url",
    "source_id",
}


def is_technical_key(key: str) -> bool:
    """True for raw identifiers, hashes, and provider payload keys."""
    lowered = key.casefold()
    return (
        key in _TECHNICAL_EXPLICIT
        or lowered.endswith("_id")
        or lowered.endswith("_ids")
        or lowered.endswith("_sha256")
        or "hash" in lowered
        or lowered.startswith("provider_")
        or lowered.startswith("raw_")
    )


def display_label(key: str) -> str:
    """Human label for a snapshot key; technical keys stay labelled as such."""
    if key in FIELD_LABELS:
        return FIELD_LABELS[key]
    if is_technical_key(key):
        return f"technical {key.replace('_', ' ')}"
    return key.replace("_", " ")


# ---------------------------------------------------------------------------
# Small deterministic formatters
# ---------------------------------------------------------------------------


def _clean(value: object) -> str:
    """Whitespace-trimmed, casefolded plain text (names, descriptions)."""
    return str(value or "").strip().casefold()


def _human(value: object) -> str:
    """Like ``_clean`` but also converts snake_case keys to words."""
    return str(value or "").replace("_", " ").strip().casefold()


def _clock(dt: datetime | None) -> str:
    if dt is None:
        return ""
    return timezone.localtime(dt).strftime("%H:%M")


def _format_euro(amount: object) -> str:
    """€ amount without cents when whole: 249.00 -> "249", 19.80 -> "19.80"."""
    try:
        value = Decimal(str(amount))
    except (InvalidOperation, ValueError, TypeError):
        return ""
    if value == value.to_integral_value():
        return f"{value:.0f}"
    return f"{value:.2f}"


def _parse_snapshot_datetime(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed)
    return parsed


def _join(parts: list[str]) -> str:
    return " · ".join(part for part in parts if part)


def date_group_for(effective_at: datetime) -> DateGroup:
    """Reverse-chronology grouping key plus a fixed, human label."""
    local = timezone.localtime(effective_at)
    day = local.date()
    label = f"{_WEEKDAYS[day.weekday()]}, {day.day} {_MONTHS[day.month - 1]}"
    return DateGroup(key=day.isoformat(), label=label)


# ---------------------------------------------------------------------------
# Summary builders (per event type)
# ---------------------------------------------------------------------------


def _journey_title(snapshot: dict[str, Any], effective_at: datetime) -> str:
    origin = _clean(snapshot.get("origin_name"))
    destination = _clean(snapshot.get("destination_name"))
    route = f"{origin} → {destination}" if origin and destination else ""
    transport = _clean(snapshot.get("transport_mode"))
    category = _clean(snapshot.get("train_category"))
    number = _clean(snapshot.get("train_number"))
    if category or number:
        tail = " ".join(part for part in (category, number) if part)
    else:
        tail = TRANSPORT_LABELS.get(transport, transport)
    return _join([_clock(effective_at), route, tail])


def _wfh_title(snapshot: dict[str, Any], effective_at: datetime) -> str:
    residence = _clean(snapshot.get("residence_name"))
    return _join([_clock(effective_at), "work from home", residence])


def _expense_title(snapshot: dict[str, Any], effective_at: datetime) -> str:
    description = _clean(snapshot.get("description")) or "expense"
    amount = _format_euro(snapshot.get("amount"))
    category = _clean(snapshot.get("category_name")) or _clean(snapshot.get("category"))
    return _join([description, f"€{amount}" if amount else "", category])


def _receipt_title(snapshot: dict[str, Any], effective_at: datetime) -> str:
    display_name = _clean(snapshot.get("display_name")) or "receipt"
    status = (
        "linked"
        if str(snapshot.get("reconciliation_status") or "").strip() == "matched"
        else "unlinked"
    )
    return _join([display_name, status, f"uploaded {_clock(effective_at)}"])


def _per_diem_incomplete(snapshot: dict[str, Any], end: datetime | None, per_diem: object) -> bool:
    if per_diem is not None:
        return not bool(getattr(per_diem, "complete", True))
    if end is None:
        return True
    if not str(snapshot.get("country_code") or "").strip():
        return True
    return snapshot.get("three_month_limit_applies") is None


def _activity_title(
    snapshot: dict[str, Any], effective_at: datetime, per_diem: object
) -> str:
    activity_type = _human(snapshot.get("activity_type")) or "activity"
    start = _parse_snapshot_datetime(snapshot.get("start_at"))
    end = _parse_snapshot_datetime(snapshot.get("end_at"))
    if start is not None:
        times = f"{_clock(start)}\u2013{_clock(end) if end is not None else 'ongoing'}"
    else:
        times = ""
    tail = "per diem incomplete" if _per_diem_incomplete(snapshot, end, per_diem) else ""
    return _join([activity_type, times, tail])


def _fallback_title(event: Event, effective_at: datetime) -> str:
    label = EVENT_TYPE_LABELS.get(event.event_type, event.event_type.replace("_", " "))
    return _join([label, _clock(effective_at)])


# ---------------------------------------------------------------------------
# Badges and missing actions
# ---------------------------------------------------------------------------


def _badges(
    event: Event, snapshot: dict[str, Any], *, expense: object, revision_count: int | None
) -> tuple[Badge, ...]:
    badges: list[Badge] = []
    revision = event.current_revision
    if revision is not None and not revision.complete:
        badges.append(Badge("incomplete", "incomplete"))
    if event.tax_relevant:
        badges.append(Badge("tax", "tax"))
    if event.employer_reimbursable:
        badges.append(Badge("employer", "employer"))
    if expense is not None and getattr(expense, "reimbursement_status", "") in {
        "reimbursed",
        "partially_reimbursed",
    }:
        badges.append(Badge("reimbursed", "reimbursed"))
    if event.event_type == "expense" and str(
        snapshot.get("documentation_status") or ""
    ).strip() not in {"attached", "not_required"}:
        badges.append(Badge("receipt_missing", "receipt missing"))
    count: int = revision_count if revision_count is not None else event.revisions.count()
    if count > 1:
        badges.append(Badge("amended", "amended"))
    return tuple(badges)


def _missing_actions(event: Event, snapshot: dict[str, Any]) -> tuple[MissingAction, ...]:
    """One primary missing action per incomplete event, mapped to a direct fix.

    Groups (mandate): add destination / add amount / link receipt /
    complete per-diem times / review tax route. Each action carries the URL
    name of its direct fix; the template resolves it with the event id.

    Incomplete events with no HONEST mandated action get no group: forcing
    them under one of the five labels would misstate the missing fact.
    Deliberately ungrouped: work-from-home
    events missing a residence/employer, journeys missing only the origin
    (destination present), and expenses incomplete only for category or
    professional share. They stay visible in history with the incomplete
    badge, and the unresolved view surfaces them as a truthful count
    (``hidden_incomplete_count``) instead of the "everything is complete"
    lie — no sixth group is added.
    """
    revision = event.current_revision
    if revision is None or revision.complete:
        return ()
    if event.event_type == "journey":
        if not snapshot.get("destination_id"):
            return (MissingAction("add_destination", "add destination", "ledger:event_detail"),)
        transport = str(snapshot.get("transport_mode") or "")
        if transport in {"private_car", "employer_car"} and not snapshot.get(
            "actual_kilometres"
        ):
            return (MissingAction("review_tax_route", "review tax route", "travel:settings"),)
        # A journey with a destination but no origin has no honest label in
        # the five mandated groups — documented, not mislabelled.
        return ()
    if event.event_type == "expense":
        if snapshot.get("amount") in (None, ""):
            return (MissingAction("add_amount", "add amount", "ledger:event_detail"),)
        if str(snapshot.get("documentation_status") or "").strip() not in {
            "attached",
            "not_required",
        }:
            return (MissingAction("link_receipt", "link receipt", "evidence:receipt_inbox"),)
        # Amount and receipt present but still incomplete (category or
        # professional share): no honest mandated group — documented.
        return ()
    if event.event_type == "receipt_only":
        if str(snapshot.get("reconciliation_status") or "unmatched").strip() != "matched":
            return (MissingAction("link_receipt", "link receipt", "evidence:receipt_inbox"),)
        return ()
    if event.event_type == "external_activity":
        end = _parse_snapshot_datetime(snapshot.get("end_at"))
        if _per_diem_incomplete(snapshot, end, None):
            return (
                MissingAction(
                    "complete_per_diem_times", "complete per-diem times", "ledger:event_detail"
                ),
            )
        # Honest fallback: per-diem facts are complete but the activity lacks
        # a destination — "add destination" truthfully names that missing
        # fact, and its fix surface is this event's detail page.
        if not snapshot.get("destination_id"):
            return (MissingAction("add_destination", "add destination", "ledger:event_detail"),)
        return ()
    return ()


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def _expense_for(event: Event) -> object:
    """The linked Expense row when it exists (prefetch-friendly)."""
    try:
        return event.expense_identity
    except AttributeError:
        return None


def present(
    event: Event,
    *,
    expense: object | None = None,
    per_diem: object | None = None,
    revision_count: int | None = None,
) -> EventSummary:
    """Deterministic, typed summary of one event's current snapshot."""
    revision = event.current_revision
    if revision is None:
        effective_at = event.created_at
        snapshot: dict[str, Any] = {}
    else:
        effective_at = revision.effective_at
        snapshot = revision.snapshot
    if expense is None:
        expense = _expense_for(event)
    if event.event_type == "journey":
        title = _journey_title(snapshot, effective_at)
    elif event.event_type == "work_from_home":
        title = _wfh_title(snapshot, effective_at)
    elif event.event_type == "expense":
        title = _expense_title(snapshot, effective_at)
    elif event.event_type == "receipt_only":
        title = _receipt_title(snapshot, effective_at)
    elif event.event_type == "external_activity":
        title = _activity_title(snapshot, effective_at, per_diem)
    else:
        title = _fallback_title(event, effective_at)
    date_group = date_group_for(effective_at)
    meta = f"{date_group.label} · {_clock(effective_at)}"
    return EventSummary(
        title=title,
        meta=meta,
        date_group=date_group,
        badges=_badges(event, snapshot, expense=expense, revision_count=revision_count),
        missing_actions=_missing_actions(event, snapshot),
    )


def format_summary(event: Event, **context: Any) -> str:
    """Convenience: the primary summary string for an event."""
    return present(event, **context).title


def human_value(value: object) -> str:
    """Snapshot value rendered for human timelines/facts without raw dicts.

    Scalars are shown as-is; structured values (dicts/lists) become compact
    JSON so long provider payloads cannot blow up ordinary sections. The
    technical section always renders the full raw JSON.
    """
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if value is None:
        return ""
    return str(value)


def _fact_value(key: str, value: object) -> str:
    """Human value for a snapshot fact row (no raw codes in ordinary UI)."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if key == "transport_mode":
        return TRANSPORT_LABELS.get(str(value or ""), str(value or ""))
    if key == "activity_type":
        return _human(value)
    if key == "three_month_limit_applies":
        return "unsure" if value is None else ("yes" if value else "no")
    if key == "documentation_status":
        status = str(value or "").strip()
        return {
            "attached": "attached",
            "not_required": "not required",
            "missing": "missing",
        }.get(status, status)
    if key == "reconciliation_status":
        return str(value or "unmatched").replace("_", " ")
    if key == "manual_train_entry":
        return "yes" if value else "no"
    return human_value(value)


def fact_rows(snapshot: dict[str, Any]) -> tuple[FactRow, ...]:
    """Human label/value rows for the ordinary facts section.

    Raw identifiers, hashes, provider payloads, and bookkeeping flags
    (tax/employer tracks are badges) never surface here — they belong to the
    collapsed technical section.
    """
    rows: list[FactRow] = []
    for key, value in snapshot.items():
        if is_technical_key(key) or key in {"tax_relevant", "employer_reimbursable"}:
            continue
        if value in (None, "") and key != "three_month_limit_applies":
            continue
        rows.append(FactRow(label=display_label(key), value=_fact_value(key, value)))
    return tuple(rows)
