from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from apps.ledger.status import check_backup


def _backup(root: Path, name: str, created: datetime, *, valid: bool = True) -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    payloads = {"database.dump": b"db", "data.tar.gz": b"data", "config.tar.gz": b"config"}
    for filename, payload in payloads.items():
        (directory / filename).write_bytes(payload)
    manifest = ["format=workledger-backup-v2", f"created_at={created.strftime('%Y%m%dT%H%M%SZ')}" ]
    for filename, payload in payloads.items():
        key = filename.replace(".dump", "_sha256").replace(".tar.gz", "_sha256")
        manifest.append(f"{key}={hashlib.sha256(payload).hexdigest()}")
    if not valid:
        manifest[0] = "format=unknown"
    (directory / "manifest.txt").write_text("\n".join(manifest) + "\n", encoding="utf-8")
    return directory


def test_backup_status_validates_manifest_and_age(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WORKLEDGER_BACKUP_PATH", str(tmp_path))
    _backup(tmp_path, "workledger-20260805T010000Z", datetime.now(UTC) - timedelta(hours=2))
    report = check_backup()
    assert report.ok is True
    assert "latest successful" in report.detail
    assert "schedule unknown" in report.detail


def test_backup_status_skips_malformed_v2_and_detects_checksum_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("WORKLEDGER_BACKUP_PATH", str(tmp_path))
    good = _backup(tmp_path, "workledger-20260805T010000Z", datetime.now(UTC))
    malformed = _backup(tmp_path, "workledger-20260805T020000Z", datetime.now(UTC))
    manifest = (malformed / "manifest.txt").read_text()
    (malformed / "manifest.txt").write_text(
        manifest.replace("created_at=", "created_at=not-a-date")
    )
    report = check_backup()
    assert report.ok is True
    assert "latest successful" in report.detail

    (good / "data.tar.gz").write_bytes(b"tampered")
    report = check_backup()
    assert report.ok is False
    assert "checksum mismatch" in report.detail
