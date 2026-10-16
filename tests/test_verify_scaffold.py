import subprocess
import threading
from pathlib import Path

import pytest

from openttd_mcp.admin import AdminLifecycleConfig
from openttd_mcp.verify import _scaffold
from tests.support import FakeProc, FakeStdin


@pytest.mark.parametrize(
    "driver, port, driver_args",
    [
        # The default: uncapped null video, and no -D / listen address at all
        # (proven by the exact-list comparison below).
        (None, None, ["-v", "null:ticks=500000"]),
        # The dedicated driver keeps the previous literal command, port included.
        ("dedicated", 3995, ["-D", "127.0.0.1:3995"]),
    ],
    ids=["null", "dedicated"],
)
def test_launch_command_is_the_driver_arguments_plus_the_shared_tail(
    monkeypatch, driver, port, driver_args
) -> None:
    if driver is None:
        monkeypatch.delenv("OPENTTD_VERIFY_DRIVER", raising=False)
    else:
        monkeypatch.setenv("OPENTTD_VERIFY_DRIVER", driver)
    root = Path("/tmp/verifier-root")
    save_path = Path("/tmp/save.sav")

    command = _scaffold.build_launch_command(
        root, save_path, **({} if port is None else {"port": port})
    )

    assert command == [
        str(_scaffold.OTTD_BIN),
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
        "-d",
        "script=4",
    ]


def test_create_run_root_name_includes_process_id(tmp_path, monkeypatch) -> None:
    data_path = tmp_path / "data"
    (data_path / "baseset").mkdir(parents=True)
    (data_path / "lang").mkdir()
    monkeypatch.setattr(_scaffold, "ROOT", tmp_path)
    monkeypatch.setattr(_scaffold, "OTTD_DATA", data_path)
    monkeypatch.setattr(_scaffold.time, "strftime", lambda _format: "20260724-014500")
    monkeypatch.setattr(_scaffold.os, "getpid", lambda: 4242)

    root, _, result_path, _ = _scaffold.create_run_root(
        prefix="bus-network",
        game_start_script="",
        info_nut="info",
        main_nut="main",
        log_filename="verify.log",
    )

    assert root.name == "bus-network-20260724-014500-4242"
    assert result_path.name == "verify_result.json"
    cfg = (root / "openttd.cfg").read_text()
    assert "server_name = bus-network-verifier" in cfg
    assert "client_name = bus-network-verifier" in cfg


def test_create_run_root_wires_optional_admin_listener_without_changing_plain_config(
    tmp_path, monkeypatch
) -> None:
    data_path = tmp_path / "data"
    (data_path / "baseset").mkdir(parents=True)
    (data_path / "lang").mkdir()
    monkeypatch.setattr(_scaffold, "ROOT", tmp_path)
    monkeypatch.setattr(_scaffold, "OTTD_DATA", data_path)
    monkeypatch.setattr(_scaffold.os, "getpid", lambda: 4242)

    plain, *_ = _scaffold.create_run_root(
        prefix="plain",
        game_start_script="",
        info_nut="info",
        main_nut="main",
        log_filename="verify.log",
    )
    configured, *_ = _scaffold.create_run_root(
        prefix="render",
        game_start_script="",
        info_nut="info",
        main_nut="main",
        log_filename="verify.log",
        admin_config=AdminLifecycleConfig(port=4123, password="local-secret"),
    )

    assert "server_admin_port" not in (plain / "openttd.cfg").read_text()
    rendered = (configured / "openttd.cfg").read_text()
    assert "server_admin_port = 4123" in rendered
    assert "admin_password = local-secret" in rendered
    assert "allow_insecure_admin_login = true" in rendered


def test_blitter_is_additive_to_dedicated_launch_and_absent_from_plain_launch() -> None:
    root = Path("/tmp/verifier-root")
    save_path = Path("/tmp/save.sav")

    rendered = _scaffold.build_launch_command(
        root,
        save_path,
        port=4122,
        driver="dedicated",
        blitter="8bpp-optimized",
    )
    plain = _scaffold.build_launch_command(root, save_path, driver="null")

    assert rendered[:3] == [str(_scaffold.OTTD_BIN), "-D", "127.0.0.1:4122"]
    assert rendered[-4:] == ["-b", "8bpp-optimized", "-d", "script=4"]
    assert "-b" not in plain


def test_null_emit_kills_without_console_quit_or_wait(tmp_path) -> None:
    proc = FakeProc()
    result_path = tmp_path / "result.json"

    _scaffold.emit_result(
        proc,
        save_path=Path("/tmp/save.sav"),
        log_path=Path("/tmp/verify.log"),
        result_path=result_path,
        result={"network_ok": True},
        verifier_module="openttd_mcp.verify.bus_network",
        driver="null",
    )

    assert result_path.read_text() == (
        '{\n  "driver": "null",\n  "network_ok": true,\n'
        '  "verifier_module": "openttd_mcp.verify.bus_network"\n}'
    )
    assert proc.kill_count == 1
    assert proc.stdin.writes == []
    assert proc.stdin.flush_count == 0
    assert proc.wait_timeouts == []


def test_dedicated_emit_keeps_console_quit_and_wait(tmp_path) -> None:
    proc = FakeProc()

    _scaffold.emit_result(
        proc,
        save_path=Path("/tmp/save.sav"),
        log_path=Path("/tmp/verify.log"),
        result_path=tmp_path / "result.json",
        result={"network_ok": True},
        verifier_module="openttd_mcp.verify.bus_network",
        driver="dedicated",
    )

    assert proc.kill_count == 0
    assert proc.stdin.writes == ["quit\n"]
    assert proc.stdin.flush_count == 1
    assert proc.wait_timeouts == [10]


def test_null_finally_teardown_kills_without_console_quit_or_wait() -> None:
    proc = FakeProc()

    _scaffold.teardown(proc, driver="null")

    assert proc.kill_count == 1
    assert proc.stdin.writes == []
    assert proc.stdin.flush_count == 0
    assert proc.wait_timeouts == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("", "null"), ("null", "null"), (" NULL ", "null"), ("Dedicated", "dedicated")],
)
def test_driver_selection_normalises_known_values(monkeypatch, raw, expected) -> None:
    monkeypatch.setenv("OPENTTD_VERIFY_DRIVER", raw)
    assert _scaffold.resolve_verify_driver() == expected


def test_unrecognised_driver_is_rejected_rather_than_defaulted(monkeypatch) -> None:
    # A typo must not silently fall back to a different driver: the driver is
    # recorded in every verdict, so a wrong one would mislabel the evidence.
    monkeypatch.setenv("OPENTTD_VERIFY_DRIVER", "sdl")
    with pytest.raises(ValueError, match="OPENTTD_VERIFY_DRIVER='sdl'"):
        _scaffold.resolve_verify_driver()


def test_launch_openttd_runs_the_built_command_in_the_run_root(
    monkeypatch, tmp_path
) -> None:
    captured: dict[str, object] = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return "proc"

    monkeypatch.setattr(_scaffold.subprocess, "Popen", fake_popen)

    proc = _scaffold.launch_openttd(
        tmp_path, Path("/tmp/save.sav"), {"HOME": str(tmp_path)}, driver="dedicated"
    )

    assert proc == "proc"
    assert captured["cmd"] == _scaffold.build_launch_command(
        tmp_path,
        Path("/tmp/save.sav"),
        3996,
        _scaffold.DEFAULT_TICK_BUDGET,
        "dedicated",
    )
    assert captured["kwargs"]["cwd"] == str(tmp_path)
    assert captured["kwargs"]["env"] == {"HOME": str(tmp_path)}


def test_read_until_returns_the_marker_line_and_logs_everything_before_it(
    tmp_path,
) -> None:
    log_path = tmp_path / "verify.log"
    proc = FakeProc(
        stdout=iter(["dbg: boot\n", "dbg: MARKER|payload\n", "dbg: after\n"])
    )

    line = _scaffold.read_until(proc, log_path, "MARKER|", timeout=5)

    assert line == "dbg: MARKER|payload\n"
    # The log is flushed before the waiter is released, so the marker line is
    # already on disk for the verifiers that re-parse the log.
    assert log_path.read_text() == "dbg: boot\ndbg: MARKER|payload\n"


def test_read_until_calls_line_callback_in_order_through_marker(tmp_path) -> None:
    seen: list[str] = []
    proc = FakeProc(stdout=iter(["boot\n", "MARKER|payload\n", "after\n"]))

    _scaffold.read_until(
        proc,
        tmp_path / "verify.log",
        "MARKER|",
        timeout=5,
        on_line=seen.append,
    )

    assert seen == ["boot\n", "MARKER|payload\n"]


def test_read_until_propagates_callback_error_without_waiting_for_timeout(
    tmp_path,
) -> None:
    proc = FakeProc(stdout=iter(["REPLAY_RENDER|overview\n", "MARKER\n"]))

    def reject_render(_line: str) -> None:
        raise ValueError("capture rejected invalid PNG")

    with pytest.raises(ValueError, match="capture rejected invalid PNG"):
        _scaffold.read_until(
            proc,
            tmp_path / "verify.log",
            "MARKER",
            timeout=5,
            on_line=reject_render,
        )

    assert (tmp_path / "verify.log").read_text() == "REPLAY_RENDER|overview\n"


def test_read_until_reports_an_early_exit_rather_than_hanging(tmp_path) -> None:
    proc = FakeProc(stdout=iter(["dbg: boot\n"]), poll_result=1)

    with pytest.raises(RuntimeError, match="OpenTTD exited before marker MARKER"):
        _scaffold.read_until(proc, tmp_path / "verify.log", "MARKER", timeout=5)


def test_read_until_reports_unavailable_stdout(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="stdout unavailable"):
        _scaffold.read_until(
            FakeProc(stdout=None), tmp_path / "verify.log", "MARKER", timeout=5
        )


def test_read_until_deadline_holds_while_openttd_is_alive_but_silent(tmp_path) -> None:
    # The blocking readline lives on a daemon thread precisely so a wedged but
    # living game cannot hang a CLI run forever.
    gate = threading.Event()

    def silent_stream():
        gate.wait(5)
        yield "dbg: MARKER late\n"

    proc = FakeProc(stdout=silent_stream())
    try:
        with pytest.raises(TimeoutError, match="Timed out waiting for MARKER"):
            _scaffold.read_until(proc, tmp_path / "verify.log", "MARKER", timeout=0)
    finally:
        gate.set()


def test_broken_console_pipe_never_fails_a_written_verdict(tmp_path) -> None:
    proc = FakeProc(stdin=FakeStdin(error=BrokenPipeError("gone")))
    result_path = tmp_path / "result.json"

    _scaffold.emit_result(
        proc,
        save_path=Path("/tmp/save.sav"),
        log_path=Path("/tmp/verify.log"),
        result_path=result_path,
        result={"network_ok": True},
        verifier_module="openttd_mcp.verify.bus_network",
        driver="dedicated",
    )

    assert result_path.is_file()
    assert proc.wait_timeouts == [10]


def test_slow_shutdown_is_tolerated_after_the_verdict_is_on_disk(
    tmp_path, capsys
) -> None:
    proc = FakeProc(wait_error=subprocess.TimeoutExpired(cmd="openttd", timeout=10))
    result_path = tmp_path / "result.json"

    _scaffold.emit_result(
        proc,
        save_path=Path("/tmp/save.sav"),
        log_path=Path("/tmp/verify.log"),
        result_path=result_path,
        result={"network_ok": True},
        verifier_module="openttd_mcp.verify.freight_network",
        extra_printed_paths=(("ROUTE_TRACE", tmp_path / "route_trace.jsonl"),),
        tolerate_slow_shutdown=True,
        driver="dedicated",
    )

    assert result_path.is_file()
    assert proc.kill_count == 1
    assert proc.wait_timeouts == [10, 5]
    assert f"ROUTE_TRACE={tmp_path / 'route_trace.jsonl'}" in capsys.readouterr().out


def test_dedicated_teardown_asks_for_quit_before_giving_up() -> None:
    proc = FakeProc()

    _scaffold.teardown(proc, driver="dedicated")

    assert proc.stdin.writes == ["quit\n"]
    assert proc.wait_timeouts == [5]
    assert proc.kill_count == 0


def test_dedicated_teardown_kills_a_process_that_ignores_quit() -> None:
    proc = FakeProc(wait_error=subprocess.TimeoutExpired(cmd="openttd", timeout=5))

    _scaffold.teardown(proc, driver="dedicated")

    assert proc.kill_count == 1


def test_teardown_leaves_an_already_exited_process_alone() -> None:
    proc = FakeProc(poll_result=0)

    _scaffold.teardown(proc, driver="dedicated")

    assert proc.kill_count == 0
    assert proc.stdin.writes == []
