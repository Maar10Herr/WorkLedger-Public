from __future__ import annotations

from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse

from apps.ledger.models import Event
from apps.taxes.models import PerDiemCalculation
from apps.travel.models import Location, LocationType

pytestmark = pytest.mark.django_db


def logged_in_client() -> Client:
    client = Client()
    session = client.session
    session["owner_authenticated"] = True
    session.save()
    return client


def test_external_activity_entry_persists_facts_and_per_diem() -> None:
    destination = Location.objects.create(
        name="Client site", location_type=LocationType.CLIENT_SITE
    )
    response = logged_in_client().post(
        reverse("travel:external_activity_entry"),
        {
            "activity_type": "client_visit",
            "start_at": "2026-08-04T08:00",
            "end_at": "2026-08-04T18:00",
            "country_code": "DE",
            "note": "Client workshop",
            "three_month_limit": "no",
            "destination": str(destination.pk),
            "departure_context": "Home",
            "return_context": "Home",
            "purpose": "Client workshop",
            "tax_relevant": "on",
            "employer_reimbursable": "on",
        },
    )

    assert response.status_code == 201
    event = Event.objects.get(event_type="external_activity")
    assert event.current_revision is not None
    assert event.current_revision.snapshot["note"] == "Client workshop"
    assert event.tax_relevant is True
    assert event.employer_reimbursable is True
    calculation = PerDiemCalculation.objects.get(activity_event=event)
    assert calculation.complete is True
    assert calculation.total == Decimal("14.00")
    assert "DE_PER_DIEM_2026" in calculation.rule_codes
