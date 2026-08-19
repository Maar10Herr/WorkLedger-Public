from datetime import timedelta

import pytest
from django.contrib.auth.hashers import identify_hasher
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import Owner
from apps.accounts.services import authenticate_pin, configure_pin

pytestmark = pytest.mark.django_db


def test_pin_must_be_at_least_four_numeric_digits() -> None:
    configure_pin("1234")
    for invalid in ("123", "123x", "", "１２３４"):  # noqa: RUF001
        with pytest.raises(ValidationError):
            configure_pin(invalid)


def test_pin_is_stored_only_as_argon2id_hash() -> None:
    configure_pin("123456")

    owner = Owner.objects.get()
    assert "123456" not in owner.pin_hash
    assert identify_hasher(owner.pin_hash).algorithm == "argon2"
    assert "$argon2id$" in owner.pin_hash


def test_correct_pin_authenticates_and_resets_failures() -> None:
    configure_pin("123456")
    owner = Owner.objects.get()
    owner.failed_attempts = 2
    owner.next_attempt_at = timezone.now() - timedelta(seconds=1)
    owner.save(update_fields=["failed_attempts", "next_attempt_at", "updated_at"])

    result = authenticate_pin("123456")

    owner.refresh_from_db()
    assert result.authenticated is True
    assert owner.failed_attempts == 0
    assert owner.next_attempt_at is None


def test_wrong_pin_enforces_escalating_server_side_delay() -> None:
    configure_pin("123456")

    first = authenticate_pin("000000")
    Owner.objects.update(next_attempt_at=timezone.now() - timedelta(seconds=1))
    second = authenticate_pin("000000")

    assert first.authenticated is False
    assert first.retry_after_seconds >= 1
    assert second.retry_after_seconds > first.retry_after_seconds
