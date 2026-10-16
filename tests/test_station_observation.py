"""Public Python-surface behavior for station traffic observation."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from openttd_mcp.server import (
    MUTATING_TOOL_NAMES,
    PRIMITIVE_TOOLS,
    QUERY_TOOLS,
    GameToolServer,
    build_fastmcp,
    command_for_tool,
)
from tests.support import (
    fastmcp_tools_by_name,
    make_tool_server,
    read_jsonl,
    tools_by_name,
)


class TimeoutDispatcher:
    def __init__(self) -> None:
        self.commands: list[list[object]] = []

    def dispatch(self, command: list[object], timestamp: str) -> dict:
        self.commands.append(list(command))
        raise TimeoutError(f"timed out waiting for MCP_RESULT|{timestamp}|")


class SuccessfulDispatcher:
    def __init__(self, result: dict) -> None:
        self.result = result

    def dispatch(self, command: list[object], timestamp: str) -> dict:
        return dict(self.result)


class MutationCounter:
    def __init__(self) -> None:
        self.count = 0

    def note_mutation(self) -> None:
        self.count += 1


def test_station_observation_registry_and_served_wrapper_keep_one_schema(
    tmp_path,
) -> None:
    """The live wrapper must not drop the required id, default, or day bounds."""
    recorded = tools_by_name()["wait_and_observe_station"]
    served = fastmcp_tools_by_name(make_tool_server(tmp_path))[
        "wait_and_observe_station"
    ].inputSchema

    assert recorded["mutates_game"] is True
    assert recorded["inputSchema"]["required"] == ["station_id"]
    assert served["required"] == recorded["inputSchema"]["required"]
    assert set(served["properties"]) == set(recorded["inputSchema"]["properties"])
    for field, constraints in {
        "station_id": {"type": "integer", "minimum": 0},
        "advance_days": {
            "type": "integer",
            "default": 14,
            "minimum": 1,
            "maximum": 90,
        },
    }.items():
        for key, value in constraints.items():
            assert served["properties"][field][key] == value
            assert recorded["inputSchema"]["properties"][field][key] == value


def test_station_observation_command_serializes_default_and_bounds() -> None:
    """The bridge must receive the requested id and days without clamping."""
    assert command_for_tool("wait_and_observe_station", {"station_id": 7}) == [
        "wait_and_observe_station",
        7,
        14,
    ]
    for days in (1, 90):
        assert command_for_tool(
            "wait_and_observe_station", {"station_id": 7, "advance_days": days}
        ) == ["wait_and_observe_station", 7, days]
    for bad_arguments in (
        {},
        {"station_id": -1},
        {"station_id": True},
        {"station_id": 7, "advance_days": 0},
        {"station_id": 7, "advance_days": 91},
    ):
        with pytest.raises(ValueError):
            command_for_tool("wait_and_observe_station", bad_arguments)


def test_station_observation_fastmcp_wrapper_forwards_public_arguments(
    tmp_path,
) -> None:
    """A valid Model Context Protocol call must reach the station handler once."""
    server = make_tool_server(tmp_path)
    mcp = build_fastmcp(server)

    with patch.object(server, "handle_tool_call", return_value={"ok": True}) as handle:
        asyncio.run(
            mcp.call_tool(
                "wait_and_observe_station",
                {"station_id": 7, "advance_days": 9},
            )
        )

    handle.assert_called_once_with(
        "wait_and_observe_station", {"station_id": 7, "advance_days": 9}
    )


def test_station_observation_is_a_mutating_query_tool() -> None:
    """Time advancement must participate in mutation policy despite returning facts."""
    assert "wait_and_observe_station" in QUERY_TOOLS
    assert "wait_and_observe_station" in MUTATING_TOOL_NAMES
    assert "wait_and_observe_station" not in PRIMITIVE_TOOLS


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("wait_and_observe_vehicle", {"vehicle_id": 3, "advance_days": 1}),
        ("wait_and_observe_station", {"station_id": 7, "advance_days": 1}),
    ],
)
def test_successful_time_advancing_observations_have_complete_status(
    tmp_path, name, arguments
) -> None:
    """A confirmed successful observation must expose a uniform completion status."""
    server: GameToolServer = make_tool_server(
        tmp_path, SuccessfulDispatcher({"ok": True, "advanced_days": 1})
    )

    result = server.handle_tool_call(name, arguments)

    assert result["ok"] is True
    assert result["status"] == "complete"


@pytest.mark.parametrize(
    ("name", "arguments", "expected_command", "requested_days"),
    [
        (
            "wait_and_observe_vehicle",
            {"vehicle_id": 3, "advance_days": 8},
            ["wait_and_observe_vehicle", 3, 8],
            8,
        ),
        (
            "wait_and_observe_station",
            {"station_id": 7},
            ["wait_and_observe_station", 7, 14],
            14,
        ),
    ],
)
def test_observation_timeout_marks_uncertain_time_and_invalidates_cached_image(
    tmp_path, name, arguments, expected_command, requested_days
) -> None:
    """A timed-out observation may already have changed the rendered game state."""
    dispatcher = TimeoutDispatcher()
    server: GameToolServer = make_tool_server(tmp_path, dispatcher)
    cache = MutationCounter()
    server.screenshot_source = cache

    result = server.handle_tool_call(name, arguments)

    assert result["ok"] is False
    assert result["status"] == "timeout"
    assert result["error"] == "bridge_timeout"
    assert result["requested_advance_days"] == requested_days
    assert result["game_mutated"] is True
    assert result["time_may_have_advanced"] is True
    assert dispatcher.commands == [expected_command]
    assert cache.count == 1
    record = read_jsonl(tmp_path / "tool_calls.jsonl")[0]
    assert record["ok"] is False
    assert record["command"] == expected_command
    assert record["result"]["time_may_have_advanced"] is True
