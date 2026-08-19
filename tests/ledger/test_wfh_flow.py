import re

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.services import configure_pin
from apps.ledger.models import Event
from apps.travel.models import Employer, Location, LocationType

pytestmark = pytest.mark.django_db


def authenticated_client() -> Client:
    configure_pin("123456")
    client = Client()
    response = client.post(reverse("accounts:login"), {"pin": "123456"})
    assert response.status_code == 302
    return client


def test_enter_page_has_exactly_three_entry_branches() -> None:
    response = authenticated_client().get(reverse("ledger:enter"))

    content = response.content.decode()
    assert response.status_code == 200
    assert content.count('data-entry-branch="true"') == 3
    assert "work from home" in content
    assert "travel / work elsewhere" in content
    assert "expense or receipt" in content
    # External activity must not compete as a top-level entry branch.
    branch_links = re.findall(
        r'<a[^>]*data-entry-branch="true"[^>]*href="([^"]+)"', content
    )
    assert branch_links
    assert not any("external-activity" in href for href in branch_links)


def test_one_tap_wfh_uses_default_residence_and_active_employer() -> None:
    residence = Location.objects.create(
        name="Berlin home",
        location_type=LocationType.RESIDENCE,
        is_default_residence=True,
    )
    employer = Employer.objects.create(name="Example GmbH", is_active=True)
    client = authenticated_client()

    response = client.post(reverse("ledger:create_wfh"))

    event = Event.objects.get(event_type="work_from_home")
    assert response.status_code == 201
    assert event.current_revision is not None
    assert event.current_revision.complete is True
    assert event.current_revision.snapshot["residence_id"] == str(residence.pk)
    assert event.current_revision.snapshot["employer_id"] == str(employer.pk)
    content = response.content.decode()
    assert "saved" in content
    assert "undo" in content
    assert "edit" in content


def test_wfh_can_be_saved_incomplete_when_defaults_are_missing() -> None:
    response = authenticated_client().post(reverse("ledger:create_wfh"))

    event = Event.objects.get(event_type="work_from_home")
    assert response.status_code == 201
    assert event.current_revision is not None
    assert event.current_revision.complete is False
    assert "default residence" in response.content.decode()
