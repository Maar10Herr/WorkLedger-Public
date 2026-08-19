"""Entry-flow acceptance tests: home menu, login/setup, and saved continuations.

These tests pin user-facing vocabulary and accessibility markers without
exercising browser behaviour (that is covered by the Playwright suite).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.accounts.services import configure_pin
from apps.ledger.models import Event
from apps.travel.models import Location, LocationType

pytestmark = pytest.mark.django_db

REPO_ROOT = Path(__file__).resolve().parents[2]


def authenticated_client() -> Client:
    configure_pin("123456")
    client = Client()
    response = client.post(reverse("accounts:login"), {"pin": "123456"})
    assert response.status_code == 302
    return client


def _create_journey(client: Client) -> Event:
    Location.objects.create(
        name="Home", location_type=LocationType.RESIDENCE, is_default_residence=True
    )
    office = Location.objects.create(name="Office", location_type=LocationType.FIRST_WORKPLACE)
    response = client.post(
        reverse("travel:journey_entry"),
        {"destination": str(office.pk), "transport_mode": "bicycle"},
    )
    assert response.status_code == 201
    return Event.objects.get(event_type="journey")


def test_home_menu_is_an_accessible_popover_with_ordered_items() -> None:
    response = authenticated_client().get(reverse("home"))

    content = response.content.decode()
    assert response.status_code == 200
    # The native details disclosure is replaced by a focus-managed popover.
    assert "<details" not in content
    assert 'aria-haspopup="menu"' in content
    assert 'aria-expanded' in content
    assert content.count('role="menu"') == 1
    # Ordered menu items, lock last and visually destructive.
    items = re.findall(
        r'<[^>]+role="menuitem"[^>]*>(.*?)</(?:a|button)>', content, re.S
    )
    labels = [re.sub(r"<[^>]+>", "", item).strip() for item in items]
    assert labels == [
        "settings",
        "exports",
        "employer packages",
        "unresolved entries",
        "system status",
        "lock",
    ]
    assert 'data-destructive="true"' in content
    assert content.rindex('data-destructive="true"') > content.rindex('role="menuitem"') - 200


def test_enter_new_shows_only_three_user_facing_choices() -> None:
    response = authenticated_client().get(reverse("ledger:enter"))

    content = response.content.decode()
    assert response.status_code == 200
    assert content.count('data-entry-branch="true"') == 3
    assert "work from home" in content
    assert "travel / work elsewhere" in content
    assert "expense or receipt" in content
    # External activity must not be a competing top-level action.
    branch_links = re.findall(
        r'<a[^>]*data-entry-branch="true"[^>]*href="([^"]+)"', content
    )
    assert branch_links
    assert not any("external-activity" in href for href in branch_links)


def test_wfh_saved_screen_shows_timestamp_undo_and_edit() -> None:
    client = authenticated_client()
    residence = Location.objects.create(
        name="Home", location_type=LocationType.RESIDENCE, is_default_residence=True
    )

    response = client.post(reverse("ledger:create_wfh"))

    event = Event.objects.get(event_type="work_from_home")
    content = response.content.decode()
    assert response.status_code == 201
    assert event.current_revision is not None
    assert event.current_revision.snapshot["residence_id"] == str(residence.pk)
    assert "saved at" in content
    assert "undo" in content
    assert "edit" in content
    assert reverse("ledger:undo_event", args=[event.pk]) in content
    assert reverse("ledger:event_detail", args=[event.pk]) in content


def test_journey_saved_screen_offers_continuations() -> None:
    client = authenticated_client()
    _create_journey(client)

    response = client.post(reverse("travel:recent_journey_action", args=["repeat"]))

    repeated = Event.objects.filter(event_type="journey").order_by("-created_at").first()
    assert repeated is not None
    content = response.content.decode()
    assert response.status_code == 201
    assert "reverse journey" in content
    assert "repeat journey" in content
    assert "add external activity / per diem" in content
    # The activity continuation prefills and links the saved journey.
    assert f"journey={repeated.pk}" in content
    assert "attach receipt" in content
    assert f"{reverse('evidence:receipt_inbox')}?journey={repeated.pk}" in content


def test_external_activity_entry_accepts_journey_prefill() -> None:
    client = authenticated_client()
    journey_event = _create_journey(client)

    response = client.get(
        reverse("travel:external_activity_entry"), {"journey": str(journey_event.pk)}
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert f'name="journey_legs" value="{journey_event.pk}" checked' in content


def test_receipt_inbox_preselects_journey_target(
    settings: Any, tmp_path: Path
) -> None:
    settings.DATA_DIR = tmp_path
    client = authenticated_client()
    journey_event = _create_journey(client)
    upload = SimpleUploadedFile(
        "receipt.png",
        b"\x89PNG\r\n\x1a\n" + b"evidence",
        content_type="image/png",
    )
    client.post(reverse("evidence:receipt_inbox"), {"attachment": upload})

    response = client.get(
        reverse("evidence:receipt_inbox"), {"journey": str(journey_event.pk)}
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert f'name="target_event" value="{journey_event.pk}"' in content


def test_login_panel_is_compact_with_masked_numeric_input() -> None:
    configure_pin("123456")

    content = Client().get(reverse("accounts:login")).content.decode()

    assert 'type="password"' in content
    assert 'inputmode="numeric"' in content
    assert "unlock" in content
    # The four-digit-minimum helper is shown only during setup.
    assert "at least four digits" not in content


def test_setup_panel_shows_four_digit_helper() -> None:
    content = Client().get(reverse("accounts:setup")).content.decode()

    assert 'inputmode="numeric"' in content
    assert "at least four digits" in content


def test_static_assets_are_self_contained() -> None:
    base = (REPO_ROOT / "templates" / "base.html").read_text()
    ui_js = (REPO_ROOT / "static" / "js" / "workledger-ui.js").read_text()
    css = (REPO_ROOT / "static" / "css" / "workledger.css").read_text()

    assert "http://" not in base and "https://" not in base
    assert "http://" not in ui_js and "https://" not in ui_js
    # The only URL anywhere in the stylesheet is the tailwind license comment.
    urls = re.findall(r"https?://[^\"'() ]*", css)
    assert urls == ["https://tailwindcss.com"]
    assert "workledger-ui.js" in base
    assert base.index("workledger-ui.js") < base.index("alpine.min.js")
