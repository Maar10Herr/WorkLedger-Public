from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from apps.taxes.per_diem import ActivityFacts, derive_per_diem

pytestmark = pytest.mark.django_db


@given(
    duration_minutes=st.integers(min_value=0, max_value=72 * 60),
    reimbursement_cents=st.integers(min_value=0, max_value=20_000),
)
def test_per_diem_is_never_negative_and_reimbursement_never_increases_it(
    duration_minutes: int, reimbursement_cents: int
) -> None:
    start = datetime(2026, 8, 4, 8, tzinfo=UTC)
    end = start + timedelta(minutes=duration_minutes)
    gross = derive_per_diem(
        ActivityFacts(
            start_at=start,
            end_at=end,
            country_code="DE",
            provided_meals={},
            three_month_limit_applies=False,
        )
    )
    net = derive_per_diem(
        ActivityFacts(
            start_at=start,
            end_at=end,
            country_code="DE",
            provided_meals={},
            three_month_limit_applies=False,
            employer_reimbursement=Decimal(reimbursement_cents) / Decimal(100),
        )
    )

    assert Decimal("0.00") <= net.total <= gross.total
    assert sum(net.daily_amounts.values(), start=Decimal("0.00")) == net.total
