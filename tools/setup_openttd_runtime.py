#!/usr/bin/env python3
"""Build the pinned Ubuntu OpenTTD runtime below the selected repo's .local/."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple


class Package(NamedTuple):
    spec: str
    filename: str
    sha256: str


PACKAGES = (
    Package(
        "openttd=15.1-1",
        "openttd_15.1-1_amd64.deb",
        "c89c5812b73daf97c55a1ee6ae0bc77ba06144b664ff445cf39104534e862d8b",
    ),
    Package(
        "openttd-data=15.1-1",
        "openttd-data_15.1-1_all.deb",
        "e7fc6ce4712d920f3c20ad5b13f98dba49f1fc0b7ea9f7b3b60bce91c9a7752b",
    ),
    Package(
        "openttd-opengfx=7.1-3",
        "openttd-opengfx_7.1-3_all.deb",
        "2dfaf3a9f97cc3e348e9416dce6ae19ff842230229c31fdc592b7aae8654d61e",
    ),
    Package(
        "openttd-openmsx=0.4.2-2",
        "openttd-openmsx_0.4.2-2_all.deb",
        "c459b2fda10c11d878a3ae56be399ccb0c3ffa282a6ed3e13d23244f934fd2ba",
    ),
    Package(
        "libicu78=78.2-2ubuntu1",
        "libicu78_78.2-2ubuntu1_amd64.deb",
        "c8b97930f9e365d6d00978144b468ac8397ef07d2fb2c453869f05fc3a98c4ca",
    ),
    Package(
        "libfluidsynth3=2.4.8+dfsg-1",
        "libfluidsynth3_2.4.8+dfsg-1_amd64.deb",
        "5ab91595f37941656f778f9556e9de3a7c32b7d0c8fa3fd120b90fe014e6152a",
    ),
    Package(
        "libinstpatch-1.0-2=1.1.7-1.1",
        "libinstpatch-1.0-2_1.1.7-1.1_amd64.deb",
        "3b8bb6eb10d345a52a72b4514c14d4e2e0f8ebbd32d2207e5a3959b5a14a81ad",
    ),
    Package(
        "libsdl3-0=3.4.2+ds-1ubuntu1",
        "libsdl3-0_3.4.2+ds-1ubuntu1_amd64.deb",
        "9cffe342d34a7787e25ddb28d6244158a4b44f977a2ab29bc6f4526a29e11825",
    ),
    Package(
        "timgm6mb-soundfont=1.3-5build1",
        "timgm6mb-soundfont_1.3-5build1_all.deb",
        "e34727ced73876bd5945b8104fa3a542ec752747f2b5855945edefccba09c7b9",
    ),
)


class RuntimeSetupError(RuntimeError):
    pass


def selected_repo_root() -> Path:
    configured = os.environ.get("OPENTTD_MCP_REPO_ROOT")
    return (
        Path(configured).expanduser().resolve()
        if configured
        else Path(__file__).resolve().parents[1]
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_program(name: str) -> str:
    resolved = shutil.which(name)
    if resolved is None:
        raise RuntimeSetupError(
            f"Required program '{name}' is unavailable. Run this command in a "
            "supported Ubuntu 26.04 LTS Resolute amd64 environment where apt-get "
            "and dpkg are already available; this script cannot prepare the runtime "
            f"without {name}."
        )
    return resolved


def command_environment(workspace: Path) -> dict[str, str]:
    env = os.environ.copy()
    for name in ("home", "tmp", "xdg-cache", "xdg-config", "xdg-data"):
        (workspace / name).mkdir()
    env.update(
        {
            "HOME": str(workspace / "home"),
            "TMPDIR": str(workspace / "tmp"),
            "XDG_CACHE_HOME": str(workspace / "xdg-cache"),
            "XDG_CONFIG_HOME": str(workspace / "xdg-config"),
            "XDG_DATA_HOME": str(workspace / "xdg-data"),
        }
    )
    return env


def remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def recover_previous_runtime(runtime_root: Path) -> None:
    previous = runtime_root.with_name(f"{runtime_root.name}.previous")
    if not (previous.exists() or previous.is_symlink()):
        return
    if runtime_root.exists() or runtime_root.is_symlink():
        remove_path(previous)
    else:
        previous.replace(runtime_root)


def publish_runtime(candidate: Path, runtime_root: Path) -> None:
    previous = runtime_root.with_name(f"{runtime_root.name}.previous")
    moved_previous = False
    try:
        if runtime_root.exists() or runtime_root.is_symlink():
            runtime_root.replace(previous)
            moved_previous = True
        candidate.replace(runtime_root)
    except BaseException:
        if moved_previous and not (runtime_root.exists() or runtime_root.is_symlink()):
            previous.replace(runtime_root)
        raise
    remove_path(previous)


def validate_candidate(candidate: Path) -> None:
    required = (
        candidate / "usr" / "games" / "openttd",
        candidate / "usr" / "share" / "games" / "openttd" / "baseset",
        candidate / "usr" / "share" / "games" / "openttd" / "lang",
        candidate / "usr" / "lib" / "x86_64-linux-gnu",
    )
    missing = [path.relative_to(candidate) for path in required if not path.exists()]
    if missing:
        rendered = ", ".join(str(path) for path in missing)
        raise RuntimeSetupError(
            f"Extracted packages did not produce required runtime paths: {rendered}; "
            "the existing runtime was left unchanged."
        )


def smoke_candidate(candidate: Path, env: dict[str, str]) -> None:
    binary = candidate / "usr" / "games" / "openttd"
    smoke_env = env.copy()
    smoke_env["LD_LIBRARY_PATH"] = str(candidate / "usr" / "lib" / "x86_64-linux-gnu")
    try:
        completed = subprocess.run(
            [binary, "-h"],
            cwd=candidate,
            env=smoke_env,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise RuntimeSetupError(
            "The extracted OpenTTD candidate could not start for its -h smoke "
            "check. Confirm that the binary is executable and the supported host "
            "libraries are available; the existing runtime was left unchanged."
        ) from error
    if completed.returncode != 0:
        raise RuntimeSetupError(
            "The extracted OpenTTD candidate failed its -h smoke check with "
            f"exit code {completed.returncode}. Confirm that the supported host "
            "libraries are available; the existing runtime was left unchanged."
        )


def setup_runtime(repo_root: Path, packages: tuple[Package, ...] = PACKAGES) -> Path:
    local_root = repo_root.resolve() / ".local"
    local_root.mkdir(parents=True, exist_ok=True)
    runtime_root = local_root / "openttd-root"
    recover_previous_runtime(runtime_root)
    downloads = local_root / "debs"
    downloads.mkdir(exist_ok=True)

    missing = []
    for package in packages:
        archive = downloads / package.filename
        if not archive.exists():
            missing.append(package)
        elif not archive.is_file() or file_sha256(archive) != package.sha256:
            raise RuntimeSetupError(
                f"Cached package {archive} failed its SHA-256 check; replace or "
                "remove that file before retrying. Nothing was downloaded or "
                "extracted."
            )

    apt_get = require_program("apt-get") if missing else None
    dpkg = require_program("dpkg")

    with tempfile.TemporaryDirectory(prefix="openttd-setup-", dir=local_root) as raw:
        workspace = Path(raw)
        staged_downloads = workspace / "downloads"
        candidate = workspace / "openttd-root"
        staged_downloads.mkdir()
        candidate.mkdir()
        env = command_environment(workspace)

        if apt_get is not None:
            for package in missing:
                try:
                    subprocess.run(
                        [apt_get, "download", package.spec],
                        cwd=staged_downloads,
                        env=env,
                        check=True,
                    )
                except (OSError, subprocess.CalledProcessError) as error:
                    raise RuntimeSetupError(
                        f"Could not download pinned package {package.spec}. Possible "
                        "causes include disabled Ubuntu Resolute main or universe "
                        "sources, stale package lists, network failure, or that exact "
                        "version no longer being available."
                    ) from error
                staged = staged_downloads / package.filename
                if not staged.is_file() or file_sha256(staged) != package.sha256:
                    raise RuntimeSetupError(
                        f"Downloaded package {package.filename} failed its SHA-256 "
                        "check; it was not added to the persistent cache and "
                        "nothing was extracted."
                    )
                staged.replace(downloads / package.filename)

        for package in packages:
            archive = downloads / package.filename
            if not archive.is_file():
                raise RuntimeSetupError(
                    f"Download for {package.spec} did not produce expected file "
                    f"{package.filename}."
                )
            actual = file_sha256(archive)
            if actual != package.sha256:
                raise RuntimeSetupError(
                    f"SHA-256 mismatch for {package.filename}: expected "
                    f"{package.sha256}, received {actual}; nothing was extracted."
                )

        for package in packages:
            try:
                subprocess.run(
                    [dpkg, "-x", str(downloads / package.filename), str(candidate)],
                    cwd=workspace,
                    env=env,
                    check=True,
                )
            except (OSError, subprocess.CalledProcessError) as error:
                raise RuntimeSetupError(
                    f"dpkg could not extract {package.filename}; the existing "
                    "runtime was left unchanged."
                ) from error

        validate_candidate(candidate)
        smoke_candidate(candidate, env)
        try:
            publish_runtime(candidate, runtime_root)
        except OSError as error:
            raise RuntimeSetupError(
                f"Could not publish the completed runtime at {runtime_root}."
            ) from error
        return runtime_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    try:
        runtime_root = setup_runtime(selected_repo_root())
    except (OSError, RuntimeSetupError) as error:
        print(f"OpenTTD runtime setup failed: {error}", file=sys.stderr)
        return 1
    print(f"OpenTTD runtime ready at {runtime_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
