"""Shared OpenTTD episode lifecycle helpers for MCP-era runners."""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import selectors
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from openttd_mcp.admin import AdminClient, AdminLifecycleConfig, wait_for_save_on_disk
from openttd_mcp.runtime import (
    OTTD_BIN,
    OTTD_DATA,
    ROOT,
    build_runtime_env,
    package_subprocess_env,
    symlink_or_copy,
)
from openttd_mcp.server import (
    append_jsonl as append_jsonl,  # noqa: PLC0414 -- re-export for episode runners
)
from openttd_mcp.server import (
    parse_result_line as parse_result_line,  # noqa: PLC0414 -- re-export for episode runners
)
from openttd_mcp.server import (
    squirrel_string,
    write_command_file,
    write_json,
)
from openttd_mcp.squirrel import MCP_BRIDGE_DIR
from openttd_mcp.verify.bus_route import ROUTE_VERIFY_TIMEOUT_SEC

BRIDGE_SRC = MCP_BRIDGE_DIR
RUNS_DIR = ROOT / "artifacts" / "runs"
LOCAL_RUNS_DIR = ROOT / ".local" / "runs"

OBSERVATION_MARKER = "MCP_OBSERVATION|"
SNAPSHOT_BEGIN = "MCP_MAP_SNAPSHOT_BEGIN|"
SNAPSHOT_TILES = "MCP_MAP_SNAPSHOT_TILES|"
SNAPSHOT_OBJECTS = "MCP_MAP_SNAPSHOT_OBJECTS|"
SNAPSHOT_END = "MCP_MAP_SNAPSHOT_END|"
BRIDGE_READY_MARKER = "MCP_BRIDGE_READY"
STARTUP_READY_POLL_INTERVAL_SEC = 0.1

DEFAULT_SEED = 1337
DEFAULT_MAP_LOG2 = 6  # 64x64
DEFAULT_TOWN_COUNT = 4
CUSTOM_TOWN_COUNT_MODE = 4
# OpenTTD's own DEF_START_YEAR (timer_game_common.h). MIN/MAX bound the input to
# the original TTD calendar span (ORIGINAL_BASE_YEAR..ORIGINAL_MAX_YEAR) rather
# than the engine's absolute MIN_YEAR=0 / MAX_YEAR=5000000 setting clamp: outside
# 1920..2090 the base vehicle set has no engines at all, so such a year is a typo
# rather than a scenario.
DEFAULT_STARTING_YEAR = 1950
MIN_STARTING_YEAR = 1920
MAX_STARTING_YEAR = 2090
DEFAULT_BUDGET = 40
DEFAULT_SAVE_INTERVAL = 5
VERIFIER_TIMEOUT_EXIT_CODE = 124
VERIFIER_MISSING_RESULT_EXIT_CODE = 125
# The outer subprocess cap must sit strictly above the verifier's own inner
# read_until so a genuine timeout surfaces the verifier's descriptive marker
# diagnostic (last output / rows received) rather than a bare verifier_timeout,
# and so the inner wait's full budget is actually reachable. The
# margin covers verifier-process startup + reload setup that runs before
# read_until begins ticking. Shared by run_verifier here and run_network_verifier.
VERIFIER_SUBPROCESS_MARGIN_SEC = 120


def capture_openttd_output(
    proc: Any,
    log_path: Path,
    ready: threading.Event,
    *,
    line_callback: Callable[[str], None] | None = None,
    thread_name: str = "openttd-log",
) -> threading.Thread:
    """Persist child output and signal when the bridge readiness line arrives."""
    if proc.stdout is None:
        raise RuntimeError("OpenTTD stdout pipe missing")

    def copy_lines() -> None:
        with log_path.open("a") as log:
            for line in proc.stdout:
                log.write(line)
                log.flush()
                if BRIDGE_READY_MARKER in line:
                    ready.set()
                if line_callback is not None:
                    line_callback(line)

    thread = threading.Thread(target=copy_lines, name=thread_name, daemon=True)
    thread.start()
    return thread


def wait_for_process_event(
    event: threading.Event,
    proc: Any,
    *,
    description: str,
    timeout: float,
    poll_interval: float = STARTUP_READY_POLL_INTERVAL_SEC,
) -> None:
    """Wait boundedly for child output state while noticing an early child exit."""
    deadline = time.monotonic() + timeout
    while True:
        if event.is_set():
            return
        exit_code = proc.poll()
        if exit_code is not None:
            raise RuntimeError(
                f"OpenTTD exited with code {exit_code} before {description}"
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"timed out waiting for {description}; OpenTTD is still running"
            )
        event.wait(timeout=min(poll_interval, remaining))


def wait_for_bridge_ready(
    ready: threading.Event,
    proc: Any,
    *,
    timeout: float,
    poll_interval: float = STARTUP_READY_POLL_INTERVAL_SEC,
) -> None:
    """Wait for the standing McpBridge readiness marker."""
    wait_for_process_event(
        ready,
        proc,
        description=BRIDGE_READY_MARKER,
        timeout=timeout,
        poll_interval=poll_interval,
    )


def now_id() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")  # noqa: DTZ005 -- local wall-clock run id


def write_openttd_config(
    run_root: Path,
    map_log2: int,
    admin_config: AdminLifecycleConfig | None = None,
    num_towns: int = DEFAULT_TOWN_COUNT,
    starting_year: int = DEFAULT_STARTING_YEAR,
) -> Path:
    if (
        not isinstance(starting_year, int)
        or not MIN_STARTING_YEAR <= starting_year <= MAX_STARTING_YEAR
    ):
        raise ValueError(
            f"starting_year must be an integer between {MIN_STARTING_YEAR} and "
            f"{MAX_STARTING_YEAR}, got {starting_year!r}"
        )
    cfg = run_root / "openttd.cfg"
    # The admin listener only opens when admin auth is configured; insecure
    # login is acceptable because the server binds loopback-only (-D 127.0.0.1).
    admin_lines = ""
    if admin_config is not None:
        admin_lines = (
            f"server_admin_port = {admin_config.port}\n"
            f"admin_password = {admin_config.password}\n"
            "allow_insecure_admin_login = true\n"
        )
    cfg.write_text(
        f"""[misc]
display_opt = SHOW_TOWN_NAMES|SHOW_STATION_NAMES|SHOW_SIGNS|FULL_ANIMATION|FULL_DETAIL|WAYPOINTS

[network]
server_name = general-surface-spike
client_name = general-surface-controller
server_game_type = local
server_advertise = false
pause_on_join = false
autoclean_companies = false
{admin_lines}
[ai]
ai_in_multiplayer = true

[difficulty]
terrain_type = 0
quantity_sea_lakes = 0
number_towns = {CUSTOM_TOWN_COUNT_MODE}
vehicle_breakdowns = 1
max_no_competitors = 0
disasters = false
infinite_money = false

[game_creation]
town_name = english
landscape = temperate
starting_year = {starting_year}
map_x = {map_log2}
map_y = {map_log2}
tgen_smoothness = 0
amount_of_rivers = 0
custom_town_number = {num_towns}
water_borders = 15

[economy]
town_layout = 2
# Keep the network calendar date and AIDate's economy date
# on the same whole-day clock. These are OpenTTD 15.1's defaults, pinned here
# because game-duration enforcement compares the two independent surfaces.
timekeeping_units = 0
minutes_per_calendar_year = 12

[vehicle]
never_expire_vehicles = false

[gui]
autosave_interval = 0
autosave_on_exit = false
autosave_on_network_disconnect = false
"""
    )
    return cfg


def assert_expected_town_count(
    observation: dict[str, Any],
    expected_count: int = DEFAULT_TOWN_COUNT,
    *,
    seed: int | None = None,
) -> None:
    """Fail before any LLM spend if map generation ignored the town-count pin."""
    towns = observation.get("towns")
    if not isinstance(towns, list):
        seed_text = f" for seed {seed}" if seed is not None else ""
        raise RuntimeError(
            f"OpenTTD startup observation{seed_text} did not include a towns[] array; "
            "cannot verify the configured town count before LLM spend"
        )
    actual_count = len(towns)
    if actual_count == expected_count:
        return
    seed_text = f" for seed {seed}" if seed is not None else ""
    raise RuntimeError(
        f"OpenTTD generated {actual_count} towns{seed_text}; expected {expected_count} from "
        f"openttd.cfg (difficulty.number_towns = {CUSTOM_TOWN_COUNT_MODE}, "
        f"game_creation.custom_town_number = {expected_count}). "
        "Aborting before LLM spend."
    )


def prepare_runtime(
    run_id: str | Path,
    map_log2: int,
    admin_config: AdminLifecycleConfig | None = None,
    num_towns: int = DEFAULT_TOWN_COUNT,
    starting_year: int = DEFAULT_STARTING_YEAR,
    emit_boot_snapshot: bool = True,
) -> tuple[Path, Path]:
    if not OTTD_BIN.exists():
        raise SystemExit(
            f"Missing OpenTTD binary at {OTTD_BIN}; see docs/setup/openttd-runtime-setup.md"
        )
    requested_run_dir = Path(run_id)
    run_dir = (
        requested_run_dir
        if requested_run_dir.is_absolute()
        else LOCAL_RUNS_DIR / requested_run_dir
    )
    run_root = run_dir / "openttd-root"
    run_root.mkdir(parents=True, exist_ok=True)
    symlink_or_copy(OTTD_DATA / "baseset", run_root / "baseset")
    symlink_or_copy(OTTD_DATA / "lang", run_root / "lang")
    ai_dst = run_root / "ai" / "McpBridge"
    ai_dst.mkdir(parents=True, exist_ok=True)
    (run_root / "scripts").mkdir(parents=True, exist_ok=True)
    for name in ("info.nut", "main.nut", "commands.nut"):
        shutil.copy2(BRIDGE_SRC / name, ai_dst / name)
    (run_root / "scripts" / "game_start.scr").write_text("start_ai McpBridge\n")
    write_openttd_config(
        run_root,
        map_log2,
        admin_config,
        num_towns=num_towns,
        starting_year=starting_year,
    )
    write_empty_commands(ai_dst, emit_map_snapshot=emit_boot_snapshot)
    return run_root, ai_dst


def write_empty_commands(ai_dst: Path, *, emit_map_snapshot: bool = True) -> None:
    content = (
        "class Commands {\n"
        f'    timestamp = "{squirrel_string("general-surface-initial")}";\n'
        f"    emit_map_snapshot = {'true' if emit_map_snapshot else 'false'};\n"
        "    lean_snapshot = true;\n"
        "    commands = [];\n"
        "}\n"
    )
    (ai_dst / "commands.nut").write_text(content)


def write_command(ai_dst: Path, timestamp: str, command: list[Any]) -> None:
    write_command_file(
        ai_dst, timestamp, command, emit_map_snapshot=True, lean_snapshot=True
    )


def write_lifecycle_metadata(
    run_dir: Path, admin_config: AdminLifecycleConfig | None
) -> Path:
    """Record which lifecycle lane the run used, with enough admin config to
    reproduce the trial — never the password value."""
    metadata: dict[str, Any] = {
        "lifecycle_lane": "admin" if admin_config is not None else "stdin"
    }
    if admin_config is not None:
        metadata["admin"] = admin_config.public_metadata()
    path = run_dir / "lifecycle_metadata.json"
    write_json(path, metadata)
    return path


def connect_admin(
    admin_config: AdminLifecycleConfig, timeout: float = 60.0
) -> AdminClient:
    """Connect the admin lifecycle lane after launch_openttd (waits for the
    listener to open and SERVER_WELCOME — the server-is-up readiness signal)."""
    client = AdminClient(admin_config)
    client.connect(timeout=timeout)
    return client


def launch_openttd(
    run_dir: Path,
    run_root: Path,
    seed: int,
    admin_config: AdminLifecycleConfig | None = None,
    *,
    vision_blitter: str | None = None,
    save_path: Path | None = None,
    game_port: int = 3996,
) -> subprocess.Popen[str]:
    env = build_runtime_env(run_root)
    cmd = [
        str(OTTD_BIN),
        "-D",
        f"127.0.0.1:{game_port}",
        "-g",
    ]
    if save_path is None:
        cmd.extend(["-G", str(seed)])
    else:
        cmd.append(str(save_path))
    cmd.extend(
        [
            "-c",
            str(run_root / "openttd.cfg"),
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
        ]
    )
    if vision_blitter is not None:
        cmd.extend(["-b", vision_blitter])
    cmd.extend(
        [
            "-d",
            "script=4,net=1",
        ]
    )
    write_json(run_dir / "openttd_command.json", cmd)
    write_lifecycle_metadata(run_dir, admin_config)
    return subprocess.Popen(
        cmd,
        cwd=str(run_root),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )


def _log_filter(text: str) -> str:
    """Drop the per-tile snapshot dump from the persisted OpenTTD log."""
    if SNAPSHOT_TILES not in text:
        return text
    return "".join(
        ln for ln in text.splitlines(keepends=True) if SNAPSHOT_TILES not in ln
    )


class _StdoutLineReader:
    """Deadline-bounded line reader over the raw OpenTTD stdout fd.

    ``proc.stdout.readline()`` can block past any deadline when the child
    wedges mid-line: ``select()`` reports the pipe readable, then ``readline``
    waits indefinitely for the terminating newline. Reading the fd directly
    keeps every wait bounded by the caller's deadline. One reader per process
    (see ``_stdout_reader``) — it owns the fd and carries partial lines across
    calls, so it must never be mixed with reads through ``proc.stdout``.
    """

    def __init__(self, proc) -> None:
        if proc.stdout is None:
            raise RuntimeError("OpenTTD stdout pipe missing")
        self.proc = proc
        self._fd = proc.stdout.fileno()
        self._selector = selectors.DefaultSelector()
        self._selector.register(self._fd, selectors.EVENT_READ)
        self._buffer = b""
        self._lines: deque[str] = deque()
        self._eof = False

    @property
    def partial_tail(self) -> str:
        return self._buffer.decode("utf-8", "replace")

    def _split_buffer(self, final: bool) -> None:
        while True:
            idx = self._buffer.find(b"\n")
            if idx < 0:
                break
            self._lines.append(self._buffer[: idx + 1].decode("utf-8", "replace"))
            self._buffer = self._buffer[idx + 1 :]
        if final and self._buffer:
            # Unterminated tail written before exit: surface it as a line so a
            # marker in it is still seen before the exit error below.
            self._lines.append(self._buffer.decode("utf-8", "replace"))
            self._buffer = b""

    def read_line(self, deadline: float) -> str:
        """Return the next line, or raise by ``deadline`` — never hang."""
        while True:
            if self._lines:
                return self._lines.popleft()
            if self._eof:
                rc = self.proc.poll()
                raise RuntimeError(f"OpenTTD exited while reading output; rc={rc}")
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError(
                    "deadline reached while waiting for the next output line"
                )
            if self._selector.select(timeout=min(0.5, remaining)):
                chunk = os.read(self._fd, 65536)
                if chunk:
                    self._buffer += chunk
                    self._split_buffer(final=False)
                else:
                    self._eof = True
                    self._split_buffer(final=True)


def _stdout_reader(proc) -> _StdoutLineReader:
    """One persistent reader per process so partial lines survive across calls."""
    reader = getattr(proc, "_openttd_mcp_stdout_reader", None)
    if reader is None:
        reader = _StdoutLineReader(proc)
        proc._openttd_mcp_stdout_reader = reader
    return reader


def _read_logged_line(proc, run_dir: Path, deadline: float) -> str:
    line = _stdout_reader(proc).read_line(deadline)
    if SNAPSHOT_TILES not in line:
        with (run_dir / "openttd.log").open("a") as f:
            f.write(line if line.endswith("\n") else line + "\n")
    return line


def _recent_output_summary(recent: deque[str], reader: _StdoutLineReader) -> str:
    parts = []
    if recent:
        parts.append(
            "last output lines: " + " | ".join(line.strip() for line in recent)
        )
    else:
        parts.append("no output lines seen")
    if reader.partial_tail:
        parts.append(f"unterminated partial line: {reader.partial_tail.strip()!r}")
    return "; ".join(parts)


def read_until(proc, run_dir: Path, marker: str, timeout: float) -> str:
    deadline = time.time() + timeout
    reader = _stdout_reader(proc)
    recent: deque[str] = deque(maxlen=8)
    while True:
        try:
            line = _read_logged_line(proc, run_dir, deadline)
        except TimeoutError:
            raise TimeoutError(
                f"Timed out after {timeout:.0f}s waiting for marker {marker!r}; "
                + _recent_output_summary(recent, reader)
            ) from None
        if SNAPSHOT_TILES not in line:
            recent.append(line)
        if marker in line:
            return line


def read_observation_and_snapshot(
    proc, run_dir: Path, timeout: float = 180.0
) -> tuple[dict, dict]:
    """Read the next MCP_OBSERVATION + full map snapshot block."""
    deadline = time.time() + timeout
    reader = _stdout_reader(proc)
    observation: dict[str, Any] | None = None
    begin_seen = False
    end_seen = False
    metadata: dict[str, Any] = {}
    rows: dict[int, list] = {}
    objects: dict[str, Any] = {}
    recent: deque[str] = deque(maxlen=8)

    def timeout_diagnostic() -> str:
        parts = []
        parts.append(
            "observation received"
            if observation is not None
            else "observation never arrived"
        )
        if not begin_seen:
            parts.append("snapshot BEGIN never arrived")
        else:
            parts.append("snapshot BEGIN received")
            expected = metadata.get("map_size", [None, None])[1] or "?"
            if rows:
                parts.append(
                    f"TILES rows {min(rows)}-{max(rows)} received ({len(rows)} of {expected})"
                )
            else:
                parts.append(f"TILES rows never arrived (expected {expected})")
            parts.append(
                "snapshot END received" if end_seen else "snapshot END never arrived"
            )
            parts.append("OBJECTS never arrived")
        return "; ".join(parts)

    while True:
        try:
            line = _read_logged_line(proc, run_dir, deadline)
        except TimeoutError:
            raise TimeoutError(
                f"Timed out after {timeout:.0f}s waiting for observation + snapshot: "
                f"{timeout_diagnostic()}; " + _recent_output_summary(recent, reader)
            ) from None
        if SNAPSHOT_TILES not in line:
            recent.append(line)
        if OBSERVATION_MARKER in line:
            observation = json.loads(line.split(OBSERVATION_MARKER, 1)[1].strip())
        elif SNAPSHOT_BEGIN in line:
            metadata = json.loads(line.split(SNAPSHOT_BEGIN, 1)[1].strip())
            begin_seen = True
            end_seen = False
            rows = {}
            objects = {}
        elif SNAPSHOT_TILES in line:
            payload = line.split(SNAPSHOT_TILES, 1)[1].strip()
            row_text, row_json = payload.split("|", 1)
            rows[int(row_text)] = json.loads(row_json)
        elif SNAPSHOT_END in line:
            # END precedes OBJECTS; nothing to finalize here.
            end_seen = True
        elif SNAPSHOT_OBJECTS in line:
            objects = json.loads(line.split(SNAPSHOT_OBJECTS, 1)[1].strip())
            tiles: list = []
            for idx in sorted(rows):
                tiles.extend(rows[idx])
            snapshot: dict[str, Any] = dict(metadata)
            snapshot["tiles"] = tiles
            for key in (
                "towns",
                "industries",
                "stations",
                "vehicles",
                "depots",
                "cargoes",
            ):
                snapshot[key] = objects.get(key, [])
            if observation is None:
                raise RuntimeError("snapshot received before any observation")
            return observation, snapshot


def read_map_snapshot(proc, run_dir: Path, timeout: float = 180.0) -> dict[str, Any]:
    """Read one map-snapshot block without requiring a general observation."""
    deadline = time.time() + timeout
    reader = _stdout_reader(proc)
    metadata: dict[str, Any] | None = None
    rows: dict[int, list[Any]] = {}
    end_seen = False
    recent: deque[str] = deque(maxlen=8)

    while True:
        try:
            line = _read_logged_line(proc, run_dir, deadline)
        except TimeoutError:
            expected = (metadata or {}).get("map_size", [None, "?"])[1]
            state = (
                "snapshot BEGIN never arrived"
                if metadata is None
                else f"snapshot BEGIN received; TILES rows {len(rows)} of {expected}; "
                + ("END received" if end_seen else "END never arrived")
            )
            raise TimeoutError(
                f"Timed out after {timeout:.0f}s waiting for map snapshot: {state}; "
                + _recent_output_summary(recent, reader)
            ) from None
        if SNAPSHOT_TILES not in line:
            recent.append(line)
        if SNAPSHOT_BEGIN in line:
            metadata = json.loads(line.split(SNAPSHOT_BEGIN, 1)[1].strip())
            rows = {}
            end_seen = False
        elif SNAPSHOT_TILES in line and metadata is not None:
            payload = line.split(SNAPSHOT_TILES, 1)[1].strip()
            row_text, row_json = payload.split("|", 1)
            rows[int(row_text)] = json.loads(row_json)
        elif SNAPSHOT_END in line and metadata is not None:
            end_seen = True
        elif SNAPSHOT_OBJECTS in line and metadata is not None:
            if not end_seen:
                raise RuntimeError("snapshot objects received before snapshot end")
            expected_rows = int(metadata.get("map_size", [0, 0])[1])
            if sorted(rows) != list(range(expected_rows)):
                raise RuntimeError(
                    f"snapshot row set is incomplete: got {len(rows)} of {expected_rows}"
                )
            objects = json.loads(line.split(SNAPSHOT_OBJECTS, 1)[1].strip())
            snapshot = dict(metadata)
            snapshot["tiles"] = [tile for idx in sorted(rows) for tile in rows[idx]]
            for key in (
                "towns",
                "industries",
                "stations",
                "vehicles",
                "depots",
                "cargoes",
                "routes",
            ):
                snapshot[key] = objects.get(key, [])
            return snapshot


def save_game(
    proc,
    run_dir: Path,
    save_path: Path,
    *,
    disk_timeout: float = 10.0,
    admin: AdminClient | None = None,
) -> None:
    if admin is not None:
        admin.save_and_confirm(save_path, disk_timeout=disk_timeout)
        return
    if proc.stdin is None:
        raise RuntimeError("OpenTTD stdin pipe missing")
    proc.stdin.write(f"save {str(save_path)[:-4]}\n")
    proc.stdin.flush()
    try:
        read_until(proc, run_dir, "Map successfully saved", 60)
    except TimeoutError:
        if save_path.exists() and save_path.stat().st_size > 0:
            return
        raise
    # OpenTTD's save can flush asynchronously; the "successfully saved" line can
    # precede the file landing on disk. Poll briefly so a quit right after the
    # final save does not kill the flush before the file exists.
    if wait_for_save_on_disk(save_path, disk_timeout) is not None:
        return
    raise TimeoutError(
        f"save marker 'Map successfully saved' arrived but {save_path} never appeared "
        f"(or stayed empty) within {disk_timeout:.0f}s"
    )


def _signal_process_group(proc: subprocess.Popen[str], sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        return
    except PermissionError:
        if sig == signal.SIGTERM:
            proc.terminate()
        else:
            proc.kill()


def _terminate_process_group(
    proc: subprocess.Popen[str],
    *,
    wait_after_term: Callable[[], Any],
    wait_after_kill: Callable[[], Any],
) -> Any:
    _signal_process_group(proc, signal.SIGTERM)
    try:
        return wait_after_term()
    except subprocess.TimeoutExpired:
        _signal_process_group(proc, signal.SIGKILL)
        return wait_after_kill()


def _wait_for_process_group(proc: subprocess.Popen[str], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while True:
        proc.poll()
        try:
            os.killpg(proc.pid, 0)
        except ProcessLookupError:
            proc.wait(timeout=max(0.0, deadline - time.monotonic()))
            return
        except PermissionError:
            pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(proc.args, timeout)
        time.sleep(min(0.01, remaining))


def terminate_process_group(
    proc: subprocess.Popen[str], *, grace_timeout: float = 5.0
) -> None:
    """Terminate a subprocess group and reap its leader within bounded waits.

    ``proc`` must lead its own process group (spawned with
    ``start_new_session=True``): the group is addressed by ``proc.pid``, so
    calling this on a process that shares the caller's group signals the
    caller itself.

    Cleanup is best effort. Callers run it from ``finally`` blocks and from a
    timeout timer thread, so a group that outlives SIGKILL past the grace
    window (a descendant wedged in uninterruptible I/O) leaves the leader
    unreaped instead of raising ``TimeoutExpired`` over the caller's in-flight
    exception -- a cleanup failure must not be what destroys the evidence
    bundle the caller was about to write.
    """
    with contextlib.suppress(subprocess.TimeoutExpired):
        _terminate_process_group(
            proc,
            wait_after_term=lambda: _wait_for_process_group(proc, grace_timeout),
            wait_after_kill=lambda: _wait_for_process_group(proc, grace_timeout),
        )


def _communicate_after_verifier_timeout(
    proc: subprocess.Popen[str], grace_timeout: float = 5.0
) -> str:
    stdout, _ = _terminate_process_group(
        proc,
        wait_after_term=lambda: proc.communicate(timeout=grace_timeout),
        wait_after_kill=proc.communicate,
    )
    return stdout or ""


def run_verifier_subprocess(
    module: str,
    save_path: Path,
    *,
    timeout: float,
    arguments: Sequence[str] = (),
) -> tuple[int, str, bool]:
    """Run a verifier module in its own process group.

    The verifier Python process launches OpenTTD as a child. A plain
    ``subprocess.run(..., timeout=...)`` only kills the Python process, which
    can leave OpenTTD orphaned and still holding its TCP port. Starting a new
    session lets the caller terminate the whole verifier tree on timeout or
    abnormal verifier exit.
    """
    cmd = [sys.executable, "-m", module, str(save_path), *arguments]
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        env=package_subprocess_env(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        stdout, _ = proc.communicate(timeout=timeout)
        if proc.returncode not in (0, None):
            _signal_process_group(proc, signal.SIGTERM)
        return int(proc.returncode or 0), stdout or "", False
    except subprocess.TimeoutExpired:
        stdout = _communicate_after_verifier_timeout(proc)
        return VERIFIER_TIMEOUT_EXIT_CODE, stdout, True


def verifier_failure_result(
    module: str,
    save_path: Path,
    returncode: int,
    *,
    timed_out: bool,
    timeout: float | None = None,
) -> dict[str, Any]:
    reason = "verifier_timeout" if timed_out else "verifier_process_failed"
    result: dict[str, Any] = {
        "ok": False,
        "verifier_module": module,
        "save_path": str(save_path),
        "exit_code": returncode,
        "reason": reason,
    }
    if timed_out and timeout is not None:
        result["timeout_seconds"] = timeout
    return result


def run_scoped_verifier(
    module: str,
    timeout_sec: float,
    *,
    save_path: Path,
    run_dir: Path,
    artifact_stem: str,
    log_name: str,
    trace_name: str | None = None,
    require_result: bool,
) -> tuple[int, dict[str, Any]]:
    """Run one reload verifier and copy its declared evidence into ``run_dir``.

    All route, network, and freight verifiers share this launch, cleanup,
    marker-copy, and failure-synthesis path. ``require_result`` preserves the
    bus-route verifier's stricter zero-exit/missing-result contract; the scope
    verifiers deliberately retain their established empty-result contract.
    """
    timeout = timeout_sec + VERIFIER_SUBPROCESS_MARGIN_SEC
    result_path = run_dir / f"{artifact_stem}_result.json"
    marker_destinations = {
        "VERIFY_RESULT": result_path,
        "VERIFY_LOG": run_dir / log_name,
    }
    if trace_name is not None:
        marker_destinations["ROUTE_TRACE"] = run_dir / trace_name
    for artifact_path in marker_destinations.values():
        artifact_path.unlink(missing_ok=True)

    returncode, stdout, timed_out = run_verifier_subprocess(
        module, save_path, timeout=timeout
    )
    (run_dir / f"{artifact_stem}_stdout.txt").write_text(stdout)
    paths: dict[str, str] = {}
    for line in stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in marker_destinations:
            paths[key] = value.strip()

    for marker, destination in marker_destinations.items():
        source = paths.get(marker)
        if source and Path(source).exists():
            shutil.copy2(source, destination)

    result: dict[str, Any] = {}
    if result_path.exists():
        result = json.loads(result_path.read_text())
    elif returncode != 0:
        result = verifier_failure_result(
            module, save_path, returncode, timed_out=timed_out, timeout=timeout
        )
        result_path.write_text(json.dumps(result, indent=2, sort_keys=True))
    elif require_result:
        process_exit_code = returncode
        returncode = VERIFIER_MISSING_RESULT_EXIT_CODE
        result = {
            "ok": False,
            "verifier_module": module,
            "save_path": str(save_path),
            "exit_code": returncode,
            "process_exit_code": process_exit_code,
            "reason": "verifier_result_missing",
        }
        result_path.write_text(json.dumps(result, indent=2, sort_keys=True))
    return returncode, result


def run_verifier(
    save_path: Path, run_dir: Path, prefix: str
) -> tuple[int, dict[str, Any]]:
    return run_scoped_verifier(
        "openttd_mcp.verify.bus_route",
        ROUTE_VERIFY_TIMEOUT_SEC,
        save_path=save_path,
        run_dir=run_dir,
        artifact_stem=f"{prefix}_verify",
        log_name=f"{prefix}_verify_bus_route.log",
        trace_name=f"{prefix}_route_trace.jsonl",
        require_result=True,
    )


def quit_game(proc, admin: AdminClient | None = None) -> dict[str, Any]:
    """Quit through the selected lane and return factual bounded shutdown evidence."""
    report: dict[str, Any] | None = None
    if admin is not None:
        try:
            return admin.shutdown(proc)
        except Exception as exc:  # noqa: BLE001 -- any admin failure must fall back to stdin
            # Admin socket wedged/closed mid-shutdown; keep the real error in
            # the report so evidence never mistakes it for a generic failure,
            # then fall back to the stdin path.
            report = {
                "graceful": False,
                "escalation": f"admin shutdown failed ({exc!r}); stdin fallback used",
            }
    quit_written = False
    exit_code: int | None = None
    escalation = report.get("escalation") if report is not None else None
    try:
        if proc.stdin is not None:
            proc.stdin.write("quit\n")
            proc.stdin.flush()
            quit_written = True
        waited = proc.wait(timeout=20)
        if isinstance(waited, int) and not isinstance(waited, bool):
            exit_code = waited
    except Exception as exc:  # noqa: BLE001 -- last-resort quit; always kill on any failure
        if escalation is None:
            escalation = f"stdin shutdown failed ({type(exc).__name__}); process killed"
        proc.kill()
        try:
            waited = proc.wait(timeout=5)
        except Exception:  # noqa: BLE001 -- the failed reap is a factual result
            waited = None
        if isinstance(waited, int) and not isinstance(waited, bool):
            exit_code = waited
    return {
        "graceful": escalation is None and quit_written and exit_code is not None,
        "escalation": escalation,
        "exit_code": exit_code,
        "quit_written": quit_written,
        "process_closed": exit_code is not None,
        "process_reaped": exit_code is not None,
    }


def _flat(tile: dict[str, Any]) -> bool:
    return int(tile.get("slope", -1)) == 0


def _is_plain_road(tile: dict[str, Any]) -> bool:
    return (
        bool(tile.get("road_tile"))
        and not tile.get("road_station_tile")
        and not tile.get("road_depot_tile")
    )


def _roadish(tile: dict[str, Any]) -> bool:
    return bool(
        tile.get("road_tile")
        or tile.get("road_station_tile")
        or tile.get("road_depot_tile")
    )


def _road_components(
    tiles_by_id: dict[int, dict[str, Any]], width: int, town_only: bool = False
) -> dict[int, int]:
    """Label connected road tiles by component id using road_connections edges."""

    def eligible(tile: dict[str, Any]) -> bool:
        if not _roadish(tile):
            return False
        return (not town_only) or bool(tile.get("in_largest_town_influence"))

    comp: dict[int, int] = {}
    next_id = 0
    for start_id, tile in tiles_by_id.items():
        if not eligible(tile) or start_id in comp:
            continue
        next_id += 1
        queue = deque([start_id])
        comp[start_id] = next_id
        while queue:
            cur = queue.popleft()
            ct = tiles_by_id[cur]
            x, y = int(ct["x"]), int(ct["y"])
            conn = ct.get("road_connections", {})
            neigh = []
            if conn.get("n"):
                neigh.append((x, y - 1))
            if conn.get("s"):
                neigh.append((x, y + 1))
            if conn.get("e"):
                neigh.append((x + 1, y))
            if conn.get("w"):
                neigh.append((x - 1, y))
            for nx, ny in neigh:
                nid = ny * width + nx
                if (
                    nid in tiles_by_id
                    and nid not in comp
                    and eligible(tiles_by_id[nid])
                ):
                    comp[nid] = next_id
                    queue.append(nid)
    return comp


def compute_scripted_plan(
    observation: dict[str, Any], snapshot: dict[str, Any]
) -> dict[str, Any]:
    width = int(snapshot["map_size"][0])
    tiles_by_id = {int(t["tile"]): t for t in snapshot["tiles"]}
    engines = observation.get("engine_candidates", [])
    if not engines:
        raise RuntimeError("no passenger engines available for scripted plan")
    engine_id = int(engines[0]["engine_id"])

    # Restrict to the largest town's own road network so the stops land on a
    # "substantial town road component" (what the verifier requires).
    comp = _road_components(tiles_by_id, width, town_only=True)
    sizes: dict[int, int] = {}
    for cid in comp.values():
        sizes[cid] = sizes.get(cid, 0) + 1
    if not sizes:
        raise RuntimeError("no in-town road tiles found in snapshot")
    best_comp = max(sizes, key=lambda c: sizes[c])
    if sizes[best_comp] < 8:
        raise RuntimeError(
            f"largest town road component too small ({sizes[best_comp]} < 8)"
        )

    # Drive-through-eligible stop tiles: plain flat road with a straight pair
    # of opposite road connections (E/W or N/S), on the best component.
    stop_options: list[tuple[int, int]] = []  # (stop_tile, front_tile)
    for tid, tile in tiles_by_id.items():
        if comp.get(tid) != best_comp:
            continue
        if not _is_plain_road(tile) or not _flat(tile):
            continue
        x, y = int(tile["x"]), int(tile["y"])
        conn = tile.get("road_connections", {})
        if conn.get("e") and conn.get("w"):
            stop_options.append((tid, y * width + (x + 1)))
        elif conn.get("n") and conn.get("s"):
            stop_options.append((tid, (y - 1) * width + x))

    if len(stop_options) < 2:
        raise RuntimeError(
            f"insufficient drive-through stop options on largest component ({len(stop_options)})"
        )

    # Pick the two stops with the largest Manhattan separation (>= 4).
    def xy(tid: int) -> tuple[int, int]:
        t = tiles_by_id[tid]
        return int(t["x"]), int(t["y"])

    best_pair = None
    best_dist = -1
    for i in range(len(stop_options)):
        for j in range(i + 1, len(stop_options)):
            ax, ay = xy(stop_options[i][0])
            bx, by = xy(stop_options[j][0])
            d = abs(ax - bx) + abs(ay - by)
            if d >= 4 and d > best_dist:
                best_dist = d
                best_pair = (stop_options[i], stop_options[j])
    if best_pair is None:
        raise RuntimeError(
            "no two stop tiles with Manhattan distance >= 4 on largest component"
        )
    (stop_a, stop_a_front), (stop_b, stop_b_front) = best_pair
    stop_set = {stop_a, stop_a_front, stop_b, stop_b_front}

    # Depot: a buildable flat tile adjacent to a road tile on the best component.
    depot_choice = None
    for tid, tile in tiles_by_id.items():
        if tid in stop_set:
            continue
        if not bool(tile.get("buildable")) or not _flat(tile):
            continue
        x, y = int(tile["x"]), int(tile["y"])
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            front_id = ny * width + nx
            if comp.get(front_id) == best_comp and front_id not in stop_set:
                front_tile = tiles_by_id.get(front_id, {})
                if front_tile.get("road_tile"):
                    depot_choice = (tid, front_id)
                    break
        if depot_choice:
            break
    if depot_choice is None:
        raise RuntimeError(
            "no buildable depot tile adjacent to the largest road component"
        )
    depot_tile, depot_front = depot_choice

    return {
        "engine_id": engine_id,
        "stop_a": stop_a,
        "stop_a_front": stop_a_front,
        "stop_b": stop_b,
        "stop_b_front": stop_b_front,
        "depot_tile": depot_tile,
        "depot_front": depot_front,
        "stop_distance": best_dist,
        "component_size": sizes[best_comp],
    }


PRIMITIVE_SURFACE_DOC = """You are playing OpenTTD as a bus company. You build ONE bus route at a time
using a small set of composable primitives. There is NO candidate menu: you must
locate your own tiles using the structured state, the rendered map, and the
query primitives. Tile ids use the frame tile_id = y * map_width + x.

Primitives (emit exactly ONE per step):
  get_tile(tile)                         -> terrain/road/owner/adjacency at a tile
  get_region(center_tile, radius)        -> summarized tiles around a center (radius<=6)
  list_towns() / list_my_vehicles() / get_my_finances()
  build_road(from_tile, to_tile, [waypoints...])  -> lays a road; endpoints are yours
  build_bus_stop_drive_through(tile, front_tile)     -> drive-through bus stop on a road tile
  build_road_depot(tile, front_tile)       -> road depot facing toward front_tile; completes its adjacent entrance edge
  buy_vehicle(depot_tile, engine_id)       -> returns vehicle_id
  append_orders(vehicle_id, stop_tile_1, stop_tile_2, ...)
  start_vehicle(vehicle_id) / stop_vehicle / send_vehicle_to_depot / sell_vehicle
  set_loan(amount) / demolish_tile(tile)

A working route needs: >=2 drive-through bus stops in a town, a depot connected
to the same road, a bus with orders to both stops, started. Stops and depot must
be on ONE connected road component or the route will not verify.

Respond with ONLY a JSON object, no prose:
{"reasoning": "...", "notes": "carried memory for next step", "primitive": {"command": "...", "args": [...]}}
"""


def build_model_prompt(
    state: dict[str, Any],
    notes: str,
    last_result: dict[str, Any] | None,
    svg_path: Path,
    png_path: Path,
    png_available: bool,
) -> str:
    # No remaining-decision count is disclosed: revealing that a budget exists
    # (or its size) taints hidden-horizon play. Do not reintroduce a
    # budget_left line here.
    parts = [PRIMITIVE_SURFACE_DOC]
    parts.append("\nRendered map (candidate-free) is saved at:")
    parts.append(f"  SVG: {svg_path}")
    if png_available:
        parts.append(f"  PNG: {png_path}")
    else:
        parts.append(
            "  (PNG rasteriser unavailable; reason over the structured state + query_* primitives)"
        )
    parts.append("\nYour running notes (your memory from previous steps):")
    parts.append(notes or "(none yet)")
    if last_result is not None:
        parts.append("\nResult of your previous primitive:")
        parts.append(json.dumps(last_result, sort_keys=True))
    parts.append(
        "\nGeneral structured state (NO candidate list — find your own tiles):"
    )
    parts.append(json.dumps(state, sort_keys=True))
    parts.append(
        '\nReturn exactly one JSON object {"reasoning","notes","primitive":{"command","args"}}.'
    )
    return "\n".join(parts)


def command_from_primitive(primitive: dict[str, Any]) -> list[Any]:
    command: list[Any] = [str(primitive["command"])]
    for arg in primitive.get("args", []):
        command.append(int(arg))
    return command
