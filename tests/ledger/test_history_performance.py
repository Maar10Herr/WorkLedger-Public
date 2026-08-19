from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.services import configure_pin
from apps.ledger.services import create_event

pytestmark = pytest.mark.django_db


def test_history_is_bounded_and_offers_next_page() -> None:
    configure_pin("123456")
    client = Client()
    assert client.post(reverse("accounts:login"), {"pin": "123456"}).status_code == 302
    base = datetime(2026, 8, 4, 12, tzinfo=UTC)
    for index in range(101):
        create_event(
            event_type="note",
            effective_at=base + timedelta(minutes=index),
            snapshot={"label": f"note {index}"},
            complete=True,
        )

    response = client.get(reverse("ledger:history"))
    body = response.content.decode()
    assert response.status_code == 200
    assert body.count('data-event-card="true"') == 100
    assert "page=2" in body
    assert "load older entries" in body


def test_pwa_assets_are_local_and_private_pages_are_not_cached() -> None:
    root = Path(__file__).resolve().parents[2]
    manifest = (root / "static/manifest.webmanifest").read_text()
    service_worker = (root / "static/sw.js").read_text()
    assert "/static/icons/workledger.svg" in manifest
    assert "/static/" in service_worker
    assert 'url.pathname.startsWith("/static/")' in service_worker
    assert "event.request.method !== \"GET\"" in service_worker
