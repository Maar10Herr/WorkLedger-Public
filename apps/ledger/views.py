from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date
from typing import Any
from urllib.parse import urlencode

from django.db import transaction
from django.db.models import Count
from django.http import HttpRequest, HttpResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.decorators import owner_login_required
from apps.expenses.models import Expense, ExpenseCategory
from apps.travel.models import Employer, Location, TransportMode

from .models import Event
from .presenters import (
    EVENT_TYPE_LABELS,
    TRANSPORT_LABELS,
    display_label,
    fact_rows,
    human_value,
    is_technical_key,
    present,
)
from .services import create_event, revise_event

READ_ONLY_SNAPSHOT_FIELDS = {
    "amount",
    "amount_personally_paid_eur",
    "employer_reimbursement_amount_eur",
    "fx_rate",
    "fx_rate_date",
    "fx_source",
    "tax_relevant",
    "employer_reimbursable",
    "reconciliation_status",
    "reconciled_to_event_id",
}

FILTER_PARAM_ORDER = (
    "event_type",
    "start",
    "end",
    "output_status",
    "reimbursement_status",
    "reconciliation_status",
    "completeness",
    "transport",
    "location",
    "category",
)


def _filter_label(key: str, value: str) -> str:
    """Human label for an active filter chip value (user vocabulary only)."""
    if key == "event_type":
        return EVENT_TYPE_LABELS.get(value, value)
    if key == "transport":
        return TRANSPORT_LABELS.get(value, value)
    if key == "reimbursement_status":
        try:
            return str(Expense.ReimbursementStatus(value).label).casefold()
        except ValueError:
            return value
    if key == "category":
        name = (
            ExpenseCategory.objects.filter(code=value)
            .values_list("name", flat=True)
            .first()
        )
        return str(name).casefold() if name else value
    return value


def _active_filter_chips(params: QueryDict) -> list[dict[str, str]]:
    """Chips for the sheet filters currently applied (search stays in the box)."""
    chips: list[dict[str, str]] = []
    for key in FILTER_PARAM_ORDER:
        value = params.get(key, "")
        if not value:
            continue
        remaining = {k: v for k, v in params.items() if k != key}
        if remaining:
            url = f"{reverse('ledger:history')}?{urlencode(remaining)}"
        else:
            url = reverse("ledger:history")
        chips.append({"key": key, "label": _filter_label(key, value), "url": url})
    return chips


def _safe_parse_date(value: str) -> date | None:
    """``parse_date`` with malformed-but-parseable input treated as absent.

    Django 5.2's ``parse_date`` raises ValueError for well-formed-but-invalid
    dates (e.g. ``2026-99-99``); history filters must treat those as no
    filter at all instead of 500ing.
    """
    try:
        return parse_date(value)
    except ValueError:
        return None


def _group_by_date(events: Sequence[Event]) -> list[dict[str, Any]]:
    """Consecutive presenter date groups over a reverse-chronological list."""
    groups: list[dict[str, Any]] = []
    for event in events:
        summary = present(event, revision_count=getattr(event, "revision_count", None))
        key = summary.date_group.key
        if groups and groups[-1]["key"] == key:
            groups[-1]["items"].append((event, summary))
        else:
            groups.append(
                {
                    "key": key,
                    "label": summary.date_group.label,
                    "items": [(event, summary)],
                }
            )
    return groups


def _history_filter_options() -> dict[str, Any]:
    return {
        "event_type_options": [
            (value, EVENT_TYPE_LABELS[value])
            for value in (
                "journey",
                "external_activity",
                "expense",
                "receipt_only",
                "work_from_home",
            )
        ],
        "transport_options": [
            (value, TRANSPORT_LABELS[value])
            for value, _label in TransportMode.choices
            if value in TRANSPORT_LABELS
        ],
        "reimbursement_options": [
            (status.value, str(status.label).casefold())
            for status in (
                Expense.ReimbursementStatus.DRAFT,
                Expense.ReimbursementStatus.READY,
                Expense.ReimbursementStatus.SUBMITTED,
                Expense.ReimbursementStatus.PARTIALLY_REIMBURSED,
                Expense.ReimbursementStatus.REIMBURSED,
                Expense.ReimbursementStatus.REJECTED,
                Expense.ReimbursementStatus.WITHDRAWN,
            )
        ],
        "category_options": list(
            ExpenseCategory.objects.filter(active=True)
            .order_by("name")
            .values_list("code", "name")
        ),
    }


@owner_login_required
@require_GET
def enter(request: HttpRequest) -> HttpResponse:
    return render(request, "ledger/enter.html")


@owner_login_required
@require_POST
def create_wfh(request: HttpRequest) -> HttpResponse:
    residence = Location.objects.filter(is_default_residence=True).first()
    employer = Employer.objects.filter(is_active=True).first()
    snapshot: dict[str, str] = {}
    missing: list[str] = []
    if residence is None:
        missing.append("default residence")
    else:
        snapshot.update({"residence_id": str(residence.pk), "residence_name": residence.name})
    if employer is None:
        missing.append("active employer")
    else:
        snapshot.update({"employer_id": str(employer.pk), "employer_name": employer.name})
    event = create_event(
        event_type="work_from_home",
        effective_at=timezone.now(),
        snapshot=snapshot,
        complete=not missing,
    )
    return render(
        request,
        "ledger/saved.html",
        {"event": event, "missing": missing},
        status=201,
    )


@owner_login_required
@require_POST
def undo_event(request: HttpRequest, event_id: object) -> HttpResponse:
    event = get_object_or_404(Event.objects.select_related("current_revision"), pk=event_id)
    current = event.current_revision
    if current is None:
        return HttpResponse(status=409)
    revise_event(
        event=event,
        effective_at=current.effective_at,
        snapshot=current.snapshot,
        complete=current.complete,
        comment="Undo from entry confirmation",
        deleted=True,
    )
    return render(request, "ledger/undone.html", {"event": event})


@owner_login_required
@require_GET
def history(request: HttpRequest) -> HttpResponse:
    queryset = (
        Event.objects.select_related("current_revision")
        .prefetch_related("expense_identity")
        .annotate(revision_count=Count("revisions"))
    )
    event_type = request.GET.get("event_type", "")
    if event_type:
        queryset = queryset.filter(event_type=event_type)
    elif request.GET.get("technical") != "1":
        queryset = queryset.exclude(event_type="attachment_upload")
    start = _safe_parse_date(request.GET.get("start", ""))
    end = _safe_parse_date(request.GET.get("end", ""))
    if start:
        queryset = queryset.filter(current_revision__effective_at__date__gte=start)
    if end:
        queryset = queryset.filter(current_revision__effective_at__date__lte=end)
    events = list(
        queryset.order_by("-current_revision__effective_at", "-created_at")
    )
    query = request.GET.get("q", "").strip().casefold()
    output_status = request.GET.get("output_status", "")
    reimbursement_status = request.GET.get("reimbursement_status", "")
    reconciliation_status = request.GET.get("reconciliation_status", "")
    completeness = request.GET.get("completeness", "")
    transport = request.GET.get("transport", "")
    location = request.GET.get("location", "").strip().casefold()
    category = request.GET.get("category", "")
    if query:
        events = [
            event
            for event in events
            if event.current_revision
            and query in json.dumps(event.current_revision.snapshot, ensure_ascii=False).casefold()
        ]
    if output_status:
        events = [
            event
            for event in events
            if (output_status == "tax" and event.tax_relevant)
            or (output_status == "employer" and event.employer_reimbursable)
            or (
                output_status == "neither"
                and not event.tax_relevant
                and not event.employer_reimbursable
            )
        ]
    if reimbursement_status:
        matching_event_ids = Expense.objects.filter(
            reimbursement_status=reimbursement_status
        ).values_list("event_id", flat=True)
        events = [event for event in events if event.pk in matching_event_ids]
    if reconciliation_status:
        events = [
            event
            for event in events
            if event.current_revision
            and event.current_revision.snapshot.get("reconciliation_status")
            == reconciliation_status
        ]
    if completeness:
        events = [
            event
            for event in events
            if event.current_revision
            and event.current_revision.complete == (completeness == "complete")
        ]
    if transport:
        events = [
            event
            for event in events
            if event.current_revision
            and event.current_revision.snapshot.get("transport_mode") == transport
        ]
    if category:
        events = [
            event
            for event in events
            if event.current_revision
            and event.current_revision.snapshot.get("category") == category
        ]
    if location:
        location_keys = (
            "origin_id",
            "origin_name",
            "destination_id",
            "destination_name",
            "destination_locality",
        )
        events = [
            event
            for event in events
            if event.current_revision
            and location
            in " ".join(
                str(event.current_revision.snapshot.get(key, "")).casefold()
                for key in location_keys
            )
        ]
    page_size = 100
    try:
        page = max(1, int(request.GET.get("page", "1")))
    except ValueError:
        page = 1
    start_index = (page - 1) * page_size
    page_events = events[start_index : start_index + page_size]
    next_url = ""
    if len(events) > start_index + page_size:
        next_params = request.GET.copy()
        next_params["page"] = str(page + 1)
        next_url = f"{reverse('ledger:history')}?{urlencode(next_params)}"
    return render(
        request,
        "ledger/history.html",
        {
            "groups": _group_by_date(page_events),
            "next_url": next_url,
            "filters": request.GET,
            "active_filters": _active_filter_chips(request.GET),
            **_history_filter_options(),
        },
    )


@owner_login_required
@require_GET
def unresolved(request: HttpRequest) -> HttpResponse:
    events = (
        Event.objects.select_related("current_revision")
        .prefetch_related("expense_identity")
        .annotate(revision_count=Count("revisions"))
        .filter(current_revision__complete=False, current_revision__deleted=False)
        .order_by("-current_revision__effective_at")
    )
    groups: list[dict[str, Any]] = []
    by_key: dict[str, dict[str, Any]] = {}
    hidden_incomplete_count = 0
    for event in events:
        summary = present(event, revision_count=getattr(event, "revision_count", None))
        if not summary.missing_actions:
            # No mandated group honestly describes this event's missing facts
            # (see presenters._missing_actions). Surface it as a truthful
            # count instead of mislabelling it under a group.
            hidden_incomplete_count += 1
            continue
        action = summary.missing_actions[0]
        group = by_key.setdefault(
            action.key,
            {
                "key": action.key,
                "label": action.label,
                "fix_url_name": action.fix_url_name,
                "items": [],
            },
        )
        fix_url = (
            reverse(action.fix_url_name, args=[event.pk])
            if action.fix_url_name == "ledger:event_detail"
            else reverse(action.fix_url_name)
        )
        group["items"].append((event, summary, fix_url))
    # Presenter-derived groups in the mandated order, only where applicable.
    for action_key in (
        "add_destination",
        "add_amount",
        "link_receipt",
        "complete_per_diem_times",
        "review_tax_route",
    ):
        if action_key in by_key:
            groups.append(by_key[action_key])
    return render(
        request,
        "ledger/unresolved.html",
        {"groups": groups, "hidden_incomplete_count": hidden_incomplete_count},
    )


@owner_login_required
@require_GET
def system_status(request: HttpRequest) -> HttpResponse:
    from apps.ledger.status import collect_status

    report = collect_status()
    return render(request, "ledger/status.html", {"report": report})


JOURNEY_PICKER_KEYS = {
    "origin_id",
    "origin_name",
    "destination_id",
    "destination_name",
    "destination_type",
}


def _correction_fields(event: Event, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Human correction fields: journey location pickers plus non-technical
    scalar snapshot fields. Raw ids/hashes/provider payloads are never
    editable here — they live in the collapsed technical section only."""
    from apps.travel.models import Location

    fields: list[dict[str, Any]] = []
    if event.event_type == "journey":
        locations = list(Location.objects.order_by("name"))
        for key, label, name_key, type_key in (
            ("origin_id", "origin", "origin_name", None),
            ("destination_id", "destination", "destination_name", "destination_type"),
        ):
            fields.append(
                {
                    "kind": "location",
                    "key": key,
                    "label": label,
                    "name_key": name_key,
                    "type_key": type_key,
                    "value": snapshot.get(key, ""),
                    "name_value": snapshot.get(name_key, "") if name_key else "",
                    "type_value": snapshot.get(type_key, "") if type_key else "",
                    "options": locations,
                }
            )
    for key, value in snapshot.items():
        if is_technical_key(key) or key in {"tax_relevant", "employer_reimbursable"}:
            continue
        if event.event_type == "journey" and key in JOURNEY_PICKER_KEYS:
            continue
        if isinstance(value, (dict, list)):
            continue
        if key in READ_ONLY_SNAPSHOT_FIELDS:
            fields.append(
                {
                    "kind": "readonly",
                    "key": key,
                    "label": display_label(key),
                    "value": value,
                }
            )
        else:
            fields.append(
                {
                    "kind": "scalar",
                    "key": key,
                    "label": display_label(key),
                    "value": value,
                    "is_boolean": isinstance(value, bool),
                }
            )
    return fields


def _human_classification(value: object) -> str:
    return str(value or "unknown").replace("_", " ").casefold()


@owner_login_required
@require_GET
def event_detail(request: HttpRequest, event_id: object) -> HttpResponse:
    from apps.evidence.models import AttachmentLink
    from apps.expenses.models import Expense
    from apps.expenses.services import expense_track_amounts
    from apps.taxes.journey import derive_journey_tax
    from apps.taxes.models import PerDiemCalculation

    event = get_object_or_404(
        Event.objects.select_related("current_revision")
        .prefetch_related("revisions")
        .prefetch_related("expense_identity"),
        pk=event_id,
    )
    assert event.current_revision is not None
    attachment_links = AttachmentLink.objects.filter(event=event).select_related("attachment")
    revision_diffs: list[dict[str, Any]] = []
    previous_snapshot: dict[str, Any] = {}
    for revision in event.revisions.order_by("revision_number"):
        keys = sorted(set(previous_snapshot) | set(revision.snapshot))
        changes = [
            {
                "label": display_label(key),
                "old": human_value(previous_snapshot.get(key)),
                "new": human_value(revision.snapshot.get(key)),
            }
            for key in keys
            if not is_technical_key(key)
            and previous_snapshot.get(key) != revision.snapshot.get(key)
        ]
        revision_diffs.append({"revision": revision, "changes": changes})
        previous_snapshot = revision.snapshot
    tax_result = derive_journey_tax(event) if event.event_type == "journey" else None
    per_diem = (
        PerDiemCalculation.objects.filter(
            activity_event=event, input_revision=event.current_revision
        ).first()
        if event.event_type == "external_activity"
        else None
    )
    expense = Expense.objects.filter(event=event).first()
    expense_tracks = expense_track_amounts(event) if expense is not None else None
    snapshot = event.current_revision.snapshot
    facts_heading = {
        "journey": "journey facts",
        "external_activity": "activity facts",
        "expense": "expense facts",
        "receipt_only": "receipt facts",
        "work_from_home": "home facts",
    }.get(event.event_type, "entry facts")
    return render(
        request,
        "ledger/event_detail.html",
        {
            "event": event,
            "summary": present(event, expense=expense, revision_count=event.revisions.count()),
            "facts": fact_rows(snapshot),
            "facts_heading": facts_heading,
            "correction_fields": _correction_fields(event, snapshot),
            "attachment_links": attachment_links,
            "revision_diffs": revision_diffs,
            "tax_result": tax_result,
            "per_diem": per_diem,
            "expense": expense,
            "expense_tracks": expense_tracks,
            "snapshot_json": json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True),
            "revision_count": event.revisions.count(),
            "tax_classification": (
                _human_classification(tax_result.classification)
                if tax_result is not None
                else None
            ),
            "tax_rule": (
                _human_classification(tax_result.rule_code)
                if tax_result is not None and tax_result.rule_code
                else None
            ),
        },
    )


@owner_login_required
@require_POST
@transaction.atomic
def correct_event(request: HttpRequest, event_id: object) -> HttpResponse:
    event = get_object_or_404(Event.objects.select_related("current_revision"), pk=event_id)
    assert event.current_revision is not None
    snapshot = dict(event.current_revision.snapshot)
    for key, old_value in event.current_revision.snapshot.items():
        if key in READ_ONLY_SNAPSHOT_FIELDS:
            continue
        form_key = f"field_{key}"
        if isinstance(old_value, bool):
            snapshot[key] = request.POST.get(form_key) == "on"
        elif form_key in request.POST:
            submitted = request.POST[form_key]
            try:
                if isinstance(old_value, int):
                    snapshot[key] = int(submitted)
                elif isinstance(old_value, float):
                    snapshot[key] = float(submitted)
                elif isinstance(old_value, str) or old_value is None:
                    snapshot[key] = submitted
            except ValueError:
                return HttpResponse(f"Invalid value for {key}", status=400)
    # New facts posted by the journey correction pickers (e.g. adding a
    # destination to an incomplete journey) are accepted ONLY for the journey
    # picker keys AND only on journey events. Arbitrary new keys — technical
    # or not — and journey picker keys on other event types are dropped: the
    # ordinary correction surface never grows the snapshot schema. Existing
    # snapshot fields were corrected in the loop above.
    for form_key, posted_value in request.POST.items():
        if not form_key.startswith("field_"):
            continue
        key = form_key[len("field_") :]
        if not isinstance(posted_value, str):
            continue
        if key in snapshot or key in READ_ONLY_SNAPSHOT_FIELDS:
            continue
        if event.event_type != "journey" or key not in JOURNEY_PICKER_KEYS:
            continue
        snapshot[key] = posted_value
    event.tax_relevant = request.POST.get("tax_relevant") == "on"
    event.employer_reimbursable = request.POST.get("employer_reimbursable") == "on"
    snapshot["tax_relevant"] = event.tax_relevant
    snapshot["employer_reimbursable"] = event.employer_reimbursable
    try:
        effective = parse_datetime(request.POST.get("effective_at", ""))
    except ValueError:
        # Same parse-family hardening as _safe_parse_date: a malformed
        # datetime in the POST falls back to the current revision time.
        effective = None
    if effective is None:
        effective = event.current_revision.effective_at
    elif timezone.is_naive(effective):
        effective = timezone.make_aware(effective, timezone.get_current_timezone())
    revise_event(
        event=event,
        effective_at=effective,
        snapshot=snapshot,
        complete=request.POST.get("complete") == "on",
        comment=request.POST.get("correction_comment", "Correction").strip() or "Correction",
    )
    return redirect("ledger:event_detail", event_id=event.pk)
