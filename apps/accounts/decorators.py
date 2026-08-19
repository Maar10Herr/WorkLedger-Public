from __future__ import annotations

from collections.abc import Callable
from functools import wraps

from django.http import HttpRequest
from django.http.response import HttpResponseBase
from django.shortcuts import redirect
from django.urls import reverse


def owner_login_required(
    view: Callable[..., HttpResponseBase],
) -> Callable[..., HttpResponseBase]:
    @wraps(view)
    def wrapped(request: HttpRequest, *args: object, **kwargs: object) -> HttpResponseBase:
        if request.session.get("owner_authenticated") is not True:
            return redirect(f"{reverse('accounts:login')}?next={request.get_full_path()}")
        return view(request, *args, **kwargs)

    return wrapped
