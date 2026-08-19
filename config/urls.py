from pathlib import Path

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import include, path

from apps.accounts.decorators import owner_login_required


def health(_request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})


def service_worker(_request: HttpRequest) -> HttpResponse:
    response = HttpResponse(
        (Path(settings.BASE_DIR) / "static" / "sw.js").read_text(encoding="utf-8"),
        content_type="application/javascript",
    )
    response["Cache-Control"] = "no-cache"
    return response


@owner_login_required
def home(request: HttpRequest) -> HttpResponse:
    from apps.travel.models import Employer, Location, LocationType

    return render(
        request,
        "home.html",
        {
            "setup_residence": not Location.objects.filter(
                location_type=LocationType.RESIDENCE
            ).exists(),
            "setup_employer": not Employer.objects.exists(),
        },
    )


urlpatterns = [
    path("health/", health, name="health"),
    path("sw.js", service_worker, name="service_worker"),
    path("accounts/", include("apps.accounts.urls")),
    path("", include("apps.ledger.urls")),
    path("", include("apps.travel.urls")),
    path("", include("apps.evidence.urls")),
    path("", include("apps.expenses.urls")),
    path("", include("apps.exports.urls")),
    path("", home, name="home"),
]
