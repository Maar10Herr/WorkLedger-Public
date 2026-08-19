from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from apps.accounts.decorators import owner_login_required
from apps.evidence.models import AttachmentLink
from apps.evidence.services import reconcile_receipt, store_attachment
from apps.ledger.models import Event

from .models import Expense, ExpenseCategory
from .services import create_expense, update_reimbursement_status


def _optional_decimal(value: str) -> Decimal | None:
    try:
        return Decimal(value) if value.strip() else None
    except InvalidOperation:
        return None


def _category_picker_context() -> dict[str, Any]:
    """Server-side view-model for the grouped/recent/searchable category sheet.

    Groups mirror the seeded parent structure: parents with children become
    group headings, childless parents render as standalone options, and any
    orphaned category still appears so every active category stays selectable.
    """
    categories = list(ExpenseCategory.objects.filter(active=True))
    groups: list[dict[str, Any]] = []
    placed: set[str] = set()
    for category in categories:
        if category.parent_id is None:
            children = [c for c in categories if c.parent_id == category.pk]
            if children:
                groups.append({"parent": category, "categories": children})
                placed.update([category.code, *(c.code for c in children)])
            else:
                groups.append({"parent": None, "categories": [category]})
                placed.add(category.code)
    for category in categories:
        if category.code not in placed:
            groups.append({"parent": None, "categories": [category]})
            placed.add(category.code)
    return {"category_groups": groups, "recent_categories": _recent_categories()}


def _recent_categories(limit: int = 6) -> list[ExpenseCategory]:
    """Most recently used categories, derived from expense event snapshots.

    Distinct codes are collected in event order and resolved with a single
    bulk query, so the category lookup is constant-time rather than one query
    per event; recent order, deduplication, and the active filter are
    preserved, and the result is capped at ``limit``.
    """
    codes: list[str] = []
    seen: set[str] = set()
    events = (
        Event.objects.filter(event_type="expense", current_revision__deleted=False)
        .select_related("current_revision")
        .order_by("-current_revision__effective_at", "-created_at")
    )
    for event in events:
        revision = event.current_revision
        if revision is None:
            continue
        code = str(revision.snapshot.get("category") or "")
        if not code or code in seen:
            continue
        seen.add(code)
        codes.append(code)
    if not codes:
        return []
    by_code = {
        category.code: category
        for category in ExpenseCategory.objects.filter(code__in=codes, active=True)
    }
    recent: list[ExpenseCategory] = []
    for code in codes:
        category = by_code.get(code)
        if category is None:
            continue
        recent.append(category)
        if len(recent) >= limit:
            break
    return recent


@owner_login_required
@require_http_methods(["GET", "POST"])
@transaction.atomic
def expense_entry(request: HttpRequest) -> HttpResponse:
    if request.method == "GET":
        return render(
            request,
            "expenses/expense_entry.html",
            {
                "categories": ExpenseCategory.objects.filter(active=True),
                "unmatched_receipts": (
                    Event.objects.filter(
                        event_type="receipt_only",
                        current_revision__deleted=False,
                        current_revision__snapshot__reconciliation_status="unmatched",
                    )
                    .select_related("current_revision")
                    .order_by("-current_revision__effective_at")[:30]
                ),
                "today_value": timezone.localdate().isoformat(),
                **_category_picker_context(),
            },
        )
    amount: Decimal | None
    try:
        original_amount = Decimal(request.POST.get("amount", ""))
    except InvalidOperation:
        original_amount = None
    currency = request.POST.get("currency", "EUR").strip().upper() or "EUR"
    try:
        exchange_rate = Decimal(request.POST.get("exchange_rate", "1") or "1")
    except InvalidOperation:
        exchange_rate = Decimal("1")
    amount = (
        (original_amount * exchange_rate).quantize(Decimal("0.01"))
        if original_amount is not None
        else None
    )
    personally_paid_input = _optional_decimal(
        request.POST.get("amount_personally_paid", "")
    )
    personally_paid = (
        (personally_paid_input * exchange_rate).quantize(Decimal("0.01"))
        if personally_paid_input is not None
        else amount
    )
    reimbursement_input = _optional_decimal(
        request.POST.get("employer_reimbursement_amount", "")
    )
    reimbursement_amount = (
        (reimbursement_input * exchange_rate).quantize(Decimal("0.01"))
        if reimbursement_input is not None
        else Decimal("0.00")
    )
    upload = request.FILES.get("attachment")
    existing_receipt_id = request.POST.get("existing_receipt", "").strip()
    existing_receipt = (
        Event.objects.filter(
            pk=existing_receipt_id,
            event_type="receipt_only",
            current_revision__deleted=False,
            current_revision__snapshot__reconciliation_status="unmatched",
        ).first()
        if existing_receipt_id
        else None
    )
    if existing_receipt_id and existing_receipt is None:
        messages.error(request, "Choose an unmatched receipt or upload a new file.")
        return redirect("expenses:expense_entry")
    expense = create_expense(
        effective_at=timezone.now(),
        category=request.POST.get("category", "").strip(),
        amount=amount,
        currency="EUR",
        tax_relevant=request.POST.get("tax_relevant") == "on",
        employer_reimbursable=request.POST.get("employer_reimbursable") == "on",
        employer_paid=request.POST.get("employer_paid") == "on",
        merchant=request.POST.get("merchant", "").strip(),
        note=request.POST.get("note", "").strip(),
        facts={
            "invoice_or_receipt_date": request.POST.get("invoice_or_receipt_date", ""),
            "payment_date": request.POST.get("payment_date", ""),
            "payment_method": request.POST.get("payment_method", "").strip(),
            "vat_amount": request.POST.get("vat_amount", "").strip(),
            "original_amount": str(original_amount) if original_amount is not None else None,
            "gross_amount_eur": str(amount) if amount is not None else None,
            "amount_personally_paid_eur": str(personally_paid)
            if personally_paid is not None
            else None,
            "employer_reimbursement_amount_eur": str(reimbursement_amount),
            "original_currency": currency,
            "exchange_rate_to_eur": str(exchange_rate),
            "reference": request.POST.get("reference", "").strip(),
            "supplier_address": request.POST.get("supplier_address", "").strip(),
            "business_reason": request.POST.get("business_reason", "").strip(),
            "description": request.POST.get("description", "").strip(),
            "professional_use_percentage": request.POST.get(
                "professional_use_percentage", ""
            ).strip()
            or None,
            "justification": request.POST.get("justification", "").strip(),
            "documentation_status": "attached"
            if upload is not None or existing_receipt is not None
            else request.POST.get("documentation_status", "missing"),
        },
    )
    if reimbursement_amount > 0 and expense.event.employer_reimbursable:
        update_reimbursement_status(expense, Expense.ReimbursementStatus.SUBMITTED)
        expense_amount = amount or Decimal("0.00")
        next_status = (
            Expense.ReimbursementStatus.REIMBURSED
            if reimbursement_amount >= expense_amount
            else Expense.ReimbursementStatus.PARTIALLY_REIMBURSED
        )
        update_reimbursement_status(
            expense,
            next_status,
            reimbursed_amount=min(reimbursement_amount, expense_amount),
            note="Reimbursement entered with expense",
        )
    if upload is not None:
        attachment = store_attachment(upload)
        AttachmentLink.objects.create(
            attachment=attachment,
            event=expense.event,
            link_type="expense_receipt",
        )
    elif existing_receipt is not None:
        try:
            reconcile_receipt(existing_receipt, expense.event)
        except ValidationError as exc:
            transaction.set_rollback(True)
            messages.error(request, str(exc))
            return redirect("expenses:expense_entry")
    missing: list[str] = []
    if amount is None:
        missing.append("amount")
    if not request.POST.get("category"):
        missing.append("category")
    if not request.POST.get("business_reason", "").strip():
        missing.append("business reason")
    if request.POST.get("tax_relevant") == "on" and not request.POST.get(
        "professional_use_percentage", ""
    ).strip():
        missing.append("professional-use percentage")
    if (
        upload is None
        and existing_receipt is None
        and request.POST.get("documentation_status", "missing") == "missing"
    ):
        missing.append("documentation")
    return render(
        request,
        "ledger/saved.html",
        {"event": expense.event, "missing": missing, "draft_key": "expense-entry"},
        status=201,
    )
