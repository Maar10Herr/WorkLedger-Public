from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from apps.taxes.models import TaxRule
from apps.taxes.per_diem import ActivityFacts, derive_per_diem

pytestmark = pytest.mark.django_db


@pytest.fixture
def domestic_rules() -> list[TaxRule]:
    rules = list(
        TaxRule.objects.filter(
            code__in=["DE_PER_DIEM_2026", "DE_MEAL_REDUCTION_2026"]
        ).order_by("code")
    )
    assert len(rules) == 2
    return rules


def test_domestic_multi_day_per_diem_and_meal_reductions(domestic_rules: list[TaxRule]) -> None:
    facts = ActivityFacts(
        start_at=datetime(2026, 8, 4, 10, 0, tzinfo=UTC),
        end_at=datetime(2026, 8, 6, 16, 0, tzinfo=UTC),
        country_code="DE",
        provided_meals={date(2026, 8, 5): {"breakfast", "lunch"}},
        three_month_limit_applies=False,
    )

    result = derive_per_diem(facts)

    assert result.daily_amounts == {
        date(2026, 8, 4): Decimal("14.00"),
        date(2026, 8, 5): Decimal("11.20"),
        date(2026, 8, 6): Decimal("14.00"),
    }
    assert result.total == Decimal("39.20")
    assert result.complete is True
    assert result.rule_codes == ("DE_PER_DIEM_2026", "DE_MEAL_REDUCTION_2026")


def test_same_day_requires_more_than_eight_hours(domestic_rules: list[TaxRule]) -> None:
    exactly_eight = ActivityFacts(
        start_at=datetime(2026, 8, 4, 8, 0, tzinfo=UTC),
        end_at=datetime(2026, 8, 4, 16, 0, tzinfo=UTC),
        country_code="DE",
        provided_meals={},
        three_month_limit_applies=False,
    )
    over_eight = ActivityFacts(
        start_at=datetime(2026, 8, 4, 8, 0, tzinfo=UTC),
        end_at=datetime(2026, 8, 4, 16, 1, tzinfo=UTC),
        country_code="DE",
        provided_meals={},
        three_month_limit_applies=False,
    )

    assert derive_per_diem(exactly_eight).total == Decimal("0.00")
    assert derive_per_diem(over_eight).total == Decimal("14.00")


def test_three_month_uncertainty_is_prompted_not_silently_decided(
    domestic_rules: list[TaxRule],
) -> None:
    facts = ActivityFacts(
        start_at=datetime(2026, 8, 4, 8, 0, tzinfo=UTC),
        end_at=datetime(2026, 8, 4, 18, 0, tzinfo=UTC),
        country_code="DE",
        provided_meals={},
        three_month_limit_applies=None,
        three_month_review_required=True,
    )

    result = derive_per_diem(facts)

    assert result.complete is False
    assert "three-month" in result.missing_facts[0]
    assert result.total == Decimal("14.00")


def test_reductions_never_make_allowance_negative(domestic_rules: list[TaxRule]) -> None:
    facts = ActivityFacts(
        start_at=datetime(2026, 8, 4, 8, 0, tzinfo=UTC),
        end_at=datetime(2026, 8, 4, 18, 0, tzinfo=UTC),
        country_code="DE",
        provided_meals={date(2026, 8, 4): {"breakfast", "lunch", "dinner"}},
        three_month_limit_applies=False,
    )

    assert derive_per_diem(facts).total == Decimal("0.00")


def test_meal_copayment_and_employer_reimbursement_reduce_only_once(
    domestic_rules: list[TaxRule],
) -> None:
    activity_day = date(2026, 8, 4)
    facts = ActivityFacts(
        start_at=datetime(2026, 8, 4, 8, 0, tzinfo=UTC),
        end_at=datetime(2026, 8, 4, 18, 0, tzinfo=UTC),
        country_code="DE",
        provided_meals={activity_day: {"breakfast"}},
        three_month_limit_applies=False,
        provided_meal_copayments={activity_day: {"breakfast": Decimal("2.00")}},
        employer_reimbursement=Decimal("3.00"),
    )

    result = derive_per_diem(facts)

    assert result.total == Decimal("7.40")
