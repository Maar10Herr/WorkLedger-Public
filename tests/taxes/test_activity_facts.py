from datetime import UTC, datetime

import pytest

from apps.taxes.models import PerDiemCalculation
from apps.travel.services import create_external_activity

pytestmark = pytest.mark.django_db


def test_external_activity_keeps_user_facts_separate_from_derived_tax_facts() -> None:
    activity = create_external_activity(
        start_at=datetime(2026, 8, 4, 8, 0, tzinfo=UTC),
        end_at=datetime(2026, 8, 4, 18, 0, tzinfo=UTC),
        country_code="DE",
        activity_type="client_visit",
        provided_meals={"2026-08-04": ["breakfast"]},
        three_month_limit_applies=False,
    )

    assert activity.event.current_revision is not None
    snapshot = activity.event.current_revision.snapshot
    assert snapshot["country_code"] == "DE"
    assert "per_diem_amount" not in snapshot
    assert PerDiemCalculation.objects.filter(activity_event=activity.event).exists() is False
