from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path
from typing import Any

import pillow_heif  # type: ignore[import-untyped]
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.evidence.services import store_attachment
from apps.evidence.tasks import generate_previews

pytestmark = pytest.mark.django_db


def make_heic_bytes() -> bytes:
    image = Image.new("RGB", (32, 24), (20, 120, 80))
    heif = pillow_heif.from_pillow(image)
    output = BytesIO()
    heif.save(output)
    return output.getvalue()


def test_heic_original_is_byte_identical_and_preview_is_created(
    settings: Any, tmp_path: Path
) -> None:
    settings.DATA_DIR = tmp_path
    original = make_heic_bytes()
    upload = SimpleUploadedFile("ticket.heic", original, content_type="image/heic")

    attachment = store_attachment(upload)
    preview_result = generate_previews(str(attachment.pk))

    attachment.refresh_from_db()
    stored_path = attachment.original_path
    with stored_path.open("rb") as stored:
        assert stored.read() == original
    assert attachment.sha256 == hashlib.sha256(original).hexdigest()
    assert attachment.detected_format in {"HEIC", "HEIF"}
    assert preview_result == "ready"
    assert attachment.preview_path.exists()
    assert attachment.thumbnail_path.exists()


def test_tiff_original_is_preserved_and_jpeg_preview_is_created(
    settings: Any, tmp_path: Path
) -> None:
    settings.DATA_DIR = tmp_path
    output = BytesIO()
    Image.new("RGB", (40, 30), (100, 50, 20)).save(output, format="TIFF")
    original = output.getvalue()
    attachment = store_attachment(
        SimpleUploadedFile("scan.tiff", original, content_type="image/tiff")
    )

    assert generate_previews(str(attachment.pk)) == "ready"
    attachment.refresh_from_db()
    assert attachment.original_path.read_bytes() == original
    assert attachment.detected_format == "TIFF"
    assert attachment.preview_path.suffix == ".jpg"
    assert attachment.preview_path.exists()
