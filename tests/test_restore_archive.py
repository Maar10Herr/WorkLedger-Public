from __future__ import annotations

import io
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

import scripts.validate_backup_archive as archive_validator
from scripts.validate_backup_archive import UnsafeArchiveError, validate_and_extract

ROOT = Path(__file__).resolve().parents[1]


def _archive(path: Path, members: list[tuple[str, bytes | None, str]]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, content, kind in members:
            info = tarfile.TarInfo(name)
            if kind == "directory":
                info.type = tarfile.DIRTYPE
                info.mode = 0o750
                archive.addfile(info)
            elif kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = "../../outside"
                archive.addfile(info)
            elif kind == "hardlink":
                info.type = tarfile.LNKTYPE
                info.linkname = "safe.txt"
                archive.addfile(info)
            elif kind == "fifo":
                info.type = tarfile.FIFOTYPE
                archive.addfile(info)
            elif kind == "device":
                info.type = tarfile.CHRTYPE
                info.devmajor = 1
                info.devminor = 3
                archive.addfile(info)
            else:
                assert content is not None
                info.mode = 0o640
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))


def test_extracts_regular_files_and_directories(tmp_path: Path) -> None:
    archive = tmp_path / "data.tar.gz"
    _archive(
        archive,
        [
            (".", None, "directory"),
            ("attachments", None, "directory"),
            ("attachments/receipt.txt", b"receipt", "file"),
        ],
    )
    destination = tmp_path / "restore"

    validate_and_extract(archive, destination)

    assert (destination / "attachments/receipt.txt").read_bytes() == b"receipt"


@pytest.mark.parametrize(
    "member",
    [
        ("../outside.txt", b"escape", "file"),
        ("/" + "tmp/outside.txt", b"escape", "file"),
        ("file.", b"unsafe", "file"),
        ("file ", b"unsafe", "file"),
        ("link", None, "symlink"),
        ("link", None, "hardlink"),
    ],
)
def test_rejects_unsafe_members(tmp_path: Path, member: tuple[str, bytes | None, str]) -> None:
    archive = tmp_path / "data.tar.gz"
    _archive(archive, [member])
    destination = tmp_path / "restore"

    with pytest.raises(UnsafeArchiveError):
        validate_and_extract(archive, destination)

    assert not (tmp_path / "outside.txt").exists()


def test_rejects_duplicate_normalised_names(tmp_path: Path) -> None:
    archive = tmp_path / "data.tar.gz"
    _archive(
        archive,
        [("file.txt", b"one", "file"), ("./file.txt", b"two", "file")],
    )

    with pytest.raises(UnsafeArchiveError, match="Duplicate"):
        validate_and_extract(archive, tmp_path / "restore")


def test_rejects_file_directory_prefix_collision(tmp_path: Path) -> None:
    archive = tmp_path / "data.tar.gz"
    _archive(
        archive,
        [
            ("file", b"one", "file"),
            ("file/child", b"two", "file"),
        ],
    )

    with pytest.raises(UnsafeArchiveError, match="blocks a member path"):
        validate_and_extract(archive, tmp_path / "restore")


def test_rejects_case_and_filesystem_punctuation_collision(tmp_path: Path) -> None:
    archive = tmp_path / "data.tar.gz"
    _archive(archive, [("Receipt", b"one", "file"), ("receipt.", b"two", "file")])

    with pytest.raises(UnsafeArchiveError, match=r"Unsafe archive member|colliding"):
        validate_and_extract(archive, tmp_path / "restore")


@pytest.mark.parametrize("kind", ["fifo", "device"])
def test_rejects_special_files(tmp_path: Path, kind: str) -> None:
    archive = tmp_path / "data.tar.gz"
    _archive(archive, [("special", None, kind)])

    with pytest.raises(UnsafeArchiveError, match="regular file or directory"):
        validate_and_extract(archive, tmp_path / "restore")


def test_rejects_member_count_bomb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = tmp_path / "data.tar.gz"
    _archive(archive, [("one", b"1", "file"), ("two", b"2", "file")])
    monkeypatch.setattr(archive_validator, "MAX_ARCHIVE_MEMBERS", 1)

    with pytest.raises(UnsafeArchiveError, match="more than 1 members"):
        validate_and_extract(archive, tmp_path / "restore")


def test_rejects_declared_size_bomb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = tmp_path / "data.tar.gz"
    _archive(archive, [("large", b"xx", "file")])
    monkeypatch.setattr(
        shutil,
        "disk_usage",
        lambda _: shutil._ntuple_diskusage(0, 0, archive_validator.MIN_FREE_SPACE),
    )

    with pytest.raises(UnsafeArchiveError, match="available staging space"):
        validate_and_extract(archive, tmp_path / "restore")


def test_rejects_symbolic_link_archive_path(tmp_path: Path) -> None:
    target = tmp_path / "data.tar.gz"
    _archive(target, [("file.txt", b"safe", "file")])
    alias = tmp_path / "alias.tar.gz"
    alias.symlink_to(target)

    with pytest.raises(UnsafeArchiveError, match="non-symbolic-link"):
        validate_and_extract(alias, tmp_path / "restore")


def test_rejects_project_root_and_its_parents(tmp_path: Path) -> None:
    archive = tmp_path / "data.tar.gz"
    _archive(archive, [("file.txt", b"safe", "file")])
    project_root = tmp_path / "project"
    project_root.mkdir()

    for destination in (project_root, project_root.parent):
        with pytest.raises(UnsafeArchiveError, match="project directory"):
            validate_and_extract(
                archive,
                destination,
                project_root=project_root,
            )


def test_data_directory_validator_rejects_roots_parents_files_and_links(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / "file").touch()
    (project / "link").symlink_to(outside, target_is_directory=True)
    script = ROOT / "scripts" / "validate_data_directory.sh"

    def run(value: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            ["sh", str(script), value, str(project), "data"],  # noqa: S607
            text=True,
            capture_output=True,
            check=False,
        )

    for value in (
        "/",
        ".",
        "..",
        "../outside",
        str(project),
        str(project.parent),
        "file/new",
        "link/new",
    ):
        assert run(value).returncode != 0, value

    valid = run("new/./child")
    assert valid.returncode == 0, valid.stderr


def test_data_directory_validator_prints_canonical_sibling_path(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "data").mkdir(parents=True)
    script = ROOT / "scripts" / "validate_data_directory.sh"
    result = subprocess.run(  # noqa: S603
        ["sh", str(script), "data/./", str(project), "data", "--print"],  # noqa: S607
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(project / "data")
