"""Validate and extract a WorkLedger data archive without following links."""

from __future__ import annotations

import posixpath
import shutil
import sys
import tarfile
import unicodedata
from collections.abc import Iterable
from pathlib import Path

MAX_ARCHIVE_MEMBERS = 100_000
MIN_FREE_SPACE = 64 * 1024 * 1024
WINDOWS_DEVICE_NAMES = {"con", "prn", "aux", "nul"} | {
    f"com{index}" for index in range(1, 10)
} | {f"lpt{index}" for index in range(1, 10)}


class UnsafeArchiveError(ValueError):
    """Raised when a backup contains an unsafe or unsupported archive member."""


def _normalise_member_name(name: str) -> str:
    """Return a relative POSIX member name or raise for traversal attempts."""
    if not name or "\x00" in name or "\\" in name:
        raise UnsafeArchiveError(f"Unsafe archive member: {name!r}")
    if name.startswith("/"):
        raise UnsafeArchiveError(f"Unsafe archive member: {name}")
    parts = [part for part in name.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise UnsafeArchiveError(f"Unsafe archive member: {name}")
    for part in parts:
        if any(ord(char) < 32 or char in '<>:"|?*' for char in part):
            raise UnsafeArchiveError(f"Unsafe archive member: {name}")
        if part.endswith((".", " ")) or not part.rstrip(" ."):
            raise UnsafeArchiveError(f"Unsafe archive member: {name}")
        if part.rstrip(" .").split(".", 1)[0].casefold() in WINDOWS_DEVICE_NAMES:
            raise UnsafeArchiveError(f"Unsafe archive member: {name}")
    normalised = posixpath.normpath("/".join(parts))
    if normalised in ("", "."):
        return "."
    if normalised.startswith("../") or normalised == "..":
        raise UnsafeArchiveError(f"Unsafe archive member: {name}")
    return normalised


def _member_collision_key(name: str) -> tuple[str, ...]:
    """Use a conservative key for filesystems that fold case or punctuation."""
    if name == ".":
        return ()
    return tuple(
        unicodedata.normalize("NFC", part).rstrip(" .").casefold()
        for part in name.split("/")
    )


def _validate_destination(destination: str | Path, project_root: str | Path | None) -> Path:
    destination_path = Path(destination)
    if any(part == ".." for part in destination_path.parts):
        raise UnsafeArchiveError("Restore destination must not contain '..' components")

    absolute_destination = (
        destination_path if destination_path.is_absolute() else Path.cwd() / destination_path
    )
    probe = absolute_destination
    while True:
        if probe.is_symlink():
            raise UnsafeArchiveError("Restore destination must not contain symlink components")
        if probe.exists() or probe.parent == probe:
            break
        probe = probe.parent
    if not probe.is_dir():
        raise UnsafeArchiveError("Restore destination parent must be a directory")
    if absolute_destination.exists() and not absolute_destination.is_dir():
        raise UnsafeArchiveError("Restore destination must be a directory")

    resolved_destination = absolute_destination.resolve(strict=False)
    if resolved_destination.parent == resolved_destination:
        raise UnsafeArchiveError("Restore destination must not be a filesystem root")
    if project_root is not None:
        resolved_project = Path(project_root).resolve()
        if resolved_destination == resolved_project or (
            resolved_destination in resolved_project.parents
        ):
            raise UnsafeArchiveError(
                "Restore destination must not be the project directory or one of its parents"
            )
    return destination_path


def _validate_members(
    members: Iterable[tarfile.TarInfo], root: Path, free_bytes: int
) -> list[tuple[tarfile.TarInfo, str]]:
    normalised_members: list[tuple[tarfile.TarInfo, str]] = []
    seen: dict[tuple[str, ...], str] = {}
    declared_bytes = 0
    for index, member in enumerate(members, start=1):
        if index > MAX_ARCHIVE_MEMBERS:
            raise UnsafeArchiveError(f"Archive contains more than {MAX_ARCHIVE_MEMBERS} members")
        name = _normalise_member_name(member.name)
        collision_key = _member_collision_key(name)
        if collision_key in seen:
            raise UnsafeArchiveError(
                f"Duplicate or colliding archive member: {member.name} "
                f"(conflicts with {seen[collision_key]})"
            )
        seen[collision_key] = member.name
        if not (member.isdir() or member.isreg()):
            raise UnsafeArchiveError(
                f"Archive member is not a regular file or directory: {member.name}"
            )
        if member.isreg():
            if member.size < 0:
                raise UnsafeArchiveError(f"Archive member has a negative size: {member.name}")
            declared_bytes += member.size
            if declared_bytes > max(0, free_bytes - MIN_FREE_SPACE):
                raise UnsafeArchiveError("Archive contents exceed available staging space")
        target = root if name == "." else root / name
        if not target.resolve().is_relative_to(root):
            raise UnsafeArchiveError(f"Unsafe archive member: {member.name}")
        normalised_members.append((member, name))

    regular_names = {
        _member_collision_key(name) for member, name in normalised_members if member.isreg()
    }
    for _, name in normalised_members:
        parts = name.split("/")
        for index in range(1, len(parts)):
            if _member_collision_key("/".join(parts[:index])) in regular_names:
                raise UnsafeArchiveError(f"Archive file blocks a member path: {name}")
    return normalised_members


def _extract_files(
    members: list[tuple[tarfile.TarInfo, str]], root: Path, archive: tarfile.TarFile
) -> None:
    directories = [(member, name) for member, name in members if member.isdir()]
    for _, name in directories:
        if name == ".":
            continue
        target = root / name
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        if target.is_symlink() or not target.is_dir():
            raise UnsafeArchiveError(f"Archive path collision: {name}")

    for member, name in members:
        if not member.isreg():
            continue
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if target.exists() or target.is_symlink():
            raise UnsafeArchiveError(f"Archive path collision: {member.name}")
        source = archive.extractfile(member)
        if source is None:
            raise UnsafeArchiveError(f"Archive member has no file data: {member.name}")
        try:
            with source, target.open("xb") as output:
                remaining = member.size
                while remaining:
                    chunk = source.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise UnsafeArchiveError(f"Archive member is truncated: {member.name}")
                    output.write(chunk)
                    remaining -= len(chunk)
        except OSError as exc:
            raise UnsafeArchiveError(f"Could not extract {member.name}: {exc}") from exc
        # Keep restored files usable for rollback/verification even if an
        # archive records a zero or otherwise unusable owner mode.
        target.chmod((member.mode & 0o777) | 0o600)

    for member, name in reversed(directories):
        if name == ".":
            continue
        (root / name).chmod((member.mode & 0o777) | 0o700)


def validate_and_extract(
    archive_path: str | Path,
    destination: str | Path,
    *,
    project_root: str | Path | None = None,
) -> None:
    """Validate all members, then extract regular files and directories.

    Backups are expected to contain only the files produced by ``backup.sh``.
    Rejecting links and duplicate names prevents an archive from redirecting a
    later extraction or silently overwriting an earlier member.
    """
    archive_path = Path(archive_path)
    if archive_path.is_symlink() or not archive_path.is_file():
        raise UnsafeArchiveError("Backup archive must be a regular, non-symbolic-link file")
    destination_path = _validate_destination(destination, project_root)
    destination_path.mkdir(parents=True, exist_ok=True)
    if not destination_path.is_dir() or any(destination_path.iterdir()):
        raise UnsafeArchiveError("Restore destination must be an empty directory")
    root = destination_path.resolve()

    try:
        with tarfile.open(archive_path, mode="r:*") as archive:
            members = _validate_members(archive, root, shutil.disk_usage(root).free)
            _extract_files(members, root, archive)
    except (OSError, tarfile.TarError) as exc:
        raise UnsafeArchiveError(f"Could not read backup archive: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    project_root: str | None = None
    if "--project-root" in args:
        index = args.index("--project-root")
        if index + 1 >= len(args):
            print(
                "Usage: validate_backup_archive.py [--project-root ROOT] ARCHIVE DESTINATION",
                file=sys.stderr,
            )
            return 2
        project_root = args[index + 1]
        args = args[:index] + args[index + 2 :]
    if len(args) != 2:
        print(
            "Usage: validate_backup_archive.py [--project-root ROOT] ARCHIVE DESTINATION",
            file=sys.stderr,
        )
        return 2
    try:
        validate_and_extract(args[0], args[1], project_root=project_root)
    except UnsafeArchiveError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
