from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.decorators import owner_login_required
from apps.ledger.models import Event
from apps.ledger.services import create_event

from .models import Attachment, AttachmentLink
from .services import receipt_display_name, reconcile_receipt, store_attachment


def _target_summary(event: Event) -> dict[str, str]:
    """Human-readable summary of a preselected link target for ?journey=."""
    revision = event.current_revision
    snapshot = revision.snapshot if revision is not None else {}
    if event.event_type == "journey":
        origin = str(snapshot.get("origin_name") or "").strip()
        destination = str(snapshot.get("destination_name") or "").strip()
        meta = f"{origin} → {destination}" if origin or destination else ""
        label = "journey"
    elif event.event_type == "expense":
        category = str(snapshot.get("category_name") or snapshot.get("category") or "")
        amount = str(snapshot.get("amount") or "—")
        meta = f"{category} · {amount} {snapshot.get('currency') or 'EUR'}".strip()
        label = "expense"
    elif event.event_type == "external_activity":
        meta = str(snapshot.get("activity_type") or "").replace("_", " ").strip()
        label = "external activity"
    else:
        label = str(event.event_type).replace("_", " ")
        meta = ""
    return {"label": label, "meta": meta}


@owner_login_required
@require_http_methods(["GET", "POST"])
@transaction.atomic
def receipt_inbox(request: HttpRequest) -> HttpResponse:
    if request.method == "GET":
        receipt_param = request.GET.get("receipt", "").strip()
        receipt_queryset = Event.objects.filter(
            event_type="receipt_only", current_revision__deleted=False
        ).select_related("current_revision")
        if receipt_param:
            receipt_queryset = receipt_queryset.filter(pk=receipt_param)
        receipts = [
            event
            for event in receipt_queryset
            if event.current_revision
            and event.current_revision.snapshot.get("reconciliation_status", "unmatched")
            == "unmatched"
        ]
        target_events = Event.objects.filter(
            event_type__in=["expense", "journey", "external_activity"],
            current_revision__deleted=False,
        ).select_related("current_revision")
        preselected_target = None
        target_param = ""
        for parameter in ("journey", "expense", "external_activity"):
            if request.GET.get(parameter, "").strip():
                target_param = request.GET[parameter].strip()
                break
        if target_param:
            preselected_target = target_events.filter(pk=target_param).first()
        return render(
            request,
            "evidence/receipt_inbox.html",
            {
                "receipts": receipts,
                "target_events": target_events.order_by(
                    "-current_revision__effective_at"
                )[:100],
                "preselected_target": preselected_target,
                "preselected_target_summary": (
                    _target_summary(preselected_target)
                    if preselected_target is not None
                    else None
                ),
            },
        )
    upload = request.FILES.get("attachment")
    event = create_event(
        event_type="receipt_only",
        effective_at=timezone.now(),
        snapshot={
            "original_filename": upload.name if upload else "",
            "display_name": receipt_display_name(
                request.POST.get("name", "") or "",
                request.POST.get("note", "") or "",
                (upload.name or "receipt") if upload else "receipt",
            ),
            "reconciliation_status": "unmatched",
            "note": (request.POST.get("note", "") or "").strip(),
        },
        complete=upload is not None,
        tax_relevant=request.POST.get("tax_relevant") == "on",
        employer_reimbursable=request.POST.get("employer_reimbursable") == "on",
    )
    missing: list[str] = []
    if upload is None:
        missing.append("receipt file")
    else:
        attachment = store_attachment(upload)
        AttachmentLink.objects.create(
            attachment=attachment,
            event=event,
            link_type="receipt_inbox",
        )
        target_event_id = request.POST.get("target_event", "").strip()
        target_event = (
            Event.objects.filter(
                pk=target_event_id,
                event_type__in=["expense", "journey", "external_activity"],
                current_revision__deleted=False,
            ).first()
            if target_event_id
            else None
        )
        if target_event is not None:
            try:
                reconcile_receipt(event, target_event)
            except ValidationError as exc:
                messages.error(request, str(exc))
    return render(
        request,
        "ledger/saved.html",
        {"event": event, "missing": missing, "draft_key": "receipt-inbox"},
        status=201,
    )


@owner_login_required
@require_POST
def reconcile_receipt_view(request: HttpRequest, event_id: str) -> HttpResponse:
    receipt = get_object_or_404(Event, pk=event_id, event_type="receipt_only")
    target = get_object_or_404(Event, pk=request.POST.get("target_event"))
    try:
        reconcile_receipt(receipt, target)
    except ValidationError as exc:
        messages.error(request, str(exc))
    return redirect("evidence:receipt_inbox")


@owner_login_required
@require_GET
def attachment_download(
    request: HttpRequest, attachment_id: object, variant: str
) -> FileResponse:
    attachment = get_object_or_404(Attachment, pk=attachment_id)
    relative = {
        "original": attachment.original_path,
        "preview": attachment.preview_path,
        "thumbnail": attachment.thumbnail_path,
    }.get(variant)
    if not relative:
        raise Http404
    base = settings.DATA_DIR.resolve()
    path = (settings.DATA_DIR / relative).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise Http404
    return FileResponse(
        path.open("rb"),
        as_attachment=variant == "original",
        filename=attachment.original_filename if variant == "original" else path.name,
    )
