from __future__ import annotations

import os

import pytest
from django.urls import reverse
from playwright.sync_api import sync_playwright

from apps.accounts.services import configure_pin

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get("WORKLEDGER_E2E") != "1", reason="set WORKLEDGER_E2E=1"),
]


def test_iphone_setup_and_one_tap_work_from_home(live_server: object) -> None:
    configure_pin("123456")
    base_url = live_server.url  # type: ignore[attr-defined]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(
            viewport={"width": 390, "height": 844},
            device_scale_factor=3,
            is_mobile=True,
            has_touch=True,
        )
        page.goto(base_url)
        page.get_by_role("textbox", name="PIN", exact=True).fill("123456")
        page.get_by_role("button", name="unlock").click()
        page.get_by_role("button", name="secondary menu").click()
        page.get_by_role("menuitem", name="settings").click()

        # Settings is now an index of section cards; each data section is a
        # separate subpage. The add-new form sits inside a
        # disclosure that must be opened first.
        page.get_by_role("link", name="locations").click()
        page.locator("[data-add-location] > summary").click()
        location_form = page.locator('form:has(input[name="action"][value="location"])')
        location_form.locator('input[name="name"]').fill("Home")
        location_form.locator('select[name="location_type"]').select_option("residence")
        location_form.locator('select[name="country_code"]').select_option("DE")
        location_form.locator('input[name="is_default_residence"]').check()
        location_form.get_by_role("button", name="save location").click()

        page.goto(base_url + reverse("travel:settings_employer"))
        page.locator("[data-add-employer] > summary").click()
        employer_form = page.locator('form:has(input[name="action"][value="employer"])')
        employer_form.locator('input[name="name"]').fill("Example Employer")
        employer_form.get_by_role("button", name="set active employer").click()

        page.goto(base_url)
        page.get_by_role("link", name="enter new").click()
        page.get_by_role("button", name="work from home").click()
        page.get_by_role("heading", name="saved").wait_for()
        assert page.locator("text=Add when convenient:").count() == 0
        browser.close()
