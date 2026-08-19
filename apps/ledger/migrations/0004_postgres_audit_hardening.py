from __future__ import annotations

import os

from django.db import migrations

HARDENING_SQL = r"""
CREATE OR REPLACE FUNCTION workledger_reject_event_delete()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION 'events cannot be deleted; append a deleted revision'
        USING ERRCODE = '55000';
END;
$function$;

CREATE TRIGGER workledger_event_no_delete
BEFORE DELETE ON ledger_event
FOR EACH ROW EXECUTE FUNCTION workledger_reject_event_delete();

CREATE OR REPLACE FUNCTION workledger_protect_event_identity()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
    revision_parent uuid;
    revision_tax boolean;
    revision_employer boolean;
BEGIN
    IF OLD.id IS DISTINCT FROM NEW.id
       OR OLD.event_type IS DISTINCT FROM NEW.event_type
       OR OLD.created_at IS DISTINCT FROM NEW.created_at THEN
        RAISE EXCEPTION 'event identity columns are immutable'
            USING ERRCODE = '55000';
    END IF;
    IF OLD.current_revision_id IS DISTINCT FROM NEW.current_revision_id THEN
        SELECT parent_revision_id,
               COALESCE((snapshot->>'tax_relevant')::boolean, false),
               COALESCE((snapshot->>'employer_reimbursable')::boolean, false)
          INTO revision_parent, revision_tax, revision_employer
          FROM ledger_eventrevision
         WHERE id = NEW.current_revision_id AND event_id = NEW.id;
        IF OLD.current_revision_id IS NOT NULL
           AND revision_parent IS DISTINCT FROM OLD.current_revision_id THEN
            RAISE EXCEPTION 'current revision may only advance to its direct child'
                USING ERRCODE = '55000';
        END IF;
        IF NEW.tax_relevant IS DISTINCT FROM revision_tax
           OR NEW.employer_reimbursable IS DISTINCT FROM revision_employer THEN
            RAISE EXCEPTION 'event track flags must match the current revision snapshot'
                USING ERRCODE = '55000';
        END IF;
    ELSIF OLD.tax_relevant IS DISTINCT FROM NEW.tax_relevant
       OR OLD.employer_reimbursable IS DISTINCT FROM NEW.employer_reimbursable THEN
        RAISE EXCEPTION 'track flags may only change with a new revision'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE TRIGGER workledger_event_identity_immutable
BEFORE UPDATE ON ledger_event
FOR EACH ROW EXECUTE FUNCTION workledger_protect_event_identity();

CREATE OR REPLACE FUNCTION workledger_validate_audit_append()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
    expected_previous text;
    latest_recorded_at timestamptz;
BEGIN
    PERFORM pg_advisory_xact_lock(9218771344);
    SELECT audit_hash, recorded_at
      INTO expected_previous, latest_recorded_at
      FROM ledger_eventrevision
     ORDER BY recorded_at DESC, id DESC
     LIMIT 1;
    expected_previous := COALESCE(expected_previous, '');
    IF NEW.previous_audit_hash IS DISTINCT FROM expected_previous THEN
        RAISE EXCEPTION 'audit chain previous hash does not match chain head'
            USING ERRCODE = '23514';
    END IF;
    IF latest_recorded_at IS NOT NULL AND NEW.recorded_at < latest_recorded_at THEN
        RAISE EXCEPTION 'revision recorded_at cannot precede the audit chain head'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.audit_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'audit hash must be 64 lowercase hexadecimal characters'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE TRIGGER workledger_revision_audit_append_valid
BEFORE INSERT ON ledger_eventrevision
FOR EACH ROW EXECUTE FUNCTION workledger_validate_audit_append();
"""

REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS workledger_revision_audit_append_valid ON ledger_eventrevision;
DROP FUNCTION IF EXISTS workledger_validate_audit_append();
DROP TRIGGER IF EXISTS workledger_event_identity_immutable ON ledger_event;
DROP FUNCTION IF EXISTS workledger_protect_event_identity();
DROP TRIGGER IF EXISTS workledger_event_no_delete ON ledger_event;
DROP FUNCTION IF EXISTS workledger_reject_event_delete();
"""


def install_hardening(apps: object, schema_editor: object) -> None:
    connection = schema_editor.connection  # type: ignore[attr-defined]
    if connection.vendor != "postgresql":
        return
    schema_editor.execute(HARDENING_SQL)  # type: ignore[attr-defined]
    runtime_user = os.environ.get("WORKLEDGER_RUNTIME_DB_USER", "workledger_app")
    quoted_user = connection.ops.quote_name(runtime_user)
    schema_editor.execute(  # type: ignore[attr-defined]
        f"REVOKE UPDATE, DELETE ON ledger_event FROM {quoted_user}; "
        f"GRANT SELECT, INSERT ON ledger_event TO {quoted_user}; "
        f"GRANT UPDATE (current_revision_id, tax_relevant, employer_reimbursable) "
        f"ON ledger_event TO {quoted_user};"
    )


def remove_hardening(apps: object, schema_editor: object) -> None:
    connection = schema_editor.connection  # type: ignore[attr-defined]
    if connection.vendor == "postgresql":
        schema_editor.execute(REVERSE_SQL)  # type: ignore[attr-defined]


class Migration(migrations.Migration):
    dependencies = [("ledger", "0003_event_employer_reimbursable_event_tax_relevant")]
    operations = [migrations.RunPython(install_hardening, remove_hardening)]
