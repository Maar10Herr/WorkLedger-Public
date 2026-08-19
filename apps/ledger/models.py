from __future__ import annotations

import uuid
from typing import Any, ClassVar

from django.core.exceptions import ValidationError
from django.db import models


class EventType(models.TextChoices):
    WORK_FROM_HOME = "work_from_home", "Work from home"
    JOURNEY = "journey", "Journey"
    WORK_LOCATION = "work_location", "Work location"
    EXTERNAL_ACTIVITY = "external_activity", "External activity"
    EXPENSE = "expense", "Expense"
    RECEIPT_ONLY = "receipt_only", "Receipt only"
    ATTACHMENT_UPLOAD = "attachment_upload", "Attachment upload"
    REIMBURSEMENT_UPDATE = "reimbursement_update", "Reimbursement update"
    NOTE = "note", "Note"


class Event(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event_type = models.CharField(max_length=32, choices=EventType.choices)
    tax_relevant = models.BooleanField(default=False, db_index=True)
    employer_reimbursable = models.BooleanField(default=False, db_index=True)
    current_revision = models.ForeignKey(
        "EventRevision",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="current_for_events",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.event_type} · {self.pk}"


class EventRevision(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="revisions")
    parent_revision = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="child_revisions",
    )
    revision_number = models.PositiveIntegerField()
    effective_at = models.DateTimeField()
    recorded_at = models.DateTimeField()
    snapshot = models.JSONField(default=dict)
    complete = models.BooleanField(default=False)
    deleted = models.BooleanField(default=False)
    comment = models.TextField(blank=True)
    previous_audit_hash = models.CharField(max_length=64, blank=True)
    audit_hash = models.CharField(max_length=64, unique=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["recorded_at", "id"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["event", "revision_number"], name="unique_event_revision_number"
            ),
            models.CheckConstraint(
                condition=models.Q(revision_number__gte=1), name="revision_number_positive"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.event_id} revision {self.revision_number}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Event revisions are immutable; create a new revision.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError("Event revisions cannot be deleted.")
