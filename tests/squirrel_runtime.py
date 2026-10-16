"""Helpers for executing production code with the supplied Squirrel runtime."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tarfile
import tempfile
from functools import cache
from pathlib import Path

import pytest

from tests.support import REPO_ROOT

SQUIRREL_EXECUTABLE_ENV = "SQUIRREL_EXECUTABLE"
SQUIRREL_ARCHIVE_ENV = "SQUIRREL_ARCHIVE"
SQUIRREL_ARCHIVE_SHA256 = (
    "936efb633f290e15b206c96c6fd44075c70e3085c9c967cab38c13e8cb51c799"
)
SQUIRREL_ARCHIVE_NAME = "squirrel_2.2_stable.tar.gz"
VENDORED_SQUIRREL_ARCHIVE = REPO_ROOT / "tests" / "vendor" / SQUIRREL_ARCHIVE_NAME


def _common_checkout_root() -> Path:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        # No usable git (missing binary, unreadable checkout, hung invocation):
        # a worktree's shared parent cannot be detected, so this checkout is the
        # only build root available.
        return REPO_ROOT
    if completed.returncode != 0:
        return REPO_ROOT
    return Path(completed.stdout.strip()).parent


def _local_roots() -> tuple[Path, ...]:
    roots = (REPO_ROOT / ".local", _common_checkout_root() / ".local")
    return tuple(dict.fromkeys(roots))


def _executable_ready(executable: Path) -> bool:
    return executable.is_file() and os.access(executable, os.X_OK)


def _install_build(source_root: Path, target: Path) -> None:
    """Move a fresh build into place over a stale or concurrently built target.

    ``Path.rename`` onto an existing non-empty directory raises ``OSError``
    (``ENOTEMPTY``), not ``FileExistsError``, so both outcomes are handled here:
    a target that already holds a usable interpreter is kept (the pinned digest
    makes it identical to this build), and anything else is discarded.
    """
    try:
        source_root.rename(target)
        return
    except OSError:
        if not target.exists():
            raise
    if _executable_ready(target / "bin" / "sq"):
        return
    shutil.rmtree(target, ignore_errors=True)
    try:
        source_root.rename(target)
    except OSError:
        if not _executable_ready(target / "bin" / "sq"):
            raise


def _validate_archive(archive_path: Path) -> None:
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    if digest != SQUIRREL_ARCHIVE_SHA256:
        pytest.fail(
            f"unexpected Squirrel archive SHA-256 digest for {archive_path}: {digest}"
        )


def _build_squirrel(archive_path: Path, target: Path) -> Path:
    _validate_archive(archive_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="squirrel-build-", dir=target.parent
    ) as temp_dir:
        temp_path = Path(temp_dir)
        with tarfile.open(archive_path) as archive:
            members = archive.getmembers()
            if any(
                not (temp_path / member.name)
                .resolve()
                .is_relative_to(temp_path.resolve())
                for member in members
            ):
                pytest.fail(f"Squirrel archive contains an unsafe path: {archive_path}")
            # The pinned digest and resolved-member check make extraction bounded.
            archive.extractall(temp_path)

        source_root = temp_path / "SQUIRREL2"
        makefile = source_root / "squirrel" / "Makefile"
        makefile.chmod(makefile.stat().st_mode | 0o200)
        original = makefile.read_text()
        old_command = "gcc -O2  -D_SQ64 -fno-rtti -Wall -c"
        new_command = "gcc -O2  -D_SQ64 -fno-rtti -fpermissive -Wall -c"
        if original.count(old_command) != 1:
            pytest.fail("Squirrel makefile lacks the expected 64-bit compiler command")
        makefile.write_text(original.replace(old_command, new_command, 1))

        completed = subprocess.run(
            ["make", "sq64"],
            cwd=source_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        if completed.returncode != 0:
            pytest.fail(
                "Squirrel runtime build failed\n"
                f"stdout:\n{completed.stdout}\n"
                f"stderr:\n{completed.stderr}"
            )
        _install_build(source_root, target)
    return target / "bin" / "sq"


def _validate_executable(executable: Path) -> Path:
    completed = subprocess.run(
        [str(executable), "-v"],
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    # The supplied archive calls itself 2.2 in README/HISTORY but carries this
    # contradictory internal banner. Reject Squirrel 3 rather than silently
    # changing the language semantics under test.
    if completed.returncode != 0 or "Squirrel 2.5 stable" not in completed.stdout:
        pytest.fail(
            f"unsupported standalone Squirrel executable: {executable}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return executable


@cache
def squirrel_executable() -> Path:
    """Return or build the pinned interpreter; absence fails the replacement tests."""
    configured = os.environ.get(SQUIRREL_EXECUTABLE_ENV)
    if configured:
        executable = Path(configured).expanduser()
        if not _executable_ready(executable):
            pytest.fail(
                f"{SQUIRREL_EXECUTABLE_ENV} is not an executable file: {executable}"
            )
        return _validate_executable(executable)

    local_roots = _local_roots()
    build_target = local_roots[-1] / "squirrel-2.2"

    # An explicitly configured archive outranks any previously built
    # interpreter, so the pinned-digest gate runs on the source named here
    # rather than being shadowed by a stale build directory.
    configured_archive = os.environ.get(SQUIRREL_ARCHIVE_ENV)
    if configured_archive:
        archive_path = Path(configured_archive).expanduser()
        if not archive_path.is_file():
            pytest.fail(
                f"{SQUIRREL_ARCHIVE_ENV} is not a readable file: {archive_path}"
            )
        return _validate_executable(_build_squirrel(archive_path, build_target))

    for local_root in local_roots:
        executable = local_root / "squirrel-2.2" / "bin" / "sq"
        if _executable_ready(executable):
            return _validate_executable(executable)

    if VENDORED_SQUIRREL_ARCHIVE.is_file():
        return _validate_executable(
            _build_squirrel(VENDORED_SQUIRREL_ARCHIVE, build_target)
        )

    pytest.fail(
        "required standalone Squirrel runtime unavailable: restore the vendored "
        f"{SQUIRREL_ARCHIVE_NAME} archive, set {SQUIRREL_ARCHIVE_ENV}, or set "
        f"{SQUIRREL_EXECUTABLE_ENV}"
    )


def run_squirrel(
    tmp_path: Path,
    source: str,
    success_marker: str,
    *,
    name: str = "test.nut",
) -> str:
    """Execute one Squirrel program and require a clean final marker line."""
    script_path = tmp_path / name
    script_path.write_text(source)
    completed = subprocess.run(
        [str(squirrel_executable()), str(script_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    output_lines = completed.stdout.splitlines()
    assert completed.returncode == 0, (
        f"Squirrel exited with {completed.returncode}\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    assert "AN ERROR HAS OCCURED" not in completed.stdout, (
        f"Squirrel reported an execution error\nstdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    assert output_lines and output_lines[-1] == success_marker, (
        "Squirrel did not finish on its exact success marker\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    return completed.stdout
