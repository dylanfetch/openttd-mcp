"""Regression tests for the factual replay-telemetry instrument."""

from __future__ import annotations

import json
import stat
import struct
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

import pytest

import openttd_mcp.render_capture as render_capture_module
import openttd_mcp.verify.replay_telemetry as telemetry_module
from openttd_mcp.verify.replay_telemetry import (
    build_report,
    classify_utilization,
    derive_route_key,
    derive_trips,
    parse_telemetry_line,
    summarize_station_series,
)
from tests.support import FakeProc, stub_verifier_run


def _meta_line(*, target_date: int = 105) -> str:
    return f"REPLAY_TELEMETRY|meta|5|100|1950|1|1|{target_date}|1|18|4|1250|1|1\n"


def _station_line(*, station_id: int = 1, name: str = "Central") -> str:
    return f"REPLAY_TELEMETRY|station|{station_id}|2|100,101|false|true|100|{name}\n"


def _vehicle_line(*, capacity: str = "0:100", sequence: str = "1,2") -> str:
    order_count = len(sequence.split(",")) if sequence else 0
    return (
        f"REPLAY_TELEMETRY|vehicle|7|{order_count}|{sequence}|{capacity}|0|0|90|Bus 7\n"
    )


def _vutil_line(*, samples: int = 10, final_state: int = 0) -> str:
    return f"REPLAY_TELEMETRY|vutil|7|{samples}|4|1|2|2|0|0|1|5|1|110|{final_state}\n"


def _done_line(*, end_date: int = 105, backstop_hit: bool = False) -> str:
    return (
        "REPLAY_TELEMETRY|done|100|105|"
        f"{end_date}|{end_date - 100}|10|0|0|0|"
        f"{'true' if backstop_hit else 'false'}\n"
    )


def _log_text(*extra: str, end_date: int = 105, backstop_hit: bool = False) -> str:
    return "".join(
        (
            _station_line(),
            _vehicle_line(),
            _meta_line(),
            *extra,
            _vutil_line(),
            _done_line(end_date=end_date, backstop_hit=backstop_hit),
        )
    )


def _parsed_records(*lines: str) -> dict[str, list[dict[str, object]]]:
    records = {record_type: [] for record_type in telemetry_module.RECORD_FIELDS}
    for line in lines:
        parsed = parse_telemetry_line(line)
        assert parsed is not None
        record_type, record = parsed
        records[record_type].append(record)
    return records


def _vstate(
    tick: int,
    *,
    state: int,
    previous: int,
    station_id: int = -1,
    order_index: int = 0,
    load: int | None = 0,
) -> dict[str, object]:
    return {
        "tick": tick,
        "date": 100 + tick,
        "vehicle_id": 7,
        "prev_state": previous,
        "state": state,
        "station_id": station_id,
        "tile": 100,
        "speed": 0,
        "order_index": order_index,
        "load_by_cargo": {} if load is None else {0: load},
    }


def _report_with_vstates(
    rows: list[dict[str, object]], *, capacity: str = "0:100"
) -> dict[str, object]:
    records = _parsed_records(
        _station_line(),
        _vehicle_line(capacity=capacity),
        _meta_line(),
        _vutil_line(),
        _done_line(),
    )
    records["vstate"] = rows
    return build_report(records, save_path=Path("/tmp/final.sav"), driver="null")


def test_record_field_count_mismatch_raises() -> None:
    with pytest.raises(RuntimeError, match="meta.*payload"):
        parse_telemetry_line("REPLAY_TELEMETRY|meta|5|100")


def test_parsed_station_name_containing_pipe_raises() -> None:
    with pytest.raises(RuntimeError, match="station.*payload"):
        parse_telemetry_line(_station_line(name="North|Road|Stop"))


def test_station_id_sentinel_65535_becomes_no_station() -> None:
    parsed = parse_telemetry_line(
        "REPLAY_TELEMETRY|vstate|10|100|7|0|3|65535|90|0|0|0:12"
    )

    assert parsed is not None
    assert parsed[1]["station_id"] == -1


def test_stop_tile_65535_is_preserved_while_order_station_sentinel_maps_to_minus_one() -> (
    None
):
    station = parse_telemetry_line(
        "REPLAY_TELEMETRY|station|1|1|65535|false|true|65535|Edge Stop"
    )
    vehicle = parse_telemetry_line(
        "REPLAY_TELEMETRY|vehicle|7|1|65535|0:100|0|0|65535|Edge Bus"
    )

    assert station is not None
    assert vehicle is not None
    assert station[1]["stop_tiles"] == [65535]
    assert vehicle[1]["order_station_sequence"] == [-1]


def test_trip_debounce_counts_one_arrival_for_a_single_multi_sample_stop() -> None:
    rows = [
        _vstate(10, state=3, previous=0, station_id=1),
        _vstate(12, state=0, previous=3),
        _vstate(14, state=3, previous=0, station_id=1),
    ]

    trips = derive_trips(rows, debounce_ticks=5)[7]

    assert len(trips["arrivals"]) == 1


def test_trip_counts_reentry_after_confirmed_departure() -> None:
    rows = [
        _vstate(10, state=3, previous=0, station_id=1),
        _vstate(12, state=0, previous=3),
        _vstate(17, state=3, previous=0, station_id=2),
    ]

    trips = derive_trips(rows, debounce_ticks=5)[7]

    assert [arrival["station_id"] for arrival in trips["arrivals"]] == [1, 2]
    assert trips["legs"] == [(1, 2)]


def test_same_station_debounced_rearrival_does_not_create_self_leg() -> None:
    rows = [
        _vstate(10, state=3, previous=0, station_id=1),
        _vstate(12, state=0, previous=3),
        _vstate(17, state=3, previous=0, station_id=1),
    ]

    trips = derive_trips(rows, debounce_ticks=5)[7]

    assert len(trips["arrivals"]) == 2
    assert trips["legs"] == []


def test_utilization_classifies_in_depot_before_moving() -> None:
    utilization = classify_utilization(
        {
            "samples": 5,
            "n_running": 1,
            "n_stopped": 0,
            "n_in_depot": 2,
            "n_at_station": 1,
            "n_broken": 0,
            "n_crashed": 0,
            "n_invalid": 1,
            "n_speed_positive": 3,
            "n_speed_positive_in_depot": 2,
        }
    )

    assert utilization["in_depot"] == 4
    assert utilization["moving"] == 1


def test_utilization_moving_inside_depot_counts_as_in_depot_not_stopped() -> None:
    utilization = classify_utilization(
        {
            "samples": 10,
            "n_running": 4,
            "n_stopped": 2,
            "n_in_depot": 2,
            "n_at_station": 1,
            "n_broken": 1,
            "n_crashed": 0,
            "n_invalid": 0,
            "n_speed_positive": 4,
            "n_speed_positive_in_depot": 1,
        }
    )

    assert utilization["in_depot"] == 3
    assert utilization["moving"] == 3
    assert utilization["stopped"] == 4


def test_two_stop_route_key_groups_reversed_order_vehicles_together() -> None:
    assert derive_route_key([4, -1, 9]) == derive_route_key([9, 4, -1])


def test_multi_stop_route_key_preserves_direction() -> None:
    forward = derive_route_key([4, 9, 12, 4])
    cyclic_rotation = derive_route_key([9, 12, 4])
    reverse = derive_route_key([4, 12, 9])

    assert forward == cyclic_rotation
    assert forward != reverse


def test_load_factor_reported_at_arrival_and_departure_separately() -> None:
    report = _report_with_vstates(
        [
            _vstate(10, state=3, previous=0, station_id=1, load=20),
            _vstate(15, state=0, previous=3, load=80),
        ]
    )

    route = report["routes"][0]
    assert route["load_factor_at_arrival"] == {
        "n": 1,
        "mean": 0.2,
        "min": 0.2,
        "max": 0.2,
    }
    assert route["load_factor_at_departure"] == {
        "n": 1,
        "mean": 0.8,
        "min": 0.8,
        "max": 0.8,
    }


def test_load_factor_omitted_when_capacity_zero() -> None:
    report = _report_with_vstates(
        [_vstate(10, state=3, previous=0, station_id=1, load=20)], capacity=""
    )

    assert report["routes"][0]["load_factor_at_arrival"] == {
        "n": 0,
        "mean": None,
        "min": None,
        "max": None,
    }


def test_invalid_state_exit_omits_departure_and_zero_load_factor() -> None:
    rows = [
        _vstate(10, state=3, previous=0, station_id=1, load=20),
        _vstate(15, state=255, previous=3, load=None),
    ]

    trips = derive_trips(rows, debounce_ticks=5)[7]
    report = _report_with_vstates(rows)

    assert trips["departures"] == []
    assert report["routes"][0]["load_factor_at_departure"] == {
        "n": 0,
        "mean": None,
        "min": None,
        "max": None,
    }


def test_total_arrivals_includes_single_station_vehicle_arrivals() -> None:
    records = _parsed_records(
        _station_line(),
        _vehicle_line(sequence="1"),
        _meta_line(),
        _vutil_line(),
        _done_line(),
    )
    records["vstate"] = [_vstate(10, state=3, previous=0, station_id=1)]

    report = build_report(records, save_path=Path("/tmp/final.sav"), driver="null")

    assert report["totals"]["total_arrivals"] == 1


def test_lost_event_records_date_and_vehicle_and_series_continues() -> None:
    event = "REPLAY_TELEMETRY|event|2|102|lost|7|-1|95|0|true"
    records = _parsed_records(
        _station_line(),
        _vehicle_line(),
        _meta_line(),
        event,
        _vutil_line(samples=10),
        _done_line(),
    )

    report = build_report(records, save_path=Path("/tmp/final.sav"), driver="null")

    assert report["events"][0]["date"] == 102
    assert report["events"][0]["vehicle_id"] == 7
    assert report["vehicles"][0]["utilization"]["samples"] == 10


def test_station_series_summary_stats_from_snapshots() -> None:
    snapshots = [
        {
            "tick": 74,
            "date": 101,
            "station_id": 1,
            "vehicles_near": 4,
            "vehicles_near_stopped": 3,
            "waiting_total": 30,
            "waiting_by_cargo": {0: 30},
        },
        {
            "tick": 0,
            "date": 100,
            "station_id": 1,
            "vehicles_near": 2,
            "vehicles_near_stopped": 1,
            "waiting_total": 10,
            "waiting_by_cargo": {0: 10},
        },
    ]

    series, summary = summarize_station_series(snapshots)

    assert [sample["tick"] for sample in series] == [0, 74]
    assert summary == {
        "sample_count": 2,
        "mean_vehicles_near": 3.0,
        "max_vehicles_near": 4,
        "mean_vehicles_near_stopped": 2.0,
        "max_vehicles_near_stopped": 3,
        "waiting_first": 10,
        "waiting_last": 30,
        "waiting_min": 10,
        "waiting_max": 30,
    }


def test_station_with_no_snapshots_yields_empty_series_not_error() -> None:
    report = _report_with_vstates([])

    station = report["stations"][0]
    assert station["series"] == []
    assert station["summary"]["sample_count"] == 0
    assert station["summary"]["waiting_first"] is None


def test_window_incomplete_when_end_date_precedes_target() -> None:
    records = _parsed_records(
        _station_line(),
        _vehicle_line(),
        _meta_line(),
        _vutil_line(),
        _done_line(end_date=104, backstop_hit=True),
    )

    report = build_report(records, save_path=Path("/tmp/final.sav"), driver="null")

    assert report["replay_window"]["window_complete"] is False


def test_backstop_hit_at_target_date_keeps_window_incomplete() -> None:
    records = _parsed_records(
        _station_line(),
        _vehicle_line(),
        _meta_line(),
        _vutil_line(),
        _done_line(end_date=105, backstop_hit=True),
    )

    report = build_report(records, save_path=Path("/tmp/final.sav"), driver="null")

    assert report["replay_window"]["window_complete"] is False


def test_main_writes_replay_telemetry_json_with_instrument_module(
    monkeypatch, tmp_path, capsys
) -> None:
    stub_verifier_run(monkeypatch, telemetry_module, tmp_path, log_text=_log_text())

    exit_code = telemetry_module.main([str(tmp_path / "save.sav")])
    capsys.readouterr()

    reports = list(tmp_path.glob("*.json"))
    assert exit_code == 0
    assert [path.name for path in reports] == ["replay_telemetry.json"]
    report = json.loads(reports[0].read_text())
    assert report["instrument_module"] == telemetry_module.INSTRUMENT_MODULE
    assert "verifier_module" not in report


def test_main_prints_replay_telemetry_marker_and_not_verify_result(
    monkeypatch, tmp_path, capsys
) -> None:
    stub_verifier_run(monkeypatch, telemetry_module, tmp_path, log_text=_log_text())

    telemetry_module.main([str(tmp_path / "save.sav")])
    output = capsys.readouterr().out

    assert "REPLAY_TELEMETRY=" in output
    assert "VERIFY_RESULT=" not in output


def test_paused_checkpoint_defect_configures_a_post_load_unpause_command(
    monkeypatch, tmp_path, capsys
) -> None:
    state = stub_verifier_run(
        monkeypatch, telemetry_module, tmp_path, log_text=_log_text()
    )
    create_calls: list[dict[str, object]] = []

    def create_run_root(**kwargs):
        create_calls.append(kwargs)
        return state.root, state.log_path, state.result_path, {}

    monkeypatch.setattr(telemetry_module, "create_run_root", create_run_root)

    exit_code = telemetry_module.main([str(tmp_path / "paused-checkpoint.sav")])
    capsys.readouterr()

    assert exit_code == 0
    script = create_calls[0]["game_start_script"]
    assert isinstance(script, str)
    commands = [line.strip() for line in script.splitlines() if line.strip()]
    assert commands.count("unpause") == 1


def test_main_out_copies_report_to_destination(monkeypatch, tmp_path, capsys) -> None:
    stub_verifier_run(monkeypatch, telemetry_module, tmp_path, log_text=_log_text())
    destination = tmp_path / "durable.json"

    exit_code = telemetry_module.main(
        [str(tmp_path / "save.sav"), "--out", str(destination)]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert destination.exists()
    assert json.loads(destination.read_text()) == json.loads(
        (tmp_path / "replay_telemetry.json").read_text()
    )
    assert f"REPLAY_TELEMETRY_OUT={destination}" in output


def test_main_returns_nonzero_when_window_incomplete(
    monkeypatch, tmp_path, capsys
) -> None:
    stub_verifier_run(
        monkeypatch,
        telemetry_module,
        tmp_path,
        log_text=_log_text(end_date=104, backstop_hit=True),
    )

    exit_code = telemetry_module.main([str(tmp_path / "save.sav")])
    capsys.readouterr()

    assert exit_code == 2


def test_main_without_render_preserves_the_null_verifier_wiring(
    monkeypatch, tmp_path, capsys
) -> None:
    state = stub_verifier_run(
        monkeypatch, telemetry_module, tmp_path, log_text=_log_text()
    )
    calls: dict[str, object] = {}

    def create_run_root(**kwargs):
        calls["create"] = kwargs
        return state.root, state.log_path, state.result_path, {}

    def launch(*_args, **kwargs):
        calls["launch"] = kwargs
        return state.proc

    def read(_proc, _log_path, _marker, timeout, *, on_line=None):
        calls["timeout"] = timeout
        calls["on_line"] = on_line
        return _done_line()

    monkeypatch.setattr(telemetry_module, "create_run_root", create_run_root)
    monkeypatch.setattr(telemetry_module, "launch_openttd", launch)
    monkeypatch.setattr(telemetry_module, "read_until", read)

    exit_code = telemetry_module.main([str(tmp_path / "save.sav")])
    capsys.readouterr()

    assert exit_code == 0
    assert calls["create"]["admin_config"] is None
    assert calls["launch"] == {
        "port": 3996,
        "driver": "null",
        "blitter": None,
    }
    assert calls["timeout"] == telemetry_module.TELEMETRY_TIMEOUT_SEC
    assert calls["on_line"] is None


@pytest.mark.parametrize(
    ("window_days", "expected"),
    [(1, (1,)), (2, (1, 2)), (3, (1, 2, 3)), (5, (2, 3, 4, 5))],
)
def test_render_checkpoint_offsets_are_ceiling_quarters_without_duplicates(
    window_days, expected
) -> None:
    assert telemetry_module._render_checkpoint_offsets(window_days) == expected


def test_render_mode_requires_pillow_before_creating_evidence_or_launching(
    monkeypatch, tmp_path
) -> None:
    state = _stub_render_run(monkeypatch, tmp_path)
    render_dir = tmp_path / "evidence"

    def missing_pillow():
        raise ModuleNotFoundError("No module named 'PIL'", name="PIL")

    monkeypatch.setattr(render_capture_module, "_load_pillow_image", missing_pillow)

    with pytest.raises(render_capture_module.RenderDependencyError) as error:
        telemetry_module.main(
            [str(tmp_path / "save.sav"), "--render-dir", str(render_dir)]
        )

    assert "uv sync --extra vision" in str(error.value)
    assert not render_dir.exists()
    assert state["create_calls"] == []
    assert state["launch_calls"] == []


def _png_header(width: int = 320, height: int = 200) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I4sII", 13, b"IHDR", width, height)


class RecordingAdmin:
    instances: ClassVar[list[RecordingAdmin]] = []
    connect_error: Exception | None = None
    close_observer: ClassVar[Callable[[], None] | None] = None

    def __init__(self, config) -> None:
        self.config = config
        self.connected: list[float] = []
        self.closed = False
        self.instances.append(self)

    def connect(self, timeout: float = 30.0) -> dict:
        self.connected.append(timeout)
        if self.connect_error is not None:
            raise self.connect_error
        return {}

    def close(self) -> None:
        observer = type(self).close_observer
        if observer is not None:
            observer()
        self.closed = True


DEFAULT_RENDER_MARKERS = [
    "REPLAY_RENDER|overview|0|10|100\n",
    "REPLAY_RENDER|checkpoint|2|20|102\n",
    "REPLAY_RENDER|checkpoint|3|30|103\n",
    "REPLAY_RENDER|checkpoint|4|40|104\n",
    "REPLAY_RENDER|checkpoint|5|50|105\n",
]


def _stub_render_run(
    monkeypatch,
    tmp_path: Path,
    *,
    capture_error=None,
    markers: list[str] | None = None,
    log_text: str | None = None,
    image_size: tuple[int, int] | None = None,
):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    log_path = runtime / "replay_telemetry.log"
    log_path.write_text(_log_text() if log_text is None else log_text)
    proc = FakeProc()
    launch_calls: list[tuple[tuple, dict]] = []
    create_calls: list[dict] = []
    teardown_calls: list[tuple[object, str]] = []
    read_timeouts: list[float] = []
    capture_calls: list[tuple[str, float]] = []
    RecordingAdmin.instances = []
    RecordingAdmin.connect_error = None
    RecordingAdmin.close_observer = None

    def create_run_root(**kwargs):
        create_calls.append(kwargs)
        return runtime, log_path, runtime / "ignored.json", {}

    def launch(*args, **kwargs):
        launch_calls.append((args, kwargs))
        return proc

    active_markers = DEFAULT_RENDER_MARKERS if markers is None else markers

    def read(_proc, _log_path, _marker, timeout, *, on_line=None):
        read_timeouts.append(timeout)
        if on_line is not None:
            for line in active_markers:
                on_line(line)
        return _done_line()

    def capture(_client, screenshot_dir, basename, *, timeout=30.0):
        capture_calls.append((basename, timeout))
        error = capture_error(basename) if callable(capture_error) else capture_error
        if error is not None:
            raise error
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        path = screenshot_dir / f"{basename}.png"
        if image_size is None:
            path.write_bytes(_png_header())
        else:
            from PIL import Image

            Image.new("RGB", image_size, (0, 128, 0)).save(path)
        return path

    monkeypatch.setattr(telemetry_module, "create_run_root", create_run_root)
    monkeypatch.setattr(telemetry_module, "launch_openttd", launch)
    monkeypatch.setattr(telemetry_module, "read_until", read)
    monkeypatch.setattr(telemetry_module, "AdminClient", RecordingAdmin)
    monkeypatch.setattr(telemetry_module, "capture_giant_screenshot", capture)
    monkeypatch.setattr(
        telemetry_module, "_allocate_render_ports", lambda: (4100, 4101)
    )
    monkeypatch.setattr(
        telemetry_module,
        "teardown",
        lambda process, *, driver: teardown_calls.append((process, driver)),
    )
    return {
        "runtime": runtime,
        "proc": proc,
        "launch_calls": launch_calls,
        "create_calls": create_calls,
        "teardown_calls": teardown_calls,
        "read_timeouts": read_timeouts,
        "capture_calls": capture_calls,
    }


def test_render_mode_uses_one_dedicated_run_and_writes_valid_relative_manifest(
    monkeypatch, tmp_path, capsys
) -> None:
    state = _stub_render_run(monkeypatch, tmp_path)
    close_capture_counts: list[int] = []
    RecordingAdmin.close_observer = lambda: close_capture_counts.append(
        len(state["capture_calls"])
    )
    monkeypatch.setenv("OPENTTD_VERIFY_DRIVER", "invalid-but-render-is-explicit")
    render_dir = tmp_path / "evidence"
    save_path = tmp_path / "save.sav"

    exit_code = telemetry_module.main(
        [str(save_path), "--window-days", "5", "--render-dir", str(render_dir)]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert len(state["launch_calls"]) == 1
    _, launch_kwargs = state["launch_calls"][0]
    assert launch_kwargs == {
        "port": 4100,
        "driver": "dedicated",
        "blitter": "8bpp-optimized",
    }
    admin_config = state["create_calls"][0]["admin_config"]
    assert admin_config.port == 4101
    assert admin_config.port != launch_kwargs["port"]
    assert RecordingAdmin.instances[0].closed is True
    assert close_capture_counts == [5]
    assert state["teardown_calls"] == [(state["proc"], "dedicated")]
    assert state["read_timeouts"] == [telemetry_module._render_timeout_seconds(5, 5)]

    manifest_path = render_dir / "manifest.json"
    assert f"REPLAY_RENDERED_EVIDENCE={manifest_path}" in output
    manifest = json.loads(manifest_path.read_text())
    assert manifest["schema_version"] == 1
    assert manifest["status"] == "complete"
    assert manifest["source_save"] == str(save_path)
    assert manifest["telemetry_report"] == "replay_telemetry.json"
    assert (render_dir / "replay_telemetry.json").is_file()
    assert manifest["driver"] == "dedicated"
    assert manifest["blitter"] == "8bpp-optimized"
    assert manifest["screenshot_kind"] == "giant"
    assert manifest["replay_window"] == {
        "requested_days": 5,
        "scheduled_offsets_days": [2, 3, 4, 5],
        "start_date": 100,
        "target_date": 105,
        "end_date": 105,
        "window_complete": True,
    }
    assert [frame["scheduled_offset_days"] for frame in manifest["frames"]] == [
        0,
        2,
        3,
        4,
        5,
    ]
    for frame in manifest["frames"]:
        archival = frame["archival"]
        assert Path(archival["path"]).is_absolute() is False
        assert (render_dir / archival["path"]).is_file()
        assert (archival["width"], archival["height"]) == (320, 200)
        assert archival["bytes"] == len(_png_header())
        assert archival["scale"] == 1.0
        assert frame["review"] is None
        assert frame["status"] == "complete"
    assert manifest["capture_timeout_seconds"] == 30.0
    assert manifest[
        "replay_timeout_seconds"
    ] == telemetry_module._render_timeout_seconds(5, 5)
    assert manifest["elapsed_wall_seconds"] >= 0


def test_render_manifest_adds_a_bounded_review_png_for_wide_archival_frames(
    monkeypatch, tmp_path
) -> None:
    _stub_render_run(monkeypatch, tmp_path, image_size=(8065, 2))
    render_dir = tmp_path / "evidence"

    exit_code = telemetry_module.main(
        [
            str(tmp_path / "save.sav"),
            "--window-days",
            "5",
            "--render-dir",
            str(render_dir),
        ]
    )
    manifest = json.loads((render_dir / "manifest.json").read_text())

    assert exit_code == 0
    for frame in manifest["frames"]:
        archival = frame["archival"]
        review = frame["review"]
        assert archival["width"] == 8065
        assert review is not None
        assert Path(review["path"]).is_absolute() is False
        assert (render_dir / review["path"]).is_file()
        assert max(review["width"], review["height"]) <= 2048
        assert review["bytes"] > 0
        assert 0 < review["scale"] < 1


def test_render_explicit_out_preserves_report_copy_behavior(
    monkeypatch, tmp_path, capsys
) -> None:
    _stub_render_run(monkeypatch, tmp_path)
    render_dir = tmp_path / "evidence"
    destination = tmp_path / "durable.json"

    exit_code = telemetry_module.main(
        [
            str(tmp_path / "save.sav"),
            "--window-days",
            "5",
            "--render-dir",
            str(render_dir),
            "--out",
            str(destination),
        ]
    )
    output = capsys.readouterr().out

    manifest = json.loads((render_dir / "manifest.json").read_text())
    assert exit_code == 0
    assert destination.is_file()
    assert manifest["telemetry_report"] == "../durable.json"
    assert not (render_dir / "replay_telemetry.json").exists()
    assert f"REPLAY_TELEMETRY_OUT={destination}" in output


def test_render_connection_failure_retains_telemetry_then_fails_loudly(
    monkeypatch, tmp_path, capsys
) -> None:
    state = _stub_render_run(monkeypatch, tmp_path)
    RecordingAdmin.connect_error = ConnectionError("admin refused login")
    render_dir = tmp_path / "evidence"

    with pytest.raises(RuntimeError, match="rendered evidence failed after telemetry"):
        telemetry_module.main(
            [str(tmp_path / "save.sav"), "--render-dir", str(render_dir)]
        )
    output = capsys.readouterr().out

    manifest = json.loads((render_dir / "manifest.json").read_text())
    assert manifest["status"] == "failed"
    assert manifest["frames"] == []
    assert manifest["errors"] == [
        {"stage": "connection", "error": "admin refused login"}
    ]
    assert (render_dir / "replay_telemetry.json").is_file()
    assert "REPLAY_TELEMETRY=" in output
    assert "REPLAY_RENDERED_EVIDENCE=" in output
    assert RecordingAdmin.instances[0].closed is True
    assert state["teardown_calls"] == [(state["proc"], "dedicated")]


def test_render_capture_failure_retains_telemetry_and_later_frames(
    monkeypatch, tmp_path, capsys
) -> None:
    state = _stub_render_run(
        monkeypatch,
        tmp_path,
        capture_error=lambda basename: (
            ValueError("invalid PNG signature") if basename == "overview" else None
        ),
    )
    render_dir = tmp_path / "evidence"

    with pytest.raises(RuntimeError, match="rendered evidence failed after telemetry"):
        telemetry_module.main(
            [
                str(tmp_path / "save.sav"),
                "--window-days",
                "5",
                "--render-dir",
                str(render_dir),
            ]
        )
    output = capsys.readouterr().out

    manifest = json.loads((render_dir / "manifest.json").read_text())
    assert manifest["status"] == "failed"
    assert manifest["frames"][0]["status"] == "failed"
    assert manifest["frames"][0]["error"] == "invalid PNG signature"
    assert manifest["errors"] == [
        {
            "stage": "capture",
            "frame": "overview",
            "error": "invalid PNG signature",
        }
    ]
    assert len(manifest["frames"]) == 5
    assert all(frame["status"] == "complete" for frame in manifest["frames"][1:])
    assert (render_dir / "replay_telemetry.json").is_file()
    assert "REPLAY_TELEMETRY=" in output
    assert len(state["capture_calls"]) == 5
    assert RecordingAdmin.instances[0].closed is True
    assert state["teardown_calls"] == [(state["proc"], "dedicated")]


def test_malformed_render_marker_is_recorded_after_stage_one_telemetry(
    monkeypatch, tmp_path, capsys
) -> None:
    markers = [
        "REPLAY_RENDER|overview|0|bad|100\n",
        *DEFAULT_RENDER_MARKERS[1:],
    ]
    _stub_render_run(monkeypatch, tmp_path, markers=markers)
    render_dir = tmp_path / "evidence"

    with pytest.raises(RuntimeError, match="rendered evidence failed after telemetry"):
        telemetry_module.main(
            [
                str(tmp_path / "save.sav"),
                "--window-days",
                "5",
                "--render-dir",
                str(render_dir),
            ]
        )
    output = capsys.readouterr().out
    manifest = json.loads((render_dir / "manifest.json").read_text())

    assert (render_dir / "replay_telemetry.json").is_file()
    assert "REPLAY_TELEMETRY=" in output
    assert any(error["stage"] == "marker" for error in manifest["errors"])


def test_missing_render_trigger_is_reported_after_stage_one_telemetry(
    monkeypatch, tmp_path, capsys
) -> None:
    _stub_render_run(monkeypatch, tmp_path, markers=DEFAULT_RENDER_MARKERS[:-1])
    render_dir = tmp_path / "evidence"

    with pytest.raises(RuntimeError, match="missing render trigger checkpoint@5"):
        telemetry_module.main(
            [
                str(tmp_path / "save.sav"),
                "--window-days",
                "5",
                "--render-dir",
                str(render_dir),
            ]
        )
    output = capsys.readouterr().out
    manifest = json.loads((render_dir / "manifest.json").read_text())

    assert (render_dir / "replay_telemetry.json").is_file()
    assert "REPLAY_TELEMETRY=" in output
    assert any(error["stage"] == "frames" for error in manifest["errors"])


def test_extra_render_marker_never_exceeds_the_five_frame_queue(
    monkeypatch, tmp_path
) -> None:
    state = _stub_render_run(
        monkeypatch,
        tmp_path,
        markers=[*DEFAULT_RENDER_MARKERS, DEFAULT_RENDER_MARKERS[-1]],
    )
    render_dir = tmp_path / "evidence"

    with pytest.raises(RuntimeError, match="duplicate render trigger"):
        telemetry_module.main(
            [
                str(tmp_path / "save.sav"),
                "--window-days",
                "5",
                "--render-dir",
                str(render_dir),
            ]
        )

    assert len(state["capture_calls"]) == 5


def test_existing_render_target_is_rejected_before_launch(
    monkeypatch, tmp_path
) -> None:
    state = _stub_render_run(monkeypatch, tmp_path)
    render_dir = tmp_path / "evidence"
    render_dir.mkdir()
    stale = render_dir / "overview.png"
    stale.write_bytes(b"stale")

    with pytest.raises(ValueError, match="already exists"):
        telemetry_module.main(
            [str(tmp_path / "save.sav"), "--render-dir", str(render_dir)]
        )

    assert stale.read_bytes() == b"stale"
    assert state["launch_calls"] == []


def test_manifest_replace_leaves_previous_complete_json_when_replace_fails(
    monkeypatch, tmp_path
) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text('{"status":"old"}')

    monkeypatch.setattr(
        telemetry_module.os,
        "replace",
        lambda _source, _destination: (_ for _ in ()).throw(OSError("disk full")),
    )
    with pytest.raises(OSError, match="disk full"):
        telemetry_module._write_render_manifest(manifest_path, {"status": "new"})

    assert json.loads(manifest_path.read_text()) == {"status": "old"}
    assert list(tmp_path.glob(".manifest.json.*.tmp")) == []


def test_atomic_manifest_replacement_leaves_a_publicly_readable_mode(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text('{"status":"old"}')
    manifest_path.chmod(0o644)

    telemetry_module._write_render_manifest(manifest_path, {"status": "new"})

    assert json.loads(manifest_path.read_text()) == {"status": "new"}
    assert stat.S_IMODE(manifest_path.stat().st_mode) == 0o644


def test_late_marker_cannot_change_a_finalized_render_manifest(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest = {
        "status": "pending",
        "replay_window": {},
        "elapsed_wall_seconds": None,
        "frames": [],
        "errors": [],
    }
    telemetry_module._write_render_manifest(manifest_path, manifest)
    renderer = telemetry_module._ReplayRenderer(
        client=object(),
        runtime_screenshot_dir=tmp_path / "runtime-screenshot",
        render_dir=tmp_path,
        manifest_path=manifest_path,
        manifest=manifest,
        checkpoint_offsets=(),
        capture_timeout_seconds=1,
        started_at=0,
    )

    renderer.finish()
    renderer.finalize(
        {
            "start_date": 100,
            "target_date": 101,
            "end_date": 101,
            "window_complete": True,
        }
    )
    finalized = manifest_path.read_bytes()

    renderer.observe_line("REPLAY_RENDER|overview|0|not-a-tick|100\n")

    assert manifest_path.read_bytes() == finalized


def test_render_timeout_budget_scales_with_days_and_frames() -> None:
    assert telemetry_module._render_timeout_seconds(6, 5) > (
        telemetry_module._render_timeout_seconds(5, 5)
    )
    assert telemetry_module._render_timeout_seconds(5, 5) > (
        telemetry_module._render_timeout_seconds(5, 4)
    )


def test_render_port_allocator_retries_an_admin_port_collision(monkeypatch) -> None:
    ports = iter([4100, 4100, 4101])
    monkeypatch.setattr(telemetry_module, "allocate_admin_port", lambda: next(ports))

    assert telemetry_module._allocate_render_ports() == (4100, 4101)
