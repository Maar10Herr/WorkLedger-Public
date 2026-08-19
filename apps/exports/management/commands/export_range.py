from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.exports.services import (
    _events_as_of,
    build_full_zip,
    build_range_csv,
    build_range_json,
    build_range_sqlite,
    build_range_xlsx,
)


class Command(BaseCommand):
    help = "Export a deterministic WorkLedger date range"

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--from", dest="start", required=True)
        parser.add_argument("--to", dest="end", required=True)
        parser.add_argument(
            "--purpose", choices=("complete", "tax", "employer"), default="complete"
        )
        parser.add_argument(
            "--format", choices=("xlsx", "csv", "json", "sqlite", "zip"), required=True
        )
        parser.add_argument("--output")

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            start = date.fromisoformat(options["start"])
            end = date.fromisoformat(options["end"])
        except ValueError as exc:
            raise CommandError("Dates must use YYYY-MM-DD") from exc
        if end < start:
            raise CommandError("--to must not precede --from")
        as_of = timezone.now()
        selected = _events_as_of(start, end, as_of)
        if options["purpose"] == "tax":
            selected = [
                row
                for row in selected
                if bool(
                    row.revision.snapshot.get("tax_relevant", row.event.tax_relevant)
                )
            ]
        elif options["purpose"] == "employer":
            selected = [
                row
                for row in selected
                if bool(
                    row.revision.snapshot.get(
                        "employer_reimbursable", row.event.employer_reimbursable
                    )
                )
            ]
        suffix = {"xlsx": "xlsx", "csv": "csv", "json": "json", "sqlite": "sqlite3", "zip": "zip"}[
            options["format"]
        ]
        destination = Path(
            options["output"]
            or Path(settings.DATA_DIR)
            / "exports"
            / f"workledger_{start}_{end}_{options['purpose']}.{suffix}"
        )
        builders = {
            "xlsx": build_range_xlsx,
            "csv": build_range_csv,
            "json": build_range_json,
            "sqlite": build_range_sqlite,
            "zip": build_full_zip,
        }
        builders[options["format"]](start, end, as_of, destination, selected)
        self.stdout.write(str(destination))
