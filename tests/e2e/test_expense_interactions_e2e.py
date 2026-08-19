"""Browser regressions for expense-entry interactions (WORKLEDGER_E2E=1 only).

Models the real production event order for the category picker:

- ``drafts.js`` replays synthetic change events for every radio during
  draft restore. Because Alpine's handlers are already bound, an unchecked
  radio's change event can overwrite the restored selection. The trigger
  label must keep showing the checked radio's category (the one POSTed).
- Pressing Enter/Go in the category search box must never submit the
  outer expense form and create an accidental expense.

Node-level event-order coverage lives in tests/js/; this file proves the
same behaviour in a real Chromium with Alpine and drafts.js both live.
"""

from __future__ import annotations

import json
import os

import pytest
from django.urls import reverse
from playwright.sync_api import Page, expect, sync_playwright

from apps.accounts.services import configure_pin
from apps.expenses.models import ExpenseCategory

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get("WORKLEDGER_E2E") != "1", reason="set WORKLEDGER_E2E=1"),
]

DRAFT = {"category:taxi": True, "category:meal_actual": False}


def _login(page: Page, base_url: str) -> None:
    page.goto(base_url)
    page.get_by_role("textbox", name="PIN", exact=True).fill("123456")
    page.get_by_role("button", name="unlock").click()


def test_category_draft_restore_keeps_the_checked_category(
    live_server: object,
) -> None:
    configure_pin("123456")
    ExpenseCategory.objects.get_or_create(code="taxi", defaults={"name": "Taxi"})
    ExpenseCategory.objects.get_or_create(
        code="meal_actual", defaults={"name": "Actual meal expense"}
    )
    base_url = live_server.url  # type: ignore[attr-defined]
    expense_url = base_url + reverse("expenses:expense_entry")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        # Seed the saved draft before the page loads: taxi checked, and the
        # meal_actual radio unchecked (fires its change event after taxi in
        # the rendered group order, the harmful restore ordering).
        page.add_init_script(
            "localStorage.setItem('workledger:draft:expense-entry', "
            + "JSON.stringify("
            + json.dumps(DRAFT)
            + "));"
        )
        _login(page, base_url)
        page.goto(expense_url)

        expect(page.locator("[data-category-trigger]")).to_be_visible()
        label = page.locator(".wl-category-trigger__label")
        # The label must follow the checked radio (taxi), not the unchecked
        # radio whose synthetic change event fires last during restore.
        expect(label).to_have_text("Taxi")
        expect(page.locator('input[name="category"][value="taxi"]')).to_be_checked()

        # The restored selection is what gets POSTed: submitting without
        # touching the picker saves an expense categorised as taxi.
        page.get_by_role("button", name="save incomplete").click()
        expect(page.locator("h1")).to_contain_text("saved")
        browser.close()


def test_category_search_enter_never_submits_the_expense_form(
    live_server: object,
) -> None:
    configure_pin("123456")
    base_url = live_server.url  # type: ignore[attr-defined]
    expense_url = base_url + reverse("expenses:expense_entry")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        _login(page, base_url)
        page.goto(expense_url)

        page.locator("[data-category-trigger]").click()
        search = page.locator("[data-category-search]")
        expect(search).to_be_visible()
        search.fill("taxi")
        search.press("Enter")

        # No implicit form submission: the page is still the entry form, the
        # sheet is still open, and no expense was created.
        expect(page).to_have_url(expense_url)
        expect(page.locator("h1")).not_to_contain_text("saved")
        expect(page.locator("[data-category-sheet]")).to_be_visible()
        browser.close()
