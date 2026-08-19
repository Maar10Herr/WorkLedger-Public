from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.accounts.services import configure_pin
from apps.evidence.models import AttachmentLink
from apps.expenses.models import Expense

pytestmark = pytest.mark.django_db


def logged_in_client() -> Client:
    configure_pin("123456")
    client = Client()
    client.post(reverse("accounts:login"), {"pin": "123456"})
    return client


def test_expense_entry_stores_receipt_and_both_output_flags(settings: Any, tmp_path: Path) -> None:
    settings.DATA_DIR = tmp_path
    receipt = SimpleUploadedFile(
        "meal.pdf", b"%PDF-1.4\n% minimal evidence", content_type="application/pdf"
    )

    response = logged_in_client().post(
        reverse("expenses:expense_entry"),
        {
            "category": "meal_actual",
            "amount": "19.80",
            "currency": "EUR",
            "tax_relevant": "on",
            "employer_reimbursable": "on",
            "attachment": receipt,
        },
    )

    expense = Expense.objects.select_related("event__current_revision").get()
    assert response.status_code == 201
    assert expense.event.current_revision is not None
    assert expense.event.current_revision.snapshot["amount"] == str(Decimal("19.80"))
    assert expense.event.tax_relevant is True
    assert expense.event.employer_reimbursable is True
    assert AttachmentLink.objects.get(link_type="expense_receipt").event == expense.event
