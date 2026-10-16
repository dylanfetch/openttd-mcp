from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from openttd_mcp.lifecycle import (
    VERIFIER_MISSING_RESULT_EXIT_CODE,
    _log_filter,
    build_model_prompt,
    command_from_primitive,
    compute_scripted_plan,
    prepare_runtime,
    run_verifier,
)
from tests.support import TempDirTestCase


def straight_road_snapshot(
    *, vertical: bool = False, include_depot: bool = True
) -> dict:
    width = 10
    tiles = []
    for offset in range(1, 9):
        x, y = (4, offset) if vertical else (offset, 4)
        connections = (
            {"n": offset > 1, "s": offset < 8}
            if vertical
            else {"w": offset > 1, "e": offset < 8}
        )
        tiles.append(
            {
                "tile": y * width + x,
                "x": x,
                "y": y,
                "slope": 0,
                "road_tile": True,
                "road_connections": connections,
                "in_largest_town_influence": True,
            }
        )
    if include_depot:
        x, y = 5, 5
        tiles.append(
            {"tile": y * width + x, "x": x, "y": y, "slope": 0, "buildable": True}
        )
    return {"map_size": [width, width], "tiles": tiles}


class RuntimePreparationTests(TempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.tmp_path
        self.binary = self.root / "openttd"
        self.binary.write_text("binary")
        self.data = self.root / "data"
        (self.data / "baseset").mkdir(parents=True)
        (self.data / "lang").mkdir()
        self.ai_src = self.root / "ai-src"
        self.ai_src.mkdir()
        for name in ("info.nut", "main.nut", "commands.nut"):
            (self.ai_src / name).write_text(f"source {name}\n")

    def test_prepare_runtime_builds_an_isolated_bridge_tree(self) -> None:
        runs = self.root / "runs"
        with (
            mock.patch("openttd_mcp.lifecycle.OTTD_BIN", self.binary),
            mock.patch("openttd_mcp.lifecycle.OTTD_DATA", self.data),
            mock.patch("openttd_mcp.lifecycle.BRIDGE_SRC", self.ai_src),
            mock.patch("openttd_mcp.lifecycle.LOCAL_RUNS_DIR", runs),
        ):
            run_root, ai_dst = prepare_runtime(
                "unit-run", 6, num_towns=7, starting_year=1930, emit_boot_snapshot=False
            )

        self.assertEqual(run_root, runs / "unit-run" / "openttd-root")
        self.assertTrue((run_root / "baseset").exists())
        self.assertTrue((run_root / "lang").exists())
        self.assertEqual(
            (run_root / "scripts" / "game_start.scr").read_text(),
            "start_ai McpBridge\n",
        )
        cfg = (run_root / "openttd.cfg").read_text()
        self.assertIn("custom_town_number = 7", cfg)
        self.assertIn("starting_year = 1930", cfg)
        # map_log2 must flow through to the generated config, not a default.
        self.assertIn("map_x = 6", cfg)
        self.assertIn("map_y = 6", cfg)
        commands = (ai_dst / "commands.nut").read_text()
        self.assertIn("emit_map_snapshot = false", commands)
        self.assertIn("commands = []", commands)

    def test_prepare_runtime_places_absolute_run_directory_without_local_prefix(
        self,
    ) -> None:
        runs = self.root / "runs"
        absolute_run_dir = self.root / "caller-selected"
        with (
            mock.patch("openttd_mcp.lifecycle.OTTD_BIN", self.binary),
            mock.patch("openttd_mcp.lifecycle.OTTD_DATA", self.data),
            mock.patch("openttd_mcp.lifecycle.BRIDGE_SRC", self.ai_src),
            mock.patch("openttd_mcp.lifecycle.LOCAL_RUNS_DIR", runs),
        ):
            run_root, ai_dst = prepare_runtime(absolute_run_dir, 7)

        self.assertEqual(run_root, absolute_run_dir / "openttd-root")
        self.assertEqual(ai_dst, run_root / "ai" / "McpBridge")
        self.assertFalse((runs / absolute_run_dir.name).exists())

    def test_prepare_runtime_names_the_missing_binary(self) -> None:
        missing = self.root / "missing-openttd"
        with (
            mock.patch("openttd_mcp.lifecycle.OTTD_BIN", missing),
            self.assertRaises(SystemExit) as ctx,
        ):
            prepare_runtime("unit-run", 6)
        self.assertIn(str(missing), str(ctx.exception))
        self.assertIn("openttd-runtime-setup.md", str(ctx.exception))

    def test_log_filter_removes_only_snapshot_tile_rows(self) -> None:
        text = "before\nMCP_MAP_SNAPSHOT_TILES|0|[1]\nafter\n"
        self.assertEqual(_log_filter(text), "before\nafter\n")
        self.assertEqual(_log_filter("ordinary output\n"), "ordinary output\n")


class ScriptedPlanTests(unittest.TestCase):
    def test_horizontal_plan_selects_separated_stops_and_connected_depot(self) -> None:
        plan = compute_scripted_plan(
            {"engine_candidates": [{"engine_id": 17}]},
            straight_road_snapshot(),
        )
        self.assertEqual(plan["engine_id"], 17)
        self.assertEqual(plan["component_size"], 8)
        self.assertEqual(plan["stop_distance"], 5)
        # Ordered pairs: each stop must be paired with its own front tile, not
        # merely the right set of tiles overall.
        self.assertEqual((plan["stop_a"], plan["stop_a_front"]), (42, 43))
        self.assertEqual((plan["stop_b"], plan["stop_b_front"]), (47, 48))
        self.assertEqual((plan["depot_tile"], plan["depot_front"]), (55, 45))

    def test_vertical_plan_uses_north_facing_stop_fronts(self) -> None:
        plan = compute_scripted_plan(
            {"engine_candidates": [{"engine_id": 3}]},
            straight_road_snapshot(vertical=True),
        )
        self.assertEqual((plan["stop_a"], plan["stop_a_front"]), (24, 14))
        self.assertEqual((plan["stop_b"], plan["stop_b_front"]), (74, 64))
        self.assertEqual((plan["depot_tile"], plan["depot_front"]), (55, 54))

    def test_plan_rejects_missing_engine_roads_and_depot(self) -> None:
        with (
            self.subTest(case="engine"),
            self.assertRaisesRegex(RuntimeError, "no passenger engines"),
        ):
            compute_scripted_plan({}, straight_road_snapshot())

        with (
            self.subTest(case="roads"),
            self.assertRaisesRegex(RuntimeError, "no in-town road tiles"),
        ):
            compute_scripted_plan(
                {"engine_candidates": [{"engine_id": 1}]},
                {"map_size": [10, 10], "tiles": []},
            )

        with (
            self.subTest(case="component"),
            self.assertRaisesRegex(RuntimeError, "component too small"),
        ):
            snapshot = straight_road_snapshot()
            snapshot["tiles"] = snapshot["tiles"][:5]
            compute_scripted_plan({"engine_candidates": [{"engine_id": 1}]}, snapshot)

        with (
            self.subTest(case="depot"),
            self.assertRaisesRegex(RuntimeError, "no buildable depot tile"),
        ):
            compute_scripted_plan(
                {"engine_candidates": [{"engine_id": 1}]},
                straight_road_snapshot(include_depot=False),
            )

    def test_plan_rejects_components_without_usable_stop_pair(self) -> None:
        observation = {"engine_candidates": [{"engine_id": 1}]}
        snapshot = straight_road_snapshot()
        for tile in snapshot["tiles"]:
            if tile.get("road_tile"):
                tile["slope"] = 1
        with self.assertRaisesRegex(
            RuntimeError, "insufficient drive-through stop options"
        ):
            compute_scripted_plan(observation, snapshot)

        snapshot = straight_road_snapshot()
        roads = [tile for tile in snapshot["tiles"] if tile.get("road_tile")]
        for tile in roads:
            tile["slope"] = 1
        roads[1]["slope"] = 0
        roads[2]["slope"] = 0
        with self.assertRaisesRegex(RuntimeError, "Manhattan distance >= 4"):
            compute_scripted_plan(observation, snapshot)

    def test_stop_pair_distance_boundary_is_exactly_four(self) -> None:
        observation = {"engine_candidates": [{"engine_id": 1}]}

        def flat_only(*indices: int) -> dict:
            snapshot = straight_road_snapshot()
            roads = [tile for tile in snapshot["tiles"] if tile.get("road_tile")]
            for tile in roads:
                tile["slope"] = 1
            for index in indices:
                roads[index]["slope"] = 0
            return snapshot

        # Endpoint tiles are not drive-through candidates (one-sided
        # connection), so both stops sit on interior tiles.
        with self.assertRaisesRegex(RuntimeError, "Manhattan distance >= 4"):
            compute_scripted_plan(observation, flat_only(1, 4))

        plan = compute_scripted_plan(observation, flat_only(1, 5))
        self.assertEqual(plan["stop_distance"], 4)


class PromptAndVerifierTests(TempDirTestCase):
    def test_model_prompt_records_render_availability_memory_and_result(self) -> None:
        prompt = build_model_prompt(
            {"date": 10},
            "remember this",
            {"ok": False, "error": "blocked"},
            Path("map.svg"),
            Path("map.png"),
            png_available=True,
        )
        self.assertIn("PNG: map.png", prompt)
        self.assertIn("remember this", prompt)
        self.assertIn('"error": "blocked"', prompt)
        self.assertIn('"date": 10', prompt)

        text_only = build_model_prompt(
            {}, "", None, Path("map.svg"), Path("map.png"), png_available=False
        )
        self.assertIn("PNG rasteriser unavailable", text_only)
        self.assertIn("(none yet)", text_only)
        self.assertNotIn("Result of your previous primitive", text_only)

    def test_command_from_primitive_normalizes_command_and_integer_arguments(
        self,
    ) -> None:
        self.assertEqual(
            command_from_primitive({"command": "build_road", "args": ["12", 14]}),
            ["build_road", 12, 14],
        )
        self.assertEqual(
            command_from_primitive({"command": "list_towns"}), ["list_towns"]
        )

    def test_run_verifier_copies_and_loads_declared_evidence(self) -> None:
        root = self.tmp_path
        source_result = root / "source-result.json"
        source_trace = root / "source-trace.jsonl"
        source_log = root / "source.log"
        source_result.write_text(json.dumps({"ok": True, "routes": 1}))
        source_trace.write_text('{"step": 1}\n')
        source_log.write_text("verified\n")
        stdout = "\n".join(
            (
                "ignored output",
                f"VERIFY_RESULT={source_result}",
                f"ROUTE_TRACE={source_trace}",
                f"VERIFY_LOG={source_log}",
            )
        )
        with mock.patch(
            "openttd_mcp.lifecycle.run_verifier_subprocess",
            return_value=(0, stdout, False),
        ):
            returncode, result = run_verifier(root / "game.sav", root, "final")

        self.assertEqual(returncode, 0)
        self.assertEqual(result, {"ok": True, "routes": 1})
        self.assertEqual(
            (root / "final_route_trace.jsonl").read_text(), '{"step": 1}\n'
        )
        self.assertEqual(
            (root / "final_verify_bus_route.log").read_text(), "verified\n"
        )
        self.assertEqual((root / "final_verify_stdout.txt").read_text(), stdout)

    def test_run_verifier_clears_only_its_stale_destinations_before_launch(
        self,
    ) -> None:
        root = self.tmp_path
        save_path = root / "game.sav"
        save_path.write_bytes(b"SAVE")
        source_result = root / "current-result.json"
        source_result.write_text('{"ok": true, "current": true}\n')
        owned_paths = {
            root / "final_verify_result.json": b'{"ok": true, "stale": true}\n',
            root / "final_route_trace.jsonl": b'{"stale": true}\n',
            root / "final_verify_bus_route.log": b"stale verifier log\n",
        }
        sibling_paths = {
            root / "checkpoint_verify_result.json": b'{"ok": true}\n',
            root / "checkpoint_route_trace.jsonl": b'{"checkpoint": true}\n',
            root / "checkpoint_verify_bus_route.log": b"checkpoint log\n",
        }
        for path, contents in {**owned_paths, **sibling_paths}.items():
            path.write_bytes(contents)

        stdout = f"VERIFY_RESULT={source_result}\n"

        def verifier_subprocess(*_args, **_kwargs):
            for path in owned_paths:
                self.assertFalse(path.exists(), f"stale destination survived: {path}")
            for path, contents in sibling_paths.items():
                self.assertEqual(path.read_bytes(), contents)
            self.assertEqual(
                source_result.read_bytes(), b'{"ok": true, "current": true}\n'
            )
            self.assertEqual(save_path.read_bytes(), b"SAVE")
            return 0, stdout, False

        with mock.patch(
            "openttd_mcp.lifecycle.run_verifier_subprocess",
            side_effect=verifier_subprocess,
        ):
            returncode, result = run_verifier(save_path, root, "final")

        self.assertEqual(returncode, 0)
        self.assertEqual(result, {"ok": True, "current": True})
        self.assertFalse((root / "final_route_trace.jsonl").exists())
        self.assertFalse((root / "final_verify_bus_route.log").exists())
        self.assertEqual((root / "final_verify_stdout.txt").read_text(), stdout)
        for path, contents in sibling_paths.items():
            self.assertEqual(path.read_bytes(), contents)
        self.assertEqual(source_result.read_bytes(), b'{"ok": true, "current": true}\n')
        self.assertEqual(save_path.read_bytes(), b"SAVE")

    def test_run_verifier_materializes_timeout_failure_result(self) -> None:
        root = self.tmp_path
        with mock.patch(
            "openttd_mcp.lifecycle.run_verifier_subprocess",
            return_value=(124, "timed out", True),
        ):
            returncode, result = run_verifier(root / "game.sav", root, "final")

        self.assertEqual(returncode, 124)
        self.assertEqual(result["reason"], "verifier_timeout")
        self.assertEqual(result["exit_code"], 124)
        self.assertIn("timeout_seconds", result)
        self.assertEqual(
            json.loads((root / "final_verify_result.json").read_text()),
            result,
        )

    def test_run_verifier_replaces_stale_result_when_current_process_fails(
        self,
    ) -> None:
        root = self.tmp_path
        result_path = root / "final_verify_result.json"
        result_path.write_text('{"ok": true, "stale": true}\n')
        with mock.patch(
            "openttd_mcp.lifecycle.run_verifier_subprocess",
            return_value=(3, "crashed before emitting markers", False),
        ):
            returncode, result = run_verifier(root / "game.sav", root, "final")

        self.assertEqual(returncode, 3)
        self.assertEqual(result["ok"], False)
        self.assertEqual(result["reason"], "verifier_process_failed")
        self.assertEqual(result["exit_code"], 3)
        self.assertNotIn("stale", result)
        self.assertEqual(json.loads(result_path.read_text()), result)

    def test_run_verifier_fails_when_current_process_exits_zero_without_result(
        self,
    ) -> None:
        root = self.tmp_path
        result_path = root / "final_verify_result.json"
        result_path.write_text('{"ok": true, "stale": true}\n')
        with mock.patch(
            "openttd_mcp.lifecycle.run_verifier_subprocess",
            return_value=(0, "clean exit without a result marker", False),
        ):
            returncode, result = run_verifier(root / "game.sav", root, "final")

        self.assertEqual(returncode, VERIFIER_MISSING_RESULT_EXIT_CODE)
        self.assertEqual(result["ok"], False)
        self.assertEqual(result["reason"], "verifier_result_missing")
        self.assertEqual(result["exit_code"], VERIFIER_MISSING_RESULT_EXIT_CODE)
        self.assertEqual(result["process_exit_code"], 0)
        self.assertNotIn("stale", result)
        self.assertEqual(json.loads(result_path.read_text()), result)


if __name__ == "__main__":
    unittest.main()
