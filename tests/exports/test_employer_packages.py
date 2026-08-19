import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.evidence.models import AttachmentLink
from apps.evidence.services import store_attachment
from apps.expenses.services import create_expense
from apps.exports.models import EmployerPackage
from apps.exports.packages import create_package, generate_package_zip, update_package_status
from apps.ledger.models import Event

pytestmark = pytest.mark.django_db


def test_package_status_changes_are_ledger_events_and_zip_contains_receipt(
    settings: Any, tmp_path: Path
) -> None:
    settings.DATA_DIR = tmp_path / "data"
    expense = create_expense(
        effective_at=datetime(2026, 8, 4, 12, 0, tzinfo=UTC),
        category="train_ticket",
        amount=Decimal("42.50"),
        currency="EUR",
        tax_relevant=True,
        employer_reimbursable=True,
        employer_paid=False,
    )
    attachment = store_attachment(
        SimpleUploadedFile("ticket.pdf", b"%PDF-1.4\nreceipt", content_type="application/pdf")
    )
    AttachmentLink.objects.create(
        attachment=attachment, event=expense.event, link_type="expense_receipt"
    )
    package = create_package(
        name="August client visit",
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        event_ids=[expense.event_id],
    )

    update_package_status(package, EmployerPackage.Status.SUBMITTED, note="Sent in portal")
    expense.refresh_from_db()
    assert expense.reimbursement_status == "submitted"
    output = generate_package_zip(package)

    package.refresh_from_db()
    assert package.status == EmployerPackage.Status.SUBMITTED
    assert package.claim_amount == Decimal("42.50")
    status_event = Event.objects.filter(event_type="reimbursement_update").get()
    assert status_event.current_revision is not None
    assert status_event.current_revision.snapshot["package_id"] == str(package.pk)
    assert package.status_changes.count() == 1
    with zipfile.ZipFile(output) as archive:
        assert "claim.xlsx" in archive.namelist()
        assert "manifest.json" in archive.namelist()
        assert any(
            name.startswith("receipts/") and name.endswith("ticket.pdf")
            for name in archive.namelist()
        )


def test_package_rejects_unknown_or_malformed_event_ids() -> None:
    with pytest.raises(ValidationError, match="invalid"):
        create_package(
            name="Invalid package",
            period_start=date(2026, 8, 1),
            period_end=date(2026, 8, 31),
            event_ids=["not-a-uuid"],
        )
    with pytest.raises(ValidationError, match="must exist"):
        create_package(
            name="Unknown package",
            period_start=date(2026, 8, 1),
            period_end=date(2026, 8, 31),
            event_ids=["00000000-0000-0000-0000-000000000001"],
        )
    assert EmployerPackage.objects.count() == 0
