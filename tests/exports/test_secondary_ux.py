"""User-facing contracts for exports and employer-package pages.

Semantic, readable rows only in ordinary markup: no raw UUIDs or hashes.
The create page leads with date range and purpose, groups formats and the
attachment option, and hides artifact hashes under technical details.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.services import configure_pin
from apps.exports.models import EmployerPackage, ExportArtifact, ExportJob
from apps.ledger.services import create_event
from apps.travel.models import Employer

pytestmark = pytest.mark.django_db

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")


def _ordinary(body: str) -> str:
    """Body minus functional hrefs and form values, where UUIDs live."""
    stripped = re.sub(r'href="[^"]*"', "", body)
    return re.sub(r'value="[^"]*"', "", stripped)


def logged_in_client() -> Client:
    configure_pin("123456")
    client = Client()
    response = client.post(reverse("accounts:login"), {"pin": "123456"})
    assert response.status_code == 302
    return client


def _artifact(tmp_path: Path, kind: str = ExportArtifact.Kind.XLSX) -> ExportArtifact:
    (tmp_path / "exports").mkdir(parents=True, exist_ok=True)
    target = tmp_path / "exports" / f"workledger_{kind}.xlsx"
    target.write_bytes(b"fake-export-bytes")
    return ExportArtifact.objects.create(
        kind=kind,
        range_start=date(2026, 8, 1),
        range_end=date(2026, 8, 31),
        as_of=timezone.now(),
        relative_path=f"exports/{target.name}",
        sha256="a" * 64,
        size_bytes=target.stat().st_size,
    )


def _package() -> EmployerPackage:
    employer = Employer.objects.create(name="Employer GmbH", is_active=True)
    expense = create_event(
        event_type="expense",
        effective_at=timezone.make_aware(datetime(2026, 8, 4, 10, 0)),
        snapshot={
            "description": "Train ticket",
            "amount": "89.50",
            "currency": "EUR",
            "category": "travel",
        },
        employer_reimbursable=True,
    )
    from apps.exports.packages import create_package

    # Reuse the real package builder for a complete package with an event.
    package = create_package(
        name="August 2026",
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
        event_ids=[str(expense.pk)],
    )
    package.employer = employer
    package.save(update_fields=["employer"])
    return package


# --------------------------------------------------------------------------
# Exports create page
# --------------------------------------------------------------------------


def test_create_page_leads_with_date_range_then_purpose_then_formats(
    tmp_path: Path,
) -> None:
    _artifact(tmp_path)
    client = logged_in_client()

    response = client.get(reverse("exports:create_export"))

    body = response.content.decode()
    assert "data-export-page" in body
    # Order: date range → purpose → grouped formats.
    date_pos = body.index('name="start"')
    purpose_pos = body.index("data-export-purpose")
    formats_pos = body.index("data-export-format-group")
    assert date_pos < purpose_pos < formats_pos
    # Purpose choices.
    assert "data-export-purpose" in body
    for label in ("tax", "employer", "complete"):
        assert label in body
    # Formats are grouped; the attachment option lives in its own group.
    assert "data-export-attachments-group" in body


def test_create_page_recent_exports_are_compact_and_hash_is_technical(
    tmp_path: Path,
) -> None:
    _artifact(tmp_path)
    client = logged_in_client()

    response = client.get(reverse("exports:create_export"))

    body = response.content.decode()
    assert "recent exports" in body or "recent" in body
    assert "Excel workbook" in body
    # The artifact hash appears only inside the technical disclosure.
    assert "data-technical-details" in body
    hash_match = SHA256_RE.search(body)
    assert hash_match is not None
    assert "data-technical-details" in body[: hash_match.start()]
    # No raw UUIDs anywhere ordinary (links carry them functionally).
    assert UUID_RE.search(_ordinary(body)) is None


def test_create_page_lists_recent_jobs_compactly(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    ExportJob.objects.create(
        kind=ExportArtifact.Kind.XLSX,
        range_start=artifact.range_start,
        range_end=artifact.range_end,
        as_of=artifact.as_of,
        status=ExportJob.Status.COMPLETE,
        artifact=artifact,
        completed_at=timezone.now(),
    )
    client = logged_in_client()

    response = client.get(reverse("exports:create_export"))

    body = response.content.decode()
    assert "Complete" in body  # job status label, not the code
    assert "data-export-job" in body
    hash_match = SHA256_RE.search(body)
    if hash_match:
        # The linked artifact's hash may only sit inside technical details.
        assert "data-technical-details" in body[: hash_match.start()]
    assert UUID_RE.search(_ordinary(body)) is None


def test_existing_export_post_contract_unchanged(tmp_path: Path) -> None:
    """The POST contract (start/end/kind) and download flow must not regress."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    client = logged_in_client()

    response = client.post(
        reverse("exports:create_export"),
        {
            "start": date(2026, 8, 1).isoformat(),
            "end": date(2026, 8, 31).isoformat(),
            "kind": "xlsx",
        },
    )

    artifact = ExportArtifact.objects.get()
    assert response.status_code == 302
    assert response.headers["Location"] == reverse(
        "exports:download_export", kwargs={"export_id": artifact.pk}
    )
    assert len(artifact.sha256) == 64


# --------------------------------------------------------------------------
# Employer packages
# --------------------------------------------------------------------------


def test_packages_page_uses_readable_rows_without_ids() -> None:
    _package()
    client = logged_in_client()

    response = client.get(reverse("exports:employer_packages"))

    body = response.content.decode()
    assert "August 2026" in body
    assert "Employer GmbH" in body
    assert "Draft" in body  # status label, not the code
    assert "89.50" in body and "EUR" in body
    assert UUID_RE.search(_ordinary(body)) is None
    assert SHA256_RE.search(body) is None


def test_package_detail_readable_status_dates_totals_download() -> None:
    package = _package()
    client = logged_in_client()

    response = client.get(reverse("exports:package_detail", kwargs={"package_id": package.pk}))

    body = response.content.decode()
    assert "August 2026" in body
    assert "2026-08-01" in body and "2026-08-31" in body
    assert "89.50" in body and "EUR" in body
    assert "train ticket · €89.50 · travel" in body  # semantic event row
    assert "2026-08-04" in body  # event date, not an id
    assert "Draft" in body
    assert UUID_RE.search(_ordinary(body)) is None
    # The package hash may only appear inside a technical disclosure.
    hash_match = SHA256_RE.search(body)
    if hash_match:
        assert "data-technical-details" in body[: hash_match.start()]


def test_package_detail_status_history_is_readable() -> None:
    from apps.exports.packages import update_package_status

    package = _package()
    update_package_status(package, EmployerPackage.Status.SUBMITTED, note="Sent via portal")
    client = logged_in_client()

    response = client.get(reverse("exports:package_detail", kwargs={"package_id": package.pk}))

    body = response.content.decode()
    assert "Submitted" in body
    assert "Draft" in body  # from-status label
    assert "Sent via portal" in body
    # The status history uses readable labels, never raw status codes.
    assert "Draft → Submitted" in body
    assert "draft → submitted" not in body
    assert UUID_RE.search(_ordinary(body)) is None
