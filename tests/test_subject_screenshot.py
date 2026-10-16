from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from openttd_mcp.server import (
    SUBJECT_SCREENSHOT_TOOLS,
    build_fastmcp,
    registry_snapshot,
)
from openttd_mcp.vision import DEFAULT_WINDOW
from tests.support import fastmcp_tools_by_name, make_tool_server, read_jsonl

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def subject_result(command: list) -> dict:
    name = command[0]
    if name == "get_tile":
        tile = command[1]
        return {
            "ok": True,
            "action": name,
            "tile_info": {"tile": tile, "x": tile % 64, "y": tile // 64},
        }
    if name == "get_vehicle":
        return {
            "ok": True,
            "action": name,
            "vehicle": {"vehicle_id": command[1], "tile": 731, "state": 2},
        }
    if name == "get_my_station":
        return {
            "ok": True,
            "action": name,
            "station_id": command[1],
            "tile": 988,
            "cargo": [],
        }
    raise AssertionError(command)


class RecordingSource:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls: list[dict] = []

    def crop_for_tile(
        self, tile: int, window: int, *, refresh: bool, call_id: str
    ) -> dict:
        path = self.root / f"{call_id}.png"
        path.write_bytes(PNG_1X1)
        self.calls.append(
            {
                "tile": tile,
                "window": window,
                "refresh": refresh,
                "call_id": call_id,
            }
        )
        return {
            "ok": True,
            "action": "view_map",
            "tile": tile,
            "window": window,
            "path": str(path),
            "capture_id": len(self.calls),
        }

    def note_mutation(self) -> None:
        raise AssertionError("subject reads must not mark the map mutated")


def tools_by_name(*, enable_vision: bool) -> dict[str, dict]:
    return {
        tool["name"]: tool
        for tool in registry_snapshot(enable_vision=enable_vision)["tools"]
    }


def test_registry_exposes_one_boolean_only_on_the_three_ruled_reads() -> None:
    off = tools_by_name(enable_vision=False)
    on = tools_by_name(enable_vision=True)

    assert SUBJECT_SCREENSHOT_TOOLS == {
        "get_tile",
        "get_vehicle",
        "get_my_station",
    }
    for name in SUBJECT_SCREENSHOT_TOOLS:
        assert "screenshot" not in off[name]["inputSchema"]["properties"]
        screenshot = on[name]["inputSchema"]["properties"]["screenshot"]
        assert screenshot == {"type": "boolean", "default": False}

    for name, tool in on.items():
        if name.startswith("list_") or (
            name.startswith("get_") and name not in SUBJECT_SCREENSHOT_TOOLS
        ):
            assert "screenshot" not in tool["inputSchema"]["properties"], name


def test_fastmcp_surface_matches_the_vision_gate(tmp_path: Path) -> None:
    off = fastmcp_tools_by_name(make_tool_server(tmp_path / "off"))
    on = fastmcp_tools_by_name(make_tool_server(tmp_path / "on", vision_enabled=True))

    for name in SUBJECT_SCREENSHOT_TOOLS:
        assert "screenshot" not in off[name].inputSchema["properties"]
        assert on[name].inputSchema["properties"]["screenshot"] == {
            "default": False,
            "title": "Screenshot",
            "type": "boolean",
        }
        assert on[name].outputSchema == off[name].outputSchema
        assert on[name].outputSchema is not None
    for name, tool in on.items():
        if name.startswith("list_") or (
            name.startswith("get_") and name not in SUBJECT_SCREENSHOT_TOOLS
        ):
            assert "screenshot" not in tool.inputSchema["properties"], name


def test_each_subject_read_crops_its_resolved_tile_after_one_wire_query_and_logs_once(
    tmp_path: Path,
) -> None:
    dispatched: list[list] = []

    def dispatch(command: list, timestamp: str) -> dict:
        dispatched.append(command)
        return subject_result(command)

    server = make_tool_server(
        tmp_path,
        dispatch,
        vision_enabled=True,
        decision_budget=10,
    )
    source = RecordingSource(tmp_path)
    server.screenshot_source = source

    calls = [
        ("get_tile", {"tile": 412, "screenshot": True}, ["get_tile", 412], 412),
        (
            "get_my_station",
            {"station_id": 3, "screenshot": True},
            ["get_my_station", 3, -1],
            988,
        ),
        (
            "get_vehicle",
            {"vehicle_id": 7, "screenshot": True},
            ["get_vehicle", 7],
            731,
        ),
    ]
    for name, arguments, expected_command, expected_tile in calls:
        result = server.handle_tool_call(name, arguments)
        assert result["ok"] is True
        assert result["screenshot"]["ok"] is True
        assert result["screenshot"]["tile"] == expected_tile
        assert result["screenshot"]["window"] == DEFAULT_WINDOW
        assert Path(result["screenshot"]["path"]).exists()
        assert dispatched[-1] == expected_command

    assert server.decisions_used == 3
    assert [call["tile"] for call in source.calls] == [412, 988, 731]
    assert all(call["window"] == DEFAULT_WINDOW for call in source.calls)
    assert all(call["refresh"] is True for call in source.calls)

    records = read_jsonl(tmp_path / "tool_calls.jsonl")
    assert len(records) == 3
    assert [record["name"] for record in records] == [row[0] for row in calls]
    assert [record["command"] for record in records] == [row[2] for row in calls]
    assert all(record["arguments"]["screenshot"] is True for record in records)
    assert all(record["result"]["screenshot"]["path"] for record in records)
    assert not any(record["name"] == "view_map" for record in records)


def test_failed_subject_read_never_attempts_a_screenshot(tmp_path: Path) -> None:
    server = make_tool_server(
        tmp_path,
        lambda command, timestamp: {
            "ok": False,
            "action": command[0],
            "error": "invalid_road_vehicle_id",
        },
        vision_enabled=True,
    )
    source = RecordingSource(tmp_path)
    server.screenshot_source = source

    result = server.handle_tool_call(
        "get_vehicle", {"vehicle_id": 999999, "screenshot": True}
    )

    assert result["ok"] is False
    assert "screenshot" not in result
    assert source.calls == []
    assert len(read_jsonl(tmp_path / "tool_calls.jsonl")) == 1


def test_capture_failure_preserves_facts_and_records_a_bounded_nested_error(
    tmp_path: Path,
) -> None:
    class BrokenSource:
        def crop_for_tile(self, tile, window, *, refresh, call_id):
            raise RuntimeError(f"could not open {tmp_path}/absolute-secret.png")

    server = make_tool_server(
        tmp_path,
        lambda command, timestamp: subject_result(command),
        vision_enabled=True,
    )
    server.screenshot_source = BrokenSource()

    result = server.handle_tool_call(
        "get_vehicle", {"vehicle_id": 7, "screenshot": True}
    )

    assert result["ok"] is True
    assert result["vehicle"] == {"vehicle_id": 7, "tile": 731, "state": 2}
    assert result["screenshot"] == {
        "ok": False,
        "action": "view_map",
        "tile": 731,
        "window": DEFAULT_WINDOW,
        "error": "screenshot_unavailable",
        "reason": "capture_failed",
        "error_detail": f"could not open {tmp_path}/absolute-secret.png",
    }


def test_vision_disabled_direct_request_preserves_the_successful_read(
    tmp_path: Path,
) -> None:
    server = make_tool_server(
        tmp_path, lambda command, timestamp: subject_result(command)
    )

    result = server.handle_tool_call("get_tile", {"tile": 412, "screenshot": True})

    assert result["ok"] is True
    assert result["tile_info"]["tile"] == 412
    assert result["screenshot"]["ok"] is False
    assert result["screenshot"]["reason"] == "vision_disabled"
    assert read_jsonl(tmp_path / "tool_calls.jsonl")[0]["command"] == [
        "get_tile",
        412,
    ]


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("list_my_vehicles", {"screenshot": True}),
        ("get_town", {"town_id": 1, "screenshot": True}),
        ("get_industry", {"industry_id": 1, "screenshot": True}),
    ],
)
def test_out_of_scope_runtime_screenshot_argument_is_rejected_without_dispatch(
    tmp_path: Path, name: str, arguments: dict
) -> None:
    dispatched: list[list] = []
    server = make_tool_server(
        tmp_path,
        lambda command, timestamp: dispatched.append(command) or {"ok": True},
        vision_enabled=True,
    )

    result = server.handle_tool_call(name, arguments)

    assert result["ok"] is False
    assert "screenshot" in result["error"]
    assert dispatched == []


def _text_and_image_blocks(content: list) -> tuple[str, list]:
    text = "\n".join(block.text for block in content if getattr(block, "text", None))
    images = [block for block in content if getattr(block, "data", None)]
    return text, images


@pytest.mark.parametrize(
    ("name", "arguments", "fact"),
    [
        ("get_tile", {"tile": 412, "screenshot": True}, '"tile":412'),
        (
            "get_my_station",
            {"station_id": 3, "screenshot": True},
            '"station_id":3',
        ),
        (
            "get_vehicle",
            {"vehicle_id": 7, "screenshot": True},
            '"vehicle_id":7',
        ),
    ],
)
def test_fastmcp_returns_real_image_content_plus_path_free_json_facts(
    tmp_path: Path, name: str, arguments: dict, fact: str
) -> None:
    server = make_tool_server(
        tmp_path,
        lambda command, timestamp: subject_result(command),
        vision_enabled=True,
    )
    server.screenshot_source = RecordingSource(tmp_path)

    result = asyncio.run(build_fastmcp(server).call_tool(name, arguments))
    text, images = _text_and_image_blocks(result.content)
    compact_text = text.replace(" ", "")

    assert fact in json.dumps(result.structuredContent, separators=(",", ":"))
    assert len(images) == 1
    assert images[0].mimeType == "image/png"
    assert base64.b64decode(images[0].data).startswith(b"\x89PNG")
    assert fact in compact_text
    assert '"screenshot"' in compact_text
    assert '"path"' not in compact_text
    assert str(tmp_path) not in text
    assert len(read_jsonl(tmp_path / "tool_calls.jsonl")) == 1


def test_fastmcp_capture_error_hides_absolute_path_but_keeps_bounded_failure(
    tmp_path: Path,
) -> None:
    class BrokenSource:
        def crop_for_tile(self, tile, window, *, refresh, call_id):
            raise RuntimeError(f"could not open {tmp_path}/absolute-secret.png")

    server = make_tool_server(
        tmp_path,
        lambda command, timestamp: subject_result(command),
        vision_enabled=True,
    )
    server.screenshot_source = BrokenSource()

    result = asyncio.run(
        build_fastmcp(server).call_tool(
            "get_vehicle", {"vehicle_id": 7, "screenshot": True}
        )
    )
    text, images = _text_and_image_blocks(result.content)

    assert images == []
    assert "screenshot_unavailable" in text
    assert "capture_failed" in text
    assert "error_detail" not in text
    assert str(tmp_path) not in text
    assert result.structuredContent["vehicle"]["vehicle_id"] == 7
    assert "error_detail" not in result.structuredContent["screenshot"]
    record = read_jsonl(tmp_path / "tool_calls.jsonl")[0]
    assert str(tmp_path) in record["result"]["screenshot"]["error_detail"]


def test_missing_crop_is_recorded_before_transport_without_losing_facts_or_leaking_path(
    tmp_path: Path,
) -> None:
    missing_path = tmp_path / "secret-missing.png"

    class MissingCropSource:
        def crop_for_tile(self, tile, window, *, refresh, call_id):
            return {
                "ok": True,
                "action": "view_map",
                "tile": tile,
                "window": window,
                "path": str(missing_path),
            }

    server = make_tool_server(
        tmp_path,
        lambda command, timestamp: subject_result(command),
        vision_enabled=True,
    )
    server.screenshot_source = MissingCropSource()

    result = asyncio.run(
        build_fastmcp(server).call_tool("get_tile", {"tile": 412, "screenshot": True})
    )
    text, images = _text_and_image_blocks(result.content)

    assert images == []
    assert result.structuredContent["tile_info"]["tile"] == 412
    assert result.structuredContent["screenshot"] == {
        "ok": False,
        "action": "view_map",
        "tile": 412,
        "window": DEFAULT_WINDOW,
        "error": "screenshot_unavailable",
        "reason": "capture_failed",
    }
    assert "screenshot_unavailable" in text
    assert str(missing_path) not in text

    records = read_jsonl(tmp_path / "tool_calls.jsonl")
    assert len(records) == 1
    record = records[0]
    assert record["transport_ok"] is True
    assert record["result"]["tile_info"]["tile"] == 412
    assert str(missing_path) in record["result"]["screenshot"]["error_detail"]


def test_non_boolean_screenshot_is_rejected_before_subject_dispatch(
    tmp_path: Path,
) -> None:
    dispatched: list[list] = []
    server = make_tool_server(
        tmp_path,
        lambda command, timestamp: (
            dispatched.append(command) or subject_result(command)
        ),
        vision_enabled=True,
    )

    result = server.handle_tool_call("get_tile", {"tile": 412, "screenshot": "true"})

    assert result["ok"] is False
    assert "boolean" in result["error"]
    assert dispatched == []


@pytest.mark.parametrize("value", ["true", 1])
def test_fastmcp_rejects_coercible_non_booleans_before_subject_dispatch(
    tmp_path: Path, value: object
) -> None:
    dispatched: list[list] = []
    server = make_tool_server(
        tmp_path,
        lambda command, timestamp: (
            dispatched.append(command) or subject_result(command)
        ),
        vision_enabled=True,
    )

    with pytest.raises(ToolError, match="boolean"):
        asyncio.run(
            build_fastmcp(server).call_tool(
                "get_tile", {"tile": 412, "screenshot": value}
            )
        )

    assert dispatched == []
    assert not (tmp_path / "tool_calls.jsonl").exists()


def test_false_screenshot_is_forwarded_consistently_without_capture(
    tmp_path: Path,
) -> None:
    server = make_tool_server(
        tmp_path,
        lambda command, timestamp: subject_result(command),
        vision_enabled=True,
    )
    source = RecordingSource(tmp_path)
    server.screenshot_source = source
    mcp = build_fastmcp(server)

    asyncio.run(mcp.call_tool("get_tile", {"tile": 412, "screenshot": False}))
    asyncio.run(mcp.call_tool("get_my_station", {"station_id": 3, "screenshot": False}))
    asyncio.run(mcp.call_tool("get_vehicle", {"vehicle_id": 7, "screenshot": False}))

    assert source.calls == []
    records = read_jsonl(tmp_path / "tool_calls.jsonl")
    assert len(records) == 3
    assert all(record["arguments"]["screenshot"] is False for record in records)
