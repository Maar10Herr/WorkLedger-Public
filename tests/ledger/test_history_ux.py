"""Semantic, grouped history with no raw identifiers.

Pins the history page contract:
- search + Filters button first, entries immediately after (no filter wall)
- reverse-chronological date groups with stable hooks
- presenter summaries on cards; no UUID/hash/implementation vocabulary
- useful badges only (tax/employer/incomplete/amended/...)
- active filter chips + sheet semantics stay filter-compatible
"""

from __future__ import annotations

import re

import pytest
from django.test import Client
from django.urls import reverse
from ux_seed import seed_demo_events

from apps.accounts.services import configure_pin

pytestmark = pytest.mark.django_db

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def logged_in_client() -> Client:
    configure_pin("123456")
    client = Client()
    response = client.post(reverse("accounts:login"), {"pin": "123456"})
    assert response.status_code == 302
    return client


def _history(client: Client, params: dict[str, str] | None = None) -> str:
    url = reverse("ledger:history")
    if params:
        url = f"{url}?{'&'.join(f'{key}={value}' for key, value in params.items())}"
    response = client.get(url)
    assert response.status_code == 200
    return response.content.decode()


def _cards(content: str) -> list[str]:
    return re.findall(r'<a[^>]*data-event-card="true"[^>]*>(.*?)</a>', content, re.S)


def test_history_first_viewport_is_entries_not_a_filter_wall() -> None:
    seed_demo_events()
    content = _history(logged_in_client())

    # Search box and Filters trigger come first; detailed controls live in
    # the sheet, and the first card renders before the sheet markup.
    assert 'name="q"' in content
    assert 'data-filter-trigger' in content
    cards = _cards(content)
    assert len(cards) == 6
    assert content.index('data-event-card="true"') < content.index("data-filter-sheet")


def test_history_cards_have_semantic_summaries_and_no_uuid() -> None:
    seed_demo_events()
    content = _history(logged_in_client())

    card_text = " ".join(_cards(content)).casefold()
    # The mandated presenter strings appear on the cards.
    assert "08:03 · berlin → hamburg · ice 78" in card_text
    assert "07:41 · work from home · berlin" in card_text
    assert "table · €249 · desk equipment" in card_text
    assert "receipt · unlinked · uploaded 07:56" in card_text
    assert "client visit · 09:30\u201318:20 · per diem incomplete" in card_text
    # No raw identifier anywhere on a card.
    assert UUID_RE.search(card_text) is None


def test_history_grouped_by_date_reverse_chronological() -> None:
    seed_demo_events()
    content = _history(logged_in_client())

    headings = re.findall(r'<h2[^>]*data-event-date-group[^>]*>(.*?)</h2>', content)
    assert headings == ["tuesday, 4 august", "monday, 3 august"]


def test_active_filter_chips_reflect_params_and_remove() -> None:
    seed_demo_events()
    content = _history(logged_in_client(), {"event_type": "journey", "output_status": "employer"})

    chips = re.findall(r'<a[^>]*data-filter-chip[^>]*>.*?</a>', content, re.S)
    assert len(chips) == 2
    joined = " ".join(chips)
    assert "journey" in joined.casefold()
    assert "employer" in joined.casefold()
    # Each chip is a link back to history without that filter.
    assert reverse("ledger:history") in joined
    assert joined.count("href=") == 2
    # No raw values leak into chip labels.
    assert UUID_RE.search(joined) is None


def test_no_implementation_vocabulary_anywhere() -> None:
    seed_demo_events()
    content = _history(logged_in_client())
    visible = re.sub(r"<[^>]+>", " ", content).casefold()
    for forbidden in ("location id", "category code", "uuid", "snapshot", "event type"):
        assert forbidden not in visible


def test_search_still_filters_semantically() -> None:
    seed_demo_events()
    content = _history(logged_in_client(), {"q": "ice"})
    cards = _cards(content)
    assert len(cards) == 1
    assert "ice 78" in cards[0].casefold()


def test_search_keeps_the_search_term_in_the_box() -> None:
    seed_demo_events()
    content = _history(logged_in_client(), {"q": "hamburg"})
    assert re.search(r'name="q"[^>]*value="hamburg"', content) is not None


def test_semantic_badges_on_cards() -> None:
    seed_demo_events()
    content = _history(logged_in_client())

    journey_card = next(card for card in _cards(content) if "ice 78" in card.casefold())
    assert 'data-badge-tax' in journey_card
    assert 'data-badge-employer' in journey_card

    wfh_card = next(card for card in _cards(content) if "work from home" in card.casefold())
    assert 'data-badge-amended' in wfh_card

    activity_card = next(card for card in _cards(content) if "client visit" in card.casefold())
    assert 'data-badge-incomplete' in activity_card

    # Only the six useful badges exist anywhere on the page.
    badges = set(re.findall(r"data-badge-([a-z_]+)", content))
    assert badges <= {"incomplete", "tax", "employer", "reimbursed", "receipt_missing", "amended"}


def test_card_links_navigate_to_detail_without_showing_the_uuid() -> None:
    seed = seed_demo_events()
    content = _history(logged_in_client())
    detail_url = reverse("ledger:event_detail", args=[seed["journey"].pk])
    assert f'href="{detail_url}"' in content
    # The identifier appears only in the href attribute, never in card text.
    assert UUID_RE.search(" ".join(_cards(content))) is None


def test_filtering_semantics_unchanged_for_all_params() -> None:
    seed_demo_events()
    client = logged_in_client()

    # event type
    assert len(_cards(_history(client, {"event_type": "journey"}))) == 2
    # completeness
    assert len(_cards(_history(client, {"completeness": "incomplete"}))) == 1
    # transport
    assert len(_cards(_history(client, {"transport": "train"}))) == 1
    # date range on the earlier event
    assert len(_cards(_history(client, {"end": "2026-08-03"}))) == 1
    # reimbursement state via Expense identity (no expenses seeded -> none)
    assert len(_cards(_history(client, {"reimbursement_status": "reimbursed"}))) == 0
    # location text search (origin/destination names only — semantics unchanged)
    assert len(_cards(_history(client, {"location": "berlin"}))) == 2


def test_events_ordering_within_a_group_is_by_effective_time() -> None:
    seed_demo_events()
    content = _history(logged_in_client())
    cards = _cards(content)
    # Aug 4 group: expense 10:00, activity 09:30, journey 08:03, receipt 07:56, wfh 07:41.
    first_five = [card.casefold() for card in cards[:5]]
    order = " | ".join(first_five)
    assert order.index("table · €249") < order.index("client visit")
    assert order.index("client visit") < order.index("08:03 · berlin")
    assert order.index("08:03 · berlin") < order.index("receipt · unlinked")
    assert order.index("receipt · unlinked") < order.index("work from home")
