from celery import shared_task  # type: ignore[import-untyped]

from .services import verify_audit_chain


@shared_task  # type: ignore[untyped-decorator]
def verify_audit_chain_task() -> dict[str, object]:
    result = verify_audit_chain()
    if not result.valid:
        raise RuntimeError(f"Audit chain verification failed at {result.broken_revision_id}")
    return {"valid": True, "checked": result.checked_revisions}
