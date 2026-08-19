from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.ledger.models import Event
from apps.ledger.services import create_event, revise_event

from .models import Attachment, AttachmentLink

FORMAT_BY_SIGNATURE = (
    (b"\xff\xd8\xff", "JPEG", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "PNG", "image/png"),
    (b"RIFF", "WEBP", "image/webp"),
    (b"II*\x00", "TIFF", "image/tiff"),
    (b"MM\x00*", "TIFF", "image/tiff"),
    (b"%PDF-", "PDF", "application/pdf"),
)


def _detect_format(path: Path, first_bytes: bytes) -> tuple[str, str]:
    for signature, image_format, mime_type in FORMAT_BY_SIGNATURE:
        if first_bytes.startswith(signature):
            if image_format == "WEBP" and first_bytes[8:12] != b"WEBP":
                continue
            return image_format, mime_type
    if len(first_bytes) >= 12 and first_bytes[4:8] == b"ftyp":
        brand = first_bytes[8:12].lower()
        if brand in {b"heic", b"heix", b"hevc", b"hevx"}:
            return "HEIC", "image/heic"
        if brand in {b"mif1", b"msf1", b"heif"}:
            return "HEIF", "image/heif"
    try:
        import magic

        mime_type = magic.from_file(str(path), mime=True) or "application/octet-stream"
    except ImportError:
        mime_type = "application/octet-stream"
    message = f"Unsupported attachment format ({mime_type}). Original was not accepted."
    raise ValidationError(message)


@transaction.atomic
def store_attachment(upload: UploadedFile) -> Attachment:
    uploaded_at = timezone.now()
    month = uploaded_at.strftime("%Y/%m")
    attachment_id = Attachment._meta.pk.get_default()
    upload_name = upload.name or "upload.bin"
    suffix = Path(upload_name).suffix.lower()[:12] or ".bin"
    relative_path = Path(month) / "originals" / f"{attachment_id}{suffix}"
    destination = Path(settings.DATA_DIR) / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)

    free_bytes = shutil.disk_usage(destination.parent).free
    if free_bytes < settings.WORKLEDGER_MIN_FREE_DISK_BYTES:
        raise ValidationError("Insufficient free disk space for attachment upload.")

    digest = hashlib.sha256()
    size = 0
    first_bytes = bytearray()
    try:
        with destination.open("xb") as output:
            for chunk in upload.chunks():
                size += len(chunk)
                if size > settings.WORKLEDGER_MAX_UPLOAD_BYTES:
                    raise ValidationError("Attachment exceeds the configured emergency ceiling.")
                digest.update(chunk)
                if len(first_bytes) < 64:
                    first_bytes.extend(chunk[: 64 - len(first_bytes)])
                output.write(chunk)
        detected_format, detected_mime = _detect_format(destination, bytes(first_bytes))
        digest_value = digest.hexdigest()
        existing = Attachment.objects.filter(sha256=digest_value, size_bytes=size).first()
        if existing is not None:
            destination.unlink(missing_ok=True)
            return existing
        try:
            with transaction.atomic():
                attachment = Attachment.objects.create(
                    id=attachment_id,
                    original_filename=Path(upload_name).name,
                    relative_original_path=relative_path.as_posix(),
                    supplied_content_type=upload.content_type or "",
                    detected_mime_type=detected_mime,
                    detected_format=detected_format,
                    size_bytes=size,
                    sha256=digest_value,
                    uploaded_at=uploaded_at,
                )
        except IntegrityError:
            destination.unlink(missing_ok=True)
            return Attachment.objects.get(sha256=digest_value, size_bytes=size)
        upload_event = create_event(
            event_type="attachment_upload",
            effective_at=attachment.uploaded_at,
            snapshot={
                "attachment_id": str(attachment.pk),
                "original_filename": attachment.original_filename,
                "media_type": attachment.detected_mime_type,
                "size_bytes": attachment.size_bytes,
                "sha256": attachment.sha256,
            },
            complete=True,
        )
        AttachmentLink.objects.create(
            attachment=attachment, event=upload_event, link_type="upload_audit"
        )
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    from .tasks import generate_previews

    transaction.on_commit(lambda: generate_previews.delay(str(attachment.pk)))
    return attachment


def receipt_display_name(name: str, note: str, original_filename: str, *, limit: int = 120) -> str:
    """Return a human label without changing immutable attachment metadata."""
    candidates = [name.strip(), note.strip(), Path(original_filename).stem.strip(), "receipt"]
    for candidate in candidates:
        cleaned = re.sub(r"^IMG[_ -]*", "", candidate, flags=re.IGNORECASE).strip()
        if cleaned:
            return cleaned[:limit]
    return "receipt"


def _expense_is_complete(snapshot: dict[str, Any]) -> bool:
    from apps.expenses.services import expense_completeness

    return expense_completeness(snapshot)


@transaction.atomic
def reconcile_receipt(receipt_event: Event, target_event: Event) -> None:
    revision = receipt_event.current_revision
    target_revision = target_event.current_revision
    if receipt_event.event_type != "receipt_only" or revision is None:
        raise ValidationError("Only receipt inbox events can be reconciled")
    if target_event.event_type not in {"expense", "journey", "external_activity"}:
        raise ValidationError("Receipt target must be an expense, journey, or external activity")
    if target_revision is None or target_revision.deleted:
        raise ValidationError("Receipt target is deleted or unavailable")
    if revision.snapshot.get("reconciliation_status", "unmatched") == "matched":
        raise ValidationError("Receipt is already linked")
    links = list(receipt_event.attachment_links.select_related("attachment"))
    if not links:
        raise ValidationError("Receipt has no attachment to link")
    for link in links:
        AttachmentLink.objects.get_or_create(
            attachment=link.attachment,
            event=target_event,
            link_type="reconciled_receipt",
        )
    snapshot = dict(revision.snapshot)
    snapshot.update(
        {
            "reconciliation_status": "matched",
            "reconciled_to_event_id": str(target_event.pk),
        }
    )
    revise_event(
        event=receipt_event,
        effective_at=revision.effective_at,
        snapshot=snapshot,
        comment=f"Linked receipt: {snapshot.get('display_name') or 'receipt'}",
        complete=True,
    )
    if target_event.event_type == "expense":
        target_revision = target_event.current_revision
        assert target_revision is not None
        expense_snapshot = dict(target_revision.snapshot)
        expense_snapshot["documentation_status"] = "attached"
        revise_event(
            event=target_event,
            effective_at=target_revision.effective_at,
            snapshot=expense_snapshot,
            complete=_expense_is_complete(expense_snapshot),
            comment=f"linked existing receipt: {snapshot.get('display_name') or 'receipt'}",
        )
