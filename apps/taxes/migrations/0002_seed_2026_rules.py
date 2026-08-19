from datetime import date

from django.db import migrations


def seed_rules(apps: object, schema_editor: object) -> None:
    TaxRule = apps.get_model("taxes", "TaxRule")
    estg = "https://www.gesetze-im-internet.de/estg/__9.html"
    brkg = "https://www.gesetze-im-internet.de/brkg_2005/__5.html"
    rules = [
        {
            "code": "DE_PER_DIEM_2026",
            "effective_from": date(2026, 1, 1),
            "jurisdiction": "DE",
            "rule_type": "meal_per_diem",
            "values": {"full_day": "28.00", "partial_day": "14.00", "minimum_hours": 8},
            "source_url": estg,
            "source_citation": "EStG § 9 Abs. 4a Sätze 2-3",
        },
        {
            "code": "DE_MEAL_REDUCTION_2026",
            "effective_from": date(2026, 1, 1),
            "jurisdiction": "DE",
            "rule_type": "meal_reduction",
            "values": {
                "breakfast": "5.60",
                "lunch": "11.20",
                "dinner": "11.20",
                "breakfast_percent_of_full_day": 20,
                "lunch_percent_of_full_day": 40,
                "dinner_percent_of_full_day": 40,
            },
            "source_url": estg,
            "source_citation": "EStG § 9 Abs. 4a Satz 8",
        },
        {
            "code": "DE_THREE_MONTH_2026",
            "effective_from": date(2026, 1, 1),
            "jurisdiction": "DE",
            "rule_type": "three_month_limit",
            "values": {"maximum_months": 3, "reset_interruption_weeks": 4},
            "source_url": estg,
            "source_citation": "EStG § 9 Abs. 4a Sätze 6-7",
        },
        {
            "code": "DE_COMMUTING_2026",
            "effective_from": date(2026, 1, 1),
            "jurisdiction": "DE",
            "rule_type": "commuting_allowance",
            "values": {
                "per_distance_km": "0.38",
                "starts_at_km": 1,
                "annual_cap_without_own_or_company_car": "4500.00",
            },
            "source_url": estg,
            "source_citation": "EStG § 9 Abs. 1 Satz 3 Nr. 4",
        },
        {
            "code": "DE_BUSINESS_MILEAGE_2026",
            "effective_from": date(2026, 1, 1),
            "jurisdiction": "DE",
            "rule_type": "business_mileage",
            "values": {"private_car_per_km": "0.30", "other_motor_vehicle_per_km": "0.20"},
            "source_url": brkg,
            "source_citation": "BRKG § 5 Abs. 2",
        },
    ]
    for rule in rules:
        TaxRule.objects.update_or_create(code=rule["code"], defaults=rule)


def unseed_rules(apps: object, schema_editor: object) -> None:
    TaxRule = apps.get_model("taxes", "TaxRule")
    TaxRule.objects.filter(code__in=[
        "DE_PER_DIEM_2026",
        "DE_MEAL_REDUCTION_2026",
        "DE_THREE_MONTH_2026",
        "DE_COMMUTING_2026",
        "DE_BUSINESS_MILEAGE_2026",
    ]).delete()


class Migration(migrations.Migration):
    dependencies = [("taxes", "0001_initial")]
    operations = [migrations.RunPython(seed_rules, unseed_rules)]
