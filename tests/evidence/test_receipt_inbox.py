from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.accounts.services import configure_pin
from apps.evidence.models import Attachment, AttachmentLink
from apps.evidence.services import receipt_display_name, reconcile_receipt, store_attachment
from apps.expenses.services import create_expense
from apps.ledger.models import Event
from apps.ledger.services import create_event

pytestmark = pytest.mark.django_db


def logged_in_client() -> Client:
    configure_pin("123456")
    client = Client()
    client.post(reverse("accounts:login"), {"pin": "123456"})
    return client


def test_receipt_only_form_uses_normal_mobile_file_input() -> None:
    response = logged_in_client().get(reverse("evidence:receipt_inbox"))

    content = response.content.decode()
    assert response.status_code == 200
    assert 'type="file"' in content
    assert "capture=" not in content


def test_receipt_only_upload_stays_in_unlinked_inbox(
    settings: Any, tmp_path: Path
) -> None:
    settings.DATA_DIR = tmp_path
    upload = SimpleUploadedFile(
        "receipt.png",
        b"\x89PNG\r\n\x1a\n" + b"evidence",
        content_type="image/png",
    )

    response = logged_in_client().post(reverse("evidence:receipt_inbox"), {"attachment": upload})

    event = Event.objects.get(event_type="receipt_only")
    attachment = Attachment.objects.get()
    link = AttachmentLink.objects.get(attachment=attachment, link_type="receipt_inbox")
    assert response.status_code == 201
    assert link.event == event
    assert link.link_type == "receipt_inbox"
    assert attachment.original_filename == "receipt.png"


def test_identical_upload_bytes_are_deduplicated(
    settings: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.DATA_DIR = tmp_path  # type: ignore[attr-defined]
    monkeypatch.setattr("apps.evidence.tasks.generate_previews.delay", lambda *_: None)
    payload = b"%PDF-1.4\nidentical"
    first = store_attachment(
        SimpleUploadedFile("first.pdf", payload, content_type="application/pdf")
    )
    second = store_attachment(
        SimpleUploadedFile("second.pdf", payload, content_type="application/pdf")
    )

    assert first.pk == second.pk
    assert Attachment.objects.count() == 1


def test_original_download_requires_owner_session(
    settings: Any, tmp_path: Path
) -> None:
    settings.DATA_DIR = tmp_path
    attachment = store_attachment(
        SimpleUploadedFile(
            "invoice.pdf", b"%PDF-1.4\nprivate-evidence", content_type="application/pdf"
        )
    )
    url = reverse(
        "evidence:attachment_download",
        kwargs={"attachment_id": attachment.pk, "variant": "original"},
    )
    assert Client().get(url).status_code == 302
    response = logged_in_client().get(url)
    assert response.status_code == 200
    body: Any = response.streaming_content  # type: ignore[attr-defined]
    assert b"".join(body) == b"%PDF-1.4\nprivate-evidence"


def test_receipt_inbox_has_preview_note_and_output_tracks() -> None:
    content = logged_in_client().get(reverse("evidence:receipt_inbox")).content.decode()
    assert "data-receipt-preview" in content
    assert "data-receipt-input" in content
    assert 'name="note"' in content
    assert 'name="tax_relevant"' in content
    assert 'name="employer_reimbursable"' in content
    assert "data-dual-track-note" in content
    assert "data-segment-nav" in content
    assert "capture=" not in content
    assert "save to receipt inbox" in content


def test_receipt_only_upload_stores_note_and_output_tracks(
    settings: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.DATA_DIR = tmp_path
    monkeypatch.setattr("apps.evidence.tasks.generate_previews.delay", lambda *_: None)
    upload = SimpleUploadedFile(
        "receipt.png", b"\x89PNG\r\n\x1a\n" + b"evidence", content_type="image/png"
    )
    response = logged_in_client().post(
        reverse("evidence:receipt_inbox"),
        {
            "attachment": upload,
            "note": "hotel folio",
            "tax_relevant": "on",
            "employer_reimbursable": "on",
        },
    )
    event = Event.objects.get(event_type="receipt_only")
    assert response.status_code == 201
    assert event.tax_relevant is True
    assert event.employer_reimbursable is True
    assert event.current_revision is not None
    assert event.current_revision.snapshot["note"] == "hotel folio"


def test_journey_direct_receipt_upload_reconciles_target_end_to_end(
    settings: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET ?journey shows a real selected-target summary and hidden target, and
    the following upload appends a reconciled link plus a matched revision —
    proving the database outcome, not merely hidden markup."""
    settings.DATA_DIR = tmp_path
    monkeypatch.setattr("apps.evidence.tasks.generate_previews.delay", lambda *_: None)
    client = logged_in_client()
    journey = create_event(
        event_type="journey",
        effective_at=datetime(2026, 8, 4, 8, 3, tzinfo=UTC),
        snapshot={"origin_name": "Berlin", "destination_name": "Hamburg"},
        complete=True,
    )

    page = client.get(reverse("evidence:receipt_inbox"), {"journey": str(journey.pk)})
    content = page.content.decode()
    assert page.status_code == 200
    assert "data-receipt-target-summary" in content
    assert f'name="target_event" value="{journey.pk}"' in content
    assert "berlin" in content.lower() and "hamburg" in content.lower()

    upload = SimpleUploadedFile(
        "train-receipt.pdf", b"%PDF-1.4\njourney receipt", content_type="application/pdf"
    )
    response = client.post(
        reverse("evidence:receipt_inbox"),
        {
            "attachment": upload,
            "target_event": str(journey.pk),
            "note": "DB ticket receipt",
        },
    )

    receipt = Event.objects.get(event_type="receipt_only")
    receipt.refresh_from_db()
    assert response.status_code == 201
    assert receipt.revisions.count() == 2
    assert receipt.current_revision is not None
    assert receipt.current_revision.snapshot["reconciliation_status"] == "matched"
    assert receipt.current_revision.snapshot["reconciled_to_event_id"] == str(journey.pk)
    assert receipt.current_revision.snapshot["note"] == "DB ticket receipt"
    assert AttachmentLink.objects.filter(
        event=journey, link_type="reconciled_receipt"
    ).exists()


def test_journey_prefill_upload_links_receipt_immediately(
    settings: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.DATA_DIR = tmp_path
    monkeypatch.setattr("apps.evidence.tasks.generate_previews.delay", lambda *_: None)
    journey = create_event(
        event_type="journey",
        effective_at=datetime(2026, 8, 4, 8, 3, tzinfo=UTC),
        snapshot={"origin_name": "Berlin", "destination_name": "Hamburg"},
        complete=True,
    )
    upload = SimpleUploadedFile(
        "train-receipt.pdf", b"%PDF-1.4\njourney receipt", content_type="application/pdf"
    )

    response = logged_in_client().post(
        reverse("evidence:receipt_inbox"),
        {"attachment": upload, "target_event": str(journey.pk)},
    )

    receipt = Event.objects.get(event_type="receipt_only")
    receipt.refresh_from_db()
    assert response.status_code == 201
    assert receipt.revisions.count() == 2
    assert receipt.current_revision is not None
    assert receipt.current_revision.snapshot["reconciliation_status"] == "matched"
    assert receipt.current_revision.snapshot["reconciled_to_event_id"] == str(journey.pk)
    assert AttachmentLink.objects.filter(
        event=journey, link_type="reconciled_receipt"
    ).exists()


def test_receipt_reconciliation_creates_revision_and_auditable_link(
    settings: Any, tmp_path: Path
) -> None:
    settings.DATA_DIR = tmp_path
    upload = SimpleUploadedFile("receipt.pdf", b"%PDF-1.4\nreceipt", content_type="application/pdf")
    client = logged_in_client()
    client.post(reverse("evidence:receipt_inbox"), {"attachment": upload})
    receipt = Event.objects.get(event_type="receipt_only")
    expense = create_expense(
        effective_at=datetime(2026, 8, 4, tzinfo=UTC),
        category="taxi",
        amount=Decimal("12.00"),
        currency="EUR",
        tax_relevant=True,
        employer_reimbursable=False,
        employer_paid=False,
    )

    response = client.post(
        reverse("evidence:reconcile_receipt", args=[receipt.pk]),
        {"target_event": str(expense.event_id)},
    )

    assert response.status_code == 302
    receipt.refresh_from_db()
    assert receipt.revisions.count() == 2
    assert receipt.current_revision is not None
    assert receipt.current_revision.snapshot["reconciliation_status"] == "matched"
    assert AttachmentLink.objects.filter(
        event=expense.event, link_type="reconciled_receipt"
    ).exists()


def test_receipt_display_name_fallback_order() -> None:
    assert receipt_display_name("desk", "note", "IMG_7052.jpeg") == "desk"
    assert receipt_display_name("", "Schreibtisch", "IMG_7052.jpeg") == "Schreibtisch"
    assert receipt_display_name("", "", "IMG_7052.jpeg") == "7052"
    assert receipt_display_name("", "", "bahncard-100.pdf") == "bahncard-100"


def test_linking_existing_receipt_updates_expense_documentation_and_completeness(
    settings: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.DATA_DIR = tmp_path
    monkeypatch.setattr("apps.evidence.tasks.generate_previews.delay", lambda *_: None)
    receipt = create_event(
        event_type="receipt_only",
        effective_at=datetime(2026, 8, 4, tzinfo=UTC),
        snapshot={
            "display_name": "desk",
            "original_filename": "IMG_7052.jpeg",
            "note": "",
            "reconciliation_status": "unmatched",
        },
        complete=True,
    )
    attachment = store_attachment(
        SimpleUploadedFile("IMG_7052.jpeg", b"%PDF-1.4\nreceipt", content_type="application/pdf")
    )
    AttachmentLink.objects.create(attachment=attachment, event=receipt, link_type="receipt_inbox")
    expense = create_expense(
        effective_at=datetime(2026, 8, 4, tzinfo=UTC),
        category="desk",
        amount=Decimal("249.00"),
        currency="EUR",
        tax_relevant=False,
        employer_reimbursable=False,
        employer_paid=False,
        facts={"business_reason": "office furniture", "documentation_status": "missing"},
    )

    reconcile_receipt(receipt, expense.event)

    receipt.refresh_from_db()
    expense.event.refresh_from_db()
    assert receipt.current_revision is not None
    assert receipt.current_revision.snapshot["reconciliation_status"] == "matched"
    assert expense.event.current_revision is not None
    assert expense.event.current_revision.snapshot["documentation_status"] == "attached"
    assert expense.event.current_revision.complete is True


def test_reconciliation_rejects_non_target_and_second_link() -> None:
    receipt = create_event(
        event_type="receipt_only",
        effective_at=datetime(2026, 8, 4, tzinfo=UTC),
        snapshot={"reconciliation_status": "unmatched"},
        complete=True,
    )
    target = create_event(
        event_type="note",
        effective_at=datetime(2026, 8, 4, tzinfo=UTC),
        snapshot={},
        complete=True,
    )
    with pytest.raises(ValidationError, match="expense, journey"):
        reconcile_receipt(receipt, target)
    assert receipt.current_revision is not None
    receipt.current_revision.snapshot["reconciliation_status"] = "matched"
    with pytest.raises(ValidationError, match="already linked"):
        reconcile_receipt(receipt, create_event(
            event_type="journey",
            effective_at=datetime(2026, 8, 4, tzinfo=UTC),
            snapshot={},
            complete=True,
        ))
