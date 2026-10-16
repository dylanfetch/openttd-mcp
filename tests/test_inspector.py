"""Public-behavior tests for the referee-only interactive save inspector."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import signal
import struct
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import openttd_mcp.inspector as inspector_module
import openttd_mcp.lifecycle as lifecycle_module
from openttd_mcp.inspector import (
    ADVANCE_TOOL_NAME,
    BRIDGE_READY_MARKER,
    INSPECTOR_SENSOR_TOOLS,
    TELEMETRY_TOOL_NAME,
    VIEW_WHOLE_MAP_TOOL_NAME,
    InspectorOwner,
    InspectorStartupError,
    InspectorToolServer,
    PausedBridgeDispatcher,
    build_inspector_fastmcp,
    inspector_registry_snapshot,
    open_inspector,
    validate_source_save,
)
from openttd_mcp.render_capture import (
    PNG_SIGNATURE,
    RenderDependencyError,
)
from openttd_mcp.server import tool_specs

EXPECTED_REVIEW_MAX_EDGE_PX = 2048
EXPECTED_INSPECTOR_TOOL_NAMES = {
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
    "advance_days",
    "view_whole_map",
    "measure_replay_telemetry",
}


def png_header(width: int = 128, height: int = 64) -> bytes:
    return PNG_SIGNATURE + struct.pack(">I4sII", 13, b"IHDR", width, height)


def png_dimensions_from_bytes(payload: bytes) -> tuple[int, int]:
    assert payload.startswith(PNG_SIGNATURE)
    return struct.unpack(">II", payload[16:24])


def structured_tool_result(mcp, name: str, arguments: dict) -> dict:
    result = asyncio.run(mcp.call_tool(name, arguments))
    if isinstance(result, tuple):
        assert len(result) == 2
        structured = result[1]
    else:
        structured = result.structuredContent
    assert isinstance(structured, dict)
    return structured


class FakeAdmin:
    def __init__(self, dates: list[int] | None = None) -> None:
        self.config = SimpleNamespace(port=3977, password="test")
        self.dates = list(dates or [730000])
        self.calls: list[tuple] = []

    def rcon(self, command: str, timeout: float = 30.0) -> list[str]:
        self.calls.append(("rcon", command))
        return []

    def poll_date(self, timeout: float = 10.0) -> int:
        self.calls.append(("poll_date",))
        if len(self.dates) > 1:
            return self.dates.pop(0)
        return self.dates[0]

    def save_and_confirm(self, save_path: Path, **_kwargs) -> dict:
        self.calls.append(("save_and_confirm", Path(save_path)))
        Path(save_path).write_bytes(b"checkpoint")
        return {"size": 10}


class FakeDispatcher:
    def __init__(self, result: dict | None = None, error: Exception | None = None):
        self.result = result or {"ok": True}
        self.error = error
        self.calls: list[tuple[list, str]] = []

    def dispatch(self, command: list, timestamp: str) -> dict:
        self.calls.append((command, timestamp))
        if self.error is not None:
            raise self.error
        return self.result


def make_owner(
    tmp_path: Path,
    *,
    admin: FakeAdmin | None = None,
    dispatcher: FakeDispatcher | None = None,
    telemetry_runner=None,
) -> InspectorOwner:
    run_dir = tmp_path / "run"
    run_root = tmp_path / "runtime"
    bridge_root = run_root / "ai" / "McpBridge"
    for directory in (run_dir, bridge_root, run_root / "screenshot"):
        directory.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "source.sav"
    source.write_bytes(b"save")
    kwargs = {}
    if telemetry_runner is not None:
        kwargs["telemetry_runner"] = telemetry_runner
    return InspectorOwner(
        source_save=source,
        run_dir=run_dir,
        run_root=run_root,
        bridge_root=bridge_root,
        openttd_log=run_dir / "openttd.log",
        screenshot_dir=run_root / "screenshot",
        proc=SimpleNamespace(),
        admin=admin or FakeAdmin(),
        dispatcher=dispatcher or FakeDispatcher(),
        **kwargs,
    )


@pytest.mark.parametrize(
    "name,payload,message",
    [
        ("missing.sav", None, "does not exist"),
        ("empty.sav", b"", "empty"),
        ("wrong.txt", b"save", ".sav suffix"),
    ],
)
def test_source_save_rejection_precedes_runtime_creation(
    tmp_path: Path, name: str, payload: bytes | None, message: str
) -> None:
    path = tmp_path / name
    if payload is not None:
        path.write_bytes(payload)

    with pytest.raises(ValueError, match=message):
        validate_source_save(path)


def test_dump_registry_precedes_save_validation(tmp_path: Path, capsys) -> None:
    missing_save = tmp_path / "does-not-exist.sav"

    exit_code = inspector_module.main([str(missing_save), "--dump-registry"])

    assert exit_code == 0
    dumped = json.loads(capsys.readouterr().out)
    assert {tool["name"] for tool in dumped["tools"]} == EXPECTED_INSPECTOR_TOOL_NAMES


def test_sigterm_runs_inspector_cleanup_before_process_exit(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "accepted.sav"
    source.write_bytes(b"save")
    owner = SimpleNamespace(close=Mock(), run_dir=tmp_path / "inspection")
    server = object()
    prior = signal.getsignal(signal.SIGTERM)
    monkeypatch.setattr(inspector_module, "open_inspector", Mock(return_value=owner))
    monkeypatch.setattr(
        inspector_module, "InspectorToolServer", Mock(return_value=server)
    )
    monkeypatch.setattr(
        inspector_module,
        "build_inspector_fastmcp",
        Mock(
            return_value=SimpleNamespace(
                run=lambda _transport: os.kill(os.getpid(), signal.SIGTERM)
            )
        ),
    )

    with pytest.raises(SystemExit) as stopped:
        inspector_module.main([str(source)])

    assert stopped.value.code == 0
    owner.close.assert_called_once_with()
    assert signal.getsignal(signal.SIGTERM) is prior


def test_paused_source_defect_startup_script_unpauses_once_then_admin_pauses(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "accepted.sav"
    source.write_bytes(b"save")
    run_dir = tmp_path / "inspection"
    run_root = tmp_path / "runtime"
    bridge_root = run_root / "ai" / "McpBridge"
    (run_root / "scripts").mkdir(parents=True)
    bridge_root.mkdir(parents=True)
    proc = SimpleNamespace(
        stdout=iter([f"dbg: [ai] {BRIDGE_READY_MARKER}\n"]),
        poll=lambda: None,
    )
    independently_observed_dates = [744998, 745000]
    admin = FakeAdmin(independently_observed_dates)
    admin.connect = Mock(
        return_value={"map_name": "loaded", "map_width": 256, "map_height": 256}
    )
    launched: dict = {}

    monkeypatch.setattr(
        inspector_module, "allocate_admin_port", Mock(side_effect=[4101, 4102])
    )
    monkeypatch.setattr(
        inspector_module,
        "prepare_runtime",
        lambda *_args, **_kwargs: (run_root, bridge_root),
    )

    def launch(*args, **kwargs):
        launched.update({"args": args, "kwargs": kwargs})
        return proc

    monkeypatch.setattr(inspector_module, "launch_openttd", launch)
    monkeypatch.setattr(inspector_module, "AdminClient", lambda _config: admin)

    owner = open_inspector(source, run_dir=run_dir, startup_timeout=1)

    assert admin.connect.call_count == 1
    assert ("rcon", "pause") in admin.calls
    script_commands = [
        line.strip()
        for line in (run_root / "scripts" / "game_start.scr").read_text().splitlines()
        if line.strip()
    ]
    assert script_commands.count("unpause") == 1
    assert launched["kwargs"]["save_path"].read_bytes() == b"save"
    assert launched["kwargs"]["game_port"] == 4102
    startup = json.loads((run_dir / "inspector_startup.json").read_text())
    assert startup["bridge_ready_marker"] == BRIDGE_READY_MARKER
    assert startup["first_observed_date"] == independently_observed_dates[0]
    assert startup["paused_date"] == independently_observed_dates[1]
    assert startup["startup_observed_elapsed_days"] == (
        independently_observed_dates[1] - independently_observed_dates[0]
    )
    assert admin.calls == [("poll_date",), ("rcon", "pause"), ("poll_date",)]
    assert startup["map_width"] == startup["map_height"] == 256
    assert startup["derived_map_log2"] == 8
    assert startup["map_log2_assertion"] is None
    public_startup_path = run_dir / "inspector_public_startup.json"
    public_startup = json.loads(public_startup_path.read_text())
    assert public_startup == {
        "ok": True,
        "bridge_ready": True,
        "first_observed_date": startup["first_observed_date"],
        "paused_date": startup["paused_date"],
        "startup_observed_elapsed_days": startup["startup_observed_elapsed_days"],
        "map_width": startup["map_width"],
        "map_height": startup["map_height"],
        "derived_map_log2": startup["derived_map_log2"],
        "map_log2_assertion": startup["map_log2_assertion"],
    }
    assert str(source) not in public_startup_path.read_text()
    assert str(run_root) not in public_startup_path.read_text()
    assert owner.map_log2 == 8
    owner.output_thread.join(timeout=1)


def test_nonempty_run_directory_reuse_refuses_evidence_mixing_without_side_effects(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "accepted.sav"
    source.write_bytes(b"save")
    run_dir = tmp_path / "inspection"
    run_dir.mkdir()
    prior_artifact = run_dir / "prior-evidence.json"
    prior_bytes = b'{"accepted": true}\n'
    prior_artifact.write_bytes(prior_bytes)
    allocate_port = Mock()
    prepare = Mock()
    launch = Mock()
    admin_client = Mock()
    monkeypatch.setattr(inspector_module, "allocate_admin_port", allocate_port)
    monkeypatch.setattr(inspector_module, "prepare_runtime", prepare)
    monkeypatch.setattr(inspector_module, "launch_openttd", launch)
    monkeypatch.setattr(inspector_module, "AdminClient", admin_client)

    with pytest.raises(InspectorStartupError) as raised:
        open_inspector(source, run_dir=run_dir)

    assert raised.value.startup_result["error_code"] == "run_directory_not_empty"
    assert Path(raised.value.startup_result["run_dir"]) == run_dir.resolve()
    assert list(run_dir.iterdir()) == [prior_artifact]
    assert prior_artifact.read_bytes() == prior_bytes
    assert allocate_port.call_count == 0
    assert prepare.call_count == 0
    assert launch.call_count == 0
    assert admin_client.call_count == 0


def test_same_basename_inspections_with_different_ports_cannot_share_runtime_target(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "accepted.sav"
    source.write_bytes(b"save")
    prepared: list[dict[str, object]] = []
    launched: list[dict[str, int]] = []

    monkeypatch.setattr(
        inspector_module,
        "allocate_admin_port",
        Mock(side_effect=[4101, 4101, 4102, 4201, 4202]),
    )

    def prepare(runtime_id, _map_log2, *, admin_config, **_kwargs):
        run_root = tmp_path / "prepared" / runtime_id / "openttd-root"
        bridge_root = run_root / "ai" / "McpBridge"
        (run_root / "scripts").mkdir(parents=True)
        bridge_root.mkdir(parents=True)
        prepared.append(
            {
                "runtime_id": runtime_id,
                "target": run_root,
                "admin_port": admin_config.port,
            }
        )
        return run_root, bridge_root

    def launch(_run_dir, _run_root, _seed, admin_config, **kwargs):
        launched.append(
            {
                "admin_port": admin_config.port,
                "game_port": kwargs["game_port"],
            }
        )
        return SimpleNamespace(
            stdout=iter([f"dbg: [ai] {BRIDGE_READY_MARKER}\n"]),
            poll=lambda: None,
        )

    def admin_client(config):
        admin = FakeAdmin([745000])
        admin.config = config
        admin.connect = Mock(return_value={"map_width": 128, "map_height": 128})
        return admin

    monkeypatch.setattr(inspector_module, "prepare_runtime", prepare)
    monkeypatch.setattr(inspector_module, "launch_openttd", launch)
    monkeypatch.setattr(inspector_module, "AdminClient", admin_client)

    inspection_dirs = [
        tmp_path / parent / "inspection" for parent in ("first", "second")
    ]
    owners = [
        open_inspector(
            source,
            run_dir=inspection_dir,
            startup_timeout=1,
        )
        for inspection_dir in inspection_dirs
    ]

    assert len({path.name for path in inspection_dirs}) == 1
    runtime_ids = {entry["runtime_id"] for entry in prepared}
    preparation_targets = {entry["target"] for entry in prepared}
    assert len(runtime_ids) == 2
    assert len(preparation_targets) == 2
    assert [entry["admin_port"] for entry in prepared] == [4101, 4201]
    assert all(
        str(entry["admin_port"]) in str(entry["runtime_id"]) for entry in prepared
    )
    assert launched == [
        {"admin_port": 4101, "game_port": 4102},
        {"admin_port": 4201, "game_port": 4202},
    ]
    assert all(call["admin_port"] != call["game_port"] for call in launched)
    for owner in owners:
        owner.output_thread.join(timeout=1)


@pytest.mark.parametrize(
    "map_width,map_height,map_log2,message",
    [
        (128, 256, None, "square"),
        (96, 96, None, "power of two"),
        (128, 128, 6, "mismatch"),
    ],
)
def test_loaded_save_geometry_rejects_non_square_non_power_of_two_and_cli_mismatch(
    tmp_path: Path,
    monkeypatch,
    map_width: int,
    map_height: int,
    map_log2: int | None,
    message: str,
) -> None:
    source = tmp_path / "accepted.sav"
    source.write_bytes(b"save")
    run_dir = tmp_path / "inspection"
    run_root = tmp_path / "runtime"
    bridge_root = run_root / "ai" / "McpBridge"
    (run_root / "scripts").mkdir(parents=True)
    bridge_root.mkdir(parents=True)
    proc = SimpleNamespace(stdout=iter([]), poll=Mock(return_value=None))
    admin = FakeAdmin()
    admin.connect = Mock(
        return_value={"map_width": map_width, "map_height": map_height}
    )

    monkeypatch.setattr(
        inspector_module, "allocate_admin_port", Mock(side_effect=[4101, 4102])
    )
    monkeypatch.setattr(
        inspector_module,
        "prepare_runtime",
        lambda *_args, **_kwargs: (run_root, bridge_root),
    )
    monkeypatch.setattr(inspector_module, "launch_openttd", lambda *_a, **_k: proc)
    monkeypatch.setattr(inspector_module, "AdminClient", lambda _config: admin)
    monkeypatch.setattr(
        inspector_module,
        "quit_game",
        Mock(return_value={"graceful": False, "exit_code": 0}),
    )

    with pytest.raises(ValueError, match=message):
        open_inspector(
            source,
            run_dir=run_dir,
            map_log2=map_log2,
            startup_timeout=0.01,
        )

    cleanup = json.loads((run_dir / "inspector_cleanup.json").read_text())
    assert cleanup["bounded"] is True
    assert message in cleanup["startup_error"]


def test_startup_failure_before_launch_still_records_bounded_cleanup(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "accepted.sav"
    source.write_bytes(b"save")
    run_dir = tmp_path / "inspection"
    monkeypatch.setattr(
        inspector_module,
        "allocate_admin_port",
        Mock(side_effect=PermissionError("listeners are unavailable")),
    )

    with pytest.raises(PermissionError, match="listeners are unavailable"):
        open_inspector(source, run_dir=run_dir)

    cleanup = json.loads((run_dir / "inspector_cleanup.json").read_text())
    assert cleanup["bounded"] is True
    assert "listeners are unavailable" in cleanup["startup_error"]
    assert cleanup["shutdown"] is None
    public_path = run_dir / "inspector_public_cleanup.json"
    public = json.loads(public_path.read_text())
    assert public == {
        "ok": False,
        "bounded": True,
        "shutdown": None,
        "error_count": 0,
        "startup_failed": True,
        "startup_error_type": "PermissionError",
        "cleanup_error_occurred": False,
    }
    assert "listeners are unavailable" not in public_path.read_text()


@pytest.mark.parametrize(
    "failure",
    [
        SystemExit("Missing OpenTTD binary"),
        KeyboardInterrupt("operator interrupted startup"),
    ],
)
def test_startup_baseexception_records_cleanup_and_reraises_the_same_failure(
    tmp_path: Path, monkeypatch, failure: BaseException
) -> None:
    source = tmp_path / "accepted.sav"
    source.write_bytes(b"save")
    run_dir = tmp_path / "inspection"
    monkeypatch.setattr(
        inspector_module, "allocate_admin_port", Mock(side_effect=[4101, 4102])
    )
    monkeypatch.setattr(
        inspector_module,
        "prepare_runtime",
        Mock(side_effect=failure),
    )

    with pytest.raises(type(failure)) as raised:
        open_inspector(source, run_dir=run_dir)

    assert raised.value is failure
    cleanup = json.loads((run_dir / "inspector_cleanup.json").read_text())
    assert cleanup["bounded"] is True
    assert cleanup["startup_error_type"] == type(failure).__name__
    assert str(failure) in cleanup["startup_error"]


def test_startup_ready_poll_exits_as_soon_as_the_openttd_child_dies(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "accepted.sav"
    source.write_bytes(b"save")
    run_dir = tmp_path / "inspection"
    run_root = tmp_path / "runtime"
    bridge_root = run_root / "ai" / "McpBridge"
    (run_root / "scripts").mkdir(parents=True)
    bridge_root.mkdir(parents=True)
    proc = SimpleNamespace(stdout=iter([]), poll=Mock(return_value=17))
    admin = FakeAdmin()
    admin.connect = Mock(return_value={"map_width": 128, "map_height": 128})

    monkeypatch.setattr(
        inspector_module, "allocate_admin_port", Mock(side_effect=[4101, 4102])
    )
    monkeypatch.setattr(
        inspector_module,
        "prepare_runtime",
        lambda *_args, **_kwargs: (run_root, bridge_root),
    )
    monkeypatch.setattr(inspector_module, "launch_openttd", lambda *_a, **_k: proc)
    monkeypatch.setattr(inspector_module, "AdminClient", lambda _config: admin)
    monkeypatch.setattr(
        inspector_module,
        "quit_game",
        Mock(return_value={"graceful": False, "exit_code": 17}),
    )

    with pytest.raises(RuntimeError, match="exited with code 17"):
        open_inspector(source, run_dir=run_dir, startup_timeout=60)

    assert proc.poll.call_count == 1
    cleanup = json.loads((run_dir / "inspector_cleanup.json").read_text())
    assert "exited with code 17" in cleanup["startup_error"]


def test_lifecycle_launch_loads_the_isolated_save_on_the_allocated_game_port(
    tmp_path: Path, monkeypatch
) -> None:
    run_dir = tmp_path / "run"
    run_root = tmp_path / "runtime"
    save_path = run_root / "save" / "inspection-source.sav"
    run_dir.mkdir()
    save_path.parent.mkdir(parents=True)
    save_path.write_bytes(b"save")
    popen = Mock(return_value=object())
    monkeypatch.setattr(lifecycle_module.subprocess, "Popen", popen)

    lifecycle_module.launch_openttd(
        run_dir,
        run_root,
        1337,
        save_path=save_path,
        game_port=4201,
    )

    command = json.loads((run_dir / "openttd_command.json").read_text())
    assert command[1:5] == ["-D", "127.0.0.1:4201", "-g", str(save_path)]
    assert "-G" not in command
    assert popen.call_args.kwargs["cwd"] == str(run_root)


def test_lifecycle_verifier_process_receives_telemetry_options_after_save(
    tmp_path: Path, monkeypatch
) -> None:
    save_path = tmp_path / "checkpoint.sav"
    save_path.write_bytes(b"save")
    proc = SimpleNamespace(returncode=0, communicate=Mock(return_value=("ok", None)))
    popen = Mock(return_value=proc)
    monkeypatch.setattr(lifecycle_module.subprocess, "Popen", popen)

    result = lifecycle_module.run_verifier_subprocess(
        "openttd_mcp.verify.replay_telemetry",
        save_path,
        timeout=10,
        arguments=("--window-days", "14"),
    )

    assert result == (0, "ok", False)
    assert popen.call_args.args[0][-3:] == [str(save_path), "--window-days", "14"]
    assert popen.call_args.kwargs["start_new_session"] is True


def test_bridge_queries_restore_pause_even_when_dispatch_fails() -> None:
    admin = FakeAdmin([730000, 730003])
    dispatcher = FakeDispatcher(error=TimeoutError("bridge stopped"))
    paused = PausedBridgeDispatcher(dispatcher, admin)

    with pytest.raises(TimeoutError, match="bridge stopped"):
        paused.dispatch(["list_towns"], "query-1")

    assert admin.calls == [
        ("poll_date",),
        ("rcon", "unpause"),
        ("rcon", "pause"),
        ("poll_date",),
    ]


def test_inspector_sensor_dispatch_exposes_date_drift_and_logs_the_same_envelope(
    tmp_path: Path,
) -> None:
    admin = FakeAdmin([730000, 730002])
    dispatcher = FakeDispatcher({"ok": True, "towns": []})
    owner = make_owner(tmp_path, admin=admin, dispatcher=dispatcher)
    server = InspectorToolServer(owner)

    result = server.handle_tool_call("list_towns", {})

    assert result["date_drift"] == {
        "starting_date": 730000,
        "ending_date": 730002,
        "observed_elapsed_days": 2,
        "pause_confirmed": True,
    }
    assert admin.calls == [
        ("poll_date",),
        ("rcon", "unpause"),
        ("rcon", "pause"),
        ("poll_date",),
    ]
    record = json.loads((owner.run_dir / "tool_calls.jsonl").read_text())
    assert record["result"]["date_drift"] == result["date_drift"]


def test_inspector_record_projection_keeps_base_monotonic_timing(
    tmp_path: Path,
) -> None:
    """The inspector finalizer override must not discard base tool timing fields."""
    owner = make_owner(
        tmp_path,
        admin=FakeAdmin([730000, 730000]),
        dispatcher=FakeDispatcher({"ok": True, "towns": []}),
    )
    server = InspectorToolServer(owner)

    server.handle_tool_call("list_towns", {})

    record = json.loads((owner.run_dir / "tool_calls.jsonl").read_text())
    assert record["timing_status"] == "success"
    assert record["finished_at_monotonic_ns"] >= record["started_at_monotonic_ns"]
    assert record["duration_ns"] == (
        record["finished_at_monotonic_ns"] - record["started_at_monotonic_ns"]
    )


def test_inspector_sensor_failure_still_pauses_and_keeps_measured_date_drift(
    tmp_path: Path,
) -> None:
    admin = FakeAdmin([730100, 730105])
    owner = make_owner(
        tmp_path,
        admin=admin,
        dispatcher=FakeDispatcher(error=TimeoutError("bridge stopped")),
    )
    server = InspectorToolServer(owner)

    result = server.handle_tool_call("list_towns", {})

    assert result["ok"] is False
    assert result["date_drift"]["starting_date"] == 730100
    assert result["date_drift"]["ending_date"] == 730105
    assert result["date_drift"]["observed_elapsed_days"] == 5
    assert result["date_drift"]["pause_confirmed"] is True
    assert admin.calls[-2:] == [("rcon", "pause"), ("poll_date",)]
    record = json.loads((owner.run_dir / "tool_calls.jsonl").read_text())
    assert record["transport_ok"] is False
    assert record["result"]["date_drift"] == result["date_drift"]


def test_inherited_sensor_timeout_scrubs_host_path_but_raw_log_retains_it(
    tmp_path: Path,
) -> None:
    dispatcher = FakeDispatcher()
    owner = make_owner(
        tmp_path,
        admin=FakeAdmin([730100, 730100]),
        dispatcher=dispatcher,
    )
    private_result = owner.bridge_root / "pending-result.json"
    dispatcher.error = TimeoutError(f"bridge timed out reading {private_result}")
    mcp = build_inspector_fastmcp(InspectorToolServer(owner))

    public_result = structured_tool_result(mcp, "list_towns", {})

    assert public_result["ok"] is False
    assert str(private_result) not in json.dumps(public_result, sort_keys=True)
    assert "<host-path>" in public_result["error"]
    raw_log = (owner.run_dir / "tool_calls.jsonl").read_text()
    assert str(private_result) in raw_log


def test_inspector_view_map_sensor_exposes_zero_drift_and_reasserts_pause(
    tmp_path: Path,
) -> None:
    admin = FakeAdmin([730200, 730200])
    owner = make_owner(tmp_path, admin=admin)
    server = InspectorToolServer(owner)

    class ScreenshotSource:
        def crop_for_tile(self, tile, window, *, refresh, call_id):
            return {
                "ok": True,
                "action": "view_map",
                "tile": tile,
                "window": window,
                "refresh": refresh,
                "call_id": call_id,
            }

    server.screenshot_source = ScreenshotSource()

    result = server.handle_tool_call(
        "view_map", {"tile": 7, "window": 64, "refresh": False}
    )

    assert result["date_drift"]["starting_date"] == 730200
    assert result["date_drift"]["ending_date"] == 730200
    assert result["date_drift"]["observed_elapsed_days"] == 0
    assert result["date_drift"]["pause_confirmed"] is True
    assert admin.calls == [("poll_date",), ("rcon", "pause"), ("poll_date",)]


def test_advance_is_date_measured_unpause_wait_pause_transition(
    tmp_path: Path,
) -> None:
    admin = FakeAdmin([710000, 710012])
    dispatcher = FakeDispatcher({"ok": True, "action": "wait", "days_elapsed": 12})
    owner = make_owner(tmp_path, admin=admin, dispatcher=dispatcher)

    result = owner.advance_days(12)

    assert result["requested_days"] == 12
    assert result["starting_date"] == 710000
    assert result["ending_date"] == 710012
    assert result["observed_elapsed_days"] == 12
    assert result["pause_confirmed"] is True
    assert dispatcher.calls[0][0] == ["wait", 12]
    assert admin.calls == [
        ("poll_date",),
        ("rcon", "unpause"),
        ("rcon", "pause"),
        ("poll_date",),
    ]


def test_advance_timeout_returns_dates_warning_and_still_restores_pause(
    tmp_path: Path,
) -> None:
    admin = FakeAdmin([710000, 710004])
    owner = make_owner(
        tmp_path,
        admin=admin,
        dispatcher=FakeDispatcher(error=TimeoutError("bridge result deadline")),
    )
    server = InspectorToolServer(owner)

    result = server.handle_tool_call(ADVANCE_TOOL_NAME, {"duration_days": 3})

    assert result["ok"] is False
    assert result["status"] == "timeout"
    assert result["requested_days"] == 3
    assert result["starting_date"] == 710000
    assert result["ending_date"] == 710004
    assert result["observed_elapsed_days"] == 4
    assert result["pause_confirmed"] is True
    assert "may remain unfinished" in result["warning"]
    assert admin.calls == [
        ("poll_date",),
        ("rcon", "unpause"),
        ("rcon", "pause"),
        ("poll_date",),
    ]
    record = json.loads((owner.run_dir / "tool_calls.jsonl").read_text())
    assert record["result"]["ending_date"] == 710004


def test_advance_non_timeout_failure_keeps_date_envelope_in_public_result_and_log(
    tmp_path: Path,
) -> None:
    class StatefulAdmin(FakeAdmin):
        def __init__(self) -> None:
            super().__init__()
            self.current_date = 710000

        def poll_date(self, timeout: float = 10.0) -> int:
            self.calls.append(("poll_date",))
            return self.current_date

    class FailingAfterDrift(FakeDispatcher):
        def __init__(self, admin: StatefulAdmin) -> None:
            super().__init__()
            self.admin = admin

        def dispatch(self, command: list, timestamp: str) -> dict:
            self.calls.append((command, timestamp))
            self.admin.current_date += 2
            raise RuntimeError("bridge rejected the wait")

    admin = StatefulAdmin()
    owner = make_owner(
        tmp_path,
        admin=admin,
        dispatcher=FailingAfterDrift(admin),
    )
    mcp = build_inspector_fastmcp(InspectorToolServer(owner))

    public_result = structured_tool_result(mcp, ADVANCE_TOOL_NAME, {"duration_days": 3})

    expected_evidence = {
        "requested_days": 3,
        "starting_date": 710000,
        "ending_date": 710002,
        "observed_elapsed_days": 2,
        "pause_confirmed": True,
    }
    assert public_result["ok"] is False
    assert public_result["status"] == "failed"
    assert "rejected the wait" in public_result["error"]
    assert {key: public_result[key] for key in expected_evidence} == expected_evidence
    record = json.loads((owner.run_dir / "tool_calls.jsonl").read_text())
    assert record["transport_ok"] is False
    assert {
        key: record["result"][key] for key in expected_evidence
    } == expected_evidence


def test_inspector_registry_is_exactly_allowlisted_and_has_no_player_action(
    tmp_path: Path,
) -> None:
    owner = make_owner(tmp_path)
    server = InspectorToolServer(owner)
    mcp = build_inspector_fastmcp(server)

    served = {tool.name for tool in asyncio.run(mcp.list_tools())}
    recorded = {tool["name"] for tool in inspector_registry_snapshot()["tools"]}
    gameplay_specs = tool_specs(enable_vision=True)
    gameplay_only = {
        spec.name for spec in gameplay_specs if spec.name not in INSPECTOR_SENSOR_TOOLS
    }
    mutating_gameplay = {spec.name for spec in gameplay_specs if spec.mutates_game}

    assert served == recorded == EXPECTED_INSPECTOR_TOOL_NAMES
    assert not (gameplay_only & served)
    assert not (mutating_gameplay & served)
    rejected = server.handle_tool_call("build_road", {"from_tile": 1, "to_tile": 2})
    assert rejected["ok"] is False
    assert "unknown tool" in rejected["error"]


def test_public_registry_records_only_the_registry_identity_contract(
    tmp_path: Path,
) -> None:
    owner = make_owner(tmp_path)
    InspectorToolServer(owner)

    public_path = owner.run_dir / "inspector_public_registry.json"
    public = json.loads(public_path.read_text())

    assert public["experiment"] == "116-interactive-save-inspection"
    assert public["owner"] == "referee"
    assert public["transport"] == "mcp-stdio"
    assert set(public["tool_names"]) == EXPECTED_INSPECTOR_TOOL_NAMES
    assert set(public) == {"experiment", "owner", "transport", "tool_names"}


def test_public_call_stream_omits_inputs_results_commands_paths_and_unknown_calls(
    tmp_path: Path,
) -> None:
    private_path = tmp_path / "private" / "provider-token.txt"
    dispatcher = FakeDispatcher(
        {
            "ok": True,
            "tile": {"index": 123, "name": "private-result"},
            "diagnostic": str(private_path),
            "signature": "signed-private-frame",
        }
    )
    owner = make_owner(
        tmp_path,
        admin=FakeAdmin([730000, 730002]),
        dispatcher=dispatcher,
    )
    server = InspectorToolServer(owner)

    server.handle_tool_call("get_tile", {"tile": 123})
    server.handle_tool_call(ADVANCE_TOOL_NAME, {})
    server.handle_tool_call("build_road", {"from_tile": 1, "to_tile": 2})

    public_path = owner.run_dir / "public_tool_calls.jsonl"
    public_records = [json.loads(line) for line in public_path.read_text().splitlines()]
    assert len(public_records) == 2
    assert public_records[0]["sequence"] == 1
    assert public_records[0]["id"] == "tool-0001-get_tile"
    assert public_records[0]["name"] == "get_tile"
    assert set(public_records[0]) == {
        "sequence",
        "id",
        "name",
        "transport_ok",
        "ok",
        "date_facts",
    }
    assert public_records[1] == {
        "sequence": 2,
        "id": "tool-0002-advance_days",
        "name": "advance_days",
        "transport_ok": False,
        "ok": False,
    }
    public_text = public_path.read_text()
    for private_material in (
        "arguments",
        "result",
        "command",
        str(private_path),
        "private-result",
        "signed-private-frame",
        "from_tile",
    ):
        assert private_material not in public_text
    raw_text = (owner.run_dir / "tool_calls.jsonl").read_text()
    assert str(private_path) in raw_text
    assert "signed-private-frame" in raw_text
    assert "from_tile" in raw_text


def test_inspector_served_wrapper_schemas_match_the_recorded_registry_contract(
    tmp_path: Path,
) -> None:
    owner = make_owner(tmp_path)
    served = {
        tool.name: tool.inputSchema
        for tool in asyncio.run(
            build_inspector_fastmcp(InspectorToolServer(owner)).list_tools()
        )
    }
    recorded = {
        tool["name"]: tool["inputSchema"]
        for tool in inspector_registry_snapshot()["tools"]
    }

    constraint_keys = frozenset(
        {
            "const",
            "enum",
            "exclusiveMaximum",
            "exclusiveMinimum",
            "maximum",
            "maxItems",
            "maxLength",
            "minimum",
            "minItems",
            "minLength",
            "multipleOf",
            "pattern",
        }
    )

    def schema_facts(value: object) -> list[tuple[str, str]]:
        facts: list[tuple[str, str]] = []
        if isinstance(value, dict):
            for key, child in value.items():
                if key in constraint_keys:
                    facts.append((key, json.dumps(child, sort_keys=True)))
                elif key == "type":
                    types = child if isinstance(child, list) else [child]
                    facts.extend(
                        ("type", json.dumps(item, sort_keys=True))
                        for item in types
                        if item != "null"
                    )
                elif key == "properties" and isinstance(child, dict):
                    for property_schema in child.values():
                        facts.extend(schema_facts(property_schema))
                else:
                    facts.extend(schema_facts(child))
        elif isinstance(value, list):
            for child in value:
                facts.extend(schema_facts(child))
        return sorted(facts)

    assert set(served) == set(recorded)
    for name, live_schema in served.items():
        recorded_schema = recorded[name]
        assert set(live_schema.get("properties", {})) == set(
            recorded_schema["properties"]
        )
        assert set(live_schema.get("required", [])) == set(recorded_schema["required"])
        for argument, expected in recorded_schema["properties"].items():
            actual = live_schema["properties"][argument]
            assert schema_facts(actual) == schema_facts(expected), (name, argument)


def test_whole_map_view_keeps_archival_png_but_inlines_only_capped_reviewer_derivative(
    tmp_path: Path,
) -> None:
    admin = FakeAdmin([730100, 730100])
    owner = make_owner(tmp_path, admin=admin)

    original_rcon = admin.rcon

    def render_rcon(command: str, timeout: float = 30.0) -> list[str]:
        result = original_rcon(command, timeout)
        if command.startswith("screenshot giant "):
            from PIL import Image

            basename = command.rsplit(" ", 1)[1]
            path = owner.screenshot_dir / f"{basename}.png"
            Image.new("RGB", (4096, 4), (0, 128, 0)).save(path)
        return result

    admin.rcon = render_rcon
    server = InspectorToolServer(owner)
    mcp = build_inspector_fastmcp(server)

    result = asyncio.run(mcp.call_tool(VIEW_WHOLE_MAP_TOOL_NAME, {}))

    public = result.structuredContent
    assert public["archival"]["width"] == 4096
    assert public["archival"]["height"] == 4
    assert public["reviewer"]["width"] == EXPECTED_REVIEW_MAX_EDGE_PX
    assert public["reviewer"]["height"] == 2
    assert public["reviewer"]["scale_factor"] == 0.5
    assert "path" not in public["archival"]
    assert "path" not in public["reviewer"]
    image_blocks = [block for block in result.content if block.type == "image"]
    assert len(image_blocks) == 1
    inline_png = base64.b64decode(image_blocks[0].data)
    assert png_dimensions_from_bytes(inline_png) == (EXPECTED_REVIEW_MAX_EDGE_PX, 2)
    archival_paths = list((owner.run_dir / "renders").glob("*-archival.png"))
    reviewer_paths = list((owner.run_dir / "renders").glob("*-review.png"))
    assert len(archival_paths) == len(reviewer_paths) == 1
    assert png_dimensions_from_bytes(archival_paths[0].read_bytes()) == (4096, 4)
    assert png_dimensions_from_bytes(reviewer_paths[0].read_bytes()) == (
        EXPECTED_REVIEW_MAX_EDGE_PX,
        2,
    )
    raw_record = json.loads((owner.run_dir / "tool_calls.jsonl").read_text())
    assert Path(raw_record["result"]["archival"]["path"]) == archival_paths[0]
    assert Path(raw_record["result"]["reviewer"]["path"]) == reviewer_paths[0]


def test_missing_pillow_keeps_archival_evidence_without_giant_inline_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    admin = FakeAdmin([730100])
    owner = make_owner(tmp_path, admin=admin)
    original_rcon = admin.rcon

    def render_rcon(command: str, timeout: float = 30.0) -> list[str]:
        result = original_rcon(command, timeout)
        if command.startswith("screenshot giant "):
            basename = command.rsplit(" ", 1)[1]
            (owner.screenshot_dir / f"{basename}.png").write_bytes(
                png_header(8192, 4096)
            )
        return result

    admin.rcon = render_rcon
    monkeypatch.setattr(
        inspector_module,
        "require_pillow_for_rendering",
        Mock(side_effect=RenderDependencyError("vision extra unavailable")),
    )
    mcp = build_inspector_fastmcp(InspectorToolServer(owner))

    result = asyncio.run(mcp.call_tool(VIEW_WHOLE_MAP_TOOL_NAME, {}))

    assert result.structuredContent["ok"] is False
    assert result.structuredContent["status"] == "reviewer_image_unavailable"
    assert result.structuredContent["archival"]["width"] == 8192
    assert result.structuredContent["archival"]["height"] == 4096
    assert result.structuredContent["reviewer"]["available"] is False
    assert (
        result.structuredContent["reviewer"]["max_edge_px"]
        == EXPECTED_REVIEW_MAX_EDGE_PX
    )
    assert not any(block.type == "image" for block in result.content)
    archival_paths = list((owner.run_dir / "renders").glob("*-archival.png"))
    assert len(archival_paths) == 1
    assert png_dimensions_from_bytes(archival_paths[0].read_bytes()) == (8192, 4096)
    assert not list((owner.run_dir / "renders").glob("*-review.png"))


def test_render_failure_is_structured_and_keeps_no_false_artifact(
    tmp_path: Path, monkeypatch
) -> None:
    owner = make_owner(tmp_path)
    server = InspectorToolServer(owner)
    monkeypatch.setattr(
        inspector_module,
        "capture_giant_screenshot",
        Mock(side_effect=RuntimeError("capture did not settle")),
    )

    result = server.handle_tool_call(VIEW_WHOLE_MAP_TOOL_NAME, {})

    assert result["ok"] is False
    assert "did not settle" in result["error"]
    assert not (owner.run_dir / "renders").exists()


def test_telemetry_checkpoints_isolated_process_and_preserves_interactive_date(
    tmp_path: Path,
) -> None:
    calls: list[tuple] = []

    def run(module, save_path, *, timeout, arguments):
        calls.append((module, Path(save_path), timeout, tuple(arguments)))
        out_index = arguments.index("--out") + 1
        Path(arguments[out_index]).write_text(
            json.dumps({"instrument_module": "replay_telemetry", "routes": []})
        )
        return 0, "REPLAY_TELEMETRY_OUT=report\n", False

    admin = FakeAdmin([720000, 720000])
    owner = make_owner(tmp_path, admin=admin, telemetry_runner=run)

    result = owner.measure_replay_telemetry(
        window_days=14, station_sample_ticks=7, queue_radius=3
    )

    module, checkpoint, timeout, arguments = calls[0]
    report_argument = Path(arguments[arguments.index("--out") + 1])
    expected_report = tmp_path / "run" / "telemetry" / "telemetry-0001.json"
    expected_checkpoint = tmp_path / "run" / "checkpoints" / "telemetry-0001.sav"
    assert module == "openttd_mcp.verify.replay_telemetry"
    assert checkpoint.read_bytes() == b"checkpoint"
    assert checkpoint == expected_checkpoint
    assert report_argument == expected_report
    assert timeout > 0
    assert arguments == (
        "--window-days",
        "14",
        "--station-sample-ticks",
        "7",
        "--queue-radius",
        "3",
        "--out",
        str(expected_report),
    )
    assert result["ok"] is True
    assert result["checkpoint_date"] == result["interactive_date_after"] == 720000
    assert Path(result["report_path"]) == report_argument
    assert report_argument.is_file()


def test_all_inspector_extras_strip_host_paths_from_mcp_but_keep_raw_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    absolute_bridge_log = tmp_path / "runtime" / "bridge.log"
    admin = FakeAdmin([720000, 720000])
    owner = make_owner(
        tmp_path,
        admin=admin,
        dispatcher=FakeDispatcher(
            error=TimeoutError(f"bridge stalled while reading {absolute_bridge_log}")
        ),
    )

    def telemetry_runner(_module, _save_path, *, timeout, arguments):
        assert timeout > 0
        report_path = Path(arguments[arguments.index("--out") + 1])
        report_path.write_text(
            json.dumps(
                {
                    "source_save": str(owner.source_save),
                    "nested": [{"artifact": str(report_path)}],
                }
            )
        )
        return 0, f"REPLAY_TELEMETRY_OUT={report_path}\n", False

    owner.telemetry_runner = telemetry_runner
    server = InspectorToolServer(owner)
    mcp = build_inspector_fastmcp(server)

    advance_public = structured_tool_result(
        mcp, ADVANCE_TOOL_NAME, {"duration_days": 1}
    )
    monkeypatch.setattr(
        inspector_module,
        "capture_giant_screenshot",
        Mock(
            side_effect=RuntimeError(
                f"capture failed beside {owner.screenshot_dir / 'giant.png'}"
            )
        ),
    )
    render_public = structured_tool_result(mcp, VIEW_WHOLE_MAP_TOOL_NAME, {})
    telemetry_public = structured_tool_result(
        mcp, TELEMETRY_TOOL_NAME, {"window_days": 1}
    )

    for public in (advance_public, render_public, telemetry_public):
        assert str(tmp_path) not in json.dumps(public, sort_keys=True)
    assert "report_path" not in telemetry_public
    assert "checkpoint_path" not in telemetry_public
    assert telemetry_public["report"]["source_save"] == "<host-path>"
    assert telemetry_public["report"]["nested"][0]["artifact"].startswith("<host-path>")

    raw_log = (owner.run_dir / "tool_calls.jsonl").read_text()
    assert str(absolute_bridge_log) in raw_log
    assert str(owner.screenshot_dir / "giant.png") in raw_log
    assert '"report_path"' in raw_log
    assert '"checkpoint_path"' in raw_log
    assert str(owner.source_save) in raw_log


def test_telemetry_timeout_is_a_logged_public_failure(tmp_path: Path) -> None:
    def timeout_runner(*_args, **_kwargs):
        return 124, "timed out", True

    owner = make_owner(
        tmp_path,
        admin=FakeAdmin([720000, 720000]),
        telemetry_runner=timeout_runner,
    )
    server = InspectorToolServer(owner)

    result = server.handle_tool_call(TELEMETRY_TOOL_NAME, {"window_days": 1})

    assert result["ok"] is False
    assert "bounded timeout" in result["error"]
    records = [
        json.loads(line)
        for line in (owner.run_dir / "tool_calls.jsonl").read_text().splitlines()
    ]
    assert records[-1]["transport_ok"] is False


def test_cleanup_is_always_persisted_and_idempotent(
    tmp_path: Path, monkeypatch
) -> None:
    owner = make_owner(tmp_path)
    shutdown = Mock(return_value={"graceful": True, "exit_code": 0})
    monkeypatch.setattr(inspector_module, "quit_game", shutdown)

    cleanup_path = owner.run_dir / "inspector_cleanup.json"
    first = owner.close()
    persisted = json.loads(cleanup_path.read_text())
    cleanup_path.unlink()
    second = owner.close()

    assert first == second
    assert first["bounded"] is True
    assert first["errors"] == []
    assert first["shutdown"]["graceful"] is True
    assert shutdown.call_count == 1
    assert persisted == first
    assert not cleanup_path.exists()


def test_public_cleanup_records_outcome_without_private_error_diagnostics(
    tmp_path: Path, monkeypatch
) -> None:
    owner = make_owner(tmp_path)
    private_path = tmp_path / "secrets" / "provider-command.txt"

    def failed_shutdown(*_args, **_kwargs):
        raise RuntimeError(f"credential=signed-private at {private_path}")

    monkeypatch.setattr(inspector_module, "quit_game", failed_shutdown)

    private = owner.close()
    public_path = owner.run_dir / "inspector_public_cleanup.json"
    public = json.loads(public_path.read_text())

    assert public == {
        "ok": False,
        "bounded": True,
        "shutdown": None,
        "output_capture_stopped": True,
        "error_count": 1,
        "startup_failed": False,
        "cleanup_error_occurred": False,
    }
    assert str(private_path) in json.dumps(private)
    assert "signed-private" in json.dumps(private)
    assert str(private_path) not in public_path.read_text()
    assert "signed-private" not in public_path.read_text()


def test_cleanup_record_write_failure_returns_and_caches_primary_shutdown_state(
    tmp_path: Path, monkeypatch
) -> None:
    owner = make_owner(tmp_path)
    shutdown = Mock(return_value={"graceful": True, "exit_code": 0})
    failed_write = Mock(side_effect=OSError("evidence disk unavailable"))
    monkeypatch.setattr(inspector_module, "quit_game", shutdown)
    monkeypatch.setattr(inspector_module, "write_json", failed_write)

    first = owner.close()
    second = owner.close()

    assert first is second
    assert first["shutdown"] == {"graceful": True, "exit_code": 0}
    assert first["ok"] is False
    assert "evidence disk unavailable" in first["cleanup_record_error"]
    assert "evidence disk unavailable" in first["public_cleanup_record_error"]
    assert shutdown.call_count == 1
    assert failed_write.call_count == 2
