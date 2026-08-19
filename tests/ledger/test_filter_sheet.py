"""History filter sheet: user labels, chips, clear/apply, and the
focus-managed dialog contract (static JS assertions, no browser required)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.test import Client
from django.urls import reverse
from ux_seed import seed_demo_events

from apps.accounts.services import configure_pin

pytestmark = pytest.mark.django_db

REPO_ROOT = Path(__file__).resolve().parents[2]
UI_JS = (REPO_ROOT / "static" / "js" / "workledger-ui.js").read_text()


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


def _sheet(content: str) -> str:
    match = re.search(r'<section[^>]*data-filter-sheet[^>]*>(.*?)</section>', content, re.S)
    assert match is not None, "filter sheet not rendered"
    return match.group(0)


def test_filter_sheet_is_a_focus_managed_dialog() -> None:
    seed_demo_events()
    content = _history(logged_in_client())
    sheet = _sheet(content)
    assert 'role="dialog"' in sheet
    assert 'aria-modal="true"' in sheet
    assert 'data-filter-sheet="true"' in content
    # The trigger is a real button wired to the sheet component.
    assert 'data-filter-trigger' in content
    assert 'x-data="filterSheet"' in content


def test_filter_sheet_contains_user_labeled_controls_only() -> None:
    seed_demo_events()
    content = _history(logged_in_client())
    sheet = _sheet(content)

    # Event type options use user vocabulary.
    event_type_options = re.findall(r'name="event_type"[^>]*>.*?</select>', sheet, re.S)
    assert event_type_options, "event type select missing from sheet"
    options = re.findall(r"<option[^>]*>([^<]*)</option>", event_type_options[0])
    assert "journey" in options
    assert "external activity" in options
    assert "work from home" in options
    assert "receipt" in options

    # Date range, output track, completeness, transport, location, category.
    for name in (
        "start",
        "end",
        "output_status",
        "completeness",
        "transport",
        "location",
        "category",
    ):
        assert f'name="{name}"' in sheet, f"{name} filter missing from sheet"

    # Category options show names, never codes.
    category_select = re.search(r'name="category"[^>]*>.*?</select>', sheet, re.S)
    assert category_select is not None
    option_texts = " ".join(re.findall(r"<option[^>]*>([^<]*)</option>", category_select.group(0)))
    assert "desk equipment" in option_texts.casefold()
    assert "desk_equipment" not in option_texts.casefold()


def test_filter_sheet_reflects_current_filters_and_clear_all() -> None:
    seed_demo_events()
    content = _history(
        logged_in_client(), {"event_type": "journey", "transport": "train", "q": "ice"}
    )
    sheet = _sheet(content)
    # Current event-type filter is preselected inside the sheet.
    assert re.search(r'<option value="journey" selected', sheet) is not None
    # Clear all drops every filter but keeps the page reachable.
    assert 'data-filter-clear' in sheet
    clear_href = re.search(r'<a[^>]*data-filter-clear[^>]*href="([^"]+)"', sheet)
    assert clear_href is not None
    assert clear_href.group(1) == reverse("ledger:history")


def test_filter_sheet_apply_submits_the_get_form() -> None:
    seed_demo_events()
    content = _history(logged_in_client())
    sheet = _sheet(content)
    assert f'action="{reverse("ledger:history")}"' in sheet
    assert 'method="get"' in sheet
    # The apply button is the sheet's submit.
    assert 'data-filter-apply' in sheet


def test_filter_sheet_has_close_affordances() -> None:
    seed_demo_events()
    content = _history(logged_in_client())
    sheet = _sheet(content)
    assert 'data-close' in sheet


def test_filter_sheet_js_focus_trap_escape_outside_and_restore() -> None:
    # The component is named filterSheet and ships the a11y behaviours:
    # trap Tab inside the panel, Escape and outside click close, focus
    # restored to the trigger.
    assert "filterSheet" in UI_JS
    component = UI_JS.split("filterSheet")[1][:6000]
    assert "trapFocus" in component
    assert "trigger.focus()" in component
    assert "focus" in component
    # Escape and outside-click wiring lives on the sheet markup.
    template = (REPO_ROOT / "templates" / "ledger" / "history.html").read_text()
    assert "@keydown.escape.window" in template
    assert "@click.outside" in template
    # The panel is explicitly contained in the viewport by the sheet CSS.
    css = (REPO_ROOT / "static" / "css" / "workledger.css").read_text()
    assert "max-height" in css
