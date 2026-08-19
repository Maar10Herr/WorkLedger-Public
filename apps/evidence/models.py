from __future__ import annotations

import uuid
from pathlib import Path
from typing import ClassVar

from django.conf import settings
from django.db import models

from apps.ledger.models import Event


class PreviewStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    READY = "ready", "Ready"
    FAILED = "failed", "Failed"


class Attachment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    original_filename = models.CharField(max_length=500)
    relative_original_path = models.CharField(max_length=1000, unique=True)
    relative_preview_path = models.CharField(max_length=1000, blank=True)
    relative_thumbnail_path = models.CharField(max_length=1000, blank=True)
    supplied_content_type = models.CharField(max_length=255, blank=True)
    detected_mime_type = models.CharField(max_length=255)
    detected_format = models.CharField(max_length=20)
    size_bytes = models.BigIntegerField()
    sha256 = models.CharField(max_length=64, db_index=True)
    uploaded_at = models.DateTimeField()
    preview_status = models.CharField(
        max_length=20, choices=PreviewStatus.choices, default=PreviewStatus.PENDING
    )
    preview_error = models.TextField(blank=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-uploaded_at"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["sha256", "size_bytes"], name="unique_attachment_bytes")
        ]

    def __str__(self) -> str:
        return self.original_filename

    @property
    def original_path(self) -> Path:
        return Path(settings.DATA_DIR) / self.relative_original_path

    @property
    def preview_path(self) -> Path:
        return Path(settings.DATA_DIR) / self.relative_preview_path

    @property
    def thumbnail_path(self) -> Path:
        return Path(settings.DATA_DIR) / self.relative_thumbnail_path

class AttachmentLink(models.Model):
    attachment = models.ForeignKey(Attachment, on_delete=models.PROTECT, related_name="links")
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="attachment_links")
    link_type = models.CharField(max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["attachment", "event", "link_type"], name="unique_attachment_link"
            )
        ]

    def __str__(self) -> str:
        return f"{self.attachment_id} → {self.event_id}"
