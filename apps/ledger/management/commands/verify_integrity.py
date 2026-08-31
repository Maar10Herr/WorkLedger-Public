from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.evidence.models import Attachment
from apps.ledger.models import Event
from apps.ledger.services import verify_audit_chain


class Command(BaseCommand):
    help = "Verify audit chain, current revision pointers, and attachment originals"

    def handle(self, *args: Any, **options: Any) -> None:
        audit = verify_audit_chain()
        if not audit.valid:
            raise CommandError(f"Audit chain broken at {audit.broken_revision_id}")
        for event in Event.objects.select_related("current_revision"):
            revision = event.current_revision
            if revision is None or revision.event_id != event.pk:
                raise CommandError(f"Invalid current revision pointer for event {event.pk}")
            latest = event.revisions.order_by("-revision_number").first()
            if latest is None or latest.pk != revision.pk:
                raise CommandError(f"Current revision is not latest for event {event.pk}")
        root = settings.DATA_DIR.resolve()
        for attachment in Attachment.objects.iterator():
            try:
                path = attachment.original_path
            except ValueError as exc:
                raise CommandError(f"Unsafe attachment path {attachment.pk}") from exc
            if not path.is_relative_to(root) or not path.is_file():
                raise CommandError(f"Missing attachment original {attachment.pk}")
            digest = hashlib.sha256()
            size = 0
            with Path(path).open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
                    size += len(chunk)
            if digest.hexdigest() != attachment.sha256 or size != attachment.size_bytes:
                raise CommandError(f"Attachment hash/size mismatch {attachment.pk}")
        self.stdout.write(
            self.style.SUCCESS(
                f"Integrity verified: {audit.checked_revisions} revisions, "
                f"{Attachment.objects.count()} attachments"
            )
        )
