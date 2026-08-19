from __future__ import annotations

from celery import Celery  # type: ignore[import-untyped]

app = Celery("workledger")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
