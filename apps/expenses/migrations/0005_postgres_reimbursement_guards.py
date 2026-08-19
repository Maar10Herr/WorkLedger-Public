from __future__ import annotations

import os

from django.db import migrations

SQL = r"""
CREATE OR REPLACE FUNCTION workledger_reject_reimbursement_history_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION 'reimbursement status history is immutable' USING ERRCODE = '55000';
END;
$function$;

CREATE TRIGGER workledger_reimbursement_history_immutable
BEFORE UPDATE OR DELETE ON expenses_reimbursementstatuschange
FOR EACH ROW EXECUTE FUNCTION workledger_reject_reimbursement_history_mutation();

CREATE OR REPLACE FUNCTION workledger_validate_expense_status_change()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
    latest_previous text;
    latest_new text;
BEGIN
    IF OLD.reimbursement_status IS DISTINCT FROM NEW.reimbursement_status THEN
        SELECT previous_status, new_status INTO latest_previous, latest_new
        FROM expenses_reimbursementstatuschange
        WHERE expense_id = NEW.event_id
        ORDER BY changed_at DESC, id DESC
        LIMIT 1;
        IF latest_previous IS DISTINCT FROM OLD.reimbursement_status
           OR latest_new IS DISTINCT FROM NEW.reimbursement_status THEN
            RAISE EXCEPTION 'expense status update requires a matching history row'
                USING ERRCODE = '55000';
        END IF;
    END IF;
    RETURN NEW;
END;
$function$;

CREATE TRIGGER workledger_expense_status_audited
BEFORE UPDATE ON expenses_expense
FOR EACH ROW EXECUTE FUNCTION workledger_validate_expense_status_change();
"""

REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS workledger_expense_status_audited ON expenses_expense;
DROP FUNCTION IF EXISTS workledger_validate_expense_status_change();
DROP TRIGGER IF EXISTS workledger_reimbursement_history_immutable ON expenses_reimbursementstatuschange;
DROP FUNCTION IF EXISTS workledger_reject_reimbursement_history_mutation();
"""


def install(apps: object, schema_editor: object) -> None:
    connection = schema_editor.connection  # type: ignore[attr-defined]
    if connection.vendor != "postgresql":
        return
    schema_editor.execute(SQL)  # type: ignore[attr-defined]
    user = connection.ops.quote_name(
        os.environ.get("WORKLEDGER_RUNTIME_DB_USER", "workledger_app")
    )
    schema_editor.execute(  # type: ignore[attr-defined]
        f"REVOKE UPDATE, DELETE ON expenses_reimbursementstatuschange FROM {user};"
    )


def remove(apps: object, schema_editor: object) -> None:
    connection = schema_editor.connection  # type: ignore[attr-defined]
    if connection.vendor == "postgresql":
        schema_editor.execute(REVERSE_SQL)  # type: ignore[attr-defined]


class Migration(migrations.Migration):
    dependencies = [("expenses", "0004_expense_category_required")]
    operations = [migrations.RunPython(install, remove)]
