"""Expense-interaction regression tests (durable, no browser required).

Covers two release-critical interaction contracts:

- The category search box is a filter inside the outer expense form.
  Enter/Go must never submit the form (which would create an accidental
  expense), and the box must never POST as the category.
- ``_recent_categories`` must resolve active categories with a single
  bulk query while preserving recent order, deduplication, and the cap.

Unchecked radios during draft restore are pinned by the event-order regression
harness in ``tests/js/category_picker_event_order.test.js``. The expense-note
disclosure is covered in ``test_expense_entry_ux.py``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.services import configure_pin
from apps.expenses.models import ExpenseCategory
from apps.expenses.services import create_expense
from apps.expenses.views import _recent_categories

pytestmark = pytest.mark.django_db


def logged_in_client() -> Client:
    configure_pin("123456")
    client = Client()
    response = client.post(reverse("accounts:login"), {"pin": "123456"})
    assert response.status_code == 302
    return client


def _expense_url() -> str:
    return reverse("expenses:expense_entry")


def test_category_search_enter_cannot_submit_the_expense_form() -> None:
    """P2: Enter/Go in the category search box must never submit the outer
    expense form, and the search box must never POST as the category."""
    content = logged_in_client().get(_expense_url()).content.decode()

    search = re.search(r"<input[^>]*data-category-search[^>]*>", content)
    assert search is not None
    tag = search.group(0)
    # Alpine prevents the keydown default, so Enter/Go filters instead of
    # implicitly submitting the form and creating an accidental expense.
    assert "@keydown.enter.prevent" in tag
    assert "x-model=\"query\"" in tag
    # No name attribute: the filter text can never be POSTed as category.
    assert 'name="' not in tag


def test_category_radios_post_the_checked_value() -> None:
    """The draft-restored selection is POSTed because every picker radio
    carries the category form name; only the checked radio is submitted."""
    content = logged_in_client().get(_expense_url()).content.decode()
    radios = re.findall(r"<input[^>]*type=\"radio\"[^>]*>", content)
    assert radios
    for tag in radios:
        assert 'name="category"' in tag
        assert 'value="' in tag


def _seed_recent_history() -> None:
    """Ten expenses with one duplicate category, one category that becomes
    inactive, and more distinct categories than the recent cap of six."""
    rows = [
        (datetime(2026, 8, 1, 12, 0, tzinfo=UTC), "taxi"),
        (datetime(2026, 8, 2, 12, 0, tzinfo=UTC), "meal_actual"),
        (datetime(2026, 8, 3, 12, 0, tzinfo=UTC), "taxi"),
        (datetime(2026, 8, 4, 12, 0, tzinfo=UTC), "hotel"),
        (datetime(2026, 8, 5, 12, 0, tzinfo=UTC), "train_ticket"),
        (datetime(2026, 8, 6, 12, 0, tzinfo=UTC), "taxi"),
        (datetime(2026, 8, 7, 12, 0, tzinfo=UTC), "other"),
        (datetime(2026, 8, 8, 12, 0, tzinfo=UTC), "flight"),
        (datetime(2026, 8, 9, 12, 0, tzinfo=UTC), "parking"),
        (datetime(2026, 8, 10, 12, 0, tzinfo=UTC), "telecom"),
    ]
    for effective_at, category in rows:
        create_expense(
            effective_at=effective_at,
            category=category,
            amount=Decimal("10.00"),
            currency="EUR",
            tax_relevant=True,
            employer_reimbursable=False,
            employer_paid=False,
        )
    ExpenseCategory.objects.filter(code="hotel").update(active=False)


def test_recent_categories_preserve_order_dedupe_active_and_cap() -> None:
    """P4-2: most-recent-first order, duplicates collapsed, inactive
    categories skipped (without truncating the scan), capped at six."""
    _seed_recent_history()

    recent = _recent_categories()

    assert [category.code for category in recent] == [
        "telecom",
        "parking",
        "flight",
        "other",
        "taxi",
        "train_ticket",
    ]
    assert len(recent) == 6


def test_recent_categories_use_one_bulk_category_query() -> None:
    """P4-2: the recent-category resolution must not run one query per event
    (the old O(N) lookup); events plus one bulk category query total two."""
    _seed_recent_history()

    with CaptureQueriesContext(connection) as queries:
        _recent_categories()

    assert len(queries.captured_queries) == 2
