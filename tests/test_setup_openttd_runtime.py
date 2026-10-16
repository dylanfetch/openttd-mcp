"""Filesystem and process behavior checks for the repository runtime setup."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from setup_openttd_runtime import (
    Package,
    RuntimeSetupError,
    publish_runtime,
    setup_runtime,
)

ROOT = Path(__file__).resolve().parents[1]
COMMAND = [sys.executable, str(ROOT / "tools" / "setup_openttd_runtime.py")]

APT_STUB = """\
#!{python}
import base64
import json
import os
import sys
from pathlib import Path

entry = json.loads(os.environ["STUB_PACKAGES"])[sys.argv[2]]
with Path(os.environ["STUB_LOG"]).open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({{"tool": "apt-get", "cwd": os.getcwd()}}) + "\\n")
Path(entry["filename"]).write_bytes(base64.b64decode(entry["content"]))
"""

DPKG_STUB = """\
#!{python}
import json
import os
import sys
from pathlib import Path

archive = Path(sys.argv[2])
target = Path(sys.argv[3])
with Path(os.environ["STUB_LOG"]).open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({{
        "tool": "dpkg",
        "cwd": os.getcwd(),
        "archive": str(archive),
        "target": str(target),
        "home": os.environ["HOME"],
        "tmp": os.environ["TMPDIR"],
    }}) + "\\n")
content = archive.read_bytes()
if content == b"fail extraction":
    raise SystemExit(2)
(target / "usr/games").mkdir(parents=True, exist_ok=True)
binary = target / "usr/games/openttd"
binary.write_text(
    "#!/bin/sh\\n"
    'printf "%s|%s|%s|%s\\n" "$1" "$LD_LIBRARY_PATH" "$HOME" "$PWD" > "$STUB_SMOKE_LOG"\\n'
    'exit "$STUB_SMOKE_EXIT"\\n',
    encoding="utf-8",
)
binary.chmod(0o755)
(target / "usr/share/games/openttd/baseset").mkdir(parents=True, exist_ok=True)
if os.environ["STUB_OMIT_LANG"] != "1":
    (target / "usr/share/games/openttd/lang").mkdir(parents=True, exist_ok=True)
(target / "usr/lib/x86_64-linux-gnu").mkdir(parents=True, exist_ok=True)
(target / "extracted").mkdir(exist_ok=True)
(target / "extracted" / archive.name).write_bytes(content)
"""


def write_executable(path: Path, source: str) -> None:
    path.write_text(source.format(python=sys.executable), encoding="utf-8")
    path.chmod(0o755)


def package(spec: str, filename: str, content: bytes) -> Package:
    return Package(spec, filename, hashlib.sha256(content).hexdigest())


def configure_stubs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    repo: Path,
    contents: dict[Package, bytes],
) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable(bin_dir / "apt-get", APT_STUB)
    write_executable(bin_dir / "dpkg", DPKG_STUB)
    log = repo / ".local" / "stub-processes.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    encoded = {
        item.spec: {
            "filename": item.filename,
            "content": base64.b64encode(content).decode("ascii"),
        }
        for item, content in contents.items()
    }
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("STUB_PACKAGES", json.dumps(encoded))
    monkeypatch.setenv("STUB_LOG", str(log))
    monkeypatch.setenv("STUB_SMOKE_LOG", str(log.with_name("smoke-env.txt")))
    monkeypatch.setenv("STUB_SMOKE_EXIT", "0")
    monkeypatch.setenv("STUB_OMIT_LANG", "0")
    return log


def runtime_snapshot(runtime: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(runtime)): path.read_bytes()
        for path in runtime.rglob("*")
        if path.is_file()
    }


def test_bad_digest_prevents_every_extraction_and_preserves_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    runtime = repo / ".local" / "openttd-root"
    runtime.mkdir(parents=True)
    (runtime / "prior").write_bytes(b"keep")
    first = package("one=1", "one.deb", b"one")
    second = package("two=2", "two.deb", b"expected")
    log = configure_stubs(
        monkeypatch, tmp_path, repo, {first: b"one", second: b"expected"}
    )
    cache = repo / ".local" / "debs"
    cache.mkdir()
    (cache / second.filename).write_bytes(b"corrupt")
    before = runtime_snapshot(runtime)

    with pytest.raises(RuntimeSetupError, match="SHA-256"):
        setup_runtime(repo, (first, second))

    assert runtime_snapshot(runtime) == before
    assert not log.exists()


def test_bad_download_digest_prevents_every_extraction_and_preserves_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    runtime = repo / ".local" / "openttd-root"
    runtime.mkdir(parents=True)
    (runtime / "prior").write_bytes(b"keep")
    item = package("one=1", "one.deb", b"expected")
    log = configure_stubs(monkeypatch, tmp_path, repo, {item: b"downloaded-corrupt"})
    before = runtime_snapshot(runtime)

    with pytest.raises(RuntimeSetupError, match="SHA-256"):
        setup_runtime(repo, (item,))

    assert runtime_snapshot(runtime) == before
    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert sum(record["tool"] == "apt-get" for record in records) == 1
    assert all(record["tool"] != "dpkg" for record in records)
    cached = repo / ".local" / "debs" / item.filename
    assert not cached.exists()

    monkeypatch.setenv(
        "STUB_PACKAGES",
        json.dumps(
            {
                item.spec: {
                    "filename": item.filename,
                    "content": base64.b64encode(b"expected").decode("ascii"),
                }
            }
        ),
    )
    installed = setup_runtime(repo, (item,))

    assert installed == runtime
    assert cached.read_bytes() == b"expected"
    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert sum(record["tool"] == "apt-get" for record in records) == 2
    assert sum(record["tool"] == "dpkg" for record in records) == 1


def test_valid_prepopulated_cache_runs_offline_without_apt_get(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    first = package("one=1", "one.deb", b"first")
    second = package("two=2", "two.deb", b"second")
    log = configure_stubs(
        monkeypatch, tmp_path, repo, {first: b"first", second: b"second"}
    )
    (tmp_path / "bin" / "apt-get").unlink()
    cache = repo / ".local" / "debs"
    cache.mkdir()
    (cache / first.filename).write_bytes(b"first")
    (cache / second.filename).write_bytes(b"second")

    runtime = setup_runtime(repo, (first, second))

    assert (runtime / "usr" / "games" / "openttd").is_file()
    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert all(record["tool"] != "apt-get" for record in records)
    assert sum(record["tool"] == "dpkg" for record in records) == 2


def test_repeat_run_rebuilds_without_stale_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    first = package("one=1", "one.deb", b"first")
    second = package("two=2", "two.deb", b"second")
    log = configure_stubs(
        monkeypatch, tmp_path, repo, {first: b"first", second: b"second"}
    )

    runtime = setup_runtime(repo, (first, second))
    stale = runtime / "stale-from-prior-run"
    stale.write_bytes(b"stale")
    setup_runtime(repo, (first, second))

    assert not stale.exists()
    assert (runtime / "extracted" / "one.deb").read_bytes() == b"first"
    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert sum(record["tool"] == "apt-get" for record in records) == 2
    assert sum(record["tool"] == "dpkg" for record in records) == 4


def test_failed_extraction_does_not_publish_partial_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    runtime = repo / ".local" / "openttd-root"
    runtime.mkdir(parents=True)
    (runtime / "prior").write_bytes(b"keep")
    first = package("one=1", "one.deb", b"first")
    second = package("two=2", "two.deb", b"fail extraction")
    configure_stubs(
        monkeypatch,
        tmp_path,
        repo,
        {first: b"first", second: b"fail extraction"},
    )
    before = runtime_snapshot(runtime)

    with pytest.raises(RuntimeSetupError, match="extract"):
        setup_runtime(repo, (first, second))

    assert runtime_snapshot(runtime) == before
    assert not (runtime / "extracted").exists()


def test_missing_language_data_is_not_published(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    runtime = repo / ".local" / "openttd-root"
    runtime.mkdir(parents=True)
    (runtime / "prior").write_bytes(b"keep")
    item = package("one=1", "one.deb", b"one")
    log = configure_stubs(monkeypatch, tmp_path, repo, {item: b"one"})
    monkeypatch.setenv("STUB_OMIT_LANG", "1")

    with pytest.raises(RuntimeSetupError, match="lang"):
        setup_runtime(repo, (item,))

    assert runtime_snapshot(runtime) == {"prior": b"keep"}
    assert not log.with_name("smoke-env.txt").exists()


def test_failed_candidate_smoke_does_not_replace_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    runtime = repo / ".local" / "openttd-root"
    runtime.mkdir(parents=True)
    (runtime / "prior").write_bytes(b"keep")
    item = package("one=1", "one.deb", b"one")
    configure_stubs(monkeypatch, tmp_path, repo, {item: b"one"})
    monkeypatch.setenv("STUB_SMOKE_EXIT", "7")

    with pytest.raises(RuntimeSetupError, match="smoke"):
        setup_runtime(repo, (item,))

    assert runtime_snapshot(runtime) == {"prior": b"keep"}


def test_publish_interruption_restores_moved_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    local = tmp_path / ".local"
    runtime = local / "openttd-root"
    candidate = local / "stage" / "openttd-root"
    runtime.mkdir(parents=True)
    candidate.mkdir(parents=True)
    (runtime / "prior").write_bytes(b"keep")
    (candidate / "replacement").write_bytes(b"new")
    original_replace = Path.replace

    def interrupt_candidate(path: Path, target: Path) -> Path:
        if path == candidate:
            raise KeyboardInterrupt
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", interrupt_candidate)

    with pytest.raises(KeyboardInterrupt):
        publish_runtime(candidate, runtime)

    assert runtime_snapshot(runtime) == {"prior": b"keep"}
    assert not (local / "openttd-root.previous").exists()


def test_setup_confines_process_paths_and_created_repo_files_to_local(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    item = package("one=1", "one.deb", b"one")
    log = configure_stubs(monkeypatch, tmp_path, repo, {item: b"one"})

    setup_runtime(repo, (item,))

    assert {path.relative_to(repo).parts[0] for path in repo.rglob("*")} == {".local"}
    local = (repo / ".local").resolve()
    records = [json.loads(line) for line in log.read_text().splitlines()]
    for record in records:
        for field in ("cwd", "archive", "target", "home", "tmp"):
            if field in record:
                assert Path(record[field]).resolve().is_relative_to(local)
    smoke_fields = log.with_name("smoke-env.txt").read_text().strip().split("|")
    argument, library_path, home, cwd = smoke_fields
    assert argument == "-h"
    for observed in (library_path, home, cwd):
        assert Path(observed).resolve().is_relative_to(local)
    assert library_path.endswith("/usr/lib/x86_64-linux-gnu")


@pytest.mark.parametrize("available", ["apt-get", "dpkg"])
def test_missing_required_program_returns_actionable_nonzero_outcome(
    tmp_path: Path, available: str
) -> None:
    repo = tmp_path / "repo"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable(bin_dir / available, "#!{python}\nraise SystemExit(0)\n")
    env = os.environ.copy()
    env.update(
        {
            "PATH": str(bin_dir),
            "OPENTTD_MCP_REPO_ROOT": str(repo),
        }
    )

    result = subprocess.run(
        COMMAND, env=env, capture_output=True, text=True, check=False
    )

    missing = "dpkg" if available == "apt-get" else "apt-get"
    assert result.returncode != 0
    assert f"'{missing}'" in result.stderr
    assert not (repo / ".local" / "openttd-root").exists()


def test_retry_recovers_sibling_backup_before_missing_program_failure(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    previous = repo / ".local" / "openttd-root.previous"
    previous.mkdir(parents=True)
    (previous / "prior").write_bytes(b"keep")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "PATH": str(bin_dir),
            "OPENTTD_MCP_REPO_ROOT": str(repo),
        }
    )

    result = subprocess.run(
        COMMAND, env=env, capture_output=True, text=True, check=False
    )

    runtime = repo / ".local" / "openttd-root"
    assert result.returncode != 0
    assert "'apt-get'" in result.stderr
    assert runtime_snapshot(runtime) == {"prior": b"keep"}
    assert not previous.exists()


def test_retry_keeps_canonical_runtime_and_discards_leftover_backup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    runtime = repo / ".local" / "openttd-root"
    previous = repo / ".local" / "openttd-root.previous"
    runtime.mkdir(parents=True)
    previous.mkdir()
    (runtime / "current").write_bytes(b"current")
    (previous / "prior").write_bytes(b"prior")
    monkeypatch.setenv("PATH", str(tmp_path / "missing-programs"))

    with pytest.raises(RuntimeSetupError, match="'dpkg'"):
        setup_runtime(repo, ())

    assert runtime_snapshot(runtime) == {"current": b"current"}
    assert not previous.exists()


def test_unavailable_pinned_version_returns_nonzero_without_replacing_runtime(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    runtime = repo / ".local" / "openttd-root"
    runtime.mkdir(parents=True)
    (runtime / "prior").write_bytes(b"keep")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable(bin_dir / "apt-get", "#!{python}\nraise SystemExit(100)\n")
    write_executable(bin_dir / "dpkg", "#!{python}\nraise SystemExit(0)\n")
    env = os.environ.copy()
    env.update(
        {
            "PATH": str(bin_dir),
            "OPENTTD_MCP_REPO_ROOT": str(repo),
        }
    )

    result = subprocess.run(
        COMMAND, env=env, capture_output=True, text=True, check=False
    )

    assert result.returncode != 0
    assert "openttd=15.1-1" in result.stderr
    assert runtime_snapshot(runtime) == {"prior": b"keep"}


@pytest.mark.parametrize(
    ("argument", "expected_returncode"), [("--help", 0), ("--unknown", 2)]
)
def test_argument_handling_does_not_start_setup(
    tmp_path: Path, argument: str, expected_returncode: int
) -> None:
    repo = tmp_path / "repo"
    env = os.environ.copy()
    env["OPENTTD_MCP_REPO_ROOT"] = str(repo)

    result = subprocess.run(
        [*COMMAND, argument], env=env, capture_output=True, text=True, check=False
    )

    assert result.returncode == expected_returncode
    assert not (repo / ".local").exists()
