from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from django.conf import settings
from django.contrib.auth.hashers import identify_hasher
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.decorators import owner_login_required
from apps.ledger.models import Event
from apps.taxes.journey import record_route_distance
from apps.taxes.models import RouteDistance
from apps.taxes.per_diem import ActivityFacts, calculate_and_store, derive_per_diem
from apps.taxes.route_lookup import (
    RouteLookupUnavailable,
    confirm_route,
    fetch_shortest_road_route,
)

from .forms import (
    EmployerForm,
    LocationForm,
    RailPassForm,
    RouteConfirmationForm,
    RouteConfirmForm,
    RouteLookupForm,
)
from .models import Employer, Location, LocationType, RailPass, TransportMode
from .services import (
    create_external_activity,
    create_journey,
    infer_origin,
    repeat_journey,
    reverse_journey,
)
from .train_lookup import (
    OfficialDbTimetablesAdapter,
    TrainLookupResult,
    TrainLookupUnavailable,
    get_train_choices,
    train_choice_snapshot,
)


def _posted_datetime(value: str) -> datetime | None:
    parsed = parse_datetime(value)
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _uuid_or_none(value: object) -> uuid.UUID | None:
    """Parse a submitted UUID without letting malformed input 500 the view."""
    try:
        return uuid.UUID(str(value))
    except (AttributeError, TypeError, ValueError):
        return None


def _posted_money(value: str) -> str | None:
    try:
        return str(Decimal(value).quantize(Decimal("0.01")))
    except (InvalidOperation, ValueError):
        return None


def _cost_facts(request: HttpRequest) -> dict[str, Any]:
    total = _posted_money(request.POST.get("total_fare", ""))
    personally_paid = _posted_money(request.POST.get("personally_paid", ""))
    reimbursed = _posted_money(request.POST.get("reimbursed_amount", ""))
    employer_paid = request.POST.get("employer_paid") == "on"
    if employer_paid:
        personally_paid = "0.00"
        reimbursed = reimbursed or total or "0.00"
    return {
        "total_fare": total,
        "personally_paid": personally_paid,
        "employer_reimbursement": reimbursed,
        "employer_paid": employer_paid,
        "payer_description": request.POST.get("payer_description", "").strip(),
    }


def _manual_train_facts(request: HttpRequest, effective_at: datetime) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "manual_train_entry": True,
        "origin_station": request.POST.get("origin_station", "").strip(),
        "destination_station": request.POST.get("destination_station", "").strip(),
        "train_category": request.POST.get("train_category", "").strip().upper(),
        "train_number": request.POST.get("train_number", "").strip().upper(),
        "train_operator": request.POST.get("train_operator", "").strip(),
    }
    for field in ("scheduled_departure", "scheduled_arrival"):
        parsed = _posted_datetime(request.POST.get(field, ""))
        facts[field] = parsed.isoformat() if parsed else None
    return facts


def _apply_rail_pass(
    facts: dict[str, Any], request: HttpRequest, effective_at: datetime
) -> None:
    """Attach rail-pass coverage facts to a train snapshot.

    A posted pass wins when it is valid on the effective date. When no pass is
    posted at all and exactly one active pass exists, that pass is selected
    unambiguously (mirrors the UI's auto-preselection so behaviour holds even
    without JavaScript). The explicit ``none`` value opts out.
    """
    active = RailPass.objects.filter(
        valid_from__lte=effective_at.date(), valid_to__gte=effective_at.date()
    )
    posted = request.POST.get("rail_pass", "").strip()
    if posted and posted != "none":
        parsed_id = _uuid_or_none(posted)
        rail_pass = active.filter(pk=parsed_id).first() if parsed_id is not None else None
        covered = rail_pass is not None
    elif posted == "" and active.count() == 1:
        rail_pass = active.first()
        covered = True
    else:
        rail_pass = None
        covered = False
    if covered and rail_pass is not None:
        facts["covered_by_pass"] = True
        facts["rail_pass_id"] = str(rail_pass.pk)
        facts["rail_pass_name"] = rail_pass.name
        facts["incremental_ticket_cost"] = "0.00"
        return
    facts["covered_by_pass"] = False
    ticket_cost = request.POST.get("ticket_cost", "").strip()
    if ticket_cost:
        try:
            facts["incremental_ticket_cost"] = str(
                Decimal(ticket_cost).quantize(Decimal("0.01"))
            )
        except InvalidOperation:
            facts["incremental_ticket_cost"] = None
    else:
        facts["incremental_ticket_cost"] = None


def _journey_context() -> dict[str, Any]:
    """Server-side view-model for the journey decision tree GET context."""
    now_local = timezone.localtime()
    inferred = infer_origin()
    favourites = list(Location.objects.filter(is_favourite=True))
    favourite_ids = {str(location.pk) for location in favourites}

    recent: list[Location] = []
    seen: set[str] = set()
    journeys = (
        Event.objects.filter(event_type="journey", current_revision__deleted=False)
        .select_related("current_revision")
        .order_by("-current_revision__effective_at", "-created_at")
    )
    for event in journeys:
        revision = event.current_revision
        if revision is None:
            continue
        destination_id = revision.snapshot.get("destination_id")
        if not destination_id or destination_id in seen or destination_id in favourite_ids:
            continue
        seen.add(destination_id)
        destination = Location.objects.filter(pk=destination_id).first()
        if destination is not None:
            recent.append(destination)
        if len(recent) >= 6:
            break
    recent_ids = {str(location.pk) for location in recent}
    others_query = Location.objects.exclude(pk__in=[*favourite_ids, *recent_ids])
    if inferred is not None:
        others_query = others_query.exclude(pk=inferred.pk)
    others = list(others_query)

    active_passes = list(
        RailPass.objects.filter(
            valid_from__lte=now_local.date(), valid_to__gte=now_local.date()
        )
    )
    auto_selected_pass = active_passes[0] if len(active_passes) == 1 else None
    other_active_passes = (
        [rail_pass for rail_pass in active_passes if rail_pass.pk != auto_selected_pass.pk]
        if auto_selected_pass is not None
        else active_passes
    )
    route_summaries = _car_route_summaries(inferred)

    def options_for(locations: list[Location]) -> list[dict[str, Any]]:
        return [
            {
                "location": location,
                "route_km": route_summaries.get(str(location.pk), {}).get("distance_km", ""),
            }
            for location in locations
        ]

    return {
        "locations": Location.objects.all(),
        "favourite_options": options_for(favourites),
        "recent_options": options_for(recent),
        "other_options": options_for(others),
        "inferred_origin": inferred,
        "origin_station_default": (
            inferred.station_name or inferred.name if inferred is not None else ""
        ),
        "transport_modes": TransportMode.choices,
        "primary_transport_modes": {
            TransportMode.TRAIN,
            TransportMode.PRIVATE_CAR,
            TransportMode.TAXI,
            TransportMode.LOCAL_TRANSIT,
            TransportMode.PASSENGER,
            TransportMode.WALKING,
            TransportMode.BICYCLE,
        },
        "active_rail_passes": active_passes,
        "auto_selected_pass": auto_selected_pass,
        "other_active_passes": other_active_passes,
        "now_local": now_local,
        "now_local_value": now_local.strftime("%Y-%m-%dT%H:%M"),
        "car_route_summaries": route_summaries,
    }


def _car_route_summaries(origin: Location | None) -> dict[str, dict[str, str]]:
    """Latest confirmed driving distances from the inferred origin, by destination id."""
    if origin is None:
        return {}
    destinations = Location.objects.exclude(pk=origin.pk)
    destination_ids = {str(location.pk) for location in destinations}
    summaries: dict[str, dict[str, str]] = {}
    for route in (
        RouteDistance.objects.filter(Q(mode="driving") & Q(confirmed=True))
        .order_by("-recorded_at", "-version")
        .iterator()
    ):
        route_ids = {str(route.origin_id), str(route.destination_id)}
        if str(origin.pk) not in route_ids:
            continue
        other = next(iter(route_ids - {str(origin.pk)}), None)
        if other is None or other not in destination_ids or other in summaries:
            continue
        summaries[other] = {
            "distance_km": str(route.distance_km),
            "source": route.source,
        }
    return summaries


def _per_diem_preview(
    start_at: datetime, end_at: datetime, country_code: str = "DE"
) -> dict[str, Any]:
    """Compact server-side allowance preview for the activity form defaults.

    Tax derivation stays in the per-diem service; this helper only shapes its
    result for display, so the template never duplicates tax logic.
    """
    facts = ActivityFacts(
        start_at=start_at,
        end_at=end_at,
        country_code=country_code,
        provided_meals={},
        three_month_limit_applies=None,
        three_month_review_required=True,
    )
    result = derive_per_diem(facts)
    return {
        "total": result.total,
        "complete": result.complete,
        "missing": result.missing_facts,
    }


@owner_login_required
@require_http_methods(["GET", "POST"])
def journey_entry(request: HttpRequest) -> HttpResponse:
    if request.method == "GET":
        context = _journey_context()
        latest_journey = (
            Event.objects.filter(event_type="journey", current_revision__deleted=False)
            .select_related("current_revision")
            .order_by("-current_revision__effective_at")
            .first()
        )
        new_location_id = _uuid_or_none(request.GET.get("new_location", ""))
        preselected = (
            Location.objects.filter(pk=new_location_id).first()
            if new_location_id is not None
            else None
        )
        preselected_in_other = bool(
            preselected is not None
            and any(
                option["location"].pk == preselected.pk
                for option in context["other_options"]
            )
        )
        return render(
            request,
            "travel/journey_entry.html",
            {
                **context,
                "latest_journey": latest_journey,
                "preselected_destination_id": (
                    str(preselected.pk) if preselected is not None else ""
                ),
                "preselected_in_other": preselected_in_other,
                "add_location_next": reverse("travel:journey_entry"),
            },
        )

    effective_at = _posted_datetime(request.POST.get("effective_at", "")) or timezone.now()
    destination_id = _uuid_or_none(request.POST.get("destination", ""))
    destination = (
        Location.objects.filter(pk=destination_id).first() if destination_id is not None else None
    )
    origin_id = _uuid_or_none(request.POST.get("origin", ""))
    origin = Location.objects.filter(pk=origin_id).first() if origin_id is not None else None
    transport_mode = request.POST.get("transport_mode", "")
    if transport_mode not in TransportMode.values:
        transport_mode = "other"
    facts: dict[str, Any] = {}
    actual_kilometres: Decimal | None = None
    if transport_mode == TransportMode.TRAIN:
        token = request.POST.get("train_choice_token", "")
        signed_facts: dict[str, Any] = {}
        if token:
            try:
                loaded = signing.loads(token, salt="workledger.train-choice", max_age=21600)
                signed_facts = loaded if isinstance(loaded, dict) else {}
            except signing.BadSignature:
                signed_facts = {}
        manual_fields = (
            request.POST.get("train_category", "").strip()
            or request.POST.get("train_number", "").strip()
            or request.POST.get("scheduled_arrival", "").strip()
            or request.POST.get("train_operator", "").strip()
        )
        stations_changed = bool(signed_facts) and (
            signed_facts.get("origin_station")
            != request.POST.get("origin_station", "").strip()
            or signed_facts.get("destination_station")
            != request.POST.get("destination_station", "").strip()
        )
        # Manual train data overrides a (possibly stale) signed lookup token;
        # a route edited after lookup clears the token as well.
        if signed_facts and not manual_fields and not stations_changed:
            facts = signed_facts
        else:
            facts = _manual_train_facts(request, effective_at)
        facts.update(_cost_facts(request))
        _apply_rail_pass(facts, request, effective_at)
        ticket_cost = facts.get("incremental_ticket_cost")
        if facts.get("personally_paid") is None:
            facts["personally_paid"] = ticket_cost
        if facts.get("employer_paid") and ticket_cost:
            facts["total_fare"] = ticket_cost
            facts["employer_reimbursement"] = ticket_cost
    elif transport_mode in {
        TransportMode.PRIVATE_CAR,
        TransportMode.EMPLOYER_CAR,
        TransportMode.PASSENGER,
    }:
        raw_kilometres = request.POST.get("actual_kilometres", "").strip()
        if raw_kilometres:
            try:
                actual_kilometres = Decimal(raw_kilometres)
            except InvalidOperation:
                actual_kilometres = None
    if transport_mode in {
        TransportMode.TAXI,
        TransportMode.LOCAL_TRANSIT,
        TransportMode.PLANE,
        TransportMode.FERRY,
        TransportMode.OTHER,
    }:
        facts.update(_cost_facts(request))
    event = create_journey(
        destination=destination,
        origin=origin,
        transport_mode=transport_mode,
        effective_at=effective_at,
        actual_kilometres=actual_kilometres,
        note=request.POST.get("note", "").strip(),
        facts=facts,
        tax_relevant=(
            request.POST.get("tax_relevant") == "on"
            if request.POST.get("track_fields_present") == "1"
            else True
        ),
        employer_reimbursable=request.POST.get("employer_reimbursable") == "on",
    )
    missing: list[str] = []
    if destination is None:
        missing.append("destination")
    if transport_mode == TransportMode.TRAIN:
        if not facts.get("train_number"):
            missing.append("train number")
        if not facts.get("scheduled_departure"):
            missing.append("scheduled departure")
        if not facts.get("scheduled_arrival"):
            missing.append("scheduled arrival")
    return render(
        request,
        "ledger/saved.html",
        {"event": event, "missing": missing, "draft_key": "journey-entry"},
        status=201,
    )


@owner_login_required
@require_POST
def train_lookup(request: HttpRequest) -> HttpResponse:
    departure_at = _posted_datetime(request.POST.get("scheduled_departure", "")) or timezone.now()
    try:
        adapter = OfficialDbTimetablesAdapter.from_environment()
        result = get_train_choices(
            adapter=adapter,
            origin_station=request.POST.get("origin_station", "").strip(),
            destination_station=request.POST.get("destination_station", "").strip(),
            departure_at=departure_at,
        )
    except TrainLookupUnavailable:
        result = TrainLookupResult(
            (), True, "Train lookup is unavailable; enter the train manually."
        )
    signed_choices: list[dict[str, Any]] = []
    for choice in result.choices:
        delay_minutes: int | None = None
        if choice.actual_departure is not None and choice.scheduled_departure is not None:
            delay = int((choice.actual_departure - choice.scheduled_departure).total_seconds())
            if delay >= 60:
                delay_minutes = delay // 60
        signed_choices.append(
            {
                "choice": choice,
                "token": signing.dumps(
                    train_choice_snapshot(choice), salt="workledger.train-choice", compress=True
                ),
                "delay_minutes": delay_minutes,
            }
        )
    return render(
        request,
        "travel/_train_choices.html",
        {"result": result, "signed_choices": signed_choices},
    )


@owner_login_required
@require_POST
def recent_journey_action(request: HttpRequest, action: str) -> HttpResponse:
    latest = (
        Event.objects.filter(event_type="journey", current_revision__deleted=False)
        .select_related("current_revision")
        .order_by("-current_revision__effective_at")
        .first()
    )
    if latest is None:
        return HttpResponse(status=404)
    if action == "reverse":
        event = reverse_journey(latest, effective_at=timezone.now())
    elif action == "repeat":
        event = repeat_journey(latest, effective_at=timezone.now())
    else:
        return HttpResponse(status=404)
    return render(request, "ledger/saved.html", {"event": event, "missing": []}, status=201)


@owner_login_required
@require_http_methods(["GET", "POST"])
def external_activity_entry(request: HttpRequest) -> HttpResponse:
    if request.method == "GET":
        local_now = timezone.localtime()
        journey_param = request.GET.get("journey", "").strip()
        preselected_journey: Event | None = None
        parsed_journey_id = _uuid_or_none(journey_param)
        if parsed_journey_id is not None:
            preselected_journey = (
                Event.objects.filter(
                    pk=parsed_journey_id, event_type="journey", current_revision__deleted=False
                )
                .select_related("current_revision")
                .first()
            )
        preselected_journey_ids = (
            [str(preselected_journey.pk)] if preselected_journey is not None else []
        )

        prefill_start = local_now
        prefill_end = local_now + timedelta(hours=9)
        prefill_destination_id = ""
        prefill_summary = ""
        departure_context = ""
        return_context = ""
        if preselected_journey is not None and preselected_journey.current_revision is not None:
            snapshot = preselected_journey.current_revision.snapshot
            scheduled_departure = snapshot.get("scheduled_departure")
            if scheduled_departure:
                try:
                    prefill_start = timezone.localtime(
                        datetime.fromisoformat(str(scheduled_departure))
                    )
                except ValueError:
                    prefill_start = timezone.localtime(
                        preselected_journey.current_revision.effective_at
                    )
            else:
                prefill_start = timezone.localtime(
                    preselected_journey.current_revision.effective_at
                )
            prefill_end = prefill_start + timedelta(hours=9)
            prefill_destination_id = str(snapshot.get("destination_id", ""))
            origin_name = str(snapshot.get("origin_name", "")).strip()
            destination_name = str(snapshot.get("destination_name", "")).strip()
            if origin_name and destination_name:
                prefill_summary = f"{origin_name} → {destination_name}"
                departure_context = origin_name
                return_context = destination_name
        available_ids = {
            str(location.pk)
            for location in Location.objects.exclude(location_type=LocationType.RESIDENCE)
        }
        if prefill_destination_id not in available_ids:
            prefill_destination_id = ""
        allowance_preview = _per_diem_preview(prefill_start, prefill_end)
        return render(
            request,
            "travel/external_activity.html",
            {
                "start_default": prefill_start.strftime("%Y-%m-%dT%H:%M"),
                "end_default": prefill_end.strftime("%Y-%m-%dT%H:%M"),
                "locations": Location.objects.exclude(location_type=LocationType.RESIDENCE),
                "recent_journeys": Event.objects.filter(
                    event_type="journey", current_revision__deleted=False
                )
                .select_related("current_revision")
                .order_by("-current_revision__effective_at")[:20],
                "preselected_journey_ids": preselected_journey_ids,
                "prefill_destination_id": prefill_destination_id,
                "prefill_summary": prefill_summary,
                "departure_context": departure_context,
                "return_context": return_context,
                "allowance_preview": allowance_preview,
            },
        )
    start_at = _posted_datetime(request.POST.get("start_at", "")) or timezone.now()
    still_ongoing = request.POST.get("still_ongoing") == "on"
    # While the activity is still ongoing the browser disables the return
    # input; the server ignores any end_at a hidden field may still submit and
    # never fabricates end=start.
    end_at = None if still_ongoing else _posted_datetime(request.POST.get("end_at", ""))
    meal_names = [
        meal for meal in ("breakfast", "lunch", "dinner") if request.POST.get(meal) == "on"
    ]
    provided_meals: dict[str, list[str]] = {}
    day = start_at.date()
    if end_at is not None:
        while day <= end_at.date():
            provided_meals[day.isoformat()] = meal_names
            day += timedelta(days=1)
    else:
        provided_meals[day.isoformat()] = meal_names
    decision = request.POST.get("three_month_limit", "unsure")
    three_month_limit = True if decision == "yes" else False if decision == "no" else None
    copayments = {
        day: {
            meal: _posted_money(request.POST.get(f"{meal}_copayment", "")) or "0.00"
            for meal in meal_names
        }
        for day in provided_meals
    }
    destination_id = _uuid_or_none(request.POST.get("destination", ""))
    destination = (
        Location.objects.filter(pk=destination_id).first() if destination_id is not None else None
    )
    destination_facts: dict[str, Any] = {}
    if destination is not None:
        destination_facts = {
            "destination_id": str(destination.pk),
            "destination_name": destination.name,
            "destination_locality": destination.locality,
        }
    journey_leg_ids = [
        parsed
        for value in request.POST.getlist("journey_legs")
        if (parsed := _uuid_or_none(value)) is not None
    ]
    journey_legs = list(
        Event.objects.filter(pk__in=journey_leg_ids, event_type="journey")
    )
    activity = create_external_activity(
        start_at=start_at,
        end_at=end_at,
        country_code=request.POST.get("country_code", "DE").strip() or "DE",
        activity_type=request.POST.get("activity_type", "client_work").strip(),
        provided_meals=provided_meals,
        three_month_limit_applies=three_month_limit,
        note=request.POST.get("note", "").strip(),
        tax_relevant=request.POST.get("tax_relevant") == "on",
        employer_reimbursable=request.POST.get("employer_reimbursable") == "on",
        facts={
            **destination_facts,
            "departure_context": request.POST.get("departure_context", "").strip(),
            "return_context": request.POST.get("return_context", "").strip(),
            "overnight": request.POST.get("overnight") == "on",
            "client": request.POST.get("client", "").strip(),
            "project": request.POST.get("project", "").strip(),
            "purpose": request.POST.get("purpose", "").strip(),
            "provided_meal_copayments": copayments,
            "employer_per_diem_reimbursement": _posted_money(
                request.POST.get("employer_per_diem_reimbursement", "")
            )
            or "0.00",
            "tax_classification_note": request.POST.get("tax_classification_note", "").strip(),
        },
        journey_legs=journey_legs,
    )
    calculation = None
    if end_at is not None:
        calculation = calculate_and_store(activity.event)
    missing: list[str] = []
    if destination is None:
        missing.append("destination")
    if end_at is None:
        missing.append("return time")
    if calculation is not None:
        for fact in calculation.missing_facts:
            if fact not in missing:
                missing.append(fact)
    return render(
        request,
        "ledger/saved.html",
        {
            "event": activity.event,
            "missing": missing,
            "per_diem": calculation,
            "draft_key": "external-activity",
        },
        status=201,
    )


def _safe_internal_next(raw: str, host: str) -> str:
    """Return ``raw`` only when it is a safe internal (same-host) target."""
    if not raw:
        return ""
    if not url_has_allowed_host_and_scheme(raw, allowed_hosts={host}, require_https=False):
        return ""
    return raw


@owner_login_required
@require_http_methods(["GET", "POST"])
def settings_view(request: HttpRequest) -> HttpResponse:
    """Legacy /settings/ entry point: the index of section cards.

    POST actions from the old single-page settings form keep working and
    redirect to the section that owns them; an invalid submission renders
    that section with the bound form and inline errors instead of a 500.
    """
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "location":
            form, _location = _create_location_from_post(request)
            if form.is_valid():
                return _settings_redirect(action)
            return _render_locations_page(request, form=form)
        if action == "employer":
            employer_form, _employer = _set_active_employer_from_post(request)
            if employer_form.is_valid():
                return _settings_redirect(action)
            return _render_employer_page(request, form=employer_form)
        if action == "rail_pass":
            rail_pass_form, _rail_pass = _create_rail_pass_from_post(request)
            if rail_pass_form.is_valid():
                return _settings_redirect(action)
            return _render_rail_passes_page(request, form=rail_pass_form)
        if action in ("route_manual", "route_lookup", "route_confirm"):
            error, _form = _dispatch_route_action(action, request)
            if error:
                request.session["settings_route_error"] = error
            return _settings_redirect(action)
    return render(request, "travel/settings.html")


def _settings_redirect(action: str | None) -> HttpResponseRedirect:
    target = {
        "location": "settings_locations",
        "employer": "settings_employer",
        "rail_pass": "settings_rail_passes",
        "route_manual": "settings_routes",
        "route_lookup": "settings_routes",
        "route_confirm": "settings_routes",
    }.get(action or "", "settings")
    return redirect(reverse(f"travel:{target}"))


def _save_location_form(form: LocationForm) -> Location:
    """Atomically create a location, demoting any previous default residence.

    The previous default is unset and the new location saved inside a single
    transaction while existing default rows are locked, so concurrent requests
    can never leave zero or two defaults. The PostgreSQL partial unique index
    remains the final backstop.
    """
    with transaction.atomic():
        default_rows = list(
            Location.objects.select_for_update().filter(is_default_residence=True)
        )
        is_residence = form.cleaned_data.get("location_type") == LocationType.RESIDENCE
        wants_default = is_residence and bool(
            form.cleaned_data.get("is_default_residence") or not default_rows
        )
        for row in default_rows:
            row.is_default_residence = False
            row.save(update_fields=["is_default_residence"])
        location = cast(Location, form.save(commit=False))
        location.is_default_residence = wants_default
        location.save()
    return location


def _create_location_from_post(request: HttpRequest) -> tuple[LocationForm, Location | None]:
    form = LocationForm(request.POST)
    location = None
    if form.is_valid():
        try:
            location = _save_location_form(form)
        except IntegrityError:
            form.add_error(
                None,
                "Another default residence was saved at the same time; please try again.",
            )
    return form, location


def _save_employer_form(form: EmployerForm) -> Employer:
    """Replace the active employer atomically; the new employer is created
    with ``is_active=True`` only after the previous one is demoted."""
    with transaction.atomic():
        for row in Employer.objects.select_for_update().filter(is_active=True):
            row.is_active = False
            row.save(update_fields=["is_active"])
        return form.save()


def _set_active_employer_from_post(
    request: HttpRequest,
) -> tuple[EmployerForm, Employer | None]:
    form = EmployerForm(request.POST)
    employer = None
    if form.is_valid():
        try:
            employer = _save_employer_form(form)
        except IntegrityError:
            # A concurrent request replaced the active employer between our
            # lock and insert; the transaction rolled back, so the previously
            # active employer is untouched and the user can retry.
            form.add_error(
                None,
                "Another employer was saved at the same time; please try again.",
            )
    return form, employer


def _create_rail_pass_from_post(
    request: HttpRequest,
) -> tuple[RailPassForm, RailPass | None]:
    form = RailPassForm(request.POST)
    rail_pass = None
    if form.is_valid():
        rail_pass = form.save()
    return form, rail_pass


def _first_form_message(form: object) -> str:
    """First human-readable error from a bound form, for the routes page."""
    errors = getattr(form, "errors", None)
    if errors is None:
        return ""
    for field_errors in errors.values():
        if field_errors:
            return str(field_errors[0])
    return ""


def _manual_route_from_post(request: HttpRequest) -> tuple[RouteConfirmationForm, str]:
    form = RouteConfirmationForm(request.POST)
    if not form.is_valid():
        return form, _first_form_message(form)
    origin = form.cleaned_data["origin"]
    destination = form.cleaned_data["destination"]
    if origin is None or destination is None:
        return form, "Choose two saved locations."
    record_route_distance(
        origin=origin,
        destination=destination,
        mode="driving",
        distance_km=form.cleaned_data["distance_km"],
        source="manual",
        manual_override=True,
        override_comment=form.cleaned_data["route_comment"].strip(),
        confirmed=True,
    )
    return form, ""


def _lookup_route_from_post(request: HttpRequest) -> tuple[RouteLookupForm, str]:
    form = RouteLookupForm(request.POST)
    if not form.is_valid():
        return form, _first_form_message(form)
    origin = form.cleaned_data["origin"]
    destination = form.cleaned_data["destination"]
    if origin is None or destination is None:
        return form, "Choose two saved locations."
    try:
        fetch_shortest_road_route(origin, destination)
    except RouteLookupUnavailable as exc:
        return form, str(exc)
    return form, ""


def _confirm_route_from_post(request: HttpRequest) -> tuple[RouteConfirmForm, str]:
    form = RouteConfirmForm(request.POST)
    if not form.is_valid():
        return form, _first_form_message(form)
    candidate = form.cleaned_data["candidate"]
    if candidate is None:
        return form, "The candidate route no longer exists."
    override = form.cleaned_data.get("distance_km")
    try:
        confirm_route(
            candidate,
            override_distance_km=override,
            comment=form.cleaned_data.get("route_comment", ""),
        )
    except ValueError as exc:
        return form, str(exc)
    return form, ""


def _dispatch_route_action(action: str | None, request: HttpRequest) -> tuple[str, object | None]:
    """Run a route POST action and return its error plus bound form."""
    if action == "route_manual":
        manual_form, error = _manual_route_from_post(request)
        return error, manual_form
    if action == "route_lookup":
        lookup_form, error = _lookup_route_from_post(request)
        return error, lookup_form
    if action == "route_confirm":
        confirm_form, error = _confirm_route_from_post(request)
        return error, confirm_form
    return "Unsupported settings action.", None


def _latest_per_pair(
    routes: list[RouteDistance],
) -> list[RouteDistance]:
    """Newest version per (origin, destination) pair, in stable order."""
    latest: dict[tuple[str, str], RouteDistance] = {}
    for route in routes:
        latest.setdefault((str(route.origin_id), str(route.destination_id)), route)
    return [latest[key] for key in sorted(latest, key=lambda pair: (pair[0], pair[1]))]


def _render_locations_page(
    request: HttpRequest, form: LocationForm | None = None
) -> HttpResponse:
    context = {
        "locations": Location.objects.all(),
        "location_types": LocationType.choices,
        "location_form": form or LocationForm(),
        "location_form_invalid": bool(form is not None and not form.is_valid()),
        # The ``next`` target is carried through the form as a hidden field so
        # the add-location flow can return to the originating page; it is
        # re-validated before every redirect and never accepts external hosts.
        "next": _safe_internal_next(request.GET.get("next", ""), request.get_host()),
    }
    return render(request, "travel/settings_locations.html", context)


@owner_login_required
@require_http_methods(["GET", "POST"])
def settings_locations(request: HttpRequest) -> HttpResponse:
    if request.method == "POST" and request.POST.get("action") == "location":
        form, location = _create_location_from_post(request)
        if form.is_valid() and location is not None:
            target = reverse("travel:settings_locations")
            nxt = _safe_internal_next(request.POST.get("next", ""), request.get_host())
            if nxt:
                separator = "&" if "?" in nxt else "?"
                target = f"{nxt}{separator}new_location={location.pk}"
            return redirect(target)
        return _render_locations_page(request, form=form)
    return _render_locations_page(request)


def _render_employer_page(
    request: HttpRequest, form: EmployerForm | None = None
) -> HttpResponse:
    context = {
        "employers": Employer.objects.all(),
        "employer_form": form or EmployerForm(),
        "employer_form_invalid": bool(form is not None and not form.is_valid()),
    }
    return render(request, "travel/settings_employer.html", context)


@owner_login_required
@require_http_methods(["GET", "POST"])
def settings_employer(request: HttpRequest) -> HttpResponse:
    if request.method == "POST" and request.POST.get("action") == "employer":
        form, _employer = _set_active_employer_from_post(request)
        if form.is_valid():
            return redirect(reverse("travel:settings_employer"))
        return _render_employer_page(request, form=form)
    return _render_employer_page(request)


def _render_rail_passes_page(
    request: HttpRequest, form: RailPassForm | None = None
) -> HttpResponse:
    context = {
        "rail_passes": RailPass.objects.all(),
        "rail_pass_form": form or RailPassForm(),
        "rail_pass_form_invalid": bool(form is not None and not form.is_valid()),
    }
    return render(request, "travel/settings_rail_passes.html", context)


@owner_login_required
@require_http_methods(["GET", "POST"])
def settings_rail_passes(request: HttpRequest) -> HttpResponse:
    if request.method == "POST" and request.POST.get("action") == "rail_pass":
        form, _rail_pass = _create_rail_pass_from_post(request)
        if form.is_valid():
            return redirect(reverse("travel:settings_rail_passes"))
        return _render_rail_passes_page(request, form=form)
    return _render_rail_passes_page(request)


@owner_login_required
@require_http_methods(["GET", "POST"])
def settings_routes(request: HttpRequest) -> HttpResponse:
    route_error = request.session.pop("settings_route_error", "")
    manual_form: RouteConfirmationForm | None = None
    confirm_form: RouteConfirmForm | None = None
    if request.method == "POST":
        action = request.POST.get("action")
        error, bound_form = _dispatch_route_action(action, request)
        if error:
            route_error = error
            if action == "route_manual":
                manual_form = bound_form  # type: ignore[assignment]
            elif action == "route_confirm":
                confirm_form = bound_form  # type: ignore[assignment]
        else:
            return redirect(reverse("travel:settings_routes"))
    confirmed = _latest_per_pair(
        list(
            RouteDistance.objects.filter(mode="driving", confirmed=True)
            .select_related("origin", "destination")
            .order_by("origin_id", "destination_id", "-version")
        )
    )
    candidates = _latest_per_pair(
        list(
            RouteDistance.objects.filter(mode="driving", confirmed=False)
            .select_related("origin", "destination")
            .order_by("origin_id", "destination_id", "-version")
        )
    )
    return render(
        request,
        "travel/settings_routes.html",
        {
            "locations": Location.objects.all(),
            "confirmed_routes": confirmed,
            "candidates": candidates,
            "route_error": route_error,
            "manual_form": manual_form or RouteConfirmationForm(),
            "confirm_form": confirm_form,
        },
    )


def _hasher_label(pin_hash: str) -> str:
    try:
        return identify_hasher(pin_hash).algorithm or "unknown"
    except ValueError:
        return "unknown"


@owner_login_required
@require_http_methods(["GET", "POST"])
def settings_security(request: HttpRequest) -> HttpResponse:
    from apps.accounts.models import Owner
    from apps.accounts.services import change_pin, validate_pin

    owner = Owner.objects.filter(pk=1).first()
    error = ""
    success = ""
    if request.method == "POST" and request.POST.get("action") == "change_pin":
        new_pin = request.POST.get("new_pin", "")
        confirmation = request.POST.get("confirmation", "")
        if new_pin != confirmation:
            error = "PIN entries do not match."
        else:
            try:
                validate_pin(new_pin)
            except ValidationError as exc:
                error = exc.messages[0]
            else:
                if change_pin(request.POST.get("current_pin", ""), new_pin):
                    success = "PIN changed."
                else:
                    error = "The current PIN could not be verified."
    now = timezone.now()
    lockout_until = (
        owner.next_attempt_at
        if owner is not None and owner.next_attempt_at is not None and owner.next_attempt_at > now
        else None
    )
    return render(
        request,
        "travel/settings_security.html",
        {
            "pin_configured": owner is not None,
            "hasher_label": _hasher_label(owner.pin_hash) if owner is not None else "",
            "failed_attempts": owner.failed_attempts if owner is not None else 0,
            "lockout_until": lockout_until,
            "session_days": settings.SESSION_COOKIE_AGE // 86400,
            "error": error,
            "success": success,
        },
        status=400 if error else 200,
    )


@owner_login_required
@require_GET
def settings_defaults(request: HttpRequest) -> HttpResponse:
    import os

    defaults = [
        {
            "label": "Time zone",
            "value": settings.TIME_ZONE,
            "note": "used for every timestamp shown in the app",
        },
        {
            "label": "Language",
            "value": settings.LANGUAGE_CODE,
            "note": "interface language",
        },
        {
            "label": "Currency",
            "value": "EUR (Euro)",
            "note": "default for new expenses and employer claims",
        },
        {
            "label": "Default export format",
            "value": "Excel workbook (.xlsx)",
            "note": "preselected on the exports page",
        },
        {
            "label": "Default activity country",
            "value": "DE",
            "note": "preselected for external activities",
        },
        {
            "label": "Data directory",
            "value": str(settings.DATA_DIR),
            "note": "exports and attachments are stored here",
        },
        {
            "label": "Backup directory",
            "value": os.environ.get("WORKLEDGER_BACKUP_DIR", "backups/"),
            "note": "target of ./backup.sh",
        },
    ]
    return render(request, "travel/settings_defaults.html", {"defaults": defaults})
