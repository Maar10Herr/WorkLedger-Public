from __future__ import annotations

import os

from django.db import migrations

SQL = r"""
CREATE OR REPLACE FUNCTION workledger_reject_tax_fact_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION '%% rows are immutable; insert a new version', TG_TABLE_NAME
        USING ERRCODE = '55000';
END;
$function$;

CREATE TRIGGER workledger_taxrule_immutable
BEFORE UPDATE OR DELETE ON taxes_taxrule
FOR EACH ROW EXECUTE FUNCTION workledger_reject_tax_fact_mutation();
CREATE TRIGGER workledger_perdiem_immutable
BEFORE UPDATE OR DELETE ON taxes_perdiemcalculation
FOR EACH ROW EXECUTE FUNCTION workledger_reject_tax_fact_mutation();
CREATE TRIGGER workledger_route_immutable
BEFORE UPDATE OR DELETE ON taxes_routedistance
FOR EACH ROW EXECUTE FUNCTION workledger_reject_tax_fact_mutation();
"""

REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS workledger_route_immutable ON taxes_routedistance;
DROP TRIGGER IF EXISTS workledger_perdiem_immutable ON taxes_perdiemcalculation;
DROP TRIGGER IF EXISTS workledger_taxrule_immutable ON taxes_taxrule;
DROP FUNCTION IF EXISTS workledger_reject_tax_fact_mutation();
"""


def install_guards(apps: object, schema_editor: object) -> None:
    connection = schema_editor.connection  # type: ignore[attr-defined]
    if connection.vendor != "postgresql":
        return
    schema_editor.execute(SQL)  # type: ignore[attr-defined]
    user = connection.ops.quote_name(
        os.environ.get("WORKLEDGER_RUNTIME_DB_USER", "workledger_app")
    )
    for table in ("taxes_taxrule", "taxes_perdiemcalculation", "taxes_routedistance"):
        schema_editor.execute(  # type: ignore[attr-defined]
            f"GRANT SELECT, INSERT ON {table} TO {user}; "
            f"REVOKE UPDATE, DELETE ON {table} FROM {user};"
        )


def remove_guards(apps: object, schema_editor: object) -> None:
    connection = schema_editor.connection  # type: ignore[attr-defined]
    if connection.vendor == "postgresql":
        schema_editor.execute(REVERSE_SQL)  # type: ignore[attr-defined]


class Migration(migrations.Migration):
    dependencies = [("taxes", "0003_routedistance")]
    operations = [migrations.RunPython(install_guards, remove_guards)]
