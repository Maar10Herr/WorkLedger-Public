from urllib.parse import parse_qs, urlsplit

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Owner
from apps.accounts.services import configure_pin

pytestmark = pytest.mark.django_db


def test_first_visit_allows_mobile_owner_pin_setup() -> None:
    client = Client()

    response = client.get(reverse("accounts:login"), follow=True)

    assert response.status_code == 200
    assert response.redirect_chain == [(reverse("accounts:setup"), 302)]
    assert 'name="pin"' in response.content.decode()
    assert 'name="confirmation"' in response.content.decode()

    response = client.post(
        reverse("accounts:setup"),
        {"pin": "1234", "confirmation": "1234"},
        follow=True,
    )

    assert response.status_code == 200
    assert Owner.objects.count() == 1
    assert client.session["owner_authenticated"] is True
    assert "enter new" in response.content.decode()


def test_login_screen_requests_pin_without_username() -> None:
    configure_pin("123456")

    response = Client().get(reverse("accounts:login"))

    content = response.content.decode()
    assert response.status_code == 200
    assert 'name="pin"' in content
    assert 'inputmode="numeric"' in content
    assert 'name="username"' not in content


def test_home_requires_owner_session() -> None:
    response = Client().get(reverse("home"))

    assert response.status_code == 302
    assert response.headers["Location"].startswith(reverse("accounts:login"))


def test_owner_login_redirect_encodes_original_path() -> None:
    path = reverse("home") + "?from=dashboard&filter=unmatched"

    response = Client().get(path)

    location = response.headers["Location"]
    assert urlsplit(location).path == reverse("accounts:login")
    assert parse_qs(urlsplit(location).query)["next"] == [path]


def test_correct_pin_creates_session_and_home_has_two_primary_actions() -> None:
    configure_pin("123456")
    client = Client()

    response = client.post(reverse("accounts:login"), {"pin": "123456"}, follow=True)

    content = response.content.decode()
    assert response.status_code == 200
    assert client.session["owner_authenticated"] is True
    assert content.count('data-primary-action="true"') == 2
    assert "enter new" in content
    assert "view past entries" in content


def test_wrong_pin_does_not_authenticate() -> None:
    configure_pin("123456")
    client = Client()

    response = client.post(reverse("accounts:login"), {"pin": "000000"})

    assert response.status_code == 200
    assert "owner_authenticated" not in client.session
    assert "PIN could not be verified" in response.content.decode()


def test_login_rejects_external_next_url() -> None:
    configure_pin("123456")

    response = Client().post(
        reverse("accounts:login") + "?next=https://evil.example/phish",
        {"pin": "123456"},
    )

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("home")
