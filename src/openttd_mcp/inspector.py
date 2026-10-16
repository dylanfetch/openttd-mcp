"""Referee-only MCP server for inspecting one completed OpenTTD save."""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import signal
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from openttd_mcp.admin import AdminClient, AdminLifecycleConfig, allocate_admin_port
from openttd_mcp.lifecycle import (
    BRIDGE_READY_MARKER,
    DEFAULT_MAP_LOG2,
    DEFAULT_SEED,
    LOCAL_RUNS_DIR,
    capture_openttd_output,
    launch_openttd,
    now_id,
    prepare_runtime,
    quit_game,
    run_verifier_subprocess,
    wait_for_bridge_ready,
)
from openttd_mcp.render_capture import (
    REVIEW_MAX_EDGE_PX,
    SCREENSHOT_BLITTER,
    RenderDependencyError,
    capture_giant_screenshot,
    read_png_dimensions,
    require_pillow_for_rendering,
    write_review_png,
)
from openttd_mcp.server import (
    FileLogBridgeDispatcher,
    GameToolServer,
    ToolSpec,
    WaitDays,
    _bounded_int,
    append_jsonl,
    build_fastmcp,
    object_schema,
    tool_specs,
    write_json,
)
from openttd_mcp.verify.replay_telemetry import (
    DEFAULT_QUEUE_RADIUS_TILES,
    DEFAULT_STATION_SAMPLE_TICKS,
    DEFAULT_WINDOW_DAYS,
    TELEMETRY_TIMEOUT_SEC,
)

INSPECTOR_EXPERIMENT = "116-interactive-save-inspection"
ADVANCE_TOOL_NAME = "advance_days"
VIEW_WHOLE_MAP_TOOL_NAME = "view_whole_map"
TELEMETRY_TOOL_NAME = "measure_replay_telemetry"
TELEMETRY_WINDOW_MAX_DAYS = 3650
TELEMETRY_PROCESS_MARGIN_SEC = 120
HOST_PATH_PLACEHOLDER = "<host-path>"
PUBLIC_STARTUP_FILENAME = "inspector_public_startup.json"
PUBLIC_REGISTRY_FILENAME = "inspector_public_registry.json"
PUBLIC_TOOL_CALLS_FILENAME = "public_tool_calls.jsonl"
PUBLIC_CLEANUP_FILENAME = "inspector_public_cleanup.json"

# The inspector registry is deliberately enumerated. Prefix filtering would let a
# future gameplay tool enter this referee surface merely because of its name.
INSPECTOR_SENSOR_TOOLS = (
    "get_tile",
    "get_region",
    "view_map",
    "list_towns",
    "get_town",
    "list_my_vehicles",
    "get_vehicle",
    "list_my_stations",
    "list_my_depots",
    "get_my_finances",
    "get_my_company",
    "list_engines",
    "list_bridge_types",
    "list_industries",
    "list_subsidies",
    "get_industry",
    "get_my_station",
    "list_my_route_economics",
    "get_cargo_payment_rate",
)
INSPECTOR_EXTRA_TOOLS = (
    ADVANCE_TOOL_NAME,
    VIEW_WHOLE_MAP_TOOL_NAME,
    TELEMETRY_TOOL_NAME,
)

TelemetryRunner = Callable[..., tuple[int, str, bool]]


class InspectorStartupError(RuntimeError):
    """Startup refusal carrying stable structured facts for callers."""

    def __init__(self, message: str, *, error_code: str, run_dir: Path) -> None:
        super().__init__(message)
        self.startup_result = {
            "ok": False,
            "stage": "run_directory_validation",
            "error_code": error_code,
            "error": message,
            "run_dir": str(run_dir),
        }


class _InspectorImageResult(dict[str, Any]):
    """Structured render result carrying bytes for the MCP image block."""

    image_bytes: bytes
    image_path: Path


class _AdvanceDaysError(RuntimeError):
    """Advance failure carrying measured dates into the structured result."""

    def __init__(self, message: str, result_fields: dict[str, Any]) -> None:
        super().__init__(message)
        self.tool_result_fields = result_fields


class _SensorDispatchError(RuntimeError):
    """Bridge failure carrying date drift into the structured tool result."""

    def __init__(
        self,
        cause: Exception,
        date_drift: dict[str, Any],
        *,
        pause_error: Exception | None = None,
    ) -> None:
        message = str(cause)
        if pause_error is not None and pause_error is not cause:
            message += f"; restoring pause also failed: {pause_error}"
        super().__init__(message)
        self.tool_result_fields = {"date_drift": date_drift}


class _SensorDispatchTimeout(TimeoutError):
    """Timeout subtype preserving the bridge timeout's public classification."""

    def __init__(
        self,
        cause: Exception,
        date_drift: dict[str, Any],
        *,
        pause_error: Exception | None = None,
    ) -> None:
        message = str(cause)
        if pause_error is not None and pause_error is not cause:
            message += f"; restoring pause also failed: {pause_error}"
        super().__init__(message)
        self.tool_result_fields = {"date_drift": date_drift}


_POSIX_ABSOLUTE_PATH = re.compile(r"(?<![\w:>])/(?:[^\s\"'<>]+)")
_WINDOWS_ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9_])(?:[A-Za-z]:[\\/]|\\\\)[^\s\"'<>]+"
)


def _without_inspector_host_paths(
    value: Any, *, known_paths: tuple[str, ...] = ()
) -> Any:
    """Apply the inspector extras' public no-host-path policy recursively."""
    if isinstance(value, dict):
        return {
            key: _without_inspector_host_paths(item, known_paths=known_paths)
            for key, item in value.items()
            if not isinstance(key, str)
            or (
                key.lower() != "error_detail"
                and key.lower() != "path"
                and not key.lower().endswith("_path")
            )
        }
    if isinstance(value, list):
        return [
            _without_inspector_host_paths(item, known_paths=known_paths)
            for item in value
        ]
    if isinstance(value, Path):
        return HOST_PATH_PLACEHOLDER
    if isinstance(value, str):
        scrubbed = value
        for known_path in sorted(set(known_paths), key=len, reverse=True):
            if known_path:
                scrubbed = scrubbed.replace(known_path, HOST_PATH_PLACEHOLDER)
        scrubbed = _WINDOWS_ABSOLUTE_PATH.sub(HOST_PATH_PLACEHOLDER, scrubbed)
        return _POSIX_ABSOLUTE_PATH.sub(HOST_PATH_PLACEHOLDER, scrubbed)
    return value


def _owner_host_paths(owner: InspectorOwner) -> tuple[str, ...]:
    return tuple(
        str(path)
        for path in (
            owner.source_save,
            owner.run_dir,
            owner.run_root,
            owner.bridge_root,
            owner.openttd_log,
            owner.screenshot_dir,
        )
    )


def _public_shutdown_facts(shutdown: Any) -> dict[str, Any] | None:
    """Project shutdown evidence without diagnostics or command material."""
    if not isinstance(shutdown, dict):
        return None
    facts: dict[str, Any] = {}
    if isinstance(shutdown.get("graceful"), bool):
        facts["graceful"] = shutdown["graceful"]
    exit_code = shutdown.get("exit_code")
    if isinstance(exit_code, int) and not isinstance(exit_code, bool):
        facts["exit_code"] = exit_code
    facts["escalation_occurred"] = bool(shutdown.get("escalation"))
    return facts


def _public_cleanup_facts(report: dict[str, Any]) -> dict[str, Any]:
    """Return the fixed, credential-free public cleanup contract."""
    public: dict[str, Any] = {
        "ok": bool(report.get("ok")),
        "bounded": bool(report.get("bounded")),
        "shutdown": _public_shutdown_facts(report.get("shutdown")),
    }
    if isinstance(report.get("output_capture_stopped"), bool):
        public["output_capture_stopped"] = report["output_capture_stopped"]
    errors = report.get("errors")
    public["error_count"] = len(errors) if isinstance(errors, list) else 0
    startup_error_type = report.get("startup_error_type")
    if isinstance(startup_error_type, str):
        public["startup_failed"] = True
        public["startup_error_type"] = startup_error_type
    else:
        public["startup_failed"] = False
    public["cleanup_error_occurred"] = bool(
        report.get("cleanup_error") or report.get("cleanup_record_error")
    )
    return public


def _public_date_facts(result: Any) -> dict[str, Any] | None:
    """Project only bounded replay dates from a private tool result."""
    if not isinstance(result, dict):
        return None
    candidate = result.get("date_drift")
    if not isinstance(candidate, dict):
        candidate = result
    allowed = {
        "starting_date": int,
        "ending_date": int,
        "observed_elapsed_days": int,
        "pause_confirmed": bool,
        "checkpoint_date": int,
        "interactive_date_after": int,
    }
    facts: dict[str, Any] = {}
    for key, expected_type in allowed.items():
        value = candidate.get(key)
        if expected_type is int:
            if isinstance(value, int) and not isinstance(value, bool):
                facts[key] = value
        elif isinstance(value, expected_type):
            facts[key] = value
    return facts or None


class PausedBridgeDispatcher:
    """Let one bridge query run, then restore the between-call pause."""

    def __init__(self, dispatcher: Any, admin: Any) -> None:
        self.dispatcher = dispatcher
        self.admin = admin

    def dispatch(self, command: list[Any], timestamp: str) -> dict[str, Any]:
        starting_date: int | None = None
        ending_date: int | None = None
        starting_date_error: Exception | None = None
        ending_date_error: Exception | None = None
        dispatch_error: Exception | None = None
        pause_error: Exception | None = None
        result: dict[str, Any] | None = None
        try:
            try:
                starting_date = self.admin.poll_date()
            except Exception as exc:
                starting_date_error = exc
                raise
            self.admin.rcon("unpause")
            result = self.dispatcher.dispatch(command, timestamp)
        except Exception as exc:  # noqa: BLE001 -- pause and date facts survive failure
            dispatch_error = exc
        finally:
            try:
                self.admin.rcon("pause")
            except Exception as exc:  # noqa: BLE001 -- do not mask the bridge failure
                pause_error = exc
            try:
                ending_date = self.admin.poll_date()
            except Exception as exc:  # noqa: BLE001 -- expose unavailable ending date
                ending_date_error = exc

        date_drift: dict[str, Any] = {
            "starting_date": starting_date,
            "ending_date": ending_date,
            "observed_elapsed_days": (
                ending_date - starting_date
                if starting_date is not None and ending_date is not None
                else None
            ),
            "pause_confirmed": pause_error is None,
        }
        if starting_date_error is not None:
            date_drift["starting_date_error"] = str(starting_date_error)
        if ending_date_error is not None:
            date_drift["ending_date_error"] = str(ending_date_error)
        if pause_error is not None:
            date_drift["pause_error"] = str(pause_error)

        failure = dispatch_error or pause_error
        if failure is not None:
            error_type = (
                _SensorDispatchTimeout
                if isinstance(failure, TimeoutError)
                else _SensorDispatchError
            )
            raise error_type(failure, date_drift, pause_error=pause_error) from failure
        assert result is not None
        result["date_drift"] = date_drift
        return result


class InspectorOwner:
    """Own one paused interactive replay and its referee-only side effects."""

    def __init__(
        self,
        *,
        source_save: Path,
        run_dir: Path,
        run_root: Path,
        bridge_root: Path,
        openttd_log: Path,
        screenshot_dir: Path,
        proc: Any,
        admin: Any,
        dispatcher: Any,
        output_thread: threading.Thread | None = None,
        telemetry_runner: TelemetryRunner = run_verifier_subprocess,
        map_log2: int = DEFAULT_MAP_LOG2,
    ) -> None:
        self.source_save = Path(source_save)
        self.run_dir = Path(run_dir)
        self.run_root = Path(run_root)
        self.bridge_root = Path(bridge_root)
        self.openttd_log = Path(openttd_log)
        self.screenshot_dir = Path(screenshot_dir)
        self.proc = proc
        self.admin = admin
        self.dispatcher = dispatcher
        self.output_thread = output_thread
        self.telemetry_runner = telemetry_runner
        self.map_log2 = map_log2
        self.server: InspectorToolServer | None = None
        self._artifact_counter = 0
        self._close_report: dict[str, Any] | None = None

    def _next_stem(self, prefix: str) -> str:
        self._artifact_counter += 1
        return f"{prefix}-{self._artifact_counter:04d}"

    @staticmethod
    def _require_days(value: int, *, maximum: int, name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name} must be an integer")
        if not 1 <= value <= maximum:
            raise ValueError(f"{name} must be between 1 and {maximum}")
        return value

    def advance_days(self, duration_days: int) -> dict[str, Any]:
        duration_days = self._require_days(
            duration_days, maximum=90, name="duration_days"
        )
        timestamp = self._next_stem("advance")
        starting_date: int | None = None
        bridge_result: dict[str, Any] | None = None
        bridge_timeout: TimeoutError | None = None
        dispatch_error: Exception | None = None
        pause_error: Exception | None = None
        ending_date: int | None = None
        ending_date_error: Exception | None = None
        try:
            starting_date = self.admin.poll_date()
            self.admin.rcon("unpause")
            try:
                bridge_result = self.dispatcher.dispatch(
                    ["wait", duration_days], timestamp
                )
            except TimeoutError as exc:
                bridge_timeout = exc
            except Exception as exc:  # noqa: BLE001 -- cleanup must run first
                dispatch_error = exc
        except Exception as exc:  # noqa: BLE001 -- pause and ending poll still run
            dispatch_error = exc
        finally:
            try:
                self.admin.rcon("pause")
            except Exception as exc:  # noqa: BLE001 -- retain the primary outcome
                pause_error = exc
            try:
                ending_date = self.admin.poll_date()
            except Exception as exc:  # noqa: BLE001 -- the poll attempt is evidence
                ending_date_error = exc

        elapsed_days = (
            ending_date - starting_date
            if ending_date is not None and starting_date is not None
            else None
        )
        date_evidence: dict[str, Any] = {
            "requested_days": duration_days,
            "starting_date": starting_date,
            "ending_date": ending_date,
            "observed_elapsed_days": elapsed_days,
            "pause_confirmed": pause_error is None,
        }
        if pause_error is not None:
            date_evidence["pause_error"] = str(pause_error)
        if ending_date_error is not None:
            date_evidence["ending_date_error"] = str(ending_date_error)

        if bridge_timeout is not None:
            result: dict[str, Any] = {
                "ok": False,
                "action": ADVANCE_TOOL_NAME,
                "status": "timeout",
                "error": str(bridge_timeout),
                "warning": (
                    "The bridge wait may remain unfinished even though this call "
                    "stopped waiting; inspect the current date before advancing again."
                ),
                "mutates_game": True,
                **date_evidence,
            }
            return result

        failure = dispatch_error or pause_error or ending_date_error
        if failure is not None:
            failure_details: list[str] = []
            if pause_error is not None and pause_error is not failure:
                failure_details.append(f"restoring pause failed: {pause_error}")
            if ending_date_error is not None and ending_date_error is not failure:
                failure_details.append(
                    f"polling the ending date failed: {ending_date_error}"
                )
            message = str(failure)
            if failure_details:
                message += "; " + "; ".join(failure_details)
            raise _AdvanceDaysError(
                message,
                {
                    "status": "failed",
                    "mutates_game": True,
                    **date_evidence,
                },
            ) from failure

        assert bridge_result is not None
        assert starting_date is not None
        assert ending_date is not None
        return {
            "ok": bool(bridge_result.get("ok", True)),
            "action": ADVANCE_TOOL_NAME,
            "bridge_result": bridge_result,
            "mutates_game": True,
            **date_evidence,
        }

    def view_whole_map(self) -> _InspectorImageResult:
        basename = self._next_stem("whole-map")
        captured = capture_giant_screenshot(self.admin, self.screenshot_dir, basename)
        archival_width, archival_height = read_png_dimensions(captured)
        evidence_dir = self.run_dir / "renders"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        archival_path = evidence_dir / f"{basename}-archival.png"
        review_path = evidence_dir / f"{basename}-review.png"
        shutil.copy2(captured, archival_path)

        archival = {
            "format": "png",
            "width": archival_width,
            "height": archival_height,
            "bytes": archival_path.stat().st_size,
            "path": str(archival_path),
        }
        try:
            require_pillow_for_rendering()
            review_width, review_height, scale_factor = write_review_png(
                archival_path,
                review_path,
                max_edge_px=REVIEW_MAX_EDGE_PX,
            )
            settled_width, settled_height = read_png_dimensions(review_path)
            if (review_width, review_height) != (settled_width, settled_height):
                raise RuntimeError(
                    "reviewer PNG dimensions changed after writing: "
                    f"expected {review_width}x{review_height}, got "
                    f"{settled_width}x{settled_height}"
                )
            if max(settled_width, settled_height) > REVIEW_MAX_EDGE_PX:
                raise RuntimeError(
                    "reviewer PNG exceeds the public maximum edge: "
                    f"{settled_width}x{settled_height} > {REVIEW_MAX_EDGE_PX}"
                )
        except (ImportError, RenderDependencyError) as exc:
            review_path.unlink(missing_ok=True)
            return _InspectorImageResult(
                {
                    "ok": False,
                    "action": VIEW_WHOLE_MAP_TOOL_NAME,
                    "status": "reviewer_image_unavailable",
                    "archival": archival,
                    "reviewer": {
                        "available": False,
                        "max_edge_px": REVIEW_MAX_EDGE_PX,
                        "error": str(exc),
                    },
                    "date": self.admin.poll_date(),
                    "mutates_game": False,
                }
            )
        except Exception as exc:  # noqa: BLE001 -- preserve the archival capture
            review_path.unlink(missing_ok=True)
            return _InspectorImageResult(
                {
                    "ok": False,
                    "action": VIEW_WHOLE_MAP_TOOL_NAME,
                    "status": "reviewer_image_failed",
                    "archival": archival,
                    "reviewer": {
                        "available": False,
                        "max_edge_px": REVIEW_MAX_EDGE_PX,
                        "error": str(exc),
                    },
                    "date": self.admin.poll_date(),
                    "mutates_game": False,
                }
            )

        reviewer = {
            "available": True,
            "format": "png",
            "width": review_width,
            "height": review_height,
            "bytes": review_path.stat().st_size,
            "scale_factor": scale_factor,
            "max_edge_px": REVIEW_MAX_EDGE_PX,
            "path": str(review_path),
        }
        result = _InspectorImageResult(
            {
                "ok": True,
                "action": VIEW_WHOLE_MAP_TOOL_NAME,
                "archival": archival,
                "reviewer": reviewer,
                "date": self.admin.poll_date(),
                "mutates_game": False,
            }
        )
        result.image_bytes = review_path.read_bytes()
        return result

    def measure_replay_telemetry(
        self,
        window_days: int = DEFAULT_WINDOW_DAYS,
        station_sample_ticks: int = DEFAULT_STATION_SAMPLE_TICKS,
        queue_radius: int = DEFAULT_QUEUE_RADIUS_TILES,
    ) -> dict[str, Any]:
        window_days = self._require_days(
            window_days, maximum=TELEMETRY_WINDOW_MAX_DAYS, name="window_days"
        )
        station_sample_ticks = self._require_days(
            station_sample_ticks,
            maximum=1_000_000,
            name="station_sample_ticks",
        )
        if isinstance(queue_radius, bool) or not isinstance(queue_radius, int):
            raise ValueError("queue_radius must be an integer")
        if not 0 <= queue_radius <= 4096:
            raise ValueError("queue_radius must be between 0 and 4096")

        checkpoint_date = self.admin.poll_date()
        stem = self._next_stem("telemetry")
        checkpoint_dir = self.run_dir / "checkpoints"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        checkpoint = checkpoint_dir / f"{stem}.sav"
        self.admin.save_and_confirm(checkpoint)
        report_dir = self.run_dir / "telemetry"
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / f"{stem}.json"
        arguments = (
            "--window-days",
            str(window_days),
            "--station-sample-ticks",
            str(station_sample_ticks),
            "--queue-radius",
            str(queue_radius),
            "--out",
            str(report_path),
        )
        returncode, stdout, timed_out = self.telemetry_runner(
            "openttd_mcp.verify.replay_telemetry",
            checkpoint,
            timeout=TELEMETRY_TIMEOUT_SEC + TELEMETRY_PROCESS_MARGIN_SEC,
            arguments=arguments,
        )
        (report_dir / f"{stem}-stdout.txt").write_text(stdout)
        ending_date = self.admin.poll_date()
        if ending_date != checkpoint_date:
            raise RuntimeError(
                "interactive replay advanced while telemetry ran against its "
                "checkpoint: "
                f"{checkpoint_date} -> {ending_date}"
            )
        if timed_out:
            raise TimeoutError("replay telemetry process exceeded its bounded timeout")
        if not report_path.is_file():
            raise RuntimeError(
                f"replay telemetry exited {returncode} without writing {report_path}"
            )
        report = json.loads(report_path.read_text())
        if not isinstance(report, dict):
            raise ValueError("replay telemetry report must be a JSON object")
        return {
            "ok": returncode == 0,
            "action": TELEMETRY_TOOL_NAME,
            "checkpoint_date": checkpoint_date,
            "interactive_date_after": ending_date,
            "window_days": window_days,
            "exit_code": returncode,
            "report": report,
            "report_path": str(report_path),
            "checkpoint_path": str(checkpoint),
            "mutates_game": False,
        }

    def close(self) -> dict[str, Any]:
        if self._close_report is not None:
            return self._close_report
        started = time.time()
        errors: list[str] = []
        screenshot_source = getattr(self.server, "screenshot_source", None)
        if screenshot_source is not None:
            try:
                screenshot_source.close()
            except Exception as exc:  # noqa: BLE001 -- continue bounded shutdown
                errors.append(f"screenshot client close failed: {exc}")
        try:
            shutdown = quit_game(self.proc, admin=self.admin)
        except Exception as exc:  # noqa: BLE001 -- preserve the shutdown failure
            shutdown = None
            errors.append(f"OpenTTD shutdown failed: {exc}")
        try:
            if self.output_thread is not None:
                self.output_thread.join(timeout=5)
                output_capture_stopped = not self.output_thread.is_alive()
            else:
                output_capture_stopped = True
        except Exception as exc:  # noqa: BLE001 -- retain shutdown state in report
            output_capture_stopped = False
            errors.append(f"OpenTTD output capture join failed: {exc}")
        else:
            if not output_capture_stopped:
                errors.append(
                    "OpenTTD output capture thread did not stop within 5 seconds"
                )
        report = {
            "ok": not errors,
            "bounded": True,
            "shutdown": shutdown,
            "errors": errors,
            "output_capture_stopped": output_capture_stopped,
            "elapsed_seconds": time.time() - started,
        }
        self._close_report = report
        try:
            write_json(self.run_dir / "inspector_cleanup.json", report)
        except Exception as exc:  # noqa: BLE001 -- return the primary shutdown state
            report["ok"] = False
            report["cleanup_record_error"] = str(exc)
            report["errors"].append(f"cleanup evidence write failed: {exc}")
        try:
            write_json(
                self.run_dir / PUBLIC_CLEANUP_FILENAME,
                _public_cleanup_facts(report),
            )
        except Exception as exc:  # noqa: BLE001 -- retain the primary shutdown state
            report["ok"] = False
            report["public_cleanup_record_error"] = str(exc)
            report["errors"].append(f"public cleanup evidence write failed: {exc}")
        return report


class InspectorToolServer(GameToolServer):
    """GameToolServer sensor implementation fenced by the inspector allowlist."""

    def __init__(self, owner: InspectorOwner, *, registry_path: Path | None = None):
        self.owner = owner
        paused_dispatcher = PausedBridgeDispatcher(owner.dispatcher, owner.admin)
        super().__init__(
            paused_dispatcher,
            tool_log=owner.run_dir / "tool_calls.jsonl",
            experiment=INSPECTOR_EXPERIMENT,
            vision_enabled=True,
            admin_config=owner.admin.config,
            screenshot_dir=owner.screenshot_dir,
            crops_dir=owner.run_dir / "crops",
            map_log2=owner.map_log2,
        )
        owner.server = self
        if registry_path is not None:
            write_json(registry_path, inspector_registry_snapshot())
        registry = inspector_registry_snapshot()
        write_json(
            owner.run_dir / PUBLIC_REGISTRY_FILENAME,
            {
                "experiment": registry["experiment"],
                "owner": registry["owner"],
                "transport": registry["transport"],
                "tool_names": [tool["name"] for tool in registry["tools"]],
            },
        )

    def _finalize_tool_call_record(
        self, record: dict[str, Any], *, finished_at_unix: float | None = None
    ) -> None:
        """Write private evidence and a result-free public call projection."""
        super()._finalize_tool_call_record(record, finished_at_unix=finished_at_unix)
        name = record.get("name")
        if name not in (*INSPECTOR_SENSOR_TOOLS, *INSPECTOR_EXTRA_TOOLS):
            return
        public_record: dict[str, Any] = {
            "sequence": self.call_counter,
            "id": record.get("id"),
            "name": name,
            "transport_ok": bool(record.get("transport_ok")),
            "ok": bool(record.get("ok")),
        }
        date_facts = _public_date_facts(record.get("result"))
        if date_facts is not None:
            public_record["date_facts"] = date_facts
        append_jsonl(self.owner.run_dir / PUBLIC_TOOL_CALLS_FILENAME, public_record)

    def handle_tool_call(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        result = super().handle_tool_call(name, arguments)
        if name not in INSPECTOR_SENSOR_TOOLS:
            return result

        image_bytes = getattr(result, "image_bytes", None)
        image_path = result.get("path") if name == "view_map" else None
        public_result = _without_inspector_host_paths(
            result, known_paths=_owner_host_paths(self.owner)
        )
        if image_bytes is None and image_path is None:
            return public_result
        image_result = _InspectorImageResult(public_result)
        if image_bytes is not None:
            image_result.image_bytes = image_bytes
        if image_path is not None:
            image_result.image_path = Path(image_path)
        return image_result

    def _handle_view_map(
        self, arguments: dict[str, Any], call_id: str
    ) -> dict[str, Any]:
        starting_date: int | None = None
        ending_date: int | None = None
        ending_date_error: Exception | None = None
        pause_error: Exception | None = None
        result: dict[str, Any] | None = None
        failure: Exception | None = None
        try:
            starting_date = self.owner.admin.poll_date()
            result = super()._handle_view_map(arguments, call_id)
        except Exception as exc:  # noqa: BLE001 -- retain measured dates on failure
            failure = exc
        finally:
            try:
                self.owner.admin.rcon("pause")
            except Exception as exc:  # noqa: BLE001 -- retain the capture failure
                pause_error = exc
            try:
                ending_date = self.owner.admin.poll_date()
            except Exception as exc:  # noqa: BLE001 -- expose unavailable date
                ending_date_error = exc

        date_drift: dict[str, Any] = {
            "starting_date": starting_date,
            "ending_date": ending_date,
            "observed_elapsed_days": (
                ending_date - starting_date
                if starting_date is not None and ending_date is not None
                else None
            ),
            "pause_confirmed": pause_error is None,
        }
        if pause_error is not None:
            date_drift["pause_error"] = str(pause_error)
        if ending_date_error is not None:
            date_drift["ending_date_error"] = str(ending_date_error)
        primary_failure = failure or pause_error
        if primary_failure is not None:
            raise _SensorDispatchError(
                primary_failure, date_drift, pause_error=pause_error
            ) from primary_failure
        if ending_date_error is not None:
            raise _SensorDispatchError(
                ending_date_error, date_drift
            ) from ending_date_error
        assert result is not None
        result["date_drift"] = date_drift
        return result

    def _handle_tool_call_unlocked(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        arguments = dict(arguments or {})
        if name in INSPECTOR_SENSOR_TOOLS:
            return super()._handle_tool_call_unlocked(name, arguments)

        call_id = self._next_call_id(name)
        started_at = time.time()
        record = self._tool_call_record_header(
            call_id=call_id,
            name=name,
            arguments=arguments,
            started_at_unix=started_at,
        )
        record["command"] = None
        try:
            if name not in INSPECTOR_EXTRA_TOOLS:
                raise ValueError(f"unknown tool in inspector registry: {name}")
            if name == ADVANCE_TOOL_NAME:
                unexpected = set(arguments) - {"duration_days"}
                if unexpected or "duration_days" not in arguments:
                    raise ValueError(
                        "advance_days requires only the duration_days argument"
                    )
                result = self.owner.advance_days(arguments["duration_days"])
            elif name == VIEW_WHOLE_MAP_TOOL_NAME:
                if arguments:
                    raise ValueError("view_whole_map accepts no arguments")
                result = self.owner.view_whole_map()
            elif name == TELEMETRY_TOOL_NAME:
                unexpected = set(arguments) - {
                    "window_days",
                    "station_sample_ticks",
                    "queue_radius",
                }
                if unexpected:
                    raise ValueError(
                        "measure_replay_telemetry received unsupported arguments"
                    )
                result = self.owner.measure_replay_telemetry(**arguments)
            else:
                raise RuntimeError(f"inspector extra has no handler: {name}")
            record["transport_ok"] = True
            record["ok"] = bool(result.get("ok", True))
            record["result"] = result
            return result
        except Exception as exc:  # noqa: BLE001 - MCP failures remain evidence
            result = {"ok": False, "action": name, "error": str(exc)}
            self._merge_exception_tool_result_fields(result, exc)
            record["transport_ok"] = False
            record["ok"] = False
            record["result"] = result
            return result
        finally:
            self._finalize_tool_call_record(record)


def _inspector_extra_specs() -> list[ToolSpec]:
    return [
        ToolSpec(
            ADVANCE_TOOL_NAME,
            (
                "Advance this paused replay by an explicit number of game days, "
                "then pause it again."
            ),
            object_schema(
                {"duration_days": {"type": "integer", "minimum": 1, "maximum": 90}},
                ["duration_days"],
            ),
            True,
        ),
        ToolSpec(
            VIEW_WHOLE_MAP_TOOL_NAME,
            "View the current paused whole map as validated PNG evidence.",
            object_schema({}),
        ),
        ToolSpec(
            TELEMETRY_TOOL_NAME,
            (
                "Checkpoint the paused replay and measure factual replay telemetry "
                "in an isolated process."
            ),
            object_schema(
                {
                    "window_days": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": TELEMETRY_WINDOW_MAX_DAYS,
                        "default": DEFAULT_WINDOW_DAYS,
                    },
                    "station_sample_ticks": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 1_000_000,
                        "default": DEFAULT_STATION_SAMPLE_TICKS,
                    },
                    "queue_radius": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 4096,
                        "default": DEFAULT_QUEUE_RADIUS_TILES,
                    },
                }
            ),
        ),
    ]


def inspector_tool_specs() -> list[ToolSpec]:
    ordinary = {spec.name: spec for spec in tool_specs(enable_vision=True)}
    missing = set(INSPECTOR_SENSOR_TOOLS) - ordinary.keys()
    if missing:
        raise RuntimeError(
            "inspector allowlist names missing from gameplay sensors: "
            f"{sorted(missing)}"
        )
    return [
        ordinary[name] for name in INSPECTOR_SENSOR_TOOLS
    ] + _inspector_extra_specs()


def inspector_registry_snapshot() -> dict[str, Any]:
    return {
        "experiment": INSPECTOR_EXPERIMENT,
        "owner": "referee",
        "transport": "mcp-stdio",
        "tools": [spec.as_registry_entry() for spec in inspector_tool_specs()],
    }


def build_inspector_fastmcp(server: InspectorToolServer):
    mcp = build_fastmcp(server)
    for spec in tool_specs(enable_vision=True):
        if spec.name not in INSPECTOR_SENSOR_TOOLS:
            mcp.remove_tool(spec.name)

    def advance_days(duration_days: WaitDays) -> dict[str, Any]:
        return _without_inspector_host_paths(
            server.handle_tool_call(
                ADVANCE_TOOL_NAME, {"duration_days": duration_days}
            ),
            known_paths=_owner_host_paths(server.owner),
        )

    async def view_whole_map():
        from mcp.server.fastmcp import Image
        from mcp.types import CallToolResult, TextContent

        result = server.handle_tool_call(VIEW_WHOLE_MAP_TOOL_NAME, {})
        model_result = _without_inspector_host_paths(
            result, known_paths=_owner_host_paths(server.owner)
        )
        content = []
        image_bytes = getattr(result, "image_bytes", None)
        if image_bytes is not None:
            content.append(Image(data=image_bytes, format="png").to_image_content())
        content.append(
            TextContent(
                type="text", text=json.dumps(model_result, indent=2, sort_keys=True)
            )
        )
        return CallToolResult(content=content, structuredContent=model_result)

    def measure_replay_telemetry(
        window_days: int = DEFAULT_WINDOW_DAYS,
        station_sample_ticks: int = DEFAULT_STATION_SAMPLE_TICKS,
        queue_radius: int = DEFAULT_QUEUE_RADIUS_TILES,
    ) -> dict[str, Any]:
        return _without_inspector_host_paths(
            server.handle_tool_call(
                TELEMETRY_TOOL_NAME,
                {
                    "window_days": window_days,
                    "station_sample_ticks": station_sample_ticks,
                    "queue_radius": queue_radius,
                },
            ),
            known_paths=_owner_host_paths(server.owner),
        )

    telemetry_days = _bounded_int(1, TELEMETRY_WINDOW_MAX_DAYS)
    positive_int = _bounded_int(1, 1_000_000)
    nonnegative_int = _bounded_int(0, 4096)
    measure_replay_telemetry.__annotations__["window_days"] = telemetry_days
    measure_replay_telemetry.__annotations__["station_sample_ticks"] = positive_int
    measure_replay_telemetry.__annotations__["queue_radius"] = nonnegative_int

    descriptions = {spec.name: spec.description for spec in _inspector_extra_specs()}
    mcp.tool(name=ADVANCE_TOOL_NAME, description=descriptions[ADVANCE_TOOL_NAME])(
        advance_days
    )
    mcp.tool(
        name=VIEW_WHOLE_MAP_TOOL_NAME,
        description=descriptions[VIEW_WHOLE_MAP_TOOL_NAME],
    )(view_whole_map)
    mcp.tool(name=TELEMETRY_TOOL_NAME, description=descriptions[TELEMETRY_TOOL_NAME])(
        measure_replay_telemetry
    )
    return mcp


def validate_source_save(path: Path) -> Path:
    source = Path(path).expanduser().resolve()
    if source.suffix.lower() != ".sav":
        raise ValueError(f"source save must have a .sav suffix: {source}")
    if not source.is_file():
        raise ValueError(f"source save does not exist as one file: {source}")
    if source.stat().st_size <= 0:
        raise ValueError(f"source save is empty: {source}")
    return source


def _loaded_map_geometry(welcome: dict[str, Any]) -> tuple[int, int, int]:
    width = welcome.get("map_width")
    height = welcome.get("map_height")
    if (
        isinstance(width, bool)
        or not isinstance(width, int)
        or isinstance(height, bool)
        or not isinstance(height, int)
    ):
        raise ValueError(
            "administrative welcome did not provide integer map_width/map_height"
        )
    if width != height:
        raise ValueError(f"loaded save map must be square, got {width}x{height}")
    if width <= 0 or width & (width - 1):
        raise ValueError(
            f"loaded save map edge must be a positive power of two, got {width}"
        )
    return width, height, width.bit_length() - 1


def _record_failed_startup(
    run_dir: Path, proc: Any | None, admin: Any | None, error: BaseException
) -> None:
    cleanup_error = None
    try:
        shutdown = quit_game(proc, admin=admin) if proc is not None else None
    except Exception as exc:  # noqa: BLE001 -- the startup failure remains primary
        shutdown = None
        cleanup_error = str(exc)
    report = {
        "ok": False,
        "bounded": True,
        "startup_error": str(error),
        "startup_error_type": type(error).__name__,
        "shutdown": shutdown,
        "cleanup_error": cleanup_error,
    }
    for path, evidence in (
        (run_dir / "inspector_cleanup.json", report),
        (run_dir / PUBLIC_CLEANUP_FILENAME, _public_cleanup_facts(report)),
    ):
        try:
            write_json(path, evidence)
        except (OSError, TypeError, ValueError) as evidence_error:
            # The original BaseException, including KeyboardInterrupt, must remain
            # the exception the caller receives when evidence storage also fails.
            logging.getLogger(__name__).warning(
                "failed to write inspector startup cleanup evidence: %s",
                evidence_error,
            )


def open_inspector(
    source_save: Path,
    *,
    run_dir: Path | None = None,
    map_log2: int | None = None,
    startup_timeout: float = 120.0,
    result_timeout: float = 240.0,
) -> InspectorOwner:
    source = validate_source_save(source_save)
    selected_run_dir = (
        Path(run_dir).resolve()
        if run_dir is not None
        else LOCAL_RUNS_DIR / f"{now_id()}-inspector"
    )
    if selected_run_dir.exists():
        if not selected_run_dir.is_dir():
            raise InspectorStartupError(
                f"inspector run path is not a directory: {selected_run_dir}",
                error_code="run_path_not_directory",
                run_dir=selected_run_dir,
            )
        if any(selected_run_dir.iterdir()):
            raise InspectorStartupError(
                f"inspector run directory is not empty: {selected_run_dir}",
                error_code="run_directory_not_empty",
                run_dir=selected_run_dir,
            )
    else:
        selected_run_dir.mkdir(parents=True)
    proc = None
    admin = None
    try:
        admin_port = allocate_admin_port()
        admin_config = AdminLifecycleConfig(
            port=admin_port, client_name="openttd-mcp-inspector"
        )
        game_port = allocate_admin_port()
        while game_port == admin_port:
            game_port = allocate_admin_port()
        runtime_id = f"{selected_run_dir.name}-admin-{admin_port}"
        run_root, bridge_root = prepare_runtime(
            runtime_id,
            DEFAULT_MAP_LOG2,
            admin_config=admin_config,
            emit_boot_snapshot=False,
        )
        (run_root / "scripts" / "game_start.scr").write_text(
            "echo inspector: using saved McpBridge AI company\nunpause\n"
        )
        save_dir = run_root / "save"
        save_dir.mkdir(parents=True, exist_ok=True)
        runtime_save = save_dir / "inspection-source.sav"
        shutil.copy2(source, runtime_save)
        log_path = selected_run_dir / "openttd.log"
    except BaseException as exc:
        _record_failed_startup(selected_run_dir, proc, admin, exc)
        raise
    try:
        proc = launch_openttd(
            selected_run_dir,
            run_root,
            DEFAULT_SEED,
            admin_config,
            vision_blitter=SCREENSHOT_BLITTER,
            save_path=runtime_save,
            game_port=game_port,
        )
        ready = threading.Event()
        output_thread = capture_openttd_output(
            proc, log_path, ready, thread_name="inspector-openttd-log"
        )
        admin = AdminClient(admin_config)
        welcome = admin.connect(timeout=startup_timeout)
        first_observed_date = admin.poll_date()
        map_width, map_height, derived_map_log2 = _loaded_map_geometry(welcome)
        if map_log2 is not None:
            if isinstance(map_log2, bool) or not isinstance(map_log2, int):
                raise ValueError("map_log2 assertion must be an integer")
            if map_log2 != derived_map_log2:
                raise ValueError(
                    "loaded save map-log2 mismatch: "
                    f"welcome reported {map_width}x{map_height}, which derives "
                    f"map_log2={derived_map_log2}, but --map-log2 asserted {map_log2}"
                )
        wait_for_bridge_ready(ready, proc, timeout=startup_timeout)
        admin.rcon("pause")
        paused_date = admin.poll_date()
        dispatcher = FileLogBridgeDispatcher(
            bridge_root, log_path, timeout=result_timeout
        )
        owner = InspectorOwner(
            source_save=source,
            run_dir=selected_run_dir,
            run_root=run_root,
            bridge_root=bridge_root,
            openttd_log=log_path,
            screenshot_dir=run_root / "screenshot",
            proc=proc,
            admin=admin,
            dispatcher=dispatcher,
            output_thread=output_thread,
            map_log2=derived_map_log2,
        )
        startup = {
            "ok": True,
            "source_save": str(source),
            "runtime_save": str(runtime_save),
            "bridge_ready_marker": BRIDGE_READY_MARKER,
            "first_observed_date": first_observed_date,
            "paused_date": paused_date,
            "startup_observed_elapsed_days": paused_date - first_observed_date,
            "map_width": map_width,
            "map_height": map_height,
            "derived_map_log2": derived_map_log2,
            "map_log2_assertion": map_log2,
        }
        write_json(selected_run_dir / "inspector_startup.json", startup)
        write_json(
            selected_run_dir / PUBLIC_STARTUP_FILENAME,
            {
                "ok": True,
                "bridge_ready": True,
                "first_observed_date": first_observed_date,
                "paused_date": paused_date,
                "startup_observed_elapsed_days": paused_date - first_observed_date,
                "map_width": map_width,
                "map_height": map_height,
                "derived_map_log2": derived_map_log2,
                "map_log2_assertion": map_log2,
            },
        )
        return owner
    except BaseException as exc:
        _record_failed_startup(selected_run_dir, proc, admin, exc)
        raise


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("save_path", type=Path)
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument(
        "--map-log2",
        type=int,
        help="optional assertion against the dimensions reported by the loaded save",
    )
    parser.add_argument("--startup-timeout", type=float, default=120.0)
    parser.add_argument("--result-timeout", type=float, default=240.0)
    parser.add_argument("--dump-registry", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.dump_registry:
        print(json.dumps(inspector_registry_snapshot(), indent=2, sort_keys=True))
        return 0
    owner = open_inspector(
        args.save_path,
        run_dir=args.run_dir,
        map_log2=args.map_log2,
        startup_timeout=args.startup_timeout,
        result_timeout=args.result_timeout,
    )
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def exit_through_cleanup(_signum: int, _frame: Any) -> None:
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, exit_through_cleanup)
    try:
        server = InspectorToolServer(
            owner, registry_path=owner.run_dir / "inspector_registry.json"
        )
        mcp = build_inspector_fastmcp(server)
        mcp.run("stdio")
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        owner.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
