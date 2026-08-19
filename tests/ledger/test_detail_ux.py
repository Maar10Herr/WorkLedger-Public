"""Semantic event-detail page contract.

Pins the seven-section layout, human presenter labels, the collapsed
technical section that owns all raw identifiers, the human revision
timeline, and the correction path (append-only mechanics preserved).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from django.test import Client
from django.urls import reverse
from ux_seed import seed_demo_events

from apps.accounts.services import configure_pin
from apps.evidence.models import Attachment, AttachmentLink
from apps.ledger.services import create_event

pytestmark = pytest.mark.django_db

REPO_ROOT = Path(__file__).resolve().parents[2]

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def logged_in_client() -> Client:
    configure_pin("123456")
    client = Client()
    response = client.post(reverse("accounts:login"), {"pin": "123456"})
    assert response.status_code == 302
    return client


def _detail(client: Client, event_id: object) -> str:
    response = client.get(reverse("ledger:event_detail", args=[event_id]))
    assert response.status_code == 200
    return response.content.decode()


def _sections(content: str) -> list[str]:
    return re.findall(r'data-detail-section="([a-z]+)"', content)


def test_detail_sections_in_mandated_order() -> None:
    seed = seed_demo_events()
    content = _detail(logged_in_client(), seed["journey"].pk)
    assert _sections(content) == [
        "summary",
        "facts",
        "attachments",
        "tax",
        "employer",
        "revisions",
        "technical",
    ]


def test_summary_section_uses_presenter_and_never_leaks_uuid() -> None:
    seed = seed_demo_events()
    content = _detail(logged_in_client(), seed["journey"].pk)
    assert "08:03 · berlin → hamburg · ice 78" in content
    ordinary = re.sub(r"<[^>]+>", " ", content.split("data-technical-details")[0])
    assert UUID_RE.search(ordinary) is None
    assert "snapshot" not in ordinary.casefold()


def test_facts_section_uses_human_labels_and_names_not_codes() -> None:
    seed = seed_demo_events()
    content = _detail(logged_in_client(), seed["expense"].pk)
    facts = re.search(r'data-detail-section="facts"[^>]*>(.*?)</section>', content, re.S)
    assert facts is not None
    body = facts.group(1)
    assert "desk equipment" in body.casefold()  # category NAME
    assert ">furniture<" not in body  # category code never shown as a value
    assert "destination_id" not in body  # no raw identifier labels


def test_journey_facts_section_is_semantic() -> None:
    seed = seed_demo_events()
    content = _detail(logged_in_client(), seed["journey"].pk)
    facts = re.search(r'data-detail-section="facts"[^>]*>(.*?)</section>', content, re.S)
    assert facts is not None
    body = facts.group(1).casefold()
    assert "train number" in body
    assert "78" in body
    assert "train category" in body
    assert "ice" in body
    assert "destination" in body
    assert "hamburg" in body


def test_technical_details_collapsed_and_owns_raw_facts() -> None:
    seed = seed_demo_events()
    content = _detail(logged_in_client(), seed["journey"].pk)
    technical = re.search(
        r'<details[^>]*data-technical-details[^>]*>(.*?)</details>', content, re.S
    )
    assert technical is not None
    body = technical.group(1)
    # Event id, audit hash, raw destination id, and the full snapshot live here.
    assert str(seed["journey"].pk) in body
    assert UUID_RE.search(body) is not None
    assert "audit_hash" in body or "destination_id" in body
    # Collapsed by default: nothing technical is visible without opening.
    assert '<details' in technical.group(0)


def test_attachments_section_is_human_and_sha_stays_technical() -> None:
    seed = seed_demo_events()
    expense = seed["expense"]
    attachment = Attachment.objects.create(
        original_filename="receipt.png",
        relative_original_path=f"{expense.pk}/receipt.png",
        detected_mime_type="image/png",
        detected_format="png",
        size_bytes=1234,
        sha256="a" * 64,
        uploaded_at=datetime(2026, 8, 4, 7, 56, tzinfo=UTC),
        preview_status="ready",
    )
    AttachmentLink.objects.create(attachment=attachment, event=expense, link_type="expense_receipt")

    content = _detail(logged_in_client(), expense.pk)
    attachments = re.search(
        r'data-detail-section="attachments"[^>]*>(.*?)</section>', content, re.S
    )
    assert attachments is not None
    body = attachments.group(1)
    assert "receipt.png" in body
    assert "download original" in body
    assert "a" * 64 not in body  # SHA-256 stays in the technical section
    ordinary = content.split("data-technical-details")[0]
    assert "a" * 64 not in ordinary


def test_tax_treatment_section_for_journey_only() -> None:
    seed = seed_demo_events()
    journey = _detail(logged_in_client(), seed["journey"].pk)
    tax = re.search(r'data-detail-section="tax"[^>]*>(.*?)</section>', journey, re.S)
    assert tax is not None
    assert "commuting allowance" in tax.group(1).casefold()

    receipt = _detail(logged_in_client(), seed["receipt"].pk)
    assert 'data-detail-section="tax"' not in receipt


def test_employer_reimbursement_section() -> None:
    seed = seed_demo_events()
    journey = _detail(logged_in_client(), seed["journey"].pk)
    employer = re.search(
        r'data-detail-section="employer"[^>]*>(.*?)</section>', journey, re.S
    )
    assert employer is not None
    assert "employer" in employer.group(1).casefold()

    receipt = _detail(logged_in_client(), seed["receipt"].pk)
    assert 'data-detail-section="employer"' not in receipt


def test_revision_timeline_is_human_with_labels_old_new_and_comments() -> None:
    seed = seed_demo_events()
    content = _detail(logged_in_client(), seed["wfh"].pk)
    revisions = re.search(
        r'data-detail-section="revisions"[^>]*>(.*?)</section>', content, re.S
    )
    assert revisions is not None
    body = revisions.group(1)
    assert "revision 1" in body
    assert "revision 2" in body
    assert "Added a note" in body  # the correction comment
    # Changed field rendered with its human label, old → new values.
    assert "note" in body.casefold()
    assert "added later" in body
    # No hashes, ids, or field_* implementation names in the ordinary timeline.
    assert "audit_hash" not in body
    assert UUID_RE.search(re.sub(r"<[^>]+>", " ", body)) is None
    timeline = body.split('<ol class="wl-timeline">')[1]
    assert "field_note" not in timeline


def test_revision_timeline_append_only_mechanics_preserved() -> None:
    seed = seed_demo_events()
    event = seed["wfh"]
    client = logged_in_client()
    content = _detail(client, event.pk)
    assert content.count("data-revision-item") == 2
    # The correction POST still appends a revision and preserves the original.
    response = client.post(
        reverse("ledger:correct_event", args=[event.pk]),
        {
            "effective_at": "2026-08-04T07:41",
            "field_note": "corrected again",
            "complete": "on",
            "correction_comment": "Another correction",
        },
    )
    assert response.status_code == 302
    event.refresh_from_db()
    assert event.revisions.count() == 3
    assert event.current_revision is not None
    assert event.current_revision.snapshot["note"] == "corrected again"
    assert event.current_revision.parent_revision is not None
    assert event.current_revision.parent_revision.snapshot["note"] == "added later"


def test_correction_form_has_human_labels_and_journey_pickers() -> None:
    seed = seed_demo_events()
    content = _detail(logged_in_client(), seed["journey"].pk)
    assert 'data-correction-form' in content
    # Destination/origin are pickers over saved locations, not raw id inputs.
    assert 'name="field_destination_id"' in content
    assert 'name="field_origin_id"' in content
    assert "Berlin" in content
    assert "Hamburg" in content
    # The comment field keeps its human label.
    assert 'name="correction_comment"' in content
    assert "correction reason" in content


def test_correction_can_add_a_missing_destination() -> None:
    """The direct fix path: an incomplete journey gains a destination via the
    correction endpoint, appending a revision (origin snapshot preserved)."""
    seed = seed_demo_events()
    client = logged_in_client()
    incomplete = create_event(
        event_type="journey",
        effective_at=datetime(2026, 8, 4, 12, 0, tzinfo=UTC),
        snapshot={"transport_mode": "bicycle", "origin_name": "Berlin"},
        complete=False,
    )
    original = incomplete.current_revision
    assert original is not None

    response = client.post(
        reverse("ledger:correct_event", args=[incomplete.pk]),
        {
            "effective_at": "2026-08-04T12:00",
            "field_destination_id": str(seed["office"].pk),
            "field_destination_name": "Hamburg",
            "field_destination_type": "first_workplace",
            "complete": "on",
            "correction_comment": "Added destination",
        },
    )
    assert response.status_code == 302
    incomplete.refresh_from_db()
    original.refresh_from_db()
    assert incomplete.revisions.count() == 2
    assert incomplete.current_revision is not None
    assert incomplete.current_revision.snapshot["destination_id"] == str(seed["office"].pk)
    assert incomplete.current_revision.snapshot["destination_name"] == "Hamburg"
    assert incomplete.current_revision.complete is True
    assert original.snapshot == {
        "transport_mode": "bicycle",
        "origin_name": "Berlin",
        "tax_relevant": False,
        "employer_reimbursable": False,
    }


def test_amended_badge_on_detail() -> None:
    seed = seed_demo_events()
    content = _detail(logged_in_client(), seed["wfh"].pk)
    assert 'data-badge-amended="true"' in content


def test_long_values_cannot_overflow_horizontally() -> None:
    # Long facts/timeline values wrap: the shared wrap class ships in the
    # compiled stylesheet (overflow-wrap:anywhere) and is used on value cells.
    css = (REPO_ROOT / "static" / "css" / "workledger.css").read_text()
    assert "overflow-wrap:anywhere" in css
    template = (REPO_ROOT / "templates" / "ledger" / "event_detail.html").read_text()
    assert "wl-break" in template
