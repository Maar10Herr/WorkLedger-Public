from __future__ import annotations

from django.db import models


class Owner(models.Model):
    """The single WorkLedger owner; plaintext PINs never reach this model."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    pin_hash = models.CharField(max_length=512)
    failed_attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "owner"

    def __str__(self) -> str:
        return "WorkLedger owner"
