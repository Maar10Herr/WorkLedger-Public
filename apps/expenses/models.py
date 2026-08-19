from __future__ import annotations

import uuid
from typing import Any, ClassVar

from django.db import models

from apps.ledger.models import Event


class ExpenseCategory(models.Model):
    code = models.SlugField(primary_key=True, max_length=50)
    name = models.CharField(max_length=100)
    parent = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="children"
    )
    active = models.BooleanField(default=True)

    def __str__(self) -> str:
        return self.name


class Expense(models.Model):
    class ReimbursementStatus(models.TextChoices):
        NOT_APPLICABLE = "not_applicable", "Not applicable"
        DRAFT = "draft", "Draft"
        READY = "ready", "Ready"
        SUBMITTED = "submitted", "Submitted"
        PARTIALLY_REIMBURSED = "partially_reimbursed", "Partially reimbursed"
        REIMBURSED = "reimbursed", "Reimbursed"
        REJECTED = "rejected", "Rejected"
        WITHDRAWN = "withdrawn", "Withdrawn"

    event = models.OneToOneField(
        Event,
        on_delete=models.PROTECT,
        primary_key=True,
        related_name="expense_identity",
    )
    category = models.ForeignKey(
        ExpenseCategory, on_delete=models.PROTECT, related_name="expenses"
    )
    reimbursement_status = models.CharField(
        max_length=20,
        choices=ReimbursementStatus.choices,
        default=ReimbursementStatus.NOT_APPLICABLE,
    )
    reimbursed_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.category}: {self.event_id}"


class ReimbursementStatusChange(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    expense = models.ForeignKey(
        Expense, on_delete=models.PROTECT, related_name="reimbursement_history"
    )
    previous_status = models.CharField(max_length=20, blank=True)
    new_status = models.CharField(max_length=24, choices=Expense.ReimbursementStatus.choices)
    previous_reimbursed_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0
    )
    new_reimbursed_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    changed_at = models.DateTimeField(auto_now_add=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering: ClassVar[list[str]] = ["changed_at", "id"]

    def __str__(self) -> str:
        return f"{self.expense_id}: {self.new_status}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValueError("Reimbursement status history is immutable")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValueError("Reimbursement status history is immutable")
