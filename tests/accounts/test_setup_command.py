import pytest
from django.core.management import call_command

from apps.accounts.models import Owner
from apps.accounts.services import authenticate_pin

pytestmark = pytest.mark.django_db


def test_setup_owner_reads_pin_without_command_line_argument(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pins = iter(["123456", "123456"])
    monkeypatch.setattr(
        "apps.accounts.management.commands.setup_owner.getpass",
        lambda _prompt: next(pins),
    )

    call_command("setup_owner")

    assert Owner.objects.count() == 1
    assert authenticate_pin("123456").authenticated is True
