from django.db import migrations


CATEGORIES = (
    ("transport", "Transport", None),
    ("train_ticket", "Train ticket", "transport"),
    ("local_transit", "Local public transport", "transport"),
    ("taxi", "Taxi", "transport"),
    ("flight", "Flight", "transport"),
    ("parking", "Parking", "transport"),
    ("toll", "Toll", "transport"),
    ("lodging", "Lodging", None),
    ("hotel", "Hotel", "lodging"),
    ("meals", "Meals", None),
    ("meal_actual", "Actual meal expense", "meals"),
    ("office", "Office supplies", None),
    ("telecom", "Phone and internet", None),
    ("training", "Training", None),
    ("books", "Books and journals", None),
    ("professional_fees", "Professional fees", None),
    ("home_office", "Home office", None),
    ("other", "Other", None),
)


def seed_categories(apps: object, schema_editor: object) -> None:
    Category = apps.get_model("expenses", "ExpenseCategory")  # type: ignore[attr-defined]
    Expense = apps.get_model("expenses", "Expense")  # type: ignore[attr-defined]
    for code, name, _parent in CATEGORIES:
        Category.objects.get_or_create(code=code, defaults={"name": name})
    for code, _name, parent in CATEGORIES:
        if parent:
            Category.objects.filter(code=code).update(parent_id=parent)
    for expense in Expense.objects.select_related("event__current_revision"):
        snapshot = expense.event.current_revision.snapshot if expense.event.current_revision else {}
        code = str(snapshot.get("category") or "other")
        category, _ = Category.objects.get_or_create(code=code, defaults={"name": code.replace("_", " ").title()})
        expense.category_id = category.pk
        expense.save(update_fields=["category"])


class Migration(migrations.Migration):
    dependencies = [("expenses", "0002_expense_reimbursement_status_expensecategory_and_more")]
    operations = [migrations.RunPython(seed_categories, migrations.RunPython.noop)]
