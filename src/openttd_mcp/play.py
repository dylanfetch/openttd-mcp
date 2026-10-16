"""Launch and own a foreground OpenTTD gameplay MCP server."""

from __future__ import annotations

import argparse
import contextlib
import json
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any

from openttd_mcp.admin import allocate_admin_port, wait_for_save_on_disk
from openttd_mcp.lifecycle import (
    BRIDGE_READY_MARKER,
    LOCAL_RUNS_DIR,
    OBSERVATION_MARKER,
    assert_expected_town_count,
    capture_openttd_output,
    launch_openttd,
    now_id,
    prepare_runtime,
    quit_game,
    wait_for_bridge_ready,
    wait_for_process_event,
)
from openttd_mcp.server import (
    FileLogBridgeDispatcher,
    GameToolServer,
    build_fastmcp,
    write_json,
)

DEFAULT_SEED = 1337
DEFAULT_MAP_LOG2 = 7
DEFAULT_NUM_TOWNS = 16
DEFAULT_STARTING_YEAR = 1950
# A 128x128/16-town game needs up to 300 seconds each for bridge readiness and
# boot observation; a single shorter cap caused pre-gameplay startup failures.
DEFAULT_STARTUP_TIMEOUT = 600.0
DEFAULT_RESULT_TIMEOUT = 240.0
PLAY_EXPERIMENT = "play"
FINAL_SAVE_FILENAME = "final.sav"
# OpenTTD prints this console line once a save has been written; lifecycle's
# save_game waits for the same marker on the stdout pipe, but here the capture
# thread owns that pipe, so the marker is read back from the log file instead.
SAVE_SUCCESS_MARKER = "Map successfully saved"
SAVE_MARKER_TIMEOUT = 60.0
SAVE_DISK_TIMEOUT = 10.0


def _read_log_from(log_path: Path, offset: int) -> str:
    try:
        with log_path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(offset)
            return handle.read()
    except FileNotFoundError:
        return ""


class PlayStartupError(RuntimeError):
    """A startup refusal with stable facts for programmatic callers."""

    def __init__(self, message: str, *, error_code: str, run_dir: Path) -> None:
        super().__init__(message)
        self.startup_result = {
            "ok": False,
            "stage": "run_directory_validation",
            "error_code": error_code,
            "error": message,
            "run_dir": str(run_dir),
        }


class _BootObservationCapture:
    def __init__(self) -> None:
        self.ready = threading.Event()
        self.observation: dict[str, Any] | None = None
        self.error: BaseException | None = None

    def accept_line(self, line: str) -> None:
        if self.ready.is_set() or OBSERVATION_MARKER not in line:
            return
        try:
            value = json.loads(line.split(OBSERVATION_MARKER, 1)[1].strip())
            if not isinstance(value, dict):
                raise ValueError("boot observation must be a JSON object")
            self.observation = value
        except BaseException as exc:  # noqa: BLE001 -- transferred to startup owner
            self.error = exc
        finally:
            self.ready.set()


class PlayOwner:
    """Own the child process, output pump, evidence, and gameplay server."""

    def __init__(
        self,
        *,
        run_dir: Path,
        run_root: Path,
        bridge_root: Path,
        proc: Any,
        output_thread: threading.Thread,
        server: GameToolServer,
    ) -> None:
        self.run_dir = run_dir
        self.run_root = run_root
        self.bridge_root = bridge_root
        self.proc = proc
        self.output_thread = output_thread
        self.server = server
        self._close_report: dict[str, Any] | None = None

    def _save_final_game(self) -> str:
        """Request a console save and confirm it landed before the quit."""
        if self.proc.stdin is None:
            raise RuntimeError("OpenTTD stdin pipe missing")
        save_path = self.run_dir / FINAL_SAVE_FILENAME
        log_path = self.run_dir / "openttd.log"
        try:
            offset = log_path.stat().st_size
        except FileNotFoundError:
            offset = 0
        save_path.unlink(missing_ok=True)
        self.proc.stdin.write(f"save {str(save_path)[:-4]}\n")
        self.proc.stdin.flush()
        deadline = time.time() + SAVE_MARKER_TIMEOUT
        while True:
            if SAVE_SUCCESS_MARKER in _read_log_from(log_path, offset):
                break
            # The capture thread is the log's only writer: once it stops no
            # marker can arrive, so fall through to the on-disk check instead
            # of waiting out the marker timeout.
            capture_finished = not self.output_thread.is_alive()
            if capture_finished and SAVE_SUCCESS_MARKER in _read_log_from(
                log_path, offset
            ):
                break
            if capture_finished or time.time() >= deadline:
                if save_path.exists() and save_path.stat().st_size > 0:
                    break
                raise TimeoutError(
                    f"no {SAVE_SUCCESS_MARKER!r} log marker and {save_path} "
                    "never appeared (or stayed empty)"
                )
            time.sleep(0.05)
        if wait_for_save_on_disk(save_path, SAVE_DISK_TIMEOUT) is None:
            raise TimeoutError(
                f"{SAVE_SUCCESS_MARKER!r} log marker arrived but {save_path} "
                f"never appeared (or stayed empty) within {SAVE_DISK_TIMEOUT:.0f}s"
            )
        return str(save_path)

    def close(self) -> dict[str, Any]:
        if self._close_report is not None:
            return self._close_report
        started = time.time()
        errors: list[str] = []
        final_save: str | None = None
        try:
            final_save = self._save_final_game()
        except Exception as exc:  # noqa: BLE001 -- quit and evidence still proceed
            errors.append(f"final save failed: {exc}")
        try:
            shutdown = quit_game(self.proc)
        except Exception as exc:  # noqa: BLE001 -- cleanup evidence keeps the failure
            shutdown = None
            errors.append(f"OpenTTD shutdown failed: {exc}")
        try:
            self.output_thread.join(timeout=5)
            output_capture_stopped = not self.output_thread.is_alive()
        except Exception as exc:  # noqa: BLE001 -- retain shutdown state
            output_capture_stopped = False
            errors.append(f"OpenTTD output capture join failed: {exc}")
        if not output_capture_stopped:
            errors.append("OpenTTD output capture thread did not stop within 5 seconds")
        report = {
            "ok": not errors,
            "bounded": True,
            "final_save": final_save,
            "shutdown": shutdown,
            "errors": errors,
            "output_capture_stopped": output_capture_stopped,
            "elapsed_seconds": time.time() - started,
        }
        self._close_report = report
        try:
            write_json(self.run_dir / "play_cleanup.json", report)
        except Exception as exc:  # noqa: BLE001 -- shutdown remains the primary result
            report["ok"] = False
            report["cleanup_record_error"] = str(exc)
            print(f"failed to write play cleanup evidence: {exc}", file=sys.stderr)
        return report


def _record_failed_startup(
    run_dir: Path,
    proc: Any | None,
    output_thread: threading.Thread | None,
    error: BaseException,
) -> None:
    cleanup_error = None
    output_capture_stopped = output_thread is None
    try:
        shutdown = quit_game(proc) if proc is not None else None
    except BaseException as exc:  # noqa: BLE001 -- original BaseException remains primary
        shutdown = None
        cleanup_error = str(exc)
    if output_thread is not None:
        try:
            output_thread.join(timeout=5)
            output_capture_stopped = not output_thread.is_alive()
        except BaseException as exc:  # noqa: BLE001 -- original BaseException remains primary
            cleanup_error = cleanup_error or str(exc)
            output_capture_stopped = False
    report = {
        "ok": False,
        "bounded": True,
        "startup_error": str(error),
        "startup_error_type": type(error).__name__,
        "shutdown": shutdown,
        "cleanup_error": cleanup_error,
        "output_capture_stopped": output_capture_stopped,
    }
    try:
        write_json(run_dir / "play_cleanup.json", report)
    except BaseException as evidence_error:  # noqa: BLE001 -- preserve original identity
        print(
            f"failed to write play startup cleanup evidence: {evidence_error}",
            file=sys.stderr,
        )


def _select_run_dir(run_dir: Path | None) -> Path:
    selected = (
        Path(run_dir).expanduser().resolve()
        if run_dir is not None
        else LOCAL_RUNS_DIR / f"{now_id()}-play"
    )
    if selected.exists():
        if not selected.is_dir():
            raise PlayStartupError(
                f"play run path is not a directory: {selected}",
                error_code="run_path_not_directory",
                run_dir=selected,
            )
        if any(selected.iterdir()):
            raise PlayStartupError(
                f"play run directory is not empty: {selected}",
                error_code="run_directory_not_empty",
                run_dir=selected,
            )
    else:
        selected.mkdir(parents=True)
    return selected


def open_play(
    *,
    run_dir: Path | None = None,
    seed: int = DEFAULT_SEED,
    map_log2: int = DEFAULT_MAP_LOG2,
    num_towns: int = DEFAULT_NUM_TOWNS,
    starting_year: int = DEFAULT_STARTING_YEAR,
    startup_timeout: float = DEFAULT_STARTUP_TIMEOUT,
    result_timeout: float = DEFAULT_RESULT_TIMEOUT,
) -> PlayOwner:
    selected_run_dir = _select_run_dir(run_dir)
    print(f"OpenTTD play run directory: {selected_run_dir}", file=sys.stderr)
    startup_deadline = time.monotonic() + startup_timeout
    proc = None
    output_thread = None
    try:
        game_port = allocate_admin_port()
        run_root, bridge_root = prepare_runtime(
            selected_run_dir,
            map_log2,
            num_towns=num_towns,
            starting_year=starting_year,
            emit_boot_snapshot=False,
        )
        log_path = selected_run_dir / "openttd.log"
        tool_log = selected_run_dir / "tool_calls.jsonl"
        tool_log.touch()
        proc = launch_openttd(
            selected_run_dir,
            run_root,
            seed,
            game_port=game_port,
        )
        bridge_ready = threading.Event()
        boot = _BootObservationCapture()
        output_thread = capture_openttd_output(
            proc,
            log_path,
            bridge_ready,
            line_callback=boot.accept_line,
            thread_name="openttd-mcp-log",
        )
        readiness_timeout = startup_deadline - time.monotonic()
        if readiness_timeout <= 0:
            raise TimeoutError("startup timeout expired before bridge readiness")
        wait_for_bridge_ready(bridge_ready, proc, timeout=readiness_timeout)
        observation_timeout = startup_deadline - time.monotonic()
        if observation_timeout <= 0:
            raise TimeoutError("startup timeout expired before boot observation")
        wait_for_process_event(
            boot.ready,
            proc,
            description=OBSERVATION_MARKER.rstrip("|"),
            timeout=observation_timeout,
        )
        if boot.error is not None:
            raise boot.error
        assert boot.observation is not None
        assert_expected_town_count(boot.observation, num_towns, seed=seed)
        dispatcher = FileLogBridgeDispatcher(
            bridge_root, log_path, timeout=result_timeout
        )
        registry_path = selected_run_dir / "registry.json"
        server = GameToolServer(
            dispatcher,
            tool_log=tool_log,
            registry_path=registry_path,
            experiment=PLAY_EXPERIMENT,
            map_log2=map_log2,
        )
        write_json(
            selected_run_dir / "play_startup.json",
            {
                "ok": True,
                "run_dir": str(selected_run_dir),
                "run_root": str(run_root),
                "bridge_root": str(bridge_root),
                "bridge_ready_marker": BRIDGE_READY_MARKER,
                "game_port": game_port,
                "seed": seed,
                "map_log2": map_log2,
                "map_width": 1 << map_log2,
                "map_height": 1 << map_log2,
                "num_towns": num_towns,
                "observed_town_count": len(boot.observation["towns"]),
                "starting_year": starting_year,
                "startup_timeout": startup_timeout,
                "result_timeout": result_timeout,
                "registry_path": str(registry_path),
                "tool_log": str(tool_log),
            },
        )
        return PlayOwner(
            run_dir=selected_run_dir,
            run_root=run_root,
            bridge_root=bridge_root,
            proc=proc,
            output_thread=output_thread,
            server=server,
        )
    except BaseException as exc:
        try:
            _record_failed_startup(selected_run_dir, proc, output_thread, exc)
        except BaseException as cleanup_error:  # noqa: BLE001 -- original is primary
            with contextlib.suppress(BaseException):
                print(
                    f"play startup cleanup itself failed: {cleanup_error}",
                    file=sys.stderr,
                )
        raise


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="openttd-mcp play",
        description="Launch OpenTTD and serve its road-vehicle gameplay tools over MCP stdio.",
    )
    parser.add_argument(
        "--run-dir",
        type=Path,
        help=(
            "output directory; default: .local/runs/<timestamp>-play. Contains "
            "openttd.log, tool_calls.jsonl, registry.json, play_startup.json, "
            "play_cleanup.json, openttd_command.json, final.sav (the final save, "
            "taken at shutdown), and the isolated OpenTTD runtime"
        ),
    )
    parser.add_argument(
        "--seed", type=int, default=DEFAULT_SEED, help="OpenTTD map-generation seed"
    )
    parser.add_argument(
        "--map-log2",
        type=int,
        default=DEFAULT_MAP_LOG2,
        help="map edge as a base-2 exponent in tiles (7 means 128 by 128)",
    )
    parser.add_argument(
        "--num-towns",
        type=int,
        default=DEFAULT_NUM_TOWNS,
        help="exact number of towns requested and verified at startup",
    )
    parser.add_argument(
        "--starting-year",
        type=int,
        default=DEFAULT_STARTING_YEAR,
        help="initial in-game calendar year",
    )
    parser.add_argument(
        "--startup-timeout",
        type=float,
        default=DEFAULT_STARTUP_TIMEOUT,
        help="total seconds allowed for preparation, launch, and boot validation",
    )
    parser.add_argument(
        "--result-timeout",
        type=float,
        default=DEFAULT_RESULT_TIMEOUT,
        help="seconds allowed for each gameplay command result",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def exit_through_cleanup(_signum: int, _frame: Any) -> None:
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, exit_through_cleanup)
    owner: PlayOwner | None = None
    try:
        owner = open_play(
            run_dir=args.run_dir,
            seed=args.seed,
            map_log2=args.map_log2,
            num_towns=args.num_towns,
            starting_year=args.starting_year,
            startup_timeout=args.startup_timeout,
            result_timeout=args.result_timeout,
        )
        build_fastmcp(owner.server).run("stdio")
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        if owner is not None:
            owner.close()
    return 0
