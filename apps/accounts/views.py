from __future__ import annotations

from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from .forms import InitialPinSetupForm, PinLoginForm
from .models import Owner
from .services import authenticate_pin, configure_pin


@never_cache
@require_http_methods(["GET", "POST"])
def login_view(request: HttpRequest) -> HttpResponse:
    if request.session.get("owner_authenticated") is True:
        return redirect("home")
    if not Owner.objects.filter(pk=1).exists():
        return redirect("accounts:setup")

    form = PinLoginForm(request.POST or None)
    error = ""
    retry_after = 0
    if request.method == "POST" and form.is_valid():
        result = authenticate_pin(form.cleaned_data["pin"])
        if result.authenticated:
            request.session.cycle_key()
            request.session["owner_authenticated"] = True
            request.session.set_expiry(60 * 60 * 24 * 30)
            target = request.GET.get("next", "")
            if not url_has_allowed_host_and_scheme(
                target, allowed_hosts=None, require_https=False
            ):
                target = reverse("home")
            return HttpResponseRedirect(target)
        error = "PIN could not be verified."
        retry_after = result.retry_after_seconds
    return render(
        request,
        "accounts/login.html",
        {"form": form, "error": error, "retry_after": retry_after},
    )


@never_cache
@require_http_methods(["GET", "POST"])
def setup_view(request: HttpRequest) -> HttpResponse:
    if Owner.objects.filter(pk=1).exists():
        return redirect("accounts:login")
    form = InitialPinSetupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        configure_pin(form.cleaned_data["pin"])
        request.session.cycle_key()
        request.session["owner_authenticated"] = True
        request.session.set_expiry(60 * 60 * 24 * 30)
        return redirect("home")
    return render(request, "accounts/setup.html", {"form": form})


@require_POST
def logout_view(request: HttpRequest) -> HttpResponse:
    request.session.flush()
    return redirect("accounts:login")
