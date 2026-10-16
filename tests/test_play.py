"""Public-contract tests for the foreground ``openttd-mcp play`` command."""

from __future__ import annotations

import io
import json
import os
import signal
import threading
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import openttd_mcp.lifecycle as lifecycle_module
import openttd_mcp.play as play_module
import openttd_mcp.server as server_module


class FakeProcess:
    def __init__(self, lines: list[str], *, exit_code: int | None = None) -> None:
        self.stdout = iter(lines)
        self.stdin = io.StringIO()
        self.exit_code = exit_code
        self.killed = False

    def poll(self) -> int | None:
        return self.exit_code

    def wait(self, timeout: float) -> int:
        del timeout
        self.exit_code = 0 if self.exit_code is None else self.exit_code
        return self.exit_code

    def kill(self) -> None:
        self.killed = True
        self.exit_code = -9


class SavingFakeProcess(FakeProcess):
    """Fake OpenTTD that honors a console ``save`` like the real binary: it
    writes the savegame to disk and then prints the success marker."""

    def __init__(self, lines: list[str]) -> None:
        super().__init__(lines)
        self.save_requested = threading.Event()
        self.saved_path: Path | None = None
        process = self

        class SaveStdin(io.StringIO):
            def write(self, text: str) -> int:
                written = super().write(text)
                for line in text.splitlines():
                    if line.startswith("save "):
                        process.saved_path = Path(line[len("save ") :] + ".sav")
                        process.save_requested.set()
                return written

        self.stdin = SaveStdin()

        def stream():
            yield from lines
            if process.save_requested.wait(timeout=5):
                assert process.saved_path is not None
                process.saved_path.write_bytes(b"SAVEGAME")
                yield "dbg: [sl] Map successfully saved\n"

        self.stdout = stream()


def observation_line(town_count: int) -> str:
    observation = {"towns": [{"town_id": index} for index in range(town_count)]}
    return f"dbg: [ai] MCP_OBSERVATION|{json.dumps(observation)}\n"


def install_runtime_fakes(
    monkeypatch: pytest.MonkeyPatch,
    proc: FakeProcess,
) -> list[dict[str, object]]:
    prepared: list[dict[str, object]] = []

    def prepare(
        run_id: str | Path,
        map_log2: int,
        admin_config=None,
        num_towns: int = 4,
        starting_year: int = 1950,
        emit_boot_snapshot: bool = True,
    ) -> tuple[Path, Path]:
        run_root = Path(run_id) / "openttd-root"
        bridge_root = run_root / "ai" / "McpBridge"
        bridge_root.mkdir(parents=True)
        (run_root / "scripts").mkdir()
        prepared.append(
            {
                "run_id": Path(run_id),
                "map_log2": map_log2,
                "num_towns": num_towns,
                "starting_year": starting_year,
                "emit_boot_snapshot": emit_boot_snapshot,
            }
        )
        return run_root, bridge_root

    monkeypatch.setattr(play_module, "prepare_runtime", prepare)
    monkeypatch.setattr(lifecycle_module.subprocess, "Popen", Mock(return_value=proc))
    monkeypatch.setattr(play_module, "allocate_admin_port", lambda: 43127)
    return prepared


def test_literal_first_token_dispatches_play_without_changing_attach_parser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    play_main = Mock(return_value=23)
    monkeypatch.setattr(play_module, "main", play_main)

    assert server_module.main(["play", "--seed", "9"]) == 23
    play_main.assert_called_once_with(["--seed", "9"])

    tool_log = tmp_path / "tools.jsonl"
    assert (
        server_module.main(
            [
                "--bridge-root",
                "play",
                "--tool-log",
                str(tool_log),
                "--dump-registry",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["transport"] == "mcp-stdio"
    with pytest.raises(SystemExit):
        server_module.parse_args(["play"])


def test_play_parser_exposes_approved_defaults_and_option_overrides() -> None:
    defaults = play_module.parse_args([])
    assert vars(defaults) == {
        "run_dir": None,
        "seed": 1337,
        "map_log2": 7,
        "num_towns": 16,
        "starting_year": 1950,
        "startup_timeout": 600.0,
        "result_timeout": 240.0,
    }

    selected = play_module.parse_args(
        [
            "--run-dir",
            "chosen",
            "--seed",
            "8",
            "--map-log2",
            "6",
            "--num-towns",
            "9",
            "--starting-year",
            "1960",
            "--startup-timeout",
            "12",
            "--result-timeout",
            "13",
        ]
    )
    assert vars(selected) == {
        "run_dir": Path("chosen"),
        "seed": 8,
        "map_log2": 6,
        "num_towns": 9,
        "starting_year": 1960,
        "startup_timeout": 12.0,
        "result_timeout": 13.0,
    }


def test_command_help_discovers_play_and_explains_its_operational_options(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as top_help:
        server_module.parse_args(["--help"])
    assert top_help.value.code == 0
    assert "openttd-mcp play --help" in capsys.readouterr().out

    with pytest.raises(SystemExit) as play_help:
        play_module.parse_args(["--help"])
    assert play_help.value.code == 0
    rendered = capsys.readouterr().out
    assert ".local/runs/<timestamp>-play" in rendered
    for option in (
        "--run-dir",
        "--seed",
        "--map-log2",
        "--num-towns",
        "--starting-year",
        "--startup-timeout",
        "--result-timeout",
    ):
        assert option in rendered


def test_successful_startup_persists_exact_play_map_and_launch_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / "caller-visible-run"
    proc = FakeProcess(["dbg: [ai] MCP_BRIDGE_READY\n", observation_line(16)])
    prepared = install_runtime_fakes(monkeypatch, proc)

    owner = play_module.open_play(run_dir=run_dir)

    assert prepared == [
        {
            "run_id": run_dir.resolve(),
            "map_log2": 7,
            "num_towns": 16,
            "starting_year": 1950,
            "emit_boot_snapshot": False,
        }
    ]
    startup = json.loads((run_dir / "play_startup.json").read_text())
    assert startup["observed_town_count"] == 16
    assert startup["game_port"] == 43127
    assert startup["startup_timeout"] == 600.0
    assert startup["result_timeout"] == 240.0
    command = json.loads((run_dir / "openttd_command.json").read_text())
    assert command[1:3] == ["-D", "127.0.0.1:43127"]
    assert json.loads((run_dir / "lifecycle_metadata.json").read_text()) == {
        "lifecycle_lane": "stdin"
    }
    assert (run_dir / "registry.json").is_file()
    assert (run_dir / "tool_calls.jsonl").read_bytes() == b""
    owner.close()
    cleanup = json.loads((run_dir / "play_cleanup.json").read_text())
    assert cleanup["bounded"] is True
    assert cleanup["shutdown"]["process_reaped"] is True
    # This fake never answers the console save, so the missing final save must
    # surface as a recorded cleanup failure instead of a silent quit.
    assert cleanup["final_save"] is None
    assert cleanup["ok"] is False
    assert any("final save" in error for error in cleanup["errors"])


def test_close_takes_the_final_save_before_quitting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / "run"
    proc = SavingFakeProcess(["dbg: [ai] MCP_BRIDGE_READY\n", observation_line(16)])
    install_runtime_fakes(monkeypatch, proc)

    owner = play_module.open_play(run_dir=run_dir)
    report = owner.close()

    save_path = run_dir / "final.sav"
    assert save_path.read_bytes() == b"SAVEGAME"
    assert report["final_save"] == str(save_path)
    assert report["ok"] is True
    # Console protocol order: the save must be requested before the quit, or
    # the shutdown destroys the game state the save was meant to preserve.
    commands = [line for line in proc.stdin.getvalue().splitlines() if line.strip()]
    assert commands[0] == f"save {run_dir / 'final'}"
    assert commands[-1] == "quit"
    cleanup = json.loads((run_dir / "play_cleanup.json").read_text())
    assert cleanup["final_save"] == str(save_path)


def test_bare_play_creates_a_caller_visible_default_run_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_runs = tmp_path / ".local" / "runs"
    monkeypatch.setattr(play_module, "LOCAL_RUNS_DIR", local_runs)
    monkeypatch.setattr(play_module, "now_id", lambda: "20260827-235959")
    proc = FakeProcess(["MCP_BRIDGE_READY\n", observation_line(16)])
    install_runtime_fakes(monkeypatch, proc)

    owner = play_module.open_play()

    assert owner.run_dir == local_runs / "20260827-235959-play"
    assert (owner.run_dir / "play_startup.json").is_file()
    owner.close()


def test_nonempty_play_directory_refuses_before_runtime_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    prior = run_dir / "accepted.json"
    prior.write_bytes(b"preserve me")
    allocate = Mock()
    prepare = Mock()
    monkeypatch.setattr(play_module, "allocate_admin_port", allocate)
    monkeypatch.setattr(play_module, "prepare_runtime", prepare)

    with pytest.raises(play_module.PlayStartupError) as raised:
        play_module.open_play(run_dir=run_dir)

    assert raised.value.startup_result["error_code"] == "run_directory_not_empty"
    assert prior.read_bytes() == b"preserve me"
    allocate.assert_not_called()
    prepare.assert_not_called()


def test_failing_startup_prints_selected_run_path_only_to_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_dir = tmp_path / "failed-startup"
    failure = RuntimeError("runtime preparation failed")
    monkeypatch.setattr(play_module, "allocate_admin_port", lambda: 43127)
    monkeypatch.setattr(play_module, "prepare_runtime", Mock(side_effect=failure))

    with pytest.raises(RuntimeError) as raised:
        play_module.open_play(run_dir=run_dir)

    assert raised.value is failure
    output = capsys.readouterr()
    assert output.out == ""
    assert str(run_dir.resolve()) in output.err
    assert json.loads((run_dir / "play_cleanup.json").read_text())["bounded"] is True


def test_town_mismatch_cleans_child_before_gameplay_server_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / "run"
    proc = FakeProcess(["MCP_BRIDGE_READY\n", observation_line(15)])
    install_runtime_fakes(monkeypatch, proc)
    server = Mock()
    monkeypatch.setattr(play_module, "GameToolServer", server)

    with pytest.raises(RuntimeError, match="generated 15 towns"):
        play_module.open_play(run_dir=run_dir)

    server.assert_not_called()
    assert proc.exit_code == 0
    cleanup = json.loads((run_dir / "play_cleanup.json").read_text())
    assert cleanup["startup_error_type"] == "RuntimeError"
    assert cleanup["output_capture_stopped"] is True


@pytest.mark.parametrize(
    "lines,exit_code,message",
    [
        ([], 17, "exited with code 17"),
        ([observation_line(16)], 19, "before MCP_BRIDGE_READY"),
    ],
)
def test_child_death_during_readiness_cleans_without_waiting_for_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lines: list[str],
    exit_code: int,
    message: str,
) -> None:
    run_dir = tmp_path / "run"
    proc = FakeProcess(lines, exit_code=exit_code)
    install_runtime_fakes(monkeypatch, proc)

    with pytest.raises(RuntimeError, match=message):
        play_module.open_play(run_dir=run_dir, startup_timeout=60)

    cleanup = json.loads((run_dir / "play_cleanup.json").read_text())
    assert cleanup["bounded"] is True
    assert cleanup["shutdown"]["exit_code"] == exit_code


def test_startup_waits_receive_only_the_remaining_total_deadline_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / "run"
    proc = FakeProcess(["MCP_BRIDGE_READY\n", observation_line(16)])
    install_runtime_fakes(monkeypatch, proc)
    wait_budgets: list[tuple[str, float]] = []

    def wait_ready(event, child, *, timeout: float) -> None:
        assert child is proc
        assert event.wait(timeout=1)
        wait_budgets.append(("bridge", timeout))

    def wait_observation(event, child, *, description: str, timeout: float) -> None:
        assert child is proc
        assert event.wait(timeout=1)
        assert description == "MCP_OBSERVATION"
        wait_budgets.append(("observation", timeout))

    monkeypatch.setattr(
        play_module.time, "monotonic", Mock(side_effect=[100, 101, 104])
    )
    monkeypatch.setattr(play_module, "wait_for_bridge_ready", wait_ready)
    monkeypatch.setattr(play_module, "wait_for_process_event", wait_observation)

    owner = play_module.open_play(run_dir=run_dir, startup_timeout=10)

    assert wait_budgets == [("bridge", 9), ("observation", 6)]
    owner.close()


def test_still_live_child_without_boot_observation_times_out_and_is_reaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / "run"
    proc = FakeProcess(["MCP_BRIDGE_READY\n"])
    install_runtime_fakes(monkeypatch, proc)

    with pytest.raises(TimeoutError, match="MCP_OBSERVATION"):
        play_module.open_play(run_dir=run_dir, startup_timeout=0.2)

    assert proc.exit_code == 0
    cleanup = json.loads((run_dir / "play_cleanup.json").read_text())
    assert cleanup["bounded"] is True
    assert cleanup["shutdown"]["process_reaped"] is True
    assert cleanup["output_capture_stopped"] is True
    assert cleanup["startup_error_type"] == "TimeoutError"


@pytest.mark.parametrize(
    "failure",
    [SystemExit("runtime missing"), KeyboardInterrupt("startup interrupted")],
)
def test_startup_baseexception_cleanup_reraises_identical_object(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
) -> None:
    run_dir = tmp_path / "run"
    monkeypatch.setattr(play_module, "allocate_admin_port", lambda: 43127)
    monkeypatch.setattr(play_module, "prepare_runtime", Mock(side_effect=failure))

    with pytest.raises(type(failure)) as raised:
        play_module.open_play(run_dir=run_dir)

    assert raised.value is failure
    cleanup = json.loads((run_dir / "play_cleanup.json").read_text())
    assert cleanup["startup_error_type"] == type(failure).__name__
    assert cleanup["shutdown"] is None


def test_startup_cleanup_failure_cannot_replace_original_baseexception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failure = SystemExit("runtime missing")
    monkeypatch.setattr(play_module, "allocate_admin_port", lambda: 43127)
    monkeypatch.setattr(play_module, "prepare_runtime", Mock(side_effect=failure))
    monkeypatch.setattr(
        play_module,
        "_record_failed_startup",
        Mock(side_effect=KeyboardInterrupt("cleanup interrupted")),
    )

    with pytest.raises(SystemExit) as raised:
        play_module.open_play(run_dir=tmp_path / "run")

    assert raised.value is failure


def test_sigterm_during_startup_cleans_child_and_restores_signal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / "run"
    proc = FakeProcess(["MCP_BRIDGE_READY\n", observation_line(16)])
    install_runtime_fakes(monkeypatch, proc)
    prior = signal.getsignal(signal.SIGTERM)

    def terminate_during_startup(*_args, **_kwargs) -> None:
        os.kill(os.getpid(), signal.SIGTERM)

    monkeypatch.setattr(play_module, "wait_for_bridge_ready", terminate_during_startup)

    with pytest.raises(SystemExit) as stopped:
        play_module.main(["--run-dir", str(run_dir)])

    assert stopped.value.code == 0
    assert proc.exit_code == 0
    assert json.loads((run_dir / "play_cleanup.json").read_text())["bounded"] is True
    assert signal.getsignal(signal.SIGTERM) is prior


@pytest.mark.parametrize("serve_outcome", ["failure", "sigterm"])
def test_serve_failure_and_sigterm_cleanup_and_restore_signal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    serve_outcome: str,
) -> None:
    prior = signal.getsignal(signal.SIGTERM)
    owner = SimpleNamespace(run_dir=tmp_path / "run", server=object(), close=Mock())
    monkeypatch.setattr(play_module, "open_play", Mock(return_value=owner))

    def run(_transport: str) -> None:
        if serve_outcome == "sigterm":
            os.kill(os.getpid(), signal.SIGTERM)
        raise RuntimeError("serve broke")

    monkeypatch.setattr(
        play_module,
        "build_fastmcp",
        Mock(return_value=SimpleNamespace(run=run)),
    )

    expected = SystemExit if serve_outcome == "sigterm" else RuntimeError
    with pytest.raises(expected):
        play_module.main(["--run-dir", str(tmp_path / "run")])

    owner.close.assert_called_once_with()
    assert signal.getsignal(signal.SIGTERM) is prior


def test_foreground_startup_and_cleanup_do_not_pollute_mcp_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = SimpleNamespace(run_dir=tmp_path / "run", server=object(), close=Mock())
    monkeypatch.setattr(play_module, "open_play", Mock(return_value=owner))
    monkeypatch.setattr(
        play_module,
        "build_fastmcp",
        Mock(return_value=SimpleNamespace(run=lambda _transport: None)),
    )
    stdout = io.StringIO()

    with redirect_stdout(stdout):
        assert play_module.main(["--run-dir", str(tmp_path / "run")]) == 0

    assert stdout.getvalue() == ""
    owner.close.assert_called_once_with()
