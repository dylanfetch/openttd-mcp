from __future__ import annotations

import asyncio
import base64
import json
import unittest

from openttd_mcp.admin import AdminLifecycleConfig
from openttd_mcp.server import (
    BridgeError,
    GameToolServer,
    build_fastmcp,
    command_for_tool,
    load_script,
    parse_result_line,
    payload_bytes,
    registry_snapshot,
    squirrel_literal,
)
from tests.support import TempDirTestCase, make_tool_server, read_jsonl


class WireFormatTests(TempDirTestCase):
    def test_squirrel_literals_and_result_lines_validate_the_bridge_wire_format(
        self,
    ) -> None:
        self.assertEqual(squirrel_literal(True), "true")
        self.assertEqual(squirrel_literal(False), "false")
        self.assertEqual(squirrel_literal(7), "7")
        self.assertEqual(squirrel_literal('a\\b"c'), '"a\\\\b\\"c"')
        with self.assertRaisesRegex(TypeError, "Unsupported command literal"):
            squirrel_literal(1.5)

        line = 'debug MCP_RESULT|turn-1|{"ok": true, "cost": 12}\n'
        self.assertEqual(parse_result_line(line, "turn-1"), {"ok": True, "cost": 12})
        with self.assertRaisesRegex(BridgeError, "result marker for missing not found"):
            parse_result_line(line, "missing")

        sensor_error_line = (
            "MCP_RESULT|turn-2|"
            '{"ok":false,"action":"get_my_station","error":"sensor_api_error",'
            '"cost":0,"bank_balance":1000,"station_id":2,'
            '"sensor_error":{"code":"station_tiles_unavailable",'
            '"field":"bus_stop_tiles"}}'
        )
        sensor_error = parse_result_line(sensor_error_line, "turn-2")
        self.assertFalse(sensor_error["ok"])
        self.assertEqual(sensor_error["error"], "sensor_api_error")
        self.assertEqual(
            sensor_error["sensor_error"],
            {"code": "station_tiles_unavailable", "field": "bus_stop_tiles"},
        )

    def test_payload_bytes_falls_back_for_non_json_values(self) -> None:
        value = {1, 2}
        self.assertEqual(payload_bytes(value), len(str(value).encode("utf-8")))


class CommandValidationTests(unittest.TestCase):
    def assertCommandRaises(self, message: str, name: str, arguments: dict) -> None:
        with self.assertRaisesRegex(ValueError, message):
            command_for_tool(name, arguments)

    def test_uncovered_invalid_argument_shapes_are_rejected(self) -> None:
        cases = (
            ("missing required argument: mode", "terraform_land", {"tile": 1}),
            (
                "corners must be a list",
                "terraform_land",
                {"tile": 1, "mode": "raise", "corners": "n"},
            ),
            (
                "non-empty list",
                "check_route_connectivity",
                {
                    "town_id": 1,
                    "depot_tile": 2,
                    "depot_front_tile": 3,
                    "stop_tiles": [],
                },
            ),
            (
                "contain only integers",
                "check_route_connectivity",
                {
                    "town_id": 1,
                    "depot_tile": 2,
                    "depot_front_tile": 3,
                    "stop_tiles": [True],
                },
            ),
            (
                "contain at most 32",
                "check_route_connectivity",
                {
                    "town_id": 1,
                    "depot_tile": 2,
                    "depot_front_tile": 3,
                    "stop_tiles": list(range(33)),
                },
            ),
            (
                "contain only order flag names",
                "insert_order",
                {"vehicle_id": 1, "position": 0, "stop_tile": 2, "flags": [1]},
            ),
            (
                "duplicate order flag",
                "insert_order",
                {
                    "vehicle_id": 1,
                    "position": 0,
                    "stop_tile": 2,
                    "flags": ["NON_STOP", "NON_STOP"],
                },
            ),
            (
                "flags must be a list",
                "append_orders",
                {"vehicle_id": 1, "stop_tiles": [2], "flags": "NON_STOP"},
            ),
            (
                "either only strings or only lists",
                "append_orders",
                {"vehicle_id": 1, "stop_tiles": [2, 3], "flags": ["NON_STOP", []]},
            ),
            (
                "detail must be",
                "get_region",
                {"center_tile": 1, "radius": 2, "detail": "raw"},
            ),
            (
                "vehicle_id must be a non-negative integer",
                "refit_vehicle",
                {"vehicle_id": -1, "cargo_id": 0},
            ),
        )
        for message, name, arguments in cases:
            with self.subTest(name=name, arguments=arguments):
                self.assertCommandRaises(message, name, arguments)

    def test_empty_append_order_flags_encode_explicit_zero_masks(self) -> None:
        self.assertEqual(
            command_for_tool(
                "append_orders",
                {"vehicle_id": 7, "stop_tiles": [10, 20], "flags": []},
            ),
            ["append_orders", 7, 1, 10, 0, 20, 0],
        )

    def test_registry_rejects_invalid_modes_and_delivery_channels(self) -> None:
        with self.assertRaisesRegex(ValueError, "end_episode_mode must be one of"):
            registry_snapshot(end_episode_mode="hidden")
        with self.assertRaisesRegex(ValueError, "unsupported tool index channel"):
            registry_snapshot(tool_index_channel="instructions")


class ServerStateTests(TempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.tmp_path

    def test_scalar_budget_override_becomes_an_evidenced_stop_reason(self) -> None:
        override = self.root / "budget_override.json"
        override.write_text(json.dumps("monitor stop"))
        server = make_tool_server(self.root, budget_override_path=override)

        result = server.handle_tool_call("list_towns")

        self.assertFalse(result["ok"])
        self.assertEqual(server.budget_cut["payload"], {"reason": "monitor stop"})
        records = read_jsonl(self.root / "tool_calls.jsonl")
        self.assertEqual(records[0]["name"], "budget_override_applied")
        self.assertEqual(records[0]["arguments"], {"reason": "monitor stop"})

    def test_distress_end_episode_does_not_echo_budget_counters(self) -> None:
        server = make_tool_server(
            self.root,
            decision_budget=5,
            budget_echo=True,
            end_episode_mode="distress",
        )
        result = server.handle_tool_call(
            "end_episode", {"reason": "no action can change state"}
        )
        self.assertTrue(result["episode_ended"])
        self.assertNotIn("decisions_used", result)
        self.assertNotIn("decisions_remaining", result)

    def test_handler_reports_disabled_missing_and_unknown_tool_errors(self) -> None:
        server = GameToolServer(None, tool_log=self.root / "tool_calls.jsonl")
        calls = (
            ("wait", {"duration_days": 1}, "requires a bridge dispatcher"),
            ("list_towns", {}, "requires a bridge dispatcher"),
            ("get_bus_route_skill", {"town_id": 1}, "skill tool is not enabled"),
            ("view_map", {"tile": 1}, "vision is not enabled"),
            ("not_a_tool", {}, "unknown tool"),
        )
        for name, arguments, message in calls:
            with self.subTest(name=name):
                result = server.handle_tool_call(name, arguments)
                self.assertFalse(result["ok"])
                self.assertIn(message, result["error"])

    def test_view_map_rejects_boolean_window_before_rendering(self) -> None:
        server = make_tool_server(self.root, vision_enabled=True)
        result = server.handle_tool_call("view_map", {"tile": 1, "window": True})
        self.assertFalse(result["ok"])
        self.assertIn("window must be an integer", result["error"])

    def test_successful_mutation_marks_cached_screenshot_drift(self) -> None:
        class Source:
            def __init__(self) -> None:
                self.mutations = 0

            def note_mutation(self) -> None:
                self.mutations += 1

        source = Source()
        server = make_tool_server(self.root)
        server.screenshot_source = source
        self.assertTrue(
            server.handle_tool_call("build_road", {"from_tile": 1, "to_tile": 2})["ok"]
        )
        self.assertEqual(source.mutations, 1)

        server.handle_tool_call("list_towns")
        self.assertEqual(source.mutations, 1)

    def test_partial_mutation_marks_cached_screenshot_drift_when_result_fails(
        self,
    ) -> None:
        """A paid depot left behind after connector failure must stale cached imagery."""

        class Source:
            def __init__(self) -> None:
                self.mutations = 0

            def note_mutation(self) -> None:
                self.mutations += 1

        partial = make_tool_server(
            self.root,
            dispatcher=lambda command, timestamp: {
                "ok": False,
                "game_mutated": True,
            },
        )
        partial_source = Source()
        partial.screenshot_source = partial_source
        result = partial.handle_tool_call(
            "build_road_depot", {"tile": 10, "front_tile": 11}
        )

        self.assertFalse(result["ok"])
        self.assertEqual(partial_source.mutations, 1)

        refused = make_tool_server(
            self.root,
            dispatcher=lambda command, timestamp: {
                "ok": False,
                "game_mutated": False,
            },
        )
        refused_source = Source()
        refused.screenshot_source = refused_source
        refused.handle_tool_call("build_road_depot", {"tile": 20, "front_tile": 21})
        self.assertEqual(refused_source.mutations, 0)

    def test_lazy_screenshot_source_uses_default_crop_directory(self) -> None:
        screenshot_dir = self.root / "screenshots"
        server = make_tool_server(
            self.root,
            vision_enabled=True,
            admin_config=AdminLifecycleConfig(port=3977),
            screenshot_dir=screenshot_dir,
            map_log2=6,
        )
        source = server._ensure_screenshot_source()
        self.assertIs(source, server._ensure_screenshot_source())
        self.assertEqual(source.crops_dir, self.root / "screenshots")
        self.assertEqual(source.map_log2, 6)


class ScriptAndFastMCPTests(TempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.tmp_path

    def test_load_script_ignores_blanks_and_rejects_non_object_rows(self) -> None:
        path = self.root / "script.jsonl"
        path.write_text(
            '\n{"name": "list_towns"}\n\n{"tool": "wait", "arguments": {"duration_days": 1}}\n'
        )
        self.assertEqual(
            load_script(path),
            [
                {"name": "list_towns"},
                {"tool": "wait", "arguments": {"duration_days": 1}},
            ],
        )
        path.write_text('["list_towns"]\n')
        with self.assertRaisesRegex(ValueError, "script line is not an object"):
            load_script(path)

    def test_branchy_fastmcp_wrappers_reach_the_real_handler(self) -> None:
        commands: list[list] = []

        def dispatch(command, _timestamp):
            commands.append(command)
            return {"ok": True}

        skill_path = self.root / "skill.json"
        skill_path.write_text(json.dumps({"name": "test-skill", "version": 1}))
        server = make_tool_server(self.root, dispatch, enable_skill_path=skill_path)
        mcp = build_fastmcp(server)
        calls = (
            ("get_tile", {"tile": 10, "cargo_id": 3}),
            (
                "get_region",
                {
                    "center_tile": 10,
                    "radius": 2,
                    "cargo_id": 3,
                    "detail": "tiles",
                    "fields": [],
                    "where": [],
                },
            ),
            ("get_my_station", {"station_id": 4}),
            ("terraform_land", {"tile": 10, "mode": "level_to", "to_tile": 11}),
            (
                "build_bus_stop_bay",
                {"tile": 10, "front_tile": 11, "join": "station_id", "station_id": 4},
            ),
            (
                "insert_order",
                {
                    "vehicle_id": 5,
                    "position": 0,
                    "stop_tile": 10,
                    "flags": ["NON_STOP"],
                },
            ),
            (
                "append_orders",
                {
                    "vehicle_id": 5,
                    "stop_tiles": [10, 20],
                    "flags": [["NON_STOP"], []],
                },
            ),
            ("get_bus_route_skill", {"town_id": 9, "engine_id": 12}),
        )
        for name, arguments in calls:
            with self.subTest(name=name):
                asyncio.run(mcp.call_tool(name, arguments))

        self.assertEqual(
            commands,
            [
                ["get_tile", 10, 3],
                ["get_region", 10, 2, 3],
                ["get_my_station", 4, -1],
                ["terraform_land", 10, "level_to", 11],
                ["build_bus_stop_bay", 10, 11, 4],
                ["insert_order", 5, 0, 10, 1],
                ["append_orders", 5, 1, 10, 1, 20, 0],
            ],
        )
        self.assertEqual(
            read_jsonl(self.root / "tool_calls.jsonl")[-1]["result"]["bound_params"],
            {"town_id": 9, "engine_id": 12},
        )

    def test_fastmcp_view_map_hides_the_host_path_but_logs_it(self) -> None:
        crop = self.root / "secret-host-path.png"
        crop.write_bytes(
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
            )
        )

        class Source:
            def crop_for_tile(self, tile, window, *, refresh, call_id):
                return {
                    "ok": True,
                    "action": "view_map",
                    "path": str(crop),
                    "tile": tile,
                    "window": window,
                    "refresh": refresh,
                    "call_id": call_id,
                }

        server = make_tool_server(self.root, vision_enabled=True)
        server.screenshot_source = Source()
        content = asyncio.run(
            build_fastmcp(server).call_tool(
                "view_map", {"tile": 7, "window": 64, "refresh": False}
            )
        )

        rendered = "\n".join(
            str(getattr(block, "text", "")) + str(getattr(block, "data", ""))
            for block in content
        )
        self.assertNotIn(str(crop), rendered)
        self.assertEqual(
            read_jsonl(self.root / "tool_calls.jsonl")[-1]["result"]["path"],
            str(crop),
        )


if __name__ == "__main__":
    unittest.main()
