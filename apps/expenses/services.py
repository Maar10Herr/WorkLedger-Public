from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.text import slugify

from apps.ledger.models import Event, EventRevision
from apps.ledger.services import create_event

from .models import Expense, ExpenseCategory, ReimbursementStatusChange


@dataclass(frozen=True)
class TrackAmounts:
    tax_deduction: Decimal
    employer_claim: Decimal




def expense_completeness(snapshot: dict[str, Any]) -> bool:
    """Single source of truth for the facts required to complete an expense."""
    professional_percentage = snapshot.get("professional_use_percentage")
    return bool(
        snapshot.get("category")
        and snapshot.get("amount") not in (None, "")
        and snapshot.get("currency")
        and (
            "business_reason" not in snapshot or snapshot.get("business_reason")
        )
        and (
            "documentation_status" not in snapshot
            or snapshot.get("documentation_status") in {"attached", "not_required"}
        )
        and (
            not snapshot.get("tax_relevant")
            or "professional_use_percentage" not in snapshot
            or professional_percentage not in (None, "")
        )
    )



@transaction.atomic
def create_expense(
    *,
    effective_at: datetime,
    category: str,
    amount: Decimal | None,
    currency: str,
    tax_relevant: bool,
    employer_reimbursable: bool,
    employer_paid: bool,
    merchant: str = "",
    note: str = "",
    facts: dict[str, Any] | None = None,
) -> Expense:
    if amount is not None and amount < 0:
        raise ValidationError("Expense amount cannot be negative")
    professional_percentage = facts.get("professional_use_percentage") if facts else None
    if professional_percentage not in (None, "") and not (
        Decimal("0") <= Decimal(str(professional_percentage)) <= Decimal("100")
    ):
        raise ValidationError("Professional-use percentage must be between 0 and 100")
    category_object, _ = ExpenseCategory.objects.get_or_create(
        code=slugify(category) or "other", defaults={"name": category or "Other"}
    )
    snapshot = {
        "category": category_object.code,
        "category_name": category_object.name,
        "amount": str(amount.quantize(Decimal("0.01"))) if amount is not None else None,
        "currency": currency.upper(),
        "employer_paid": employer_paid,
        "tax_relevant": tax_relevant,
        "employer_reimbursable": employer_reimbursable,
        "merchant": merchant,
        "note": note,
    }
    if facts:
        snapshot.update(facts)
    facts_complete = expense_completeness(snapshot)
    event = create_event(
        event_type="expense",
        effective_at=effective_at,
        snapshot=snapshot,
        complete=bool(category and amount is not None and currency and facts_complete),
        tax_relevant=tax_relevant,
        employer_reimbursable=employer_reimbursable,
    )
    status = (
        (
            Expense.ReimbursementStatus.READY
            if event.current_revision and event.current_revision.complete
            else Expense.ReimbursementStatus.DRAFT
        )
        if employer_reimbursable and not employer_paid
        else Expense.ReimbursementStatus.NOT_APPLICABLE
    )
    expense = Expense.objects.create(
        event=event, category=category_object, reimbursement_status=status
    )
    ReimbursementStatusChange.objects.create(
        expense=expense, previous_status="", new_status=status, note="Initial state"
    )
    return expense


VALID_REIMBURSEMENT_TRANSITIONS: dict[str, set[str]] = {
    Expense.ReimbursementStatus.NOT_APPLICABLE.value: {
        Expense.ReimbursementStatus.DRAFT.value,
        Expense.ReimbursementStatus.READY.value,
    },
    Expense.ReimbursementStatus.DRAFT.value: {
        Expense.ReimbursementStatus.READY.value,
        Expense.ReimbursementStatus.SUBMITTED.value,
        Expense.ReimbursementStatus.WITHDRAWN.value,
    },
    Expense.ReimbursementStatus.READY.value: {
        Expense.ReimbursementStatus.SUBMITTED.value,
        Expense.ReimbursementStatus.WITHDRAWN.value,
    },
    Expense.ReimbursementStatus.SUBMITTED.value: {
        Expense.ReimbursementStatus.PARTIALLY_REIMBURSED.value,
        Expense.ReimbursementStatus.REIMBURSED.value,
        Expense.ReimbursementStatus.REJECTED.value,
        Expense.ReimbursementStatus.WITHDRAWN.value,
    },
    Expense.ReimbursementStatus.PARTIALLY_REIMBURSED.value: {
        Expense.ReimbursementStatus.REIMBURSED.value,
        Expense.ReimbursementStatus.REJECTED.value,
        Expense.ReimbursementStatus.WITHDRAWN.value,
    },
    Expense.ReimbursementStatus.REJECTED.value: {Expense.ReimbursementStatus.READY.value},
    Expense.ReimbursementStatus.WITHDRAWN.value: {Expense.ReimbursementStatus.READY.value},
    Expense.ReimbursementStatus.REIMBURSED.value: set(),
}


@transaction.atomic
def update_reimbursement_status(
    expense: Expense,
    new_status: str,
    *,
    note: str = "",
    reimbursed_amount: Decimal | None = None,
) -> None:
    locked = Expense.objects.select_for_update().get(pk=expense.pk)
    if new_status not in VALID_REIMBURSEMENT_TRANSITIONS.get(locked.reimbursement_status, set()):
        raise ValidationError(
            f"Invalid reimbursement transition: {locked.reimbursement_status} -> {new_status}"
        )
    previous = locked.reimbursement_status
    previous_amount = locked.reimbursed_amount
    event_amount = Decimal(
        (locked.event.current_revision.snapshot if locked.event.current_revision else {}).get(
            "amount"
        )
        or "0.00"
    )
    new_amount = previous_amount if reimbursed_amount is None else reimbursed_amount
    if new_status == Expense.ReimbursementStatus.REIMBURSED:
        new_amount = event_amount if reimbursed_amount is None else reimbursed_amount
    if not Decimal("0.00") <= new_amount <= event_amount:
        raise ValidationError("Reimbursed amount must be between zero and the expense amount")
    if new_status == Expense.ReimbursementStatus.PARTIALLY_REIMBURSED and not (
        Decimal("0.00") < new_amount < event_amount
    ):
        raise ValidationError("Partial reimbursement requires a partial amount")
    ReimbursementStatusChange.objects.create(
        expense=locked,
        previous_status=previous,
        new_status=new_status,
        previous_reimbursed_amount=previous_amount,
        new_reimbursed_amount=new_amount,
        note=note,
    )
    locked.reimbursement_status = new_status
    locked.reimbursed_amount = new_amount
    locked.save(update_fields=["reimbursement_status", "reimbursed_amount"])
    expense.reimbursement_status = new_status
    expense.reimbursed_amount = new_amount


def expense_track_amounts(
    event: Event,
    revision: EventRevision | None = None,
    as_of: datetime | None = None,
) -> TrackAmounts:
    selected_revision = revision or event.current_revision
    if event.event_type != "expense" or selected_revision is None:
        raise ValueError("Track amounts require an expense event")
    snapshot = selected_revision.snapshot
    amount = Decimal(
        snapshot.get("amount_personally_paid_eur") or snapshot.get("amount") or "0.00"
    )
    if snapshot.get("employer_paid") or selected_revision.deleted:
        return TrackAmounts(Decimal("0.00"), Decimal("0.00"))
    tax_relevant = bool(snapshot.get("tax_relevant", event.tax_relevant))
    employer_reimbursable = bool(
        snapshot.get("employer_reimbursable", event.employer_reimbursable)
    )
    status = Expense.ReimbursementStatus.NOT_APPLICABLE.value
    reimbursed_amount = Decimal("0.00")
    if employer_reimbursable:
        status = Expense.ReimbursementStatus.READY.value
        changes = ReimbursementStatusChange.objects.filter(expense__event=event)
        if as_of is not None:
            changes = changes.filter(changed_at__lte=as_of)
        latest_change = changes.order_by("-changed_at", "-id").values(
            "new_status", "new_reimbursed_amount"
        ).first()
        if latest_change:
            status = str(latest_change["new_status"])
            reimbursed_amount = Decimal(latest_change["new_reimbursed_amount"])
    remaining_amount = max(Decimal("0.00"), amount - reimbursed_amount)
    professional_percentage_raw = snapshot.get("professional_use_percentage", "100")
    professional_percentage = (
        Decimal(str(professional_percentage_raw))
        if professional_percentage_raw not in (None, "")
        else Decimal("0.00")
    )
    tax_amount = (remaining_amount * professional_percentage / Decimal("100")).quantize(
        Decimal("0.01")
    )
    tax_allowed = not employer_reimbursable or status in {
        Expense.ReimbursementStatus.NOT_APPLICABLE.value,
        Expense.ReimbursementStatus.REJECTED.value,
        Expense.ReimbursementStatus.WITHDRAWN.value,
    }
    employer_claim_allowed = employer_reimbursable and status in {
        Expense.ReimbursementStatus.DRAFT.value,
        Expense.ReimbursementStatus.READY.value,
        Expense.ReimbursementStatus.SUBMITTED.value,
        Expense.ReimbursementStatus.PARTIALLY_REIMBURSED.value,
    }
    return TrackAmounts(
        tax_deduction=tax_amount
        if tax_relevant and tax_allowed
        else Decimal("0.00"),
        employer_claim=remaining_amount if employer_claim_allowed else Decimal("0.00"),
    )
