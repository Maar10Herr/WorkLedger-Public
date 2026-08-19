from __future__ import annotations

import uuid
from typing import Any, ClassVar

from django.core.exceptions import ValidationError
from django.db import models

from apps.ledger.models import Event, EventRevision
from apps.travel.models import Location


class TaxRule(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=100, unique=True)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    jurisdiction = models.CharField(max_length=10)
    rule_type = models.CharField(max_length=50)
    values = models.JSONField(default=dict)
    source_url = models.URLField(max_length=1000)
    source_citation = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["effective_from", "code"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(effective_to__isnull=True)
                | models.Q(effective_to__gte=models.F("effective_from")),
                name="tax_rule_dates_ordered",
            )
        ]

    def __str__(self) -> str:
        return self.code

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Tax rules are versioned; create a new rule row.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError("Tax rule versions cannot be deleted.")


class PerDiemCalculation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    activity_event = models.ForeignKey(Event, on_delete=models.PROTECT)
    input_revision = models.ForeignKey(EventRevision, on_delete=models.PROTECT)
    rule_codes = models.JSONField(default=list)
    daily_amounts = models.JSONField(default=dict)
    total = models.DecimalField(max_digits=12, decimal_places=2)
    complete = models.BooleanField()
    missing_facts = models.JSONField(default=list)
    generated_at = models.DateTimeField(auto_now_add=True)
    derivation_hash = models.CharField(max_length=64, unique=True)

    def __str__(self) -> str:
        return f"Per diem calculation {self.pk}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Tax derivations are immutable; create a new calculation.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError("Tax derivations cannot be deleted.")


class RouteDistance(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    origin = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="route_origins")
    destination = models.ForeignKey(
        Location, on_delete=models.PROTECT, related_name="route_destinations"
    )
    mode = models.CharField(max_length=30)
    distance_km = models.DecimalField(max_digits=10, decimal_places=2)
    version = models.PositiveIntegerField()
    source = models.CharField(max_length=50)
    source_url = models.URLField(blank=True)
    provider_input = models.JSONField(default=dict)
    provider_response = models.JSONField(default=dict)
    raw_response_hash = models.CharField(max_length=64, blank=True)
    returned_metres = models.PositiveBigIntegerField(null=True, blank=True)
    full_tax_km = models.PositiveIntegerField(null=True, blank=True)
    calculation_date = models.DateField()
    manual_override = models.BooleanField(default=False)
    override_comment = models.TextField(max_length=500, blank=True)
    confirmed = models.BooleanField(default=False)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["origin_id", "destination_id", "mode", "version"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["origin", "destination", "mode", "version"],
                name="unique_route_distance_version",
            ),
            models.CheckConstraint(
                condition=models.Q(distance_km__gte=0), name="route_distance_nonnegative"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.origin} → {self.destination}: {self.distance_km} km v{self.version}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError("Route distances are versioned; create a new row.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError("Route distance versions cannot be deleted.")
