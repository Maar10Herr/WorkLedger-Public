from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.utils import timezone

from .models import Event, EventRevision, EventType

AUDIT_LOCK_ID = 9_218_771_344


@dataclass(frozen=True, slots=True)
class AuditVerificationResult:
    valid: bool
    checked_revisions: int
    broken_revision_id: object | None = None


def _canonical_payload(revision: EventRevision) -> bytes:
    payload = {
        "revision_id": str(revision.pk),
        "event_id": str(revision.event_id),
        "parent_revision_id": str(revision.parent_revision_id)
        if revision.parent_revision_id
        else None,
        "revision_number": revision.revision_number,
        "effective_at": revision.effective_at.isoformat(timespec="microseconds"),
        "recorded_at": revision.recorded_at.isoformat(timespec="microseconds"),
        "snapshot": revision.snapshot,
        "complete": revision.complete,
        "deleted": revision.deleted,
        "comment": revision.comment,
        "previous_audit_hash": revision.previous_audit_hash,
    }
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def _hash_revision(revision: EventRevision) -> str:
    return hashlib.sha256(_canonical_payload(revision)).hexdigest()


def _lock_audit_chain() -> None:
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [AUDIT_LOCK_ID])


def _validate_event_type(event_type: str) -> None:
    if event_type not in EventType.values:
        raise ValidationError({"event_type": "Unsupported event type."})


@transaction.atomic
def create_event(
    *,
    event_type: str,
    effective_at: datetime,
    snapshot: dict[str, Any],
    complete: bool = True,
    comment: str = "",
    tax_relevant: bool = False,
    employer_reimbursable: bool = False,
) -> Event:
    _validate_event_type(event_type)
    revision_snapshot = dict(snapshot)
    revision_snapshot["tax_relevant"] = tax_relevant
    revision_snapshot["employer_reimbursable"] = employer_reimbursable
    event = Event.objects.create(
        event_type=event_type,
        tax_relevant=tax_relevant,
        employer_reimbursable=employer_reimbursable,
    )
    revision = _append_revision(
        event=event,
        parent=None,
        effective_at=effective_at,
        snapshot=revision_snapshot,
        complete=complete,
        deleted=False,
        comment=comment,
    )
    Event.objects.filter(pk=event.pk).update(current_revision=revision)
    event.current_revision = revision
    return event


@transaction.atomic
def revise_event(
    *,
    event: Event,
    effective_at: datetime,
    snapshot: dict[str, Any],
    complete: bool,
    comment: str = "",
    deleted: bool = False,
) -> EventRevision:
    locked_event = Event.objects.select_for_update().get(pk=event.pk)
    if locked_event.current_revision is None:
        raise ValidationError("Event has no current revision.")
    tax_relevant = bool(snapshot.get("tax_relevant", locked_event.tax_relevant))
    employer_reimbursable = bool(
        snapshot.get("employer_reimbursable", locked_event.employer_reimbursable)
    )
    revision_snapshot = dict(snapshot)
    revision_snapshot["tax_relevant"] = tax_relevant
    revision_snapshot["employer_reimbursable"] = employer_reimbursable
    revision = _append_revision(
        event=locked_event,
        parent=locked_event.current_revision,
        effective_at=effective_at,
        snapshot=revision_snapshot,
        complete=complete,
        deleted=deleted,
        comment=comment,
    )
    Event.objects.filter(pk=event.pk).update(
        current_revision=revision,
        tax_relevant=tax_relevant,
        employer_reimbursable=employer_reimbursable,
    )
    event.current_revision = revision
    event.tax_relevant = tax_relevant
    event.employer_reimbursable = employer_reimbursable
    return revision


def _append_revision(
    *,
    event: Event,
    parent: EventRevision | None,
    effective_at: datetime,
    snapshot: dict[str, Any],
    complete: bool,
    deleted: bool,
    comment: str,
) -> EventRevision:
    _lock_audit_chain()
    previous = EventRevision.objects.order_by("-recorded_at", "-id").first()
    revision = EventRevision(
        event=event,
        parent_revision=parent,
        revision_number=1 if parent is None else parent.revision_number + 1,
        effective_at=effective_at,
        recorded_at=timezone.now(),
        snapshot=snapshot,
        complete=complete,
        deleted=deleted,
        comment=comment,
        previous_audit_hash=previous.audit_hash if previous is not None else "",
        audit_hash="0" * 64,
    )
    revision.audit_hash = _hash_revision(revision)
    revision.save(force_insert=True)
    return revision


def verify_audit_chain() -> AuditVerificationResult:
    previous_hash = ""
    checked = 0
    for revision in EventRevision.objects.order_by("recorded_at", "id").iterator():
        checked += 1
        if revision.previous_audit_hash != previous_hash or revision.audit_hash != _hash_revision(
            revision
        ):
            return AuditVerificationResult(False, checked, revision.pk)
        previous_hash = revision.audit_hash
    return AuditVerificationResult(True, checked)
