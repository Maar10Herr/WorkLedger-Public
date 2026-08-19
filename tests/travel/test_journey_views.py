from datetime import date

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.services import configure_pin
from apps.ledger.models import Event
from apps.taxes.journey import derive_journey_tax
from apps.travel.models import Location, LocationType, RailPass

pytestmark = pytest.mark.django_db


def logged_in_client() -> Client:
    configure_pin("123456")
    client = Client()
    client.post(reverse("accounts:login"), {"pin": "123456"})
    return client


def test_journey_form_uses_browser_decision_tree_for_mode_fields() -> None:
    Location.objects.create(name="Office", location_type=LocationType.FIRST_WORKPLACE)

    response = logged_in_client().get(reverse("travel:journey_entry"))

    content = response.content.decode()
    assert response.status_code == 200
    assert 'x-data="journeyForm()"' in content
    assert 'x-show="transportMode === \'train\'"' in content
    assert 'x-show="transportMode === \'private_car\'"' in content
    assert "BahnCard 100" not in content


def test_non_train_submission_contains_no_train_facts() -> None:
    home = Location.objects.create(
        name="Home", location_type=LocationType.RESIDENCE, is_default_residence=True
    )
    office = Location.objects.create(name="Office", location_type=LocationType.FIRST_WORKPLACE)

    response = logged_in_client().post(
        reverse("travel:journey_entry"),
        {"destination": str(office.pk), "transport_mode": "bicycle"},
    )

    event = Event.objects.get(event_type="journey")
    assert response.status_code == 201
    assert event.current_revision is not None
    assert event.current_revision.snapshot["origin_id"] == str(home.pk)
    assert "train_number" not in event.current_revision.snapshot


def test_manual_train_submission_and_bahncard_100_coverage() -> None:
    Location.objects.create(
        name="Home", location_type=LocationType.RESIDENCE, is_default_residence=True
    )
    office = Location.objects.create(name="Office", location_type=LocationType.FIRST_WORKPLACE)
    rail_pass = RailPass.objects.create(
        name="BahnCard 100",
        pass_type="bahncard_100",  # noqa: S106 -- rail-pass product type, not a password
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
    )

    response = logged_in_client().post(
        reverse("travel:journey_entry"),
        {
            "destination": str(office.pk),
            "transport_mode": "train",
            "manual_train_entry": "true",
            "origin_station": "Berlin Hbf",
            "destination_station": "Hamburg Hbf",
            "train_category": "ICE",
            "train_number": "78",
            "scheduled_departure": "2026-08-04T07:00",
            "scheduled_arrival": "2026-08-04T08:05",
            "rail_pass": str(rail_pass.pk),
        },
    )

    event = Event.objects.get(event_type="journey")
    assert response.status_code == 201
    assert event.current_revision is not None
    snapshot = event.current_revision.snapshot
    assert snapshot["manual_train_entry"] is True
    assert snapshot["train_number"] == "78"
    assert snapshot["covered_by_pass"] is True
    assert snapshot["incremental_ticket_cost"] == "0.00"
    assert snapshot["rail_pass_id"] == str(rail_pass.pk)


def test_train_lookup_failure_keeps_manual_entry_available() -> None:
    response = logged_in_client().post(
        reverse("travel:train_lookup"),
        {
            "origin_station": "Berlin Hbf",
            "destination_station": "Hamburg Hbf",
            "scheduled_departure": "2026-08-04T07:00",
        },
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "enter the train manually" in content
    assert "manual entry remains available" in content


@pytest.mark.parametrize(
    ("employer_paid", "personally_paid", "reimbursed", "expected_tax"),
    [
        ("on", "", "", "0.00"),
        ("", "50.00", "20.00", "30.00"),
    ],
)
def test_taxi_payment_and_reimbursement_never_double_count(
    employer_paid: str, personally_paid: str, reimbursed: str, expected_tax: str
) -> None:
    Location.objects.create(
        name="Home", location_type=LocationType.RESIDENCE, is_default_residence=True
    )
    client_site = Location.objects.create(name="Client", location_type=LocationType.CLIENT_SITE)

    response = logged_in_client().post(
        reverse("travel:journey_entry"),
        {
            "destination": str(client_site.pk),
            "transport_mode": "taxi",
            "total_fare": "50.00",
            "personally_paid": personally_paid,
            "reimbursed_amount": reimbursed,
            "employer_paid": employer_paid,
            "track_fields_present": "1",
            "tax_relevant": "on",
            "employer_reimbursable": "on" if not employer_paid else "",
        },
    )

    assert response.status_code == 201
    event = Event.objects.get(event_type="journey")
    assert event.current_revision is not None
    snapshot = event.current_revision.snapshot
    assert snapshot["employer_paid"] is (employer_paid == "on")
    assert str(derive_journey_tax(event).amount) == expected_tax
    assert event.employer_reimbursable is (not employer_paid)
