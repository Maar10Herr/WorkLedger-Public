"""Lightweight, bounded backend health checks for the system status page.

Every check is fast (short timeouts) and fails safely: an unreachable
runtime service reports a failed check instead of raising. Nothing here
touches user data or blocks on external providers.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from django.conf import settings
from django.db import connection

from .services import verify_audit_chain

MIN_FREE_GIB = 1.0


@dataclass(frozen=True, slots=True)
class BackendCheck:
    key: str
    label: str
    ok: bool
    detail: str


@dataclass(frozen=True, slots=True)
class StatusReport:
    checks: tuple[BackendCheck, ...]
    event_count: int
    incomplete_count: int

    @property
    def healthy(self) -> bool:
        return all(check.ok for check in self.checks)

    @property
    def failing(self) -> tuple[BackendCheck, ...]:
        return tuple(check for check in self.checks if not check.ok)

    def check(self, key: str) -> BackendCheck | None:
        return next((check for check in self.checks if check.key == key), None)


def check_database() -> BackendCheck:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return BackendCheck("database", "database", True, "reachable")
    except Exception as exc:
        return BackendCheck("database", "database", False, f"unreachable ({type(exc).__name__})")


def check_audit() -> BackendCheck:
    result = verify_audit_chain()
    if result.valid:
        return BackendCheck(
            "audit", "audit chain", True, f"{result.checked_revisions} revisions verified"
        )
    return BackendCheck(
        "audit", "audit chain", False, f"broken at revision {result.broken_revision_id}"
    )


def check_storage() -> BackendCheck:
    try:
        data_dir = Path(settings.DATA_DIR)
        data_dir.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(data_dir)
    except OSError as exc:
        return BackendCheck("storage", "storage", False, f"unavailable ({type(exc).__name__})")
    free_gib = usage.free / (1024**3)
    total_gib = usage.total / (1024**3)
    return BackendCheck(
        "storage",
        "storage",
        free_gib >= MIN_FREE_GIB,
        f"{free_gib:.1f} GiB free of {total_gib:.1f} GiB",
    )


def check_redis() -> BackendCheck:
    try:
        import redis  # imported lazily; only needed for the probe

        client = redis.Redis.from_url(
            settings.CELERY_BROKER_URL,
            socket_connect_timeout=1.0,
            socket_timeout=1.0,
        )
        client.ping()
        return BackendCheck("redis", "redis", True, "reachable")
    except Exception as exc:
        return BackendCheck("redis", "redis", False, f"unreachable ({type(exc).__name__})")


def check_celery() -> BackendCheck:
    try:
        from config.celery import app as celery_app  # lazy import

        replies = celery_app.control.ping(timeout=1.0)
        worker_count = sum(len(reply) for reply in replies)
        if worker_count:
            return BackendCheck(
                "celery", "celery workers", True, f"{worker_count} worker(s) reachable"
            )
        return BackendCheck("celery", "celery workers", False, "no worker responded")
    except Exception as exc:
        return BackendCheck(
            "celery", "celery workers", False, f"unavailable ({type(exc).__name__})"
        )


_BACKUP_STATUS_CACHE: dict[tuple[str, int, tuple[tuple[str, int, int], ...]], BackendCheck] = {}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_backup() -> BackendCheck:
    root = Path(
        os.environ.get(
            "WORKLEDGER_BACKUP_PATH",
            os.environ.get("WORKLEDGER_BACKUP_DIR", str(Path(settings.BASE_DIR) / "backups")),
        )
    )
    try:
        candidates = sorted(
            (
                entry
                for entry in root.iterdir()
                if entry.is_dir() and (entry / "manifest.txt").is_file()
            ),
            key=lambda entry: entry.name,
            reverse=True,
        )
    except OSError:
        return BackendCheck("backup", "backup", False, "backup root unavailable")

    schedule = (
        "automatic schedule installed"
        if (root / ".workledger-auto-backup-installed").exists()
        else "backup exists but schedule unknown"
    )
    cache_bucket = int(time.time() // 300)
    for candidate in candidates:
        manifest = candidate / "manifest.txt"
        try:
            values = {}
            for line in manifest.read_text(encoding="utf-8").splitlines():
                if "=" in line:
                    key, value = line.split("=", 1)
                    values[key] = value
            if values.get("format") != "workledger-backup-v2":
                continue
            files = {
                "database.dump": values.get("database_sha256"),
                "data.tar.gz": values.get("data_sha256"),
                "config.tar.gz": values.get("config_sha256"),
            }
            paths = {name: candidate / name for name in files}
            if any(
                not expected or not paths[name].is_file()
                for name, expected in files.items()
            ):
                continue
            stat_key = tuple(
                (name, path.stat().st_size, path.stat().st_mtime_ns)
                for name, path in paths.items()
            )
            cache_key = (str(candidate), cache_bucket, stat_key)
            if cache_key in _BACKUP_STATUS_CACHE:
                return _BACKUP_STATUS_CACHE[cache_key]
            if any(_sha256_file(paths[name]) != expected for name, expected in files.items()):
                result = BackendCheck(
                    "backup",
                    "backup",
                    False,
                    f"checksum mismatch: {candidate.name}",
                )
                _BACKUP_STATUS_CACHE[cache_key] = result
                return result
            created = datetime.strptime(values["created_at"], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        except (OSError, KeyError, TypeError, ValueError):
            continue
        age_hours = (datetime.now(UTC) - created).total_seconds() / 3600
        if age_hours > 36:
            result = BackendCheck(
                "backup", "backup", False,
                f"stale ({age_hours:.0f}h); {schedule}; latest successful: {created.isoformat()}",
            )
        else:
            result = BackendCheck(
                "backup", "backup", True,
                f"latest successful: {created.isoformat()} ({age_hours:.1f}h ago); "
                f"{schedule}; local recovery copy only",
            )
        _BACKUP_STATUS_CACHE[cache_key] = result
        return result
    return BackendCheck(
        "backup", "backup", False, "no validated backup found; automatic schedule not detected"
    )


def _bounded(check: Callable[[], BackendCheck]) -> BackendCheck:
    """Run one probe; a broken check is reported as failed, never raised."""
    try:
        return check()
    except Exception as exc:
        label = check.__name__.removeprefix("check_").replace("_", " ")
        return BackendCheck(label, label, False, f"unavailable ({type(exc).__name__})")


def collect_status() -> StatusReport:
    """Run every bounded check plus the cheap ledger counts."""
    from apps.ledger.models import Event  # imported lazily to avoid import cycles

    checks = (
        _bounded(check_database),
        _bounded(check_audit),
        _bounded(check_storage),
        _bounded(check_celery),
        _bounded(check_redis),
        _bounded(check_backup),
    )
    return StatusReport(
        checks=checks,
        event_count=Event.objects.exclude(event_type="attachment_upload").count(),
        incomplete_count=Event.objects.exclude(event_type="attachment_upload").filter(
            current_revision__complete=False, current_revision__deleted=False
        ).count(),
    )
