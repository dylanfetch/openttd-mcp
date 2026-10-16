"""Shared OpenTTD runtime paths and helpers for the trial/verify/launch scripts.

Previously every runner script defined its own identical copy of the extracted
OpenTTD root paths and a ``symlink_or_copy`` helper. They are centralised here so
a fix to the runtime layout or the symlink/copy fallback is made once instead of
in seven places.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

ROOT = Path(
    os.environ.get("OPENTTD_MCP_REPO_ROOT", Path(__file__).resolve().parents[2])
)
OTTD_ROOT = ROOT / ".local" / "openttd-root"
OTTD_DATA = OTTD_ROOT / "usr" / "share" / "games" / "openttd"
OTTD_BIN = OTTD_ROOT / "usr" / "games" / "openttd"
LIB_PATH = OTTD_ROOT / "usr" / "lib" / "x86_64-linux-gnu"


def symlink_or_copy(src: Path, dst: Path) -> None:
    if dst.exists() or dst.is_symlink():
        if dst.is_dir() and not dst.is_symlink():
            shutil.rmtree(dst)
        else:
            dst.unlink()
    try:
        dst.symlink_to(src, target_is_directory=src.is_dir())
    except OSError:
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)


def package_subprocess_env() -> dict[str, str]:
    """Environment for ``python -m openttd_mcp.*`` child processes.

    Prepends ``src/`` to PYTHONPATH so the child resolves the package even when
    the parent imported it through an in-process sys.path entry the child would
    not inherit (running an entry point from a checkout without an installed
    environment).
    """
    env = os.environ.copy()
    src_dir = str(ROOT / "src")
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = src_dir if not existing else src_dir + os.pathsep + existing
    return env


def build_runtime_env(
    run_root: Path, extra: dict[str, str] | None = None
) -> dict[str, str]:
    """Build the child OpenTTD environment.

    Points ``LD_LIBRARY_PATH`` at the extracted libs and isolates HOME/XDG under
    ``run_root`` so launches never touch the real user config. ``extra`` lets a
    caller add launch-specific variables (e.g. software-GL hints) before the
    HOME/XDG directories are created.
    """
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = str(LIB_PATH)
    env["HOME"] = str(run_root / "home")
    env["XDG_CONFIG_HOME"] = str(run_root / "xdg-config")
    env["XDG_DATA_HOME"] = str(run_root / "xdg-data")
    if extra:
        env.update(extra)
    for key in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
        Path(env[key]).mkdir(parents=True, exist_ok=True)
    return env
