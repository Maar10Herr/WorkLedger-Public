from __future__ import annotations

from getpass import getpass
from typing import Any

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.accounts.models import Owner
from apps.accounts.services import configure_pin


class Command(BaseCommand):
    help = "Configure the single WorkLedger owner PIN using a hidden terminal prompt."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--force",
            action="store_true",
            help="Replace an existing owner PIN after an explicit confirmation prompt.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if Owner.objects.exists() and not options["force"]:
            raise CommandError("Owner is already configured. Use --force to replace the PIN.")
        pin = getpass("New PIN (at least four digits): ")
        confirmation = getpass("Repeat new PIN: ")
        if pin != confirmation:
            raise CommandError("PIN entries did not match.")
        try:
            configure_pin(pin)
        except ValidationError as exc:
            raise CommandError("PIN must contain at least four ASCII digits.") from exc
        self.stdout.write(self.style.SUCCESS("WorkLedger owner PIN configured."))
