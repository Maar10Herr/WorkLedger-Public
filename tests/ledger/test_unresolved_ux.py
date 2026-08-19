"""Unresolved entries grouped by presenter-derived missing action,
each item carrying exactly one direct fix link."""

from __future__ import annotations

import re
from datetime import datetime

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from ux_seed import seed_demo_events

from apps.accounts.services import configure_pin
from apps.ledger.models import Event
from apps.ledger.services import create_event

pytestmark = pytest.mark.django_db

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _at(hour: int, minute: int = 0) -> datetime:
    return timezone.make_aware(datetime(2026, 8, 4, hour, minute))


def logged_in_client() -> Client:
    configure_pin("123456")
    client = Client()
    response = client.post(reverse("accounts:login"), {"pin": "123456"})
    assert response.status_code == 302
    return client


def _incomplete_journey() -> Event:
    return create_event(
        event_type="journey",
        effective_at=_at(8, 30),
        snapshot={"transport_mode": "train"},
        complete=False,
    )


def _expense_without_amount() -> Event:
    return create_event(
        event_type="expense",
        effective_at=_at(9, 0),
        snapshot={"description": "desk", "category_name": "Desk equipment"},
        complete=False,
    )


def _unmatched_receipt() -> Event:
    return create_event(
        event_type="receipt_only",
        effective_at=_at(9, 30),
        snapshot={"reconciliation_status": "unmatched"},
        complete=False,
    )


def _car_without_route() -> Event:
    return create_event(
        event_type="journey",
        effective_at=_at(11, 0),
        snapshot={
            "transport_mode": "private_car",
            "origin_id": "33333333-3333-4333-8333-333333333333",
            "origin_name": "Berlin",
            "destination_id": "44444444-4444-4444-8444-444444444444",
            "destination_name": "Hamburg",
        },
        complete=False,
    )


def _unresolved(client: Client) -> str:
    response = client.get(reverse("ledger:unresolved"))
    assert response.status_code == 200
    return response.content.decode()


def test_unresolved_groups_are_presenter_derived_and_ordered() -> None:
    seed_demo_events()  # contributes the per-diem-incomplete activity
    _incomplete_journey()
    _expense_without_amount()
    _unmatched_receipt()
    _car_without_route()

    content = _unresolved(logged_in_client())
    groups = re.findall(r'data-unresolved-group="([a-z_]+)"', content)
    assert groups == [
        "add_destination",
        "add_amount",
        "link_receipt",
        "complete_per_diem_times",
        "review_tax_route",
    ]
    headings = re.sub(r"<[^>]+>", " ", content).casefold()
    for label in (
        "add destination",
        "add amount",
        "link receipt",
        "complete per-diem times",
        "review tax route",
    ):
        assert label in headings


def test_unresolved_groups_never_use_other_labels() -> None:
    seed_demo_events()
    content = _unresolved(logged_in_client())
    groups = re.findall(r'data-unresolved-group="([a-z_]+)"', content)
    assert set(groups) <= {
        "add_destination",
        "add_amount",
        "link_receipt",
        "complete_per_diem_times",
        "review_tax_route",
    }


def test_each_item_has_exactly_one_direct_fix() -> None:
    seed_demo_events()
    _incomplete_journey()
    _expense_without_amount()
    _unmatched_receipt()
    _car_without_route()

    content = _unresolved(logged_in_client())
    items = re.findall(r'<[^>]*data-unresolved-item[^>]*>.*?</(?:div|li)>', content, re.S)
    assert len(items) == 5
    for item in items:
        fix_links = re.findall(r'data-unresolved-fix', item)
        assert len(fix_links) == 1, f"expected one direct fix per item: {item[:120]}"


def test_direct_fix_urls_point_to_the_fix_surface() -> None:
    seed_demo_events()
    journey = _incomplete_journey()
    _expense_without_amount()
    _unmatched_receipt()
    _car_without_route()

    content = _unresolved(logged_in_client())
    # add destination -> the entry's correction page
    destination_group = re.search(
        r'data-unresolved-group="add_destination".*?</section>', content, re.S
    )
    assert destination_group is not None
    expected = f'href="{reverse("ledger:event_detail", args=[journey.pk])}"'
    assert expected in destination_group.group(0)
    # link receipt -> the receipt inbox
    receipt_group = re.search(
        r'data-unresolved-group="link_receipt".*?</section>', content, re.S
    )
    assert receipt_group is not None
    assert f'href="{reverse("evidence:receipt_inbox")}"' in receipt_group.group(0)
    # review tax route -> settings (routes)
    route_group = re.search(
        r'data-unresolved-group="review_tax_route".*?</section>', content, re.S
    )
    assert route_group is not None
    assert f'href="{reverse("travel:settings")}"' in route_group.group(0)


def test_unresolved_items_show_semantic_summaries_no_uuid() -> None:
    seed_demo_events()
    _incomplete_journey()
    _expense_without_amount()
    _unmatched_receipt()
    _car_without_route()

    content = _unresolved(logged_in_client())
    visible = re.sub(r"<[^>]+>", " ", content)
    assert "08:30 · train" in visible  # journey summary (no destination facts)
    assert "desk · desk equipment" in visible  # expense summary (no amount)
    assert "receipt · unlinked · uploaded 09:30" in visible
    assert UUID_RE.search(visible) is None


def test_unresolved_empty_state() -> None:
    seed_demo_events()
    from apps.ledger.models import EventRevision

    # Detach, then drop the append-only rows via queryset delete (bypasses the
    # model-level immutability guards, which are irrelevant to test teardown).
    Event.objects.update(current_revision=None)
    EventRevision.objects.update(parent_revision=None)
    EventRevision.objects.all().delete()
    Event.objects.all().delete()
    content = _unresolved(logged_in_client())
    assert "everything is complete" in content


def test_completed_events_are_not_listed() -> None:
    seed_demo_events()
    content = _unresolved(logged_in_client())
    # Journey A is complete: its summary must not appear on the unresolved page.
    assert "ice 78" not in content
    assert "work from home" not in content
