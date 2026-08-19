from datetime import UTC, datetime
from decimal import Decimal

import pytest

from apps.expenses.models import Expense
from apps.expenses.services import (
    create_expense,
    expense_track_amounts,
    update_reimbursement_status,
)

pytestmark = pytest.mark.django_db


def test_single_expense_can_feed_tax_and_employer_tracks_without_duplication() -> None:
    expense = create_expense(
        effective_at=datetime(2026, 8, 4, 12, 0, tzinfo=UTC),
        category="train_ticket",
        amount=Decimal("42.50"),
        currency="EUR",
        tax_relevant=True,
        employer_reimbursable=True,
        employer_paid=False,
    )

    event = expense.event
    amounts = expense_track_amounts(event)
    assert event.tax_relevant is True
    assert event.employer_reimbursable is True
    assert amounts.tax_deduction == Decimal("0.00")
    assert amounts.employer_claim == Decimal("42.50")
    update_reimbursement_status(expense, Expense.ReimbursementStatus.SUBMITTED)
    update_reimbursement_status(expense, Expense.ReimbursementStatus.REJECTED)
    after_rejection = expense_track_amounts(event)
    assert after_rejection.tax_deduction == Decimal("42.50")
    assert after_rejection.employer_claim == Decimal("0.00")


def test_employer_paid_expense_is_not_deducted_or_reclaimed() -> None:
    expense = create_expense(
        effective_at=datetime(2026, 8, 4, 12, 0, tzinfo=UTC),
        category="hotel",
        amount=Decimal("120.00"),
        currency="EUR",
        tax_relevant=True,
        employer_reimbursable=True,
        employer_paid=True,
    )

    amounts = expense_track_amounts(expense.event)
    assert amounts.tax_deduction == Decimal("0.00")
    assert amounts.employer_claim == Decimal("0.00")
    assert expense.event.current_revision is not None
    assert expense.event.current_revision.snapshot["employer_paid"] is True
