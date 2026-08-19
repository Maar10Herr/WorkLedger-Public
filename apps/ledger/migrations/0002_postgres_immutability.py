from __future__ import annotations

import os

from django.db import migrations

IMMUTABILITY_SQL = r"""
CREATE OR REPLACE FUNCTION workledger_reject_revision_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION 'event revisions are immutable; append a new revision'
        USING ERRCODE = '55000';
END;
$function$;

CREATE TRIGGER workledger_revision_immutable
BEFORE UPDATE OR DELETE ON ledger_eventrevision
FOR EACH ROW EXECUTE FUNCTION workledger_reject_revision_mutation();

CREATE OR REPLACE FUNCTION workledger_validate_revision_parent()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
    parent_event uuid;
    parent_number integer;
BEGIN
    IF NEW.parent_revision_id IS NULL THEN
        IF NEW.revision_number <> 1 THEN
            RAISE EXCEPTION 'first revision must have revision number 1';
        END IF;
    ELSE
        SELECT event_id, revision_number
          INTO parent_event, parent_number
          FROM ledger_eventrevision
         WHERE id = NEW.parent_revision_id;
        IF parent_event IS DISTINCT FROM NEW.event_id THEN
            RAISE EXCEPTION 'parent revision belongs to a different event';
        END IF;
        IF NEW.revision_number <> parent_number + 1 THEN
            RAISE EXCEPTION 'revision number is not sequential';
        END IF;
    END IF;
    RETURN NEW;
END;
$function$;

CREATE TRIGGER workledger_revision_parent_valid
BEFORE INSERT ON ledger_eventrevision
FOR EACH ROW EXECUTE FUNCTION workledger_validate_revision_parent();

CREATE OR REPLACE FUNCTION workledger_validate_current_revision()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    IF NEW.current_revision_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM ledger_eventrevision
         WHERE id = NEW.current_revision_id AND event_id = NEW.id
    ) THEN
        RAISE EXCEPTION 'current revision must belong to its event';
    END IF;
    RETURN NULL;
END;
$function$;

CREATE CONSTRAINT TRIGGER workledger_event_current_revision_valid
AFTER INSERT OR UPDATE OF current_revision_id ON ledger_event
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION workledger_validate_current_revision();
"""

REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS workledger_event_current_revision_valid ON ledger_event;
DROP FUNCTION IF EXISTS workledger_validate_current_revision();
DROP TRIGGER IF EXISTS workledger_revision_parent_valid ON ledger_eventrevision;
DROP FUNCTION IF EXISTS workledger_validate_revision_parent();
DROP TRIGGER IF EXISTS workledger_revision_immutable ON ledger_eventrevision;
DROP FUNCTION IF EXISTS workledger_reject_revision_mutation();
"""


def install_postgres_guards(apps: object, schema_editor: object) -> None:
    connection = schema_editor.connection  # type: ignore[attr-defined]
    if connection.vendor != "postgresql":
        return
    schema_editor.execute(IMMUTABILITY_SQL)  # type: ignore[attr-defined]
    runtime_user = os.environ.get("WORKLEDGER_RUNTIME_DB_USER", "workledger_app")
    quoted_user = connection.ops.quote_name(runtime_user)
    schema_editor.execute(
        f"GRANT SELECT, INSERT ON ledger_eventrevision TO {quoted_user}; "
        f"REVOKE UPDATE, DELETE ON ledger_eventrevision FROM {quoted_user};"
    )


def remove_postgres_guards(apps: object, schema_editor: object) -> None:
    connection = schema_editor.connection  # type: ignore[attr-defined]
    if connection.vendor == "postgresql":
        schema_editor.execute(REVERSE_SQL)  # type: ignore[attr-defined]


class Migration(migrations.Migration):
    dependencies = [("ledger", "0001_initial")]
    operations = [migrations.RunPython(install_postgres_guards, remove_postgres_guards)]
