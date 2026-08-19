from datetime import UTC, datetime
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.expenses.models import Expense, ReimbursementStatusChange
from apps.expenses.services import (
    create_expense,
    expense_track_amounts,
    update_reimbursement_status,
)

pytestmark = pytest.mark.django_db


def test_reimbursement_status_history_is_append_only() -> None:
    expense = create_expense(
        effective_at=datetime(2026, 8, 4, 10, tzinfo=UTC),
        category="taxi",
        amount=Decimal("42.00"),
        currency="EUR",
        tax_relevant=True,
        employer_reimbursable=True,
        employer_paid=False,
    )

    assert expense.reimbursement_status == Expense.ReimbursementStatus.READY
    update_reimbursement_status(expense, Expense.ReimbursementStatus.SUBMITTED)
    update_reimbursement_status(expense, Expense.ReimbursementStatus.REIMBURSED)

    assert list(
        expense.reimbursement_history.values_list("new_status", flat=True)
    ) == ["ready", "submitted", "reimbursed"]
    with pytest.raises(ValidationError):
        update_reimbursement_status(expense, Expense.ReimbursementStatus.READY)
    assert ReimbursementStatusChange.objects.filter(expense=expense).count() == 3


def test_partial_reimbursement_updates_remaining_tracks() -> None:
    expense = create_expense(
        effective_at=datetime(2026, 8, 4, 10, tzinfo=UTC),
        category="taxi",
        amount=Decimal("100.00"),
        currency="EUR",
        tax_relevant=True,
        employer_reimbursable=True,
        employer_paid=False,
    )
    update_reimbursement_status(expense, Expense.ReimbursementStatus.SUBMITTED)
    update_reimbursement_status(
        expense,
        Expense.ReimbursementStatus.PARTIALLY_REIMBURSED,
        reimbursed_amount=Decimal("40.00"),
    )
    partial = expense_track_amounts(expense.event)
    assert partial.tax_deduction == Decimal("0.00")
    assert partial.employer_claim == Decimal("60.00")
    update_reimbursement_status(expense, Expense.ReimbursementStatus.REJECTED)
    rejected = expense_track_amounts(expense.event)
    assert rejected.tax_deduction == Decimal("60.00")
    assert rejected.employer_claim == Decimal("0.00")
