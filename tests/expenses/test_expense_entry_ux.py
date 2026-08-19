"""Expense-entry acceptance tests: receipt segmentation, primary-field
expense entry, grouped/recent/searchable category picker, progressive
disclosure of advanced details, and dual-track record-for semantics.

These tests pin the user-facing vocabulary and stable test hooks without
exercising browser behaviour (Playwright covers that). They must stay green
without API credentials.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.services import configure_pin
from apps.expenses.models import Expense
from apps.expenses.services import create_expense

pytestmark = pytest.mark.django_db


def logged_in_client() -> Client:
    configure_pin("123456")
    client = Client()
    response = client.post(reverse("accounts:login"), {"pin": "123456"})
    assert response.status_code == 302
    return client


def _expense_url() -> str:
    return reverse("expenses:expense_entry")


def test_minimal_expense_post_with_blank_primary_saves_incomplete() -> None:
    client = logged_in_client()
    response = client.post(
        _expense_url(),
        {"description": "coffee with client"},
    )

    assert response.status_code == 201
    expense = Expense.objects.get()
    assert expense.event.current_revision is not None
    assert expense.event.current_revision.complete is False
    assert expense.category.code == "other"
    content = response.content.decode()
    assert "add when convenient" in content
    assert "amount" in content
    assert "category" in content


def test_expense_post_stores_category_amount_date_and_both_tracks() -> None:
    client = logged_in_client()
    response = client.post(
        _expense_url(),
        {
            "category": "meal_actual",
            "amount": "19.80",
            "currency": "EUR",
            "invoice_or_receipt_date": "2026-08-04",
            "description": "client lunch",
            "tax_relevant": "on",
            "employer_reimbursable": "on",
        },
    )
    assert response.status_code == 201
    expense = Expense.objects.select_related("event__current_revision").get()
    assert expense.event.current_revision is not None
    snapshot = expense.event.current_revision.snapshot
    assert expense.category.code == "meal_actual"
    assert snapshot["amount"] == "19.80"
    assert expense.event.tax_relevant is True
    assert expense.event.employer_reimbursable is True
    assert snapshot["description"] == "client lunch"
    assert snapshot["invoice_or_receipt_date"] == "2026-08-04"


def test_personally_paid_defaults_to_gross_after_amount_supplied() -> None:
    response = logged_in_client().post(
        _expense_url(),
        {"category": "taxi", "amount": "12.50", "currency": "EUR"},
    )
    assert response.status_code == 201
    expense = Expense.objects.get()
    assert expense.event.current_revision is not None
    assert expense.event.current_revision.snapshot["amount_personally_paid_eur"] == "12.50"


def test_category_picker_grouped_recent_search_markup() -> None:
    create_expense(
        effective_at=datetime(2026, 8, 3, 12, 0, tzinfo=UTC),
        category="taxi",
        amount=Decimal("18.00"),
        currency="EUR",
        tax_relevant=True,
        employer_reimbursable=False,
        employer_paid=False,
    )
    content = logged_in_client().get(_expense_url()).content.decode()

    assert "data-category-picker" in content
    assert "data-category-trigger" in content
    assert "data-category-search" in content
    assert "data-category-option" in content
    assert "data-category-group" in content
    assert "data-uncategorised" in content

    # Recent categories are derived from saved expense snapshots and shown first.
    recent = re.search(r'data-category-recent[^>]*>(.*?)</section>', content, re.S)
    assert recent is not None
    assert "taxi" in recent.group(1).lower()

    # Grouped by parent: the transport group contains its children.
    transport_group = re.search(
        r'data-category-group="transport"[^>]*>(.*?)</section>', content, re.S
    )
    assert transport_group is not None
    assert "train ticket" in transport_group.group(1).lower()

    # Search field filters options client-side.
    assert 'type="search"' in content

    # uncategorised / decide later maps safely to the existing `other` identity.
    uncategorised = re.search(r'data-uncategorised[^>]*>.*?</label>', content, re.S)
    assert uncategorised is not None
    assert 'value="other"' in uncategorised.group(0)
    assert "decide later" in uncategorised.group(0).lower()


def test_optional_fields_have_no_html_required() -> None:
    content = logged_in_client().get(_expense_url()).content.decode()
    for name in ("category", "business_reason", "professional_use_percentage", "merchant"):
        assert re.search(rf'name="{name}"[^>]*required', content) is None
        assert re.search(rf'name="{name}"', content) is not None


def test_date_defaults_today_and_currency_eur() -> None:
    content = logged_in_client().get(_expense_url()).content.decode()
    today = timezone.localdate().isoformat()
    assert f'name="invoice_or_receipt_date" value="{today}"' in content
    assert 'name="currency" value="EUR"' in content


def test_advanced_details_hidden_by_default() -> None:
    content = logged_in_client().get(_expense_url()).content.decode()
    assert "data-advanced-section" in content
    assert "data-advanced-toggle" in content

    advanced_start = content.find("data-advanced-section")
    assert advanced_start != -1
    # Alpine hides the closed disclosure via x-cloak until it initialises.
    assert "x-cloak" in content[advanced_start : advanced_start + 400]

    # The disclosure has no nested sections, so its own closing </section>
    # is the first one after the opening tag. Fields inside it must sit
    # between the two; fields after it would be visible without disclosure.
    advanced_end = content.find("</section>", advanced_start)
    assert advanced_end != -1

    # Advanced-only fields live inside the disclosure, after the primary view.
    for name in (
        "note",
        "business_reason",
        "merchant",
        "professional_use_percentage",
        "payment_method",
        "vat_amount",
        "supplier_address",
        "payment_date",
    ):
        field = re.search(rf'name="{name}"', content)
        assert field is not None
        assert advanced_start < field.start() < advanced_end


def test_record_for_toggles_and_dual_track_note() -> None:
    content = logged_in_client().get(_expense_url()).content.decode()
    assert "data-record-for" in content
    assert "data-track-tax" in content
    assert "data-track-employer" in content
    assert 'name="tax_relevant"' in content
    assert 'name="employer_reimbursable"' in content
    # The explanatory dual-track note appears only when both tracks are chosen.
    note = re.search(r'data-dual-track-note[^>]*>', content)
    assert note is not None
    assert 'x-show="taxRelevant && employerReimbursable"' in note.group(0)
    assert "one event, two outputs" in content


def test_expense_receipt_segmented_flow_and_native_file_input() -> None:
    content = logged_in_client().get(_expense_url()).content.decode()
    assert "data-segment-nav" in content
    assert "data-segment-expense" in content
    assert "data-segment-receipt" in content
    assert 'type="file"' in content
    assert 'name="attachment"' in content
    assert "capture=" not in content
    assert "data-sticky-submit" in content
    assert "data-save-incomplete" in content
    primary = content.find("save expense")
    incomplete = content.find("save incomplete")
    assert primary != -1 and incomplete != -1
