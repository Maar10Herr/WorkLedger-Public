"""User-facing contract for the system status page.

Summary-first presentation (healthy/problem) with the DB/audit/storage/
Celery/Redis/backup internals in an expandable technical disclosure.
Backend checks fail safely: nothing raises, every check is bounded.
"""

from __future__ import annotations

import re
from datetime import datetime

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.services import configure_pin
from apps.ledger.services import create_event
from apps.ledger.status import BackendCheck
from apps.travel.models import Location, LocationType

pytestmark = pytest.mark.django_db

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")


def logged_in_client() -> Client:
    configure_pin("123456")
    client = Client()
    response = client.post(reverse("accounts:login"), {"pin": "123456"})
    assert response.status_code == 302
    return client


def _status_url() -> str:
    return reverse("ledger:system_status")


def _healthy_check(key: str, label: str, detail: str = "ok") -> BackendCheck:
    return BackendCheck(key=key, label=label, ok=True, detail=detail)


def _all_healthy() -> list[BackendCheck]:
    return [
        _healthy_check("database", "database", "reachable"),
        _healthy_check("audit", "audit chain", "7 revisions verified"),
        _healthy_check("storage", "storage", "42.0 GiB free"),
        _healthy_check("celery", "celery workers", "1 worker reachable"),
        _healthy_check("redis", "redis", "reachable"),
        _healthy_check("backup", "backup", "latest: workledger-20260801"),
    ]


def _patch_checks(monkeypatch: pytest.MonkeyPatch, checks: list[BackendCheck]) -> None:
    """Replace every backend check so results are fully deterministic."""

    def fake(key: str) -> BackendCheck:
        for check in checks:
            if check.key == key:
                return check
        return _healthy_check(key, key)

    monkeypatch.setattr("apps.ledger.status.check_database", lambda: fake("database"))
    monkeypatch.setattr("apps.ledger.status.check_audit", lambda: fake("audit"))
    monkeypatch.setattr("apps.ledger.status.check_storage", lambda: fake("storage"))
    monkeypatch.setattr("apps.ledger.status.check_celery", lambda: fake("celery"))
    monkeypatch.setattr("apps.ledger.status.check_redis", lambda: fake("redis"))
    monkeypatch.setattr("apps.ledger.status.check_backup", lambda: fake("backup"))


def test_status_summary_first_then_expandable_technical_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_checks(monkeypatch, _all_healthy())
    client = logged_in_client()

    response = client.get(_status_url())

    assert response.status_code == 200
    body = response.content.decode()
    summary_pos = body.index("data-status-summary")
    technical_pos = body.index("data-technical-details")
    assert summary_pos < technical_pos
    assert "all healthy" in body or "healthy" in body
    # All six internals are present and labelled semantically.
    for label in ("database", "audit chain", "storage", "celery", "redis", "backup"):
        assert label in body[technical_pos:], label


def test_status_problem_summary_when_a_check_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checks = _all_healthy()
    checks[1] = BackendCheck("audit", "audit chain", False, "broken at revision")
    _patch_checks(monkeypatch, checks)
    client = logged_in_client()

    response = client.get(_status_url())

    body = response.content.decode()
    assert "data-status-summary" in body
    assert "problem" in body or "attention" in body
    assert "audit chain" in body


def test_status_backend_checks_fail_safely(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unreachable runtime services never 500 the page."""

    def boom() -> BackendCheck:
        raise RuntimeError("backend gone")

    monkeypatch.setattr("apps.ledger.status.check_redis", boom)
    monkeypatch.setattr("apps.ledger.status.check_celery", boom)
    client = logged_in_client()

    response = client.get(_status_url())

    assert response.status_code == 200
    body = response.content.decode()
    assert "unavailable" in body or "reachable" in body or "worker" in body


def test_status_no_raw_hashes_in_ordinary_markup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checks = _all_healthy()
    checks[1] = BackendCheck(
        "audit", "audit chain", False, "broken at revision 00000000-0000-0000-0000-000000000001"
    )
    _patch_checks(monkeypatch, checks)
    client = logged_in_client()

    response = client.get(_status_url())

    body = response.content.decode()
    technical_start = body.index("data-technical-details")
    assert UUID_RE.search(body[:technical_start]) is None
    assert SHA256_RE.search(body[:technical_start]) is None


def test_status_page_shows_event_and_incomplete_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_checks(monkeypatch, _all_healthy())
    home = Location.objects.create(
        name="Home", location_type=LocationType.RESIDENCE, is_default_residence=True
    )
    create_event(
        event_type="work_from_home",
        effective_at=timezone.make_aware(datetime(2026, 8, 4, 8, 0)),
        snapshot={"residence_id": str(home.pk), "residence_name": "Home"},
    )
    create_event(
        event_type="journey",
        effective_at=timezone.make_aware(datetime(2026, 8, 4, 9, 0)),
        snapshot={"transport_mode": "train"},
        complete=False,
    )
    client = logged_in_client()

    response = client.get(_status_url())

    body = response.content.decode()
    assert "2 events" in body
    assert "1 incomplete" in body
