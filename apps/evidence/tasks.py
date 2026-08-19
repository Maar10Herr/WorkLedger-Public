from __future__ import annotations

from pathlib import Path

import pillow_heif  # type: ignore[import-untyped]
import pypdfium2 as pdfium  # type: ignore[import-untyped]
from celery import shared_task  # type: ignore[import-untyped]
from PIL import Image, ImageOps

from .models import Attachment, PreviewStatus

pillow_heif.register_heif_opener()


def _source_image(attachment: Attachment) -> Image.Image:
    if attachment.detected_format == "PDF":
        document = pdfium.PdfDocument(str(attachment.original_path))
        if len(document) == 0:
            raise ValueError("PDF has no pages")
        bitmap = document[0].render(scale=2)
        image: Image.Image = bitmap.to_pil()
        return image
    image = Image.open(attachment.original_path)
    image.load()
    return image


@shared_task(  # type: ignore[untyped-decorator]
    bind=True,
    autoretry_for=(OSError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def generate_previews(self: object, attachment_id: str) -> str:
    attachment = Attachment.objects.get(pk=attachment_id)
    month = attachment.uploaded_at.strftime("%Y/%m")
    preview_relative = Path(month) / "previews" / f"{attachment.pk}.jpg"
    thumbnail_relative = Path(month) / "thumbnails" / f"{attachment.pk}.jpg"
    preview_path = attachment.original_path.parents[3] / preview_relative
    thumbnail_path = attachment.original_path.parents[3] / thumbnail_relative
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with _source_image(attachment) as source:
            oriented = ImageOps.exif_transpose(source).convert("RGB")
            preview = oriented.copy()
            preview.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
            preview.save(preview_path, format="JPEG", quality=88, optimize=True)
            thumbnail = oriented.copy()
            thumbnail.thumbnail((360, 360), Image.Resampling.LANCZOS)
            thumbnail.save(thumbnail_path, format="JPEG", quality=82, optimize=True)
        Attachment.objects.filter(pk=attachment.pk).update(
            relative_preview_path=preview_relative.as_posix(),
            relative_thumbnail_path=thumbnail_relative.as_posix(),
            preview_status=PreviewStatus.READY,
            preview_error="",
        )
        return "ready"
    except Exception as exc:
        Attachment.objects.filter(pk=attachment.pk).update(
            preview_status=PreviewStatus.FAILED,
            preview_error=str(exc)[:1000],
        )
        return "failed"
