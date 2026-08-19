from __future__ import annotations

from celery import shared_task  # type: ignore[import-untyped]
from django.utils import timezone

from .models import ExportJob
from .services import generate_export


@shared_task  # type: ignore[untyped-decorator]
def generate_export_job(job_id: str) -> None:
    job = ExportJob.objects.get(pk=job_id)
    job.status = ExportJob.Status.RUNNING
    job.started_at = timezone.now()
    job.save(update_fields=["status", "started_at"])
    try:
        artifact = generate_export(
            start=job.range_start,
            end=job.range_end,
            kind=job.kind,
            as_of=job.as_of,
        )
    except Exception as exc:
        job.status = ExportJob.Status.FAILED
        job.error = f"{type(exc).__name__}: {exc}"[:2000]
        job.completed_at = timezone.now()
        job.save(update_fields=["status", "error", "completed_at"])
        raise
    job.status = ExportJob.Status.COMPLETE
    job.artifact = artifact
    job.completed_at = timezone.now()
    job.save(update_fields=["status", "artifact", "completed_at"])
