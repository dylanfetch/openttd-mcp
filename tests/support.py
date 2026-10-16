"""Shared helpers for the test suite.

The suite is unittest-style, so these are plain importable functions rather
than pytest fixtures. Import as ``from tests.support import ...``.
"""

from __future__ import annotations

import ast
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import pytest

from openttd_mcp.server import (
    FunctionBridgeDispatcher,
    GameToolServer,
    registry_snapshot,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MCP_BRIDGE_DIR = REPO_ROOT / "src" / "openttd_mcp" / "squirrel" / "mcp_bridge"
MAIN_NUT = MCP_BRIDGE_DIR / "main.nut"
REGION_FIXTURE_PATH = (
    REPO_ROOT / "tests" / "fixtures" / "get_region_seed9973_radius2.json"
)
CLAUDE_EVENTS_FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "claude_events"


class TempDirTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp_path = Path(tmp.name)


def feedback_args(**overrides) -> dict:
    args = {
        "kind": "missing_observation",
        "description": "no engine catalog",
        "why_it_matters": "cannot pick a buildable engine",
        "current_blocker": "buy_vehicle fails on guessed ids",
    }
    args.update(overrides)
    return args


def count_calls(source: str, function_name: str) -> int:
    """Count calls to ``function_name`` in ``source``, whatever their formatting.

    The one-funnel guards exist to catch a second call site being added. Counting
    a source substring cannot do that: the literal has to include the assignment
    target and the receiver to be unambiguous, so a duplicate written with other
    variable names or split across lines leaves the count at one and the guard
    passes. Parsing counts calls rather than spellings.
    """
    counted = 0
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        )
        if name == function_name:
            counted += 1
    return counted


def touch(path: Path, text: str = "x\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def write_json(path: Path, data: object | None = None) -> None:
    touch(
        path, json.dumps({} if data is None else data, indent=2, sort_keys=True) + "\n"
    )


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def null_dispatch(command: list, timestamp: str) -> dict:
    return {"ok": True}


def make_tool_server(tmp_path: Path | str, dispatcher=None, **kwargs) -> GameToolServer:
    """GameToolServer logging to tmp_path/tool_calls.jsonl.

    ``dispatcher`` may be a dispatcher object, a bare ``(command, timestamp)``
    callable (wrapped in FunctionBridgeDispatcher), or None for an ok-stub.
    """
    if dispatcher is None:
        dispatcher = FunctionBridgeDispatcher(null_dispatch)
    elif callable(dispatcher) and not hasattr(dispatcher, "dispatch"):
        dispatcher = FunctionBridgeDispatcher(dispatcher)
    return GameToolServer(
        dispatcher, tool_log=Path(tmp_path) / "tool_calls.jsonl", **kwargs
    )


def tools_by_name(**registry_kwargs) -> dict[str, dict]:
    return {
        tool["name"]: tool for tool in registry_snapshot(**registry_kwargs)["tools"]
    }


def fastmcp_tools_by_name(server: GameToolServer) -> dict[str, object]:
    """FastMCP tool map for ``server``; skips when the mcp extra is absent."""
    pytest.importorskip("mcp", reason="mcp extra (FastMCP) not installed")
    from openttd_mcp.server import build_fastmcp

    return {tool.name: tool for tool in asyncio.run(build_fastmcp(server).list_tools())}


def bridge_function_source(source: str, function_name: str) -> str:
    """The body of one McpBridge function from main.nut source."""
    body = source.split(f"function McpBridge::{function_name}(", 1)[1]
    return body.split("function McpBridge::", 1)[0]


# ---------------------------------------------------------------------------
# Network-verifier route fixtures (033-test-hygiene)
#
# Shared base route dict for the bus_network / freight_network verifier tests.
# Holds only the keys common to both verifiers' ROUTE_FIELDS schemas; each test
# file wraps this in a local ``make_route`` that adds its verifier-specific
# keys (bus: town/component fields; freight: cargo/truck fields).
# ---------------------------------------------------------------------------


def network_route(**overrides) -> dict:
    """A fully-connected, operating two-station route: the keys shared by the
    bus_network and freight_network verifier schemas."""
    route = {
        "route_index": 0,
        "vehicle_id": 1,
        "order_count": 2,
        "stopped_in_depot": False,
        "initial_tile": 2404,
        "final_tile": 2402,
        "vehicle_moved": True,
        "station_a": 0,
        "station_b": 1,
        "station_a_tile": 2402,
        "station_b_tile": 2532,
        "depot_tile": 2341,
        "depot_front_tile": 2340,
        "station_a_connected": True,
        "station_b_connected": True,
        "depot_connected": True,
        "station_a_to_station_b": True,
        "station_b_to_station_a": True,
        "depot_to_station_a": True,
        "station_a_to_depot": True,
        "station_a_visited_by_vehicle": True,
        "station_b_visited_by_vehicle": True,
        "vehicle_lost_event": False,
        "vehicle_crashed_event": False,
        "order_progressed": True,
    }
    route.update(overrides)
    return route


# ---------------------------------------------------------------------------
# Verifier process/scaffold stand-ins (045-test-suite)
# ---------------------------------------------------------------------------


class FakeStdin:
    """Records what a verifier writes to OpenTTD's console stdin."""

    def __init__(self, error: Exception | None = None) -> None:
        self.writes: list[str] = []
        self.flush_count = 0
        self.error = error

    def write(self, value: str) -> None:
        if self.error is not None:
            raise self.error
        self.writes.append(value)

    def flush(self) -> None:
        self.flush_count += 1


class FakeProc:
    """Minimal ``subprocess.Popen`` stand-in for the verifier scaffold.

    ``poll_result`` None means "still running" (the interesting case for
    teardown/emit); ``wait_error`` raises on the first ``wait`` call so the
    slow-shutdown branches can be exercised without a real process.
    """

    def __init__(
        self,
        *,
        poll_result: int | None = None,
        stdin: FakeStdin | None = None,
        stdout=None,
        wait_error: Exception | None = None,
    ) -> None:
        self.stdin = FakeStdin() if stdin is None else stdin
        self.stdout = stdout
        self.poll_result = poll_result
        self.wait_error = wait_error
        self.kill_count = 0
        self.wait_timeouts: list[int] = []

    def poll(self) -> int | None:
        return self.poll_result

    def kill(self) -> None:
        self.kill_count += 1

    def wait(self, timeout: int) -> None:
        self.wait_timeouts.append(timeout)
        if self.wait_error is not None and len(self.wait_timeouts) == 1:
            raise self.wait_error


def stub_verifier_run(
    monkeypatch, module, tmp_path: Path, *, marker_line: str = "", log_text: str = ""
) -> SimpleNamespace:
    """Patch a verifier module's scaffold seams so ``main()`` runs with no OpenTTD.

    Everything the module owns -- payload parsing, derived checks, the verdict --
    still runs for real; only the launch/read seams are replaced.
    """
    root = Path(tmp_path)
    log_path = root / "verify.log"
    log_path.write_text(log_text)
    result_path = root / "verify_result.json"
    proc = FakeProc()

    monkeypatch.setattr(
        module, "create_run_root", lambda **kwargs: (root, log_path, result_path, {})
    )
    monkeypatch.setattr(module, "resolve_verify_driver", lambda: "null")
    monkeypatch.setattr(module, "launch_openttd", lambda *args, **kwargs: proc)
    monkeypatch.setattr(module, "read_until", lambda *args, **kwargs: marker_line)
    return SimpleNamespace(
        proc=proc, root=root, log_path=log_path, result_path=result_path
    )
