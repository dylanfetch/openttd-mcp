"""Shared runtime scaffold for independent OpenTTD save verifiers."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from openttd_mcp.admin import AdminLifecycleConfig
from openttd_mcp.runtime import (
    OTTD_BIN,
    OTTD_DATA,
    ROOT,
    build_runtime_env,
    symlink_or_copy,
)

DEFAULT_TICK_BUDGET = 500000
VERIFY_DRIVER_ENV = "OPENTTD_VERIFY_DRIVER"
DEDICATED_DRIVER = "dedicated"

OPENTTD_CFG_TEMPLATE = """[ai]
ai_in_multiplayer = true
[network]
server_name = {server_name}
client_name = {client_name}
server_game_type = local
server_advertise = false
{admin_lines}
[gui]
autosave_interval = 0
autosave_on_exit = false
"""


def create_run_root(
    *,
    prefix: str,
    game_start_script: str,
    info_nut: str,
    main_nut: str,
    log_filename: str,
    admin_config: AdminLifecycleConfig | None = None,
) -> tuple[Path, Path, Path, dict[str, str]]:
    """Create one verifier's isolated OpenTTD run root and return its output paths."""
    run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
    root = ROOT / ".local" / "verify-runs" / f"{prefix}-{run_id}"
    root.mkdir(parents=True, exist_ok=True)
    symlink_or_copy(OTTD_DATA / "baseset", root / "baseset")
    symlink_or_copy(OTTD_DATA / "lang", root / "lang")
    ai_dir = root / "ai" / "McpBridge"
    ai_dir.mkdir(parents=True, exist_ok=True)
    (ai_dir / "info.nut").write_text(info_nut)
    (ai_dir / "main.nut").write_text(main_nut)
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "scripts" / "game_start.scr").write_text(game_start_script)
    network_name = f"{prefix}-verifier"
    admin_lines = ""
    if admin_config is not None:
        admin_lines = (
            f"server_admin_port = {admin_config.port}\n"
            f"admin_password = {admin_config.password}\n"
            "allow_insecure_admin_login = true"
        )
    (root / "openttd.cfg").write_text(
        OPENTTD_CFG_TEMPLATE.format(
            server_name=network_name,
            client_name=network_name,
            admin_lines=admin_lines,
        )
    )
    env = build_runtime_env(root)
    return root, root / log_filename, root / "verify_result.json", env


def resolve_verify_driver() -> str:
    """Return the selected verifier driver, defaulting to uncapped null video."""
    raw = os.environ.get(VERIFY_DRIVER_ENV, "")
    normalized = raw.strip().lower()
    if normalized == DEDICATED_DRIVER:
        return DEDICATED_DRIVER
    if normalized in ("", "null"):
        return "null"
    raise ValueError(
        f"Unrecognized {VERIFY_DRIVER_ENV}={raw!r}; expected 'dedicated' or 'null'"
    )


def build_launch_command(
    root: Path,
    save_path: Path,
    port: int = 3996,
    tick_budget: int = DEFAULT_TICK_BUDGET,
    driver: str | None = None,
    *,
    blitter: str | None = None,
) -> list[str]:
    """Build the common headless OpenTTD verifier command."""
    selected_driver = resolve_verify_driver() if driver is None else driver
    driver_args = (
        ["-D", f"127.0.0.1:{port}"]
        if selected_driver == DEDICATED_DRIVER
        else ["-v", f"null:ticks={tick_budget}"]
    )
    return [
        str(OTTD_BIN),
        *driver_args,
        "-g",
        str(save_path),
        "-c",
        str(root / "openttd.cfg"),
        "-x",
        "-I",
        "OpenGFX",
        "-S",
        "NoSound",
        "-M",
        "NoMusic",
        "-s",
        "null",
        "-m",
        "null",
        *(["-b", blitter] if blitter is not None else []),
        "-d",
        "script=4",
    ]


def launch_openttd(
    root: Path,
    save_path: Path,
    env: dict[str, str],
    port: int = 3996,
    tick_budget: int = DEFAULT_TICK_BUDGET,
    *,
    driver: str,
    blitter: str | None = None,
) -> subprocess.Popen:
    """Launch OpenTTD with the verifier's isolated runtime environment."""
    cmd = build_launch_command(
        root, save_path, port, tick_budget, driver, blitter=blitter
    )
    return subprocess.Popen(
        cmd,
        cwd=str(root),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )


def read_until(
    proc: subprocess.Popen,
    log_path: Path,
    marker: str,
    timeout: float,
    *,
    on_line: Callable[[str], None] | None = None,
) -> str:
    """Pump stdout into the log until the verifier's completion marker appears.

    The blocking readline lives on a daemon thread so the deadline holds in
    wall-clock time even when OpenTTD is alive but silent; a bare readline
    loop would hang a standalone CLI run forever on a wedged game.
    """
    outcome: dict[str, str | bool | BaseException] = {}
    done = threading.Event()

    def pump() -> None:
        try:
            stream = proc.stdout
            if stream is None:
                return
            with log_path.open("a") as log:
                for line in stream:
                    log.write(line)
                    if on_line is not None:
                        on_line(line)
                    if marker in line:
                        outcome["found"] = line
                        break
                else:
                    outcome["eof"] = True
        except BaseException as exc:  # noqa: BLE001 -- propagate thread failures
            outcome["error"] = exc
        finally:
            # Signal only after the with-block has closed (and so flushed) the
            # log. The waiter parses it immediately after this event.
            done.set()

    threading.Thread(target=pump, daemon=True).start()
    if not done.wait(timeout):
        raise TimeoutError(f"Timed out waiting for {marker}")
    error = outcome.get("error")
    if isinstance(error, BaseException):
        raise error
    found = outcome.get("found")
    if isinstance(found, str):
        return found
    if outcome.get("eof"):
        raise RuntimeError(f"OpenTTD exited before marker {marker}; rc={proc.poll()}")
    raise RuntimeError(f"OpenTTD stdout unavailable before marker {marker}")


def _request_quit(proc: subprocess.Popen) -> None:
    # A broken stdin pipe (OpenTTD already gone) must never turn a verdict
    # that is already on disk into a reported failure.
    if proc.stdin is not None:
        try:
            proc.stdin.write("quit\n")
            proc.stdin.flush()
        except Exception:  # noqa: BLE001, S110 -- broken pipe must not fail a written verdict
            pass


def emit_result(
    proc: subprocess.Popen,
    *,
    save_path: Path,
    log_path: Path,
    result_path: Path,
    result: dict,
    verifier_module: str,
    extra_printed_paths: tuple[tuple[str, Path], ...] = (),
    tolerate_slow_shutdown: bool = False,
    driver: str,
) -> None:
    """Write the verdict, stop OpenTTD, and emit the verifier's common stdout block."""
    rendered = json.dumps(
        {**result, "driver": driver, "verifier_module": verifier_module},
        indent=2,
        sort_keys=True,
    )
    result_path.write_text(rendered)
    if driver == DEDICATED_DRIVER:
        _request_quit(proc)
        if tolerate_slow_shutdown:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                # A slow shutdown must not turn a completed verification into a
                # reported failure; the verdict below is already on disk.
                proc.kill()
                proc.wait(timeout=5)
        else:
            proc.wait(timeout=10)
    else:
        if proc.poll() is None:
            proc.kill()
    print(f"VERIFY_SAVE={save_path}")
    print(f"VERIFY_LOG={log_path}")
    for label, path in extra_printed_paths:
        print(f"{label}={path}")
    print(f"VERIFY_RESULT={result_path}")
    print(rendered)


def teardown(proc: subprocess.Popen, *, driver: str) -> None:
    """Best-effort verifier teardown for the caller's ``finally`` block."""
    if proc.poll() is None:
        if driver != DEDICATED_DRIVER:
            proc.kill()
            return
        _request_quit(proc)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
