from datetime import date
from pathlib import Path
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.services import configure_pin
from apps.exports.models import ExportArtifact

pytestmark = pytest.mark.django_db


def test_export_request_persists_hash_and_downloads(settings: Any, tmp_path: Path) -> None:
    settings.DATA_DIR = tmp_path
    configure_pin("123456")
    client = Client()
    client.post(reverse("accounts:login"), {"pin": "123456"})

    response = client.post(
        reverse("exports:create_export"),
        {
            "start": date(2026, 8, 1).isoformat(),
            "end": date(2026, 8, 31).isoformat(),
            "kind": "xlsx",
        },
    )

    artifact = ExportArtifact.objects.get()
    assert response.status_code == 302
    location = reverse("exports:download_export", kwargs={"export_id": artifact.pk})
    assert response.headers["Location"] == location
    assert len(artifact.sha256) == 64
    download = client.get(location)
    assert download.status_code == 200
    assert "attachment;" in download.headers["Content-Disposition"]


def test_export_download_rejects_paths_outside_data_dir(settings: Any, tmp_path: Path) -> None:
    settings.DATA_DIR = tmp_path / "data"
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"must not be served")
    configure_pin("123456")
    client = Client()
    client.post(reverse("accounts:login"), {"pin": "123456"})
    artifact = ExportArtifact.objects.create(
        kind=ExportArtifact.Kind.JSON,
        range_start=date(2026, 8, 1),
        range_end=date(2026, 8, 31),
        as_of=timezone.now(),
        relative_path="../outside.bin",
        sha256="a" * 64,
        size_bytes=outside.stat().st_size,
    )

    response = client.get(
        reverse("exports:download_export", kwargs={"export_id": artifact.pk})
    )

    assert response.status_code == 404
