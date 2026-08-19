from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import Owner

ASCII_PIN = re.compile(r"[0-9]{4,}\Z")
DUMMY_PIN_HASH = make_password("000000", hasher="argon2")


@dataclass(frozen=True, slots=True)
class PinAuthenticationResult:
    authenticated: bool
    retry_after_seconds: int = 0


def validate_pin(pin: str) -> None:
    if ASCII_PIN.fullmatch(pin) is None:
        raise ValidationError("PIN must contain at least four ASCII digits.")


@transaction.atomic
def configure_pin(pin: str) -> Owner:
    validate_pin(pin)
    owner, _ = Owner.objects.select_for_update().get_or_create(
        pk=1,
        defaults={"pin_hash": make_password(pin, hasher="argon2")},
    )
    if not _:
        owner.pin_hash = make_password(pin, hasher="argon2")
        owner.failed_attempts = 0
        owner.next_attempt_at = None
        owner.save()
    return owner


@transaction.atomic
def authenticate_pin(pin: str) -> PinAuthenticationResult:
    now = timezone.now()
    owner = Owner.objects.select_for_update().filter(pk=1).first()
    stored_hash = owner.pin_hash if owner is not None else DUMMY_PIN_HASH

    if owner is not None and owner.next_attempt_at is not None and owner.next_attempt_at > now:
        remaining = max(1, int((owner.next_attempt_at - now).total_seconds() + 0.999))
        return PinAuthenticationResult(False, remaining)

    valid = check_password(pin, stored_hash)
    if owner is None:
        return PinAuthenticationResult(False, 1)

    if valid:
        owner.failed_attempts = 0
        owner.next_attempt_at = None
        owner.save(update_fields=["failed_attempts", "next_attempt_at", "updated_at"])
        return PinAuthenticationResult(True)

    owner.failed_attempts += 1
    delay_seconds = (
        900 if owner.failed_attempts >= 5 else min(2 ** (owner.failed_attempts - 1), 300)
    )
    owner.next_attempt_at = now + timedelta(seconds=delay_seconds)
    owner.save(update_fields=["failed_attempts", "next_attempt_at", "updated_at"])
    return PinAuthenticationResult(False, delay_seconds)


@transaction.atomic
def change_pin(current_pin: str, new_pin: str) -> bool:
    validate_pin(new_pin)
    result = authenticate_pin(current_pin)
    if not result.authenticated:
        return False
    configure_pin(new_pin)
    return True
