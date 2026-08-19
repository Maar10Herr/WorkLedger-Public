from __future__ import annotations

from datetime import date

from django.core.exceptions import ValidationError
from django.http import FileResponse, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_GET, require_http_methods

from apps.accounts.decorators import owner_login_required
from apps.ledger.models import Event
from apps.ledger.presenters import present

from .models import EmployerPackage, ExportArtifact, ExportJob
from .packages import create_package, generate_package_zip, update_package_status
from .tasks import generate_export_job


@owner_login_required
@require_http_methods(["GET", "POST"])
def create_export(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        start = parse_date(request.POST.get("start", ""))
        end = parse_date(request.POST.get("end", ""))
        kind = request.POST.get("kind", "xlsx")
        if start is None or end is None or start > end or kind not in ExportArtifact.Kind.values:
            return render(
                request,
                "exports/create.html",
                {
                    "error": "Choose a valid date range and format.",
                    "artifacts": ExportArtifact.objects.all(),
                },
                status=400,
            )
        job = ExportJob.objects.create(
            range_start=start, range_end=end, kind=kind, as_of=timezone.now()
        )
        generate_export_job.delay(str(job.pk))
        job.refresh_from_db()
        if job.artifact_id:
            return redirect("exports:download_export", export_id=job.artifact_id)
        return redirect("exports:export_job", job_id=job.pk)
    today = date.today()
    return render(
        request,
        "exports/create.html",
        {
            "today": today,
            "artifacts": ExportArtifact.objects.all()[:20],
            "jobs": ExportJob.objects.select_related("artifact")[:10],
        },
    )


@owner_login_required
@require_GET
def download_export(request: HttpRequest, export_id: object) -> FileResponse:
    artifact = get_object_or_404(ExportArtifact, pk=export_id)
    response = FileResponse(
        artifact.path.open("rb"), as_attachment=True, filename=artifact.path.name
    )
    response["X-Content-SHA256"] = artifact.sha256
    return response


@owner_login_required
@require_GET
def export_job(request: HttpRequest, job_id: object) -> HttpResponse:
    job = get_object_or_404(ExportJob, pk=job_id)
    return render(request, "exports/job.html", {"job": job})


@owner_login_required
@require_http_methods(["GET", "POST"])
def employer_packages(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        start = parse_date(request.POST.get("period_start", ""))
        end = parse_date(request.POST.get("period_end", ""))
        name = request.POST.get("name", "").strip()
        if not name or start is None or end is None or start > end:
            return HttpResponse("Invalid package details", status=400)
        try:
            package = create_package(
                name=name,
                period_start=start,
                period_end=end,
                event_ids=request.POST.getlist("event_ids"),
            )
        except ValidationError as exc:
            return HttpResponse(exc.message, status=400)
        return redirect("exports:package_detail", package_id=package.pk)
    candidates = Event.objects.filter(employer_reimbursable=True).select_related(
        "current_revision"
    )
    candidate_rows = []
    for event in candidates:
        revision = event.current_revision
        summary = present(event)
        candidate_rows.append(
            {
                "event_id": str(event.pk),
                "title": summary.title,
                "meta": summary.meta,
                "date": revision.effective_at.date() if revision is not None else None,
            }
        )
    return render(
        request,
        "exports/packages.html",
        {
            "packages": EmployerPackage.objects.all(),
            "candidates": candidate_rows,
        },
    )


@owner_login_required
@require_http_methods(["GET", "POST"])
def package_detail(request: HttpRequest, package_id: object) -> HttpResponse:
    package = get_object_or_404(
        EmployerPackage.objects.prefetch_related("package_events__event", "status_changes"),
        pk=package_id,
    )
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "generate":
            generate_package_zip(package)
        elif action in EmployerPackage.Status.values:
            update_package_status(package, action, note=request.POST.get("note", ""))
        return redirect("exports:package_detail", package_id=package.pk)
    event_rows = []
    for item in package.package_events.select_related("event", "event__current_revision"):
        summary = present(item.event)
        revision = item.event.current_revision
        event_rows.append(
            {
                "title": summary.title,
                "meta": summary.meta,
                "date": revision.effective_at.date() if revision is not None else None,
                "claimed_amount": item.claimed_amount,
            }
        )
    return render(
        request,
        "exports/package_detail.html",
        {"package": package, "event_rows": event_rows},
    )


@owner_login_required
@require_GET
def download_package(request: HttpRequest, package_id: object) -> FileResponse:
    package = get_object_or_404(EmployerPackage, pk=package_id)
    if not package.relative_package_path or not package.package_path.exists():
        generate_package_zip(package)
    response = FileResponse(
        package.package_path.open("rb"), as_attachment=True, filename=package.package_path.name
    )
    response["X-Content-SHA256"] = package.package_sha256
    return response
