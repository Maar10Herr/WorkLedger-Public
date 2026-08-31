from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, ClassVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.ledger.models import Event, EventRevision
from apps.travel.models import Employer


def _safe_data_path(relative_path: str) -> Path:
    if not relative_path:
        raise ValueError("A stored artifact path is required")
    root = Path(settings.DATA_DIR).resolve()
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Stored artifact path escapes the data directory")
    return path


class ExportArtifact(models.Model):
    class Kind(models.TextChoices):
        XLSX = "xlsx", "Excel workbook"
        CSV = "csv", "CSV"
        JSON = "json", "JSON"
        SQLITE = "sqlite", "Portable SQLite"
        FULL_ZIP = "full_zip", "Complete ZIP"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    range_start = models.DateField()
    range_end = models.DateField()
    as_of = models.DateTimeField()
    relative_path = models.CharField(max_length=1000, unique=True)
    sha256 = models.CharField(max_length=64)
    size_bytes = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.kind}: {self.range_start} to {self.range_end}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Export records are immutable; generate a new export.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError("Export audit records cannot be deleted.")

    @property
    def path(self) -> Path:
        return _safe_data_path(self.relative_path)


class ExportJob(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETE = "complete", "Complete"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=20, choices=ExportArtifact.Kind.choices)
    range_start = models.DateField()
    range_end = models.DateField()
    as_of = models.DateTimeField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    artifact = models.OneToOneField(
        ExportArtifact, null=True, blank=True, on_delete=models.PROTECT, related_name="job"
    )
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.kind}: {self.status}"


class EmployerPackage(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        REIMBURSED = "reimbursed", "Reimbursed"
        REJECTED = "rejected", "Rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    employer = models.ForeignKey(Employer, null=True, blank=True, on_delete=models.PROTECT)
    period_start = models.DateField()
    period_end = models.DateField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    claim_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default="EUR")
    submitted_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    relative_package_path = models.CharField(max_length=1000, blank=True)
    package_sha256 = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    events: models.ManyToManyField[Event, Event] = models.ManyToManyField(
        Event, through="PackageEvent", related_name="employer_packages"
    )

    class Meta:
        ordering: ClassVar[list[str]] = ["-period_end", "name"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(period_end__gte=models.F("period_start")),
                name="employer_package_valid_period",
            )
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def package_path(self) -> Path:
        return _safe_data_path(self.relative_package_path)


class PackageEvent(models.Model):
    package = models.ForeignKey(
        EmployerPackage, on_delete=models.PROTECT, related_name="package_events"
    )
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="package_memberships")
    included_revision = models.ForeignKey(EventRevision, on_delete=models.PROTECT)
    claimed_amount = models.DecimalField(max_digits=12, decimal_places=2)
    inclusion_reason = models.CharField(max_length=200, default="Employer reimbursement")

    class Meta:
        ordering: ClassVar[list[str]] = ["event_id"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["package", "event"], name="unique_event_per_package")
        ]

    def __str__(self) -> str:
        return f"{self.package}: {self.event_id}"


class PackageStatusChange(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    package = models.ForeignKey(
        EmployerPackage, on_delete=models.PROTECT, related_name="status_changes"
    )
    status_event = models.OneToOneField(Event, on_delete=models.PROTECT)
    from_status = models.CharField(max_length=20, choices=EmployerPackage.Status.choices)
    to_status = models.CharField(max_length=20, choices=EmployerPackage.Status.choices)
    note = models.TextField(blank=True)
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["changed_at", "id"]

    def __str__(self) -> str:
        return f"{self.package}: {self.from_status} → {self.to_status}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Package status history is immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError("Package status history cannot be deleted.")
