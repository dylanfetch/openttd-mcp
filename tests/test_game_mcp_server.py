from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from typing import get_args

import pytest

from openttd_mcp.feedback import FEEDBACK_KINDS
from openttd_mcp.region import DETAIL_VALUES
from openttd_mcp.server import (
    CONDITIONAL_ORDER_COMPARES,
    CONDITIONAL_ORDER_CONDITIONS,
    EPISODE_STOP_SIGNAL,
    ORDER_FLAG_BITS,
    QUERY_REGION_RADIUS_MAX_BY_DETAIL,
    QUERY_REGION_RADIUS_MAX_GRID,
    QUERY_REGION_RADIUS_MAX_TILES,
    ROUTE_CONNECTIVITY_STOP_MAX,
    FunctionBridgeDispatcher,
    GameToolServer,
    OrderFlagName,
    build_fastmcp,
    command_for_tool,
    generate_server_instructions,
    registry_snapshot,
    tool_specs,
    write_command_file,
)
from openttd_mcp.tool_names import RETIRED_TOOL_NAME_MAP
from tests.support import (
    REGION_FIXTURE_PATH,
    TempDirTestCase,
    fastmcp_tools_by_name,
    feedback_args,
    make_tool_server,
    read_jsonl,
    tools_by_name,
    write_json,
)

# ---------------------------------------------------------------------------
# command_for_tool mapping tables
#
# Every tool whose only Python-side contract is "these arguments become this
# flat bridge command" lives here as a row, rather than as its own one-shape
# test. Rows that carry provenance or a non-obvious invariant keep it as a
# comment on the row. Tests that assert more than the mapping (specific error
# text, served-schema shape, dispatch round-trips) stay as their own tests.
# ---------------------------------------------------------------------------

TOOL_COMMAND_MAPPINGS: tuple[tuple[str, dict, list], ...] = (
    # --- inventory / report sensors ---
    ("list_my_route_economics", {}, ["list_my_route_economics"]),
    ("get_my_company", {}, ["get_my_company"]),
    ("list_my_stations", {}, ["list_my_stations"]),
    ("list_my_depots", {}, ["list_my_depots"]),
    ("list_industries", {}, ["list_industries"]),
    ("get_industry", {"industry_id": 4}, ["get_industry", 4]),
    ("get_town", {"town_id": 4}, ["get_town", 4]),
    (
        "check_truck_stop_catchment",
        {"tile": 2398},
        ["check_truck_stop_catchment", 2398],
    ),
    ("get_my_station", {"station_id": 2}, ["get_my_station", 2, -1]),
    ("get_my_station", {"tile": 2608}, ["get_my_station", -1, 2608]),
    # None means unset (the _optional_* convention): a JSON-null identifier is
    # absent, not a second identifier.
    (
        "get_my_station",
        {"station_id": None, "tile": 2608},
        ["get_my_station", -1, 2608],
    ),
    # 021 no-change guard: sensor additions (query_station, list_engines) must
    # not alter the dispatch of the tools that were already there.
    ("get_tile", {"tile": 2608}, ["get_tile", 2608]),
    ("get_my_finances", {}, ["get_my_finances"]),
    ("list_towns", {}, ["list_towns"]),
    ("buy_vehicle", {"depot_tile": 300, "engine_id": 116}, ["buy_vehicle", 300, 116]),
    # get_region's compaction options (detail/fields/where) are Python-side
    # only: they never change the flat bridge command.
    (
        "get_region",
        {"center_tile": 2735, "radius": 6, "detail": "grid"},
        ["get_region", 2735, 6],
    ),
    (
        "get_region",
        {
            "center_tile": 2735,
            "radius": 2,
            "fields": ["road_tile"],
            "where": ["road_tile"],
        },
        ["get_region", 2735, 2],
    ),
    (
        "get_region",
        {
            "center_tile": 2735,
            "radius": 2,
            "fields": ["passenger_acceptance", "passenger_production"],
        },
        ["get_region", 2735, 2],
    ),
    # --- estimates map exactly like the build they estimate ---
    (
        "check_build_road",
        {"from_tile": 10, "to_tile": 20, "waypoints": [15]},
        ["check_build_road", 10, 15, 20],
    ),
    (
        "check_build_road",
        {"from_tile": 10, "to_tile": 20},
        ["check_build_road", 10, 20],
    ),
    (
        "check_build_road_depot",
        {"tile": 300, "front_tile": 301},
        ["check_build_road_depot", 300, 301],
    ),
    # --- terrain / geography ---
    ("list_bridge_types", {"length": 5}, ["list_bridge_types", 5]),
    (
        "build_bridge",
        {"start_tile": 100, "end_tile": 105, "bridge_id": 7},
        ["build_bridge", 100, 105, 7],
    ),
    ("build_tunnel", {"start_tile": 525}, ["build_tunnel", 525]),
    (
        "terraform_land",
        {"tile": 525, "mode": "raise", "corners": ["w", "n"]},
        ["terraform_land", 525, "raise", -1, "w", "n"],
    ),
    (
        "terraform_land",
        {"tile": 525, "mode": "lower", "corners": ["s"]},
        ["terraform_land", 525, "lower", -1, "s"],
    ),
    (
        "terraform_land",
        {"tile": 525, "mode": "level_to", "to_tile": 526},
        ["terraform_land", 525, "level_to", 526],
    ),
    # --- roads ---
    # Waypoints sit BETWEEN the endpoints: the bridge builds the point list
    # pairwise in command order (018 episode-005 model feedback,
    # harness:build_road_waypoint_order — trailing waypoints made it attempt
    # the direct blocked span first).
    (
        "build_road",
        {"from_tile": 10, "to_tile": 20, "waypoints": [15]},
        ["build_road", 10, 15, 20],
    ),
    (
        "build_road",
        {"from_tile": 10, "to_tile": 20, "waypoints": [12, 16]},
        ["build_road", 10, 12, 16, 20],
    ),
    ("build_road", {"from_tile": 10, "to_tile": 20}, ["build_road", 10, 20]),
    (
        "build_one_way_road",
        {"from_tile": 10, "to_tile": 20},
        ["build_one_way_road", 10, 20],
    ),
    ("remove_road", {"from_tile": 453, "to_tile": 456}, ["remove_road", 453, 456]),
    (
        "check_route_connectivity",
        {
            "town_id": 2,
            "depot_tile": 300,
            "depot_front_tile": 301,
            "stop_tiles": [100, 110],
        },
        ["check_route_connectivity", 2, 300, 301, 100, 110],
    ),
    # Depot builds carry no station-join suffix.
    (
        "build_road_depot",
        {"tile": 300, "front_tile": 301},
        ["build_road_depot", 300, 301],
    ),
    # --- stations, including every join mode ---
    (
        "build_bus_stop_drive_through",
        {"tile": 2608, "front_tile": 2607},
        ["build_bus_stop_drive_through", 2608, 2607],
    ),
    (
        "build_truck_stop_drive_through",
        {"tile": 2608, "front_tile": 2607},
        ["build_truck_stop_drive_through", 2608, 2607],
    ),
    (
        "build_bus_stop_drive_through",
        {"tile": 2608, "front_tile": 2607, "join": "new"},
        ["build_bus_stop_drive_through", 2608, 2607],
    ),
    (
        "build_bus_stop_drive_through",
        {"tile": 2608, "front_tile": 2607, "join": "adjacent"},
        ["build_bus_stop_drive_through", 2608, 2607, "adjacent"],
    ),
    (
        "build_bus_stop_drive_through",
        {"tile": 2608, "front_tile": 2607, "join": "station_id", "station_id": 4},
        ["build_bus_stop_drive_through", 2608, 2607, 4],
    ),
    (
        "build_truck_stop_drive_through",
        {"tile": 2608, "front_tile": 2607, "join": "adjacent"},
        ["build_truck_stop_drive_through", 2608, 2607, "adjacent"],
    ),
    (
        "build_truck_stop_drive_through",
        {"tile": 2608, "front_tile": 2607, "join": "station_id", "station_id": 4},
        ["build_truck_stop_drive_through", 2608, 2607, 4],
    ),
    (
        "build_bus_stop_bay",
        {"tile": 2608, "front_tile": 2607},
        ["build_bus_stop_bay", 2608, 2607],
    ),
    # Python must not invent an adjacency restriction: the script API uses any
    # distinct, axis-aligned front selector to derive entrance direction.
    (
        "build_bus_stop_bay",
        {"tile": 2608, "front_tile": 2600},
        ["build_bus_stop_bay", 2608, 2600],
    ),
    (
        "build_bus_stop_bay",
        {"tile": 2608, "front_tile": 2607, "join": "adjacent"},
        ["build_bus_stop_bay", 2608, 2607, "adjacent"],
    ),
    (
        "build_truck_stop_bay",
        {"tile": 2608, "front_tile": 2607, "join": "station_id", "station_id": 4},
        ["build_truck_stop_bay", 2608, 2607, 4],
    ),
    # None means unset for optional arguments (the _optional_* convention): a
    # JSON-null join defaults to "new" and a JSON-null station_id is absent,
    # not a forbidden companion argument.
    (
        "build_bus_stop_drive_through",
        {"tile": 2608, "front_tile": 2607, "join": None},
        ["build_bus_stop_drive_through", 2608, 2607],
    ),
    (
        "build_bus_stop_drive_through",
        {"tile": 2608, "front_tile": 2607, "join": "adjacent", "station_id": None},
        ["build_bus_stop_drive_through", 2608, 2607, "adjacent"],
    ),
    # --- vehicles ---
    ("refit_vehicle", {"vehicle_id": 7, "cargo_id": 8}, ["refit_vehicle", 7, 8]),
    ("reverse_vehicle", {"vehicle_id": 7}, ["reverse_vehicle", 7]),
    ("list_engines", {}, ["list_engines", 1, 0]),
    (
        "list_engines",
        {"only_buildable": False, "passenger_only": True},
        ["list_engines", 0, 1],
    ),
    (
        "clone_vehicle",
        {"depot_tile": 1128, "vehicle_id": 2, "share_orders": True},
        ["clone_vehicle", 1128, 2, 1],
    ),
    (
        "clone_vehicle",
        {"depot_tile": 1128, "vehicle_id": 2, "share_orders": False},
        ["clone_vehicle", 1128, 2, 0],
    ),
    # --- orders ---
    (
        "insert_order",
        {"vehicle_id": 7, "position": 1, "stop_tile": 1200},
        ["insert_order", 7, 1, 1200],
    ),
    (
        "insert_order",
        {
            "vehicle_id": 7,
            "position": 1,
            "stop_tile": 1200,
            "flags": ["FULL_LOAD", "NON_STOP"],
        },
        ["insert_order", 7, 1, 1200, 65],
    ),
    ("delete_order", {"vehicle_id": 7, "position": 0}, ["delete_order", 7, 0]),
    (
        "set_order_flags",
        {"vehicle_id": 7, "position": 0, "flags": ["FULL_LOAD"]},
        ["set_order_flags", 7, 0, 64],
    ),
    (
        "set_order_flags",
        {"vehicle_id": 7, "position": 0, "flags": []},
        ["set_order_flags", 7, 0, 0],
    ),
    # command[2] is a mode discriminator: 0 = flagless (bare stop tiles),
    # 1 = flagged (stop, flag_mask pairs). This keeps a flagless first stop
    # tile from ever aliasing onto the flagged pair-count form.
    (
        "append_orders",
        {"vehicle_id": 7, "stop_tiles": [100, 110]},
        ["append_orders", 7, 0, 100, 110],
    ),
    # A single flagless stop whose tile equals the old pair-count still decodes
    # as flagless under the mode discriminator.
    ("append_orders", {"vehicle_id": 7, "stop_tiles": [0]}, ["append_orders", 7, 0, 0]),
    (
        "append_orders",
        {"vehicle_id": 7, "stop_tiles": [100, 110], "flags": ["NON_STOP"]},
        ["append_orders", 7, 1, 100, 1, 110, 1],
    ),
    (
        "append_orders",
        {
            "vehicle_id": 7,
            "stop_tiles": [100, 110],
            "flags": [["FULL_LOAD"], ["NO_LOAD"]],
        },
        ["append_orders", 7, 1, 100, 64, 110, 128],
    ),
    (
        "insert_conditional_order",
        {
            "vehicle_id": 7,
            "position": 2,
            "condition": "RELIABILITY",
            "compare": "LESS_THAN",
            "value": 25,
            "jump_to": 0,
        },
        ["insert_conditional_order", 7, 2, 1, 2, 25, 0],
    ),
    (
        "insert_conditional_order",
        {
            "vehicle_id": 7,
            "position": 2,
            "condition": "REQUIRES_SERVICE",
            "compare": "IS_FALSE",
            "value": 0,
            "jump_to": 1,
        },
        ["insert_conditional_order", 7, 2, 4, 7, 0, 1],
    ),
    (
        "insert_conditional_order",
        {
            "vehicle_id": 7,
            "position": 2,
            "condition": "UNCONDITIONALLY",
            "compare": "EQUALS",
            "value": 0,
            "jump_to": 2,
        },
        ["insert_conditional_order", 7, 2, 5, 0, 0, 2],
    ),
    ("skip_to_order", {"vehicle_id": 7, "position": 1}, ["skip_to_order", 7, 1]),
    (
        "copy_orders",
        {"vehicle_id": 8, "source_vehicle_id": 7},
        ["copy_orders", 8, 7],
    ),
    (
        "share_orders",
        {"vehicle_id": 8, "source_vehicle_id": 7},
        ["share_orders", 8, 7],
    ),
    ("unshare_orders", {"vehicle_id": 8}, ["unshare_orders", 8]),
    # --- groups ---
    ("create_group", {"name": "Route A"}, ["create_group", "Route A"]),
    (
        "move_vehicle_to_group",
        {"vehicle_id": 7, "group_id": 3},
        ["move_vehicle_to_group", 7, 3],
    ),
    (
        "set_group_autoreplace",
        {"group_id": 3, "engine_id_old": 116, "engine_id_new": 118},
        ["set_group_autoreplace", 3, 116, 118],
    ),
)

TOOL_COMMAND_BAD_ARGUMENTS: tuple[tuple[str, dict], ...] = (
    # Argument-less sensors take no arguments at all.
    ("list_my_stations", {"station_id": 1}),
    ("list_my_depots", {"depot_tile": 1}),
    ("get_my_finances", {"interest_rate_percent": 2}),
    # Required arguments are required; ids are non-negative ints, and bools are
    # never accepted as ints.
    ("get_industry", {}),
    ("get_industry", {"industry_id": True}),
    ("get_industry", {"industry_id": -1}),
    ("get_town", {}),
    ("get_town", {"town_id": True}),
    ("get_town", {"town_id": "4"}),
    ("get_town", {"town_id": -1}),
    ("check_truck_stop_catchment", {}),
    ("check_truck_stop_catchment", {"tile": "2398"}),
    ("check_truck_stop_catchment", {"tile": True}),
    ("check_truck_stop_catchment", {"tile": -1}),
    # get_my_station takes exactly one identifier, and negative identifiers
    # would collide with the absent-slot sentinel (-1).
    ("get_my_station", {}),
    ("get_my_station", {"station_id": 2, "tile": 2608}),
    ("get_my_station", {"station_id": "2"}),
    ("get_my_station", {"tile": -1}),
    ("get_my_station", {"station_id": -3}),
    ("list_bridge_types", {"length": True}),
    ("list_bridge_types", {"length": 1}),
    ("build_bridge", {"start_tile": 100, "end_tile": 105}),
    ("build_bridge", {"start_tile": 100, "end_tile": 105, "bridge_id": True}),
    ("build_tunnel", {}),
    ("build_tunnel", {"start_tile": True}),
    ("terraform_land", {"tile": 525, "mode": "raise"}),
    ("terraform_land", {"tile": 525, "mode": "raise", "corners": ["nw"]}),
    ("terraform_land", {"tile": 525, "mode": "raise", "corners": ["w", "w"]}),
    ("terraform_land", {"tile": 525, "mode": "level_to"}),
    ("terraform_land", {"tile": 525, "mode": "flatten", "corners": ["w"]}),
    # Cross-mode arguments are rejected, never silently dropped, so the result
    # cannot echo a substituted sentinel that contradicts the call.
    (
        "terraform_land",
        {"tile": 525, "mode": "level_to", "to_tile": 526, "corners": ["s"]},
    ),
    (
        "terraform_land",
        {"tile": 525, "mode": "raise", "corners": ["w"], "to_tile": 530},
    ),
    (
        "terraform_land",
        {"tile": 525, "mode": "lower", "corners": ["s"], "to_tile": 530},
    ),
    ("check_build_road", {"from_tile": 10, "to_tile": 20, "waypoints": "15"}),
    ("check_build_road", {"from_tile": 10, "to_tile": 20, "waypoints": [True]}),
    ("check_build_road_depot", {"tile": 300}),
    ("check_build_road_depot", {"tile": 300, "front_tile": True}),
    ("build_one_way_road", {}),
    ("build_one_way_road", {"from_tile": 10}),
    ("build_one_way_road", {"from_tile": True, "to_tile": 20}),
    ("build_one_way_road", {"from_tile": 10, "to_tile": "20"}),
    ("build_one_way_road", {"from_tile": -1, "to_tile": 20}),
    ("remove_road", {"from_tile": 453}),
    ("remove_road", {"from_tile": 453, "to_tile": "456"}),
    ("build_truck_stop_drive_through", {"tile": 2608}),
    ("build_truck_stop_drive_through", {"tile": True, "front_tile": 2607}),
    ("refit_vehicle", {"vehicle_id": 7}),
    ("refit_vehicle", {"vehicle_id": True, "cargo_id": 8}),
    ("refit_vehicle", {"vehicle_id": 7, "cargo_id": -1}),
    ("refit_vehicle", {"vehicle_id": 7, "cargo_id": 64}),
    ("list_engines", {"only_buildable": 1}),
    ("list_engines", {"passenger_only": "true"}),
    ("clone_vehicle", {"depot_tile": 1128, "vehicle_id": 2}),
    ("clone_vehicle", {"depot_tile": 1128, "vehicle_id": 2, "share_orders": 1}),
    ("clone_vehicle", {"depot_tile": 1128, "vehicle_id": True, "share_orders": False}),
    ("insert_order", {"vehicle_id": 7, "stop_tile": 1200}),
    ("insert_order", {"vehicle_id": 7, "position": 1}),
    ("insert_order", {"vehicle_id": True, "position": 1, "stop_tile": 1200}),
    ("insert_order", {"vehicle_id": 7, "position": "1", "stop_tile": 1200}),
    ("insert_order", {"vehicle_id": 7, "position": 1, "stop_tile": False}),
    (
        "insert_order",
        {"vehicle_id": 7, "position": 1, "stop_tile": 1200, "flags": "FULL_LOAD"},
    ),
    (
        "insert_order",
        {"vehicle_id": 7, "position": 1, "stop_tile": 1200, "flags": ["unknown"]},
    ),
    (
        "insert_order",
        {
            "vehicle_id": 7,
            "position": 1,
            "stop_tile": 1200,
            "flags": ["FULL_LOAD", "NO_LOAD"],
        },
    ),
    ("delete_order", {"vehicle_id": 7}),
    ("delete_order", {"vehicle_id": True, "position": 0}),
    ("delete_order", {"vehicle_id": 7, "position": "0"}),
    ("set_order_flags", {"vehicle_id": 7, "position": 0}),
    ("set_order_flags", {"vehicle_id": 7, "position": 0, "flags": None}),
    ("set_order_flags", {"vehicle_id": 7, "position": 0, "flags": "FULL_LOAD"}),
    (
        "set_order_flags",
        {"vehicle_id": 7, "position": 0, "flags": ["FULL_LOAD", "FULL_LOAD_ANY"]},
    ),
    (
        "append_orders",
        {"vehicle_id": 7, "stop_tiles": [100], "flags": [["FULL_LOAD"], ["NO_LOAD"]]},
    ),
    ("append_orders", {"vehicle_id": 7, "stop_tiles": [100], "flags": [["unknown"]]}),
    ("insert_conditional_order", {}),
    (
        "insert_conditional_order",
        {
            "vehicle_id": True,
            "position": 0,
            "condition": "RELIABILITY",
            "compare": "EQUALS",
            "value": 50,
            "jump_to": 0,
        },
    ),
    (
        "insert_conditional_order",
        {
            "vehicle_id": 7,
            "position": -1,
            "condition": "MAX_RELIABILITY",
            "compare": "EQUALS",
            "value": 50,
            "jump_to": 0,
        },
    ),
    (
        "insert_conditional_order",
        {
            "vehicle_id": 7,
            "position": 0,
            "condition": "RELIABILITY",
            "compare": "IS_TRUE",
            "value": 50,
            "jump_to": 0,
        },
    ),
    (
        "insert_conditional_order",
        {
            "vehicle_id": 7,
            "position": 0,
            "condition": "REQUIRES_SERVICE",
            "compare": "IS_TRUE",
            "value": 1,
            "jump_to": 0,
        },
    ),
    (
        "insert_conditional_order",
        {
            "vehicle_id": 7,
            "position": 0,
            "condition": "UNCONDITIONALLY",
            "compare": "NOT_EQUALS",
            "value": 0,
            "jump_to": 0,
        },
    ),
    (
        "insert_conditional_order",
        {
            "vehicle_id": 7,
            "position": 0,
            "condition": "LOAD_PERCENTAGE",
            "compare": "MORE_EQUALS",
            "value": 101,
            "jump_to": 0,
        },
    ),
    ("skip_to_order", {"vehicle_id": 7}),
    ("skip_to_order", {"vehicle_id": 7, "position": -1}),
    ("copy_orders", {"vehicle_id": 8}),
    ("copy_orders", {"vehicle_id": 8, "source_vehicle_id": True}),
    ("share_orders", {"vehicle_id": -1, "source_vehicle_id": 7}),
    ("unshare_orders", {"vehicle_id": -1}),
    ("reverse_vehicle", {}),
    ("reverse_vehicle", {"vehicle_id": True}),
    ("reverse_vehicle", {"vehicle_id": "7"}),
    ("reverse_vehicle", {"vehicle_id": -1}),
    ("create_group", {}),
    ("create_group", {"name": ""}),
    ("create_group", {"name": 3}),
    ("move_vehicle_to_group", {"vehicle_id": 7}),
    ("move_vehicle_to_group", {"vehicle_id": True, "group_id": 3}),
    ("move_vehicle_to_group", {"vehicle_id": 7, "group_id": "3"}),
    ("set_group_autoreplace", {"group_id": 3, "engine_id_old": 116}),
    (
        "set_group_autoreplace",
        {"group_id": False, "engine_id_old": 116, "engine_id_new": 118},
    ),
    (
        "set_group_autoreplace",
        {"group_id": 3, "engine_id_old": "116", "engine_id_new": 118},
    ),
    (
        "set_group_autoreplace",
        {"group_id": 3, "engine_id_old": 116, "engine_id_new": True},
    ),
)


class CommandForToolMappingTests(unittest.TestCase):
    """The table-driven half of the command_for_tool contract."""

    def test_tool_arguments_map_to_their_flat_bridge_commands(self):
        for tool, arguments, expected in TOOL_COMMAND_MAPPINGS:
            with self.subTest(tool=tool, arguments=arguments):
                self.assertEqual(command_for_tool(tool, dict(arguments)), expected)

    def test_bad_tool_arguments_are_rejected(self):
        for tool, arguments in TOOL_COMMAND_BAD_ARGUMENTS:
            with (
                self.subTest(tool=tool, arguments=arguments),
                self.assertRaises(ValueError),
            ):
                command_for_tool(tool, dict(arguments))


class GameMCPServerTests(TempDirTestCase):
    def test_registry_contains_well_formed_primitive_and_observation_schemas(self):
        tools = tools_by_name(enable_skill=True)
        self.assertIn("build_road", tools)
        self.assertIn("check_route_connectivity", tools)
        self.assertIn("wait_and_observe_vehicle", tools)
        self.assertIn("get_bus_route_skill", tools)
        for tool in tools.values():
            schema = tool["inputSchema"]
            self.assertEqual(schema["type"], "object")
            self.assertIn("properties", schema)
            self.assertIn("required", schema)

    def test_experiment_label_is_wired_not_hardcoded(self):
        """018 episode-002 defect (harness:experiment_label_stale_016): the
        registry snapshot and evidence timestamps must carry the run's real
        initiative; only the default stays at the historical 016 label."""
        self.assertEqual(
            registry_snapshot()["experiment"], "016-native-tool-skill-library"
        )
        self.assertEqual(
            registry_snapshot(experiment="018-profit-frontier")["experiment"],
            "018-profit-frontier",
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            timestamps: list[str] = []

            def fake(command: list, timestamp: str) -> dict:
                timestamps.append(timestamp)
                return {"ok": True, "action": command[0]}

            server = make_tool_server(
                tmp_path,
                fake,
                registry_path=tmp_path / "tool_registry.json",
                experiment="018-profit-frontier",
            )
            server.handle_tool_call("list_towns", {})
            registry = json.loads((tmp_path / "tool_registry.json").read_text())
            self.assertEqual(registry["experiment"], "018-profit-frontier")
            self.assertTrue(timestamps[0].startswith("018-profit-frontier-"))
            record = read_jsonl(tmp_path / "tool_calls.jsonl")[0]
            self.assertEqual(record["timestamp"], timestamps[0])
            pytest.importorskip("mcp", reason="mcp extra (FastMCP) not installed")
            fastmcp = build_fastmcp(server)
            self.assertEqual(fastmcp.name, "openttd-018-profit-frontier")

    def test_fastmcp_serves_no_instructions(self):
        """030: codex code mode prepends a non-empty initialize.instructions
        string to every ALL_TOOLS description (50 catalog copies in the client
        registry) and shows it nowhere else. The served surface must stay
        empty; the generated tool index travels in the episode prompt."""
        pytest.importorskip("mcp", reason="mcp extra (FastMCP) not installed")
        with tempfile.TemporaryDirectory() as tmp:
            server = make_tool_server(
                tmp,
                experiment="030-instructions-channel",
                end_episode_mode="distress",
                vision_enabled=False,
            )
            fastmcp = build_fastmcp(server)

        self.assertFalse(fastmcp.instructions)

    def test_generated_index_matches_actually_registered_tools(self):
        """The index (derived from tool_specs) and the hand-maintained
        @mcp.tool decorator block are two enumerations of the same surface.
        A tool_specs entry without a decorator would be advertised in the
        always-visible index but fail as 'unknown tool' when called — the
        029 exposure gap inverted — so pin index lines == live registrations."""
        with tempfile.TemporaryDirectory() as tmp:
            server = make_tool_server(tmp)
            registered = sorted(fastmcp_tools_by_name(server))

        index = generate_server_instructions()
        indexed = sorted(line.split(": ", 1)[0] for line in index.splitlines()[1:])
        self.assertEqual(indexed, registered)

    def test_every_evidence_record_is_timestamped(self):
        """018 episode-003 defect (harness:end_episode_timestamp_null): control
        tools (end_episode, report_feedback) and exhausted refusals must stamp
        their tool_calls.jsonl records like bridge-dispatched tools do."""
        tmp_path = self.tmp_path
        server = make_tool_server(
            tmp_path,
            lambda command, timestamp: {"ok": True, "action": command[0]},
            feedback_log=tmp_path / "feedback.jsonl",
            experiment="018-profit-frontier",
            decision_budget=2,
        )
        server.handle_tool_call("list_towns", {})
        server.handle_tool_call(
            "end_episode", {"reason": "done playing, verifying evidence stamps"}
        )
        server.handle_tool_call("list_towns", {})  # exhausted refusal path
        server.handle_tool_call(
            "report_feedback",
            feedback_args(
                kind="strategic_note",
                description="d",
                why_it_matters="w",
                current_blocker="c",
            ),
        )
        records = read_jsonl(tmp_path / "tool_calls.jsonl")
        self.assertEqual(len(records), 4)
        for record in records:
            self.assertTrue(
                str(record.get("timestamp", "")).startswith("018-profit-frontier-"),
                f"unstamped record: {record.get('name')}",
            )

    def test_every_evidence_record_retains_monotonic_duration_and_honest_status(self):
        """A timeout, failure, or exhausted refusal must not look successful in timing evidence."""

        def dispatch(command: list, timestamp: str) -> dict:
            if command[0] in {"wait", "get_my_company"}:
                raise TimeoutError("bridge did not confirm the wait")
            return {"ok": True, "action": command[0]}

        server = make_tool_server(self.tmp_path, dispatch, decision_budget=4)
        results = [
            server.handle_tool_call("list_towns", {}),
            server.handle_tool_call("wait", {"duration_days": 1}),
            server.handle_tool_call("get_my_company", {}),
            server.handle_tool_call("not_a_tool", {}),
            server.handle_tool_call("list_towns", {}),
        ]

        records = read_jsonl(self.tmp_path / "tool_calls.jsonl")
        self.assertEqual(
            [record["timing_status"] for record in records],
            ["success", "timeout", "timeout", "failure", "skipped"],
        )
        for result, record in zip(results, records, strict=True):
            self.assertEqual(record["result"], result)
            self.assertIsInstance(record["started_at_monotonic_ns"], int)
            self.assertIsInstance(record["finished_at_monotonic_ns"], int)
            self.assertEqual(
                record["duration_ns"],
                record["finished_at_monotonic_ns"] - record["started_at_monotonic_ns"],
            )
            self.assertGreaterEqual(record["duration_ns"], 0)

    def test_budget_override_and_interrupted_dispatch_have_distinct_timing_statuses(
        self,
    ) -> None:
        """A monitor cut and an unhandled interruption must not become successes."""
        override = self.tmp_path / "budget_override.json"
        override.write_text('{"reason":"monitor"}')

        def dispatch_after_cut(command: list, timestamp: str) -> dict:
            self.fail("a budget cut must refuse before transport dispatch")

        server = make_tool_server(self.tmp_path, dispatch_after_cut)

        stopped = server.handle_tool_call("list_towns", {})

        self.assertEqual(stopped["error"], EPISODE_STOP_SIGNAL)
        records = read_jsonl(self.tmp_path / "tool_calls.jsonl")
        self.assertEqual(
            [record["timing_status"] for record in records], ["skipped", "skipped"]
        )

        interrupted_root = self.tmp_path / "interrupted"

        def interrupt(command: list, timestamp: str) -> dict:
            raise KeyboardInterrupt

        interrupted = make_tool_server(interrupted_root, interrupt)
        with self.assertRaises(KeyboardInterrupt):
            interrupted.handle_tool_call("list_towns", {})
        [record] = read_jsonl(interrupted_root / "tool_calls.jsonl")
        self.assertEqual(record["timing_status"], "unmatched_interruption")

    def test_skill_tool_is_omitted_when_disabled(self):
        self.assertNotIn(
            "get_bus_route_skill",
            {spec.name for spec in tool_specs(enable_skill=False)},
        )
        self.assertIn(
            "get_bus_route_skill", {spec.name for spec in tool_specs(enable_skill=True)}
        )

    def test_registry_contains_018_prep_tools(self):
        tools = tools_by_name(enable_skill=False)
        self.assertTrue(tools["remove_road"]["mutates_game"])
        self.assertEqual(
            tools["remove_road"]["inputSchema"]["required"], ["from_tile", "to_tile"]
        )
        feedback = tools["report_feedback"]
        self.assertFalse(feedback["mutates_game"])
        self.assertEqual(
            feedback["inputSchema"]["required"],
            ["kind", "description", "why_it_matters", "current_blocker"],
        )

    def test_registry_contains_separate_one_way_road_action(self):
        """107: directional road construction is its own two-endpoint action,
        not a build_road mode that inherits waypoint or corner-fallback semantics."""
        tools = tools_by_name(enable_skill=False)
        spec = tools["build_one_way_road"]

        self.assertTrue(spec["mutates_game"])
        self.assertEqual(spec["inputSchema"]["required"], ["from_tile", "to_tile"])
        self.assertEqual(
            spec["inputSchema"]["properties"],
            {
                "from_tile": {"type": "integer", "minimum": 0},
                "to_tile": {"type": "integer", "minimum": 0},
            },
        )
        self.assertIn("waypoints", tools["build_road"]["inputSchema"]["properties"])
        self.assertNotIn("waypoints", spec["inputSchema"]["properties"])

    def test_fastmcp_report_feedback_schema_exposes_allowed_kinds(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = make_tool_server(tmp)
            tools = fastmcp_tools_by_name(server)

        self.assertEqual(
            tools["report_feedback"].inputSchema["properties"]["kind"]["enum"],
            list(FEEDBACK_KINDS),
        )

    def test_fastmcp_station_build_schema_exposes_join_enum(self):
        """The FastMCP lane must advertise the same join enum as the registry
        schema (StationJoinMode Literal, one-constant-many-consumers): an
        out-of-enum join is rejected at schema level, not burned at runtime."""
        with tempfile.TemporaryDirectory() as tmp:
            server = make_tool_server(tmp)
            tools = fastmcp_tools_by_name(server)

        for name in (
            "build_bus_stop_drive_through",
            "build_truck_stop_drive_through",
            "build_bus_stop_bay",
            "build_truck_stop_bay",
        ):
            self.assertEqual(
                tools[name].inputSchema["properties"]["join"]["enum"],
                ["new", "adjacent", "station_id"],
            )

    def test_served_fastmcp_schemas_carry_the_registry_bounds_and_enums(self):
        """Concrete registry/served constraint values (039).

        FastMCP generates the served schema from the wrapper's annotations
        alone, so a bare ``int``/``str`` silently drops every bound and enum the
        registry ToolSpec schema declares and the model is handed a
        weaker-than-recorded surface. This pins the intended values for the
        historically constrained tools; the whole-surface parity gate lives in
        tests/test_server_wrappers.py.
        """
        expected: dict[str, dict[str, dict[str, object]]] = {
            "get_region": {
                # One served bound for two modes: the widest (grid) maximum,
                # with the tiles maximum enforced by name in command_for_tool.
                "radius": {"minimum": 0, "maximum": QUERY_REGION_RADIUS_MAX_GRID},
                "detail": {"enum": ["tiles", "grid"]},
                "cargo_id": {"minimum": 0, "maximum": 63},
            },
            "get_tile": {"cargo_id": {"minimum": 0, "maximum": 63}},
            "wait": {"duration_days": {"minimum": 1, "maximum": 90}},
            "wait_and_observe_vehicle": {"advance_days": {"minimum": 1, "maximum": 90}},
            "get_cargo_payment_rate": {
                "cargo_id": {"minimum": 0, "maximum": 63},
                "distance": {"minimum": 0, "maximum": 4_294_967_295},
                "days_in_transit": {"minimum": 0, "maximum": 163_838},
            },
            "refit_vehicle": {"cargo_id": {"minimum": 0, "maximum": 63}},
            "list_bridge_types": {"length": {"minimum": 2}},
            "terraform_land": {"mode": {"enum": ["raise", "lower", "level_to"]}},
        }
        with tempfile.TemporaryDirectory() as tmp:
            server = make_tool_server(tmp)
            served = fastmcp_tools_by_name(server)
        registry = tools_by_name()

        # Every registered FastMCP tool has a served schema at all.
        for name, tool in served.items():
            self.assertIsInstance(tool.inputSchema.get("properties"), dict, name)

        def constraint_value(schema: dict, key: str) -> object:
            if key in schema:
                return schema[key]
            for arm in schema.get("anyOf", []):
                if isinstance(arm, dict) and key in arm:
                    return arm[key]
            return None

        for tool_name, arguments in expected.items():
            for argument, constraints in arguments.items():
                served_schema = served[tool_name].inputSchema["properties"][argument]
                registry_schema = registry[tool_name]["inputSchema"]["properties"][
                    argument
                ]
                for key, value in constraints.items():
                    with self.subTest(
                        tool=tool_name, argument=argument, constraint=key
                    ):
                        self.assertEqual(constraint_value(served_schema, key), value)
                        self.assertEqual(constraint_value(registry_schema, key), value)

        # Order-flag enums: the flags argument is a (possibly nested, possibly
        # nullable) array, so the enum lives on the items schemas, not the
        # property itself. Every array arm of every flags shape must carry the
        # full uppercase vocabulary on BOTH the served and registry sides.
        expected_flags = list(get_args(OrderFlagName))
        self.assertEqual(sorted(expected_flags), sorted(ORDER_FLAG_BITS))

        def collect_item_enums(schema: dict) -> list[list[str]]:
            enums: list[list[str]] = []
            for arm in schema.get("anyOf", [schema]):
                items = arm.get("items")
                while isinstance(items, dict):
                    if "enum" in items:
                        enums.append(items["enum"])
                        break
                    items = items.get("items")
            return enums

        flags_arms = {"insert_order": 1, "set_order_flags": 1, "append_orders": 2}
        for tool_name, arm_count in flags_arms.items():
            for side, schema in (
                ("served", served[tool_name].inputSchema["properties"]["flags"]),
                ("registry", registry[tool_name]["inputSchema"]["properties"]["flags"]),
            ):
                with self.subTest(tool=tool_name, side=side):
                    enums = collect_item_enums(schema)
                    self.assertEqual(len(enums), arm_count, schema)
                    for enum in enums:
                        self.assertEqual(sorted(enum), sorted(expected_flags))

    def test_registry_contains_separate_bus_and_truck_bay_station_actions(self):
        """021 bay/terminus unit: non-drive-through topology is explicit and
        does not overload either shipped drive-through tool."""
        tools = tools_by_name(enable_skill=False)

        for name in ("build_bus_stop_bay", "build_truck_stop_bay"):
            spec = tools[name]
            self.assertTrue(spec["mutates_game"])
            self.assertEqual(spec["inputSchema"]["required"], ["tile", "front_tile"])
            self.assertEqual(
                spec["inputSchema"]["properties"]["join"]["enum"],
                ["new", "adjacent", "station_id"],
            )

    def test_registry_contains_list_engines_sensor(self):
        """021 Unit 1: honest engine catalog with factual predicate filters."""
        tools = tools_by_name(enable_skill=False)
        spec = tools["list_engines"]
        self.assertFalse(spec["mutates_game"])
        self.assertEqual(spec["inputSchema"]["required"], [])
        self.assertEqual(
            spec["inputSchema"]["properties"]["only_buildable"],
            {"type": "boolean", "default": True},
        )
        self.assertEqual(
            spec["inputSchema"]["properties"]["passenger_only"],
            {"type": "boolean", "default": False},
        )

    def test_registry_contains_clone_vehicle_action(self):
        """021 primitive backlog: thin CmdCloneVehicle/AIVehicle.CloneVehicle wrapper."""
        tools = tools_by_name(enable_skill=False)
        spec = tools["clone_vehicle"]

        self.assertTrue(spec["mutates_game"])
        self.assertEqual(
            spec["inputSchema"]["required"],
            ["depot_tile", "vehicle_id", "share_orders"],
        )
        self.assertEqual(
            spec["inputSchema"]["properties"]["depot_tile"], {"type": "integer"}
        )
        self.assertEqual(
            spec["inputSchema"]["properties"]["vehicle_id"], {"type": "integer"}
        )
        self.assertEqual(
            spec["inputSchema"]["properties"]["share_orders"], {"type": "boolean"}
        )

    def test_registry_contains_reverse_vehicle_action(self):
        """106: the result must not be readable as a completed turn, and the
        description must disclose that the game names no specific reason for
        its stopped/in-depot refusals (both come back as ERR_UNKNOWN,
        live-probed on 15.1)."""
        tools = tools_by_name()
        spec = tools["reverse_vehicle"]
        self.assertTrue(spec["mutates_game"])
        schema = spec["inputSchema"]
        self.assertEqual(set(schema["properties"]), {"vehicle_id"})
        self.assertEqual(schema["required"], ["vehicle_id"])
        self.assertEqual(
            schema["properties"]["vehicle_id"],
            {"type": "integer", "minimum": 0},
        )

    def test_registry_contains_order_surgery_actions(self):
        """021 Lane A1: thin CmdInsertOrder/CmdDeleteOrder wrappers for order surgery."""
        tools = tools_by_name(enable_skill=False)

        insert_spec = tools["insert_order"]
        self.assertTrue(insert_spec["mutates_game"])
        self.assertEqual(
            insert_spec["inputSchema"]["required"],
            ["vehicle_id", "position", "stop_tile"],
        )
        self.assertEqual(
            insert_spec["inputSchema"]["properties"]["vehicle_id"], {"type": "integer"}
        )
        self.assertEqual(
            insert_spec["inputSchema"]["properties"]["position"], {"type": "integer"}
        )
        self.assertEqual(
            insert_spec["inputSchema"]["properties"]["stop_tile"], {"type": "integer"}
        )
        self.assertIn("flags", insert_spec["inputSchema"]["properties"])

        delete_spec = tools["delete_order"]
        self.assertTrue(delete_spec["mutates_game"])
        self.assertEqual(
            delete_spec["inputSchema"]["required"], ["vehicle_id", "position"]
        )
        self.assertEqual(
            delete_spec["inputSchema"]["properties"]["vehicle_id"], {"type": "integer"}
        )
        self.assertEqual(
            delete_spec["inputSchema"]["properties"]["position"], {"type": "integer"}
        )

    def test_registry_contains_order_flag_action_and_compatible_append_orders_flags(
        self,
    ):
        """021 Lane A2: order flags are optional on order creation and mutable later."""
        tools = tools_by_name(enable_skill=False)
        flag_enum = ["FULL_LOAD", "FULL_LOAD_ANY", "NO_LOAD", "NON_STOP"]

        append_orders = tools["append_orders"]
        self.assertTrue(append_orders["mutates_game"])
        self.assertEqual(
            append_orders["inputSchema"]["required"], ["vehicle_id", "stop_tiles"]
        )
        self.assertIn("flags", append_orders["inputSchema"]["properties"])
        self.assertEqual(
            append_orders["inputSchema"]["properties"]["flags"]["anyOf"][0]["items"][
                "enum"
            ],
            flag_enum,
        )

        spec = tools["set_order_flags"]
        self.assertTrue(spec["mutates_game"])
        self.assertEqual(
            spec["inputSchema"]["required"], ["vehicle_id", "position", "flags"]
        )
        self.assertEqual(
            spec["inputSchema"]["properties"]["vehicle_id"], {"type": "integer"}
        )
        self.assertEqual(
            spec["inputSchema"]["properties"]["position"], {"type": "integer"}
        )
        self.assertEqual(
            spec["inputSchema"]["properties"]["flags"]["items"]["enum"], flag_enum
        )

    def test_registry_contains_holdable_order_control_actions(self):
        """105: one configured conditional tool plus four explicit list controls."""
        self.assertEqual(
            CONDITIONAL_ORDER_CONDITIONS,
            {
                "LOAD_PERCENTAGE": 0,
                "RELIABILITY": 1,
                "MAX_SPEED": 2,
                "AGE": 3,
                "REQUIRES_SERVICE": 4,
                "UNCONDITIONALLY": 5,
                "REMAINING_LIFETIME": 6,
            },
        )
        self.assertEqual(
            CONDITIONAL_ORDER_COMPARES,
            {
                "EQUALS": 0,
                "NOT_EQUALS": 1,
                "LESS_THAN": 2,
                "LESS_EQUALS": 3,
                "MORE_THAN": 4,
                "MORE_EQUALS": 5,
                "IS_TRUE": 6,
                "IS_FALSE": 7,
            },
        )
        tools = tools_by_name(enable_skill=False)
        conditional = tools["insert_conditional_order"]
        self.assertTrue(conditional["mutates_game"])
        self.assertEqual(
            conditional["inputSchema"]["required"],
            ["vehicle_id", "position", "condition", "compare", "value", "jump_to"],
        )
        self.assertEqual(
            conditional["inputSchema"]["properties"]["condition"]["enum"],
            list(CONDITIONAL_ORDER_CONDITIONS),
        )
        self.assertEqual(
            conditional["inputSchema"]["properties"]["compare"]["enum"],
            list(CONDITIONAL_ORDER_COMPARES),
        )
        self.assertNotIn(
            "MAX_RELIABILITY",
            conditional["inputSchema"]["properties"]["condition"]["enum"],
        )
        self.assertEqual(
            conditional["inputSchema"]["properties"]["value"],
            {"type": "integer", "minimum": 0, "maximum": 2047},
        )

        expected_required = {
            "skip_to_order": ["vehicle_id", "position"],
            "copy_orders": ["vehicle_id", "source_vehicle_id"],
            "share_orders": ["vehicle_id", "source_vehicle_id"],
            "unshare_orders": ["vehicle_id"],
        }
        for name, required in expected_required.items():
            with self.subTest(name=name):
                self.assertTrue(tools[name]["mutates_game"])
                self.assertEqual(tools[name]["inputSchema"]["required"], required)

    def test_registry_describes_total_vehicle_state_fields(self):
        """021 Lane A3: list_my_vehicles always exposes raw and named vehicle state."""
        tools = tools_by_name(enable_skill=False)
        spec = tools["list_my_vehicles"]

        self.assertFalse(spec["mutates_game"])
        self.assertEqual(
            spec["inputSchema"],
            {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        )

    def test_vehicle_station_and_depot_lists_own_distinct_intents(self):
        """039 D16 / 042 Unit 3: the sibling inventory sensors are read-only
        and take no arguments."""
        tools = tools_by_name(enable_skill=False)

        for spec in (tools["list_my_stations"], tools["list_my_depots"]):
            self.assertFalse(spec["mutates_game"])
            self.assertEqual(
                spec["inputSchema"],
                {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
            )

    def test_registry_contains_group_and_autoreplace_actions(self):
        """021 Lane A4: model-facing group/autoreplace primitives expose API-backed schemas."""
        tools = tools_by_name(enable_skill=False)

        create = tools["create_group"]
        self.assertTrue(create["mutates_game"])
        self.assertEqual(create["inputSchema"]["required"], ["name"])
        self.assertEqual(
            create["inputSchema"]["properties"]["name"], {"type": "string"}
        )

        assign = tools["move_vehicle_to_group"]
        self.assertTrue(assign["mutates_game"])
        self.assertEqual(assign["inputSchema"]["required"], ["vehicle_id", "group_id"])
        self.assertEqual(
            assign["inputSchema"]["properties"]["vehicle_id"], {"type": "integer"}
        )
        self.assertEqual(
            assign["inputSchema"]["properties"]["group_id"], {"type": "integer"}
        )

        autoreplace = tools["set_group_autoreplace"]
        self.assertTrue(autoreplace["mutates_game"])
        self.assertEqual(
            autoreplace["inputSchema"]["required"],
            ["group_id", "engine_id_old", "engine_id_new"],
        )
        for prop in ("group_id", "engine_id_old", "engine_id_new"):
            self.assertEqual(
                autoreplace["inputSchema"]["properties"][prop], {"type": "integer"}
            )

    def test_registry_contains_compact_query_region_options(self):
        """021 Unit A/C4: compact get_region plus optional arbitrary-cargo facts."""
        tools = tools_by_name(enable_skill=False)
        spec = tools["get_region"]

        self.assertFalse(spec["mutates_game"])
        self.assertEqual(spec["inputSchema"]["required"], ["center_tile", "radius"])
        self.assertEqual(
            spec["inputSchema"]["properties"]["cargo_id"]["type"], "integer"
        )
        self.assertEqual(
            {
                key: spec["inputSchema"]["properties"]["cargo_id"][key]
                for key in ("minimum", "maximum")
            },
            {"minimum": 0, "maximum": 63},
        )
        self.assertEqual(
            spec["inputSchema"]["properties"]["detail"]["enum"], ["tiles", "grid"]
        )
        self.assertIn("fields", spec["inputSchema"]["properties"])
        self.assertIn("where", spec["inputSchema"]["properties"])

    def test_registry_documents_query_tile_cargo_probe(self):
        """021 Lane C4: get_tile exposes the same optional cargo probe as get_region."""
        tools = tools_by_name(enable_skill=False)
        spec = tools["get_tile"]

        self.assertFalse(spec["mutates_game"])
        self.assertEqual(spec["inputSchema"]["required"], ["tile"])
        self.assertEqual(spec["inputSchema"]["properties"]["tile"], {"type": "integer"})
        self.assertEqual(
            spec["inputSchema"]["properties"]["cargo_id"],
            {"type": "integer", "minimum": 0, "maximum": 63},
        )

    def test_query_region_radius_out_of_range_is_rejected_not_clamped(self):
        """An out-of-range radius surveys a different region than the one asked
        for if it is clamped, and never says so — the same silent-clamp defect
        as `wait` (tests/test_wait_timeout.py::test_out_of_range_duration_is_rejected_not_clamped).
        Both per-mode ranges are enforced as rejections naming that mode's
        maximum, and 042 Unit 6 removed the bridge's own clamp."""
        dispatched: list[list] = []

        def record(command: list, timestamp: str) -> dict:
            dispatched.append(command)
            return {"ok": True, "action": command[0]}

        cases = [
            (
                "tiles",
                QUERY_REGION_RADIUS_MAX_TILES,
                (-1, -5, QUERY_REGION_RADIUS_MAX_TILES + 1, 99),
            ),
            (
                "grid",
                QUERY_REGION_RADIUS_MAX_GRID,
                (-1, -5, QUERY_REGION_RADIUS_MAX_GRID + 1, 99),
            ),
        ]
        for detail, maximum, bad_radii in cases:
            for bad in bad_radii:
                with self.subTest(detail=detail, radius=bad):
                    arguments = {"center_tile": 2735, "radius": bad, "detail": detail}
                    with self.assertRaisesRegex(ValueError, "radius"):
                        command_for_tool("get_region", arguments)
                    with tempfile.TemporaryDirectory() as tmp:
                        server = make_tool_server(tmp, record)
                        result = server.handle_tool_call("get_region", dict(arguments))
                    self.assertFalse(result["ok"])
                    self.assertIn(f"between 0 and {maximum}", result["error"])
                    # The rejection names the mode whose bound was exceeded.
                    self.assertIn(f"detail='{detail}'", result["error"])
        # Nothing out of range reached the bridge, so nothing was clamped.
        self.assertEqual(dispatched, [])
        # Every bound stays callable, and the default mode is tiles.
        for detail, maximum in (
            ("tiles", QUERY_REGION_RADIUS_MAX_TILES),
            ("grid", QUERY_REGION_RADIUS_MAX_GRID),
        ):
            for radius in (0, maximum):
                self.assertEqual(
                    command_for_tool(
                        "get_region",
                        {"center_tile": 2735, "radius": radius, "detail": detail},
                    ),
                    ["get_region", 2735, radius],
                )
        self.assertEqual(
            command_for_tool(
                "get_region",
                {"center_tile": 2735, "radius": QUERY_REGION_RADIUS_MAX_TILES},
            ),
            ["get_region", 2735, QUERY_REGION_RADIUS_MAX_TILES],
        )
        with self.assertRaisesRegex(ValueError, "radius"):
            command_for_tool(
                "get_region",
                {"center_tile": 2735, "radius": QUERY_REGION_RADIUS_MAX_TILES + 1},
            )

    def test_query_region_radius_maxima_are_stated_per_mode_on_the_served_surface(self):
        """The served schema can carry only one maximum, so the wider (grid)
        bound is what pydantic enforces and the per-mode maxima have to be
        readable from the model-visible text — otherwise a tiles caller reads
        the grid bound as its own."""
        self.assertEqual(set(QUERY_REGION_RADIUS_MAX_BY_DETAIL), set(DETAIL_VALUES))
        radius_schema = tools_by_name()["get_region"]["inputSchema"]["properties"][
            "radius"
        ]
        self.assertEqual(radius_schema["maximum"], QUERY_REGION_RADIUS_MAX_GRID)

    def test_query_tile_and_region_append_optional_cargo_id_only_when_provided(self):
        self.assertEqual(
            command_for_tool("get_tile", {"tile": 2608}), ["get_tile", 2608]
        )
        self.assertEqual(
            command_for_tool("get_tile", {"tile": 2608, "cargo_id": 4}),
            ["get_tile", 2608, 4],
        )
        self.assertEqual(
            command_for_tool("get_region", {"center_tile": 2735, "radius": 2}),
            ["get_region", 2735, 2],
        )
        self.assertEqual(
            command_for_tool(
                "get_region", {"center_tile": 2735, "radius": 2, "cargo_id": 4}
            ),
            ["get_region", 2735, 2, 4],
        )
        with self.assertRaisesRegex(ValueError, "cargo_id"):
            command_for_tool("get_tile", {"tile": 2608, "cargo_id": -1})
        for name, arguments in (
            ("get_tile", {"tile": 2608, "cargo_id": 64}),
            (
                "get_region",
                {"center_tile": 2735, "radius": 2, "cargo_id": 300},
            ),
            ("refit_vehicle", {"vehicle_id": 7, "cargo_id": 64}),
        ):
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(ValueError, "cargo_id"),
            ):
                command_for_tool(name, arguments)

    def test_query_region_processing_is_logged_after_compaction(self):
        tmp_path = self.tmp_path
        full_result = json.loads(REGION_FIXTURE_PATH.read_text())

        server = make_tool_server(
            tmp_path, lambda command, timestamp: dict(full_result)
        )
        result = server.handle_tool_call(
            "get_region", {"center_tile": 2735, "radius": 2, "detail": "grid"}
        )

        self.assertIn("grid", result)
        self.assertNotIn("tiles", result)
        record = read_jsonl(tmp_path / "tool_calls.jsonl")[0]
        self.assertEqual(record["command"], ["get_region", 2735, 2])
        self.assertEqual(record["result"], result)
        self.assertLess(record["result_bytes"], 3000)

    def test_registry_contains_build_truck_station_action(self):
        """021 Lane C1: freight gets a dedicated truck-stop build primitive."""
        tools = tools_by_name(enable_skill=False)
        spec = tools["build_truck_stop_drive_through"]

        self.assertTrue(spec["mutates_game"])
        self.assertEqual(spec["inputSchema"]["required"], ["tile", "front_tile"])
        self.assertEqual(spec["inputSchema"]["properties"]["tile"], {"type": "integer"})
        self.assertEqual(
            spec["inputSchema"]["properties"]["front_tile"], {"type": "integer"}
        )
        self.assertEqual(
            spec["inputSchema"]["properties"]["join"],
            {
                "type": "string",
                "enum": ["new", "adjacent", "station_id"],
                "default": "new",
            },
        )
        self.assertEqual(
            spec["inputSchema"]["properties"]["station_id"],
            {"type": "integer", "minimum": 0},
        )

    def test_registry_contains_refit_vehicle_action(self):
        """021 Lane C3: road vehicles can be refit to model-chosen cargo ids."""
        tools = tools_by_name(enable_skill=False)
        spec = tools["refit_vehicle"]

        self.assertTrue(spec["mutates_game"])
        self.assertEqual(spec["inputSchema"]["required"], ["vehicle_id", "cargo_id"])
        self.assertEqual(
            spec["inputSchema"]["properties"]["vehicle_id"], {"type": "integer"}
        )
        self.assertEqual(
            spec["inputSchema"]["properties"]["cargo_id"],
            {"type": "integer", "minimum": 0, "maximum": 63},
        )

    def test_registry_contains_query_station_sensor(self):
        """018-loop get_my_station (key hidden_information:station_cargo_state):
        factual per-station cargo state, identified by station_id or tile."""
        tools = tools_by_name(enable_skill=False)
        spec = tools["get_my_station"]
        self.assertFalse(spec["mutates_game"])
        self.assertEqual(spec["inputSchema"]["required"], [])
        self.assertEqual(
            sorted(spec["inputSchema"]["properties"]), ["station_id", "tile"]
        )

    def test_registry_contains_query_truck_catchment_sensor(self):
        """021 demand-gated intake (024 ep-001 feedback #1): factual producer/
        acceptor catchment plus 079 raw town-cargo acceptance for a model-chosen
        truck-stop tile."""
        tools = tools_by_name(enable_skill=False)
        spec = tools["check_truck_stop_catchment"]
        self.assertFalse(spec["mutates_game"])
        self.assertEqual(spec["inputSchema"]["required"], ["tile"])
        self.assertEqual(sorted(spec["inputSchema"]["properties"]), ["tile"])

    def test_registry_contains_query_route_economics_sensor(self):
        """021 primitive backlog: factual route-level economics grouped by order signature."""
        tools = tools_by_name(enable_skill=False)
        spec = tools["list_my_route_economics"]

        self.assertFalse(spec["mutates_game"])
        self.assertEqual(spec["inputSchema"]["required"], [])
        self.assertEqual(spec["inputSchema"]["properties"], {})

    def test_registry_contains_industry_sensors(self):
        """021 Lane C2: industry/source-destination facts are read-only sensors."""
        tools = tools_by_name(enable_skill=False)

        list_spec = tools["list_industries"]
        self.assertFalse(list_spec["mutates_game"])
        self.assertEqual(list_spec["inputSchema"]["properties"], {})
        self.assertEqual(list_spec["inputSchema"]["required"], [])

        query_spec = tools["get_industry"]
        self.assertFalse(query_spec["mutates_game"])
        self.assertEqual(query_spec["inputSchema"]["required"], ["industry_id"])
        self.assertEqual(
            query_spec["inputSchema"]["properties"]["industry_id"], {"type": "integer"}
        )

    def test_registry_contains_query_town_sensor(self):
        """042 Unit 1: town cargo statistics reach the surface as a per-town
        sensor beside get_industry/get_my_station. AITown.GetLastMonthSupplied
        is a naming trap — it reads the transported history slot, and
        "transported" there means moved into a station's waiting queue, not
        loaded onto a vehicle and not delivered — so neither the field name nor
        the description may echo "supplied" or a bare "transported" count."""
        tools = tools_by_name(enable_skill=False)
        spec = tools["get_town"]

        self.assertFalse(spec["mutates_game"])
        self.assertEqual(spec["inputSchema"]["required"], ["town_id"])
        self.assertEqual(
            spec["inputSchema"]["properties"], {"town_id": {"type": "integer"}}
        )

    def test_registry_contains_estimate_road_build_sensor(self):
        """021 primitive backlog: factual road build-cost/blocker dry-run."""
        tools = tools_by_name(enable_skill=False)
        spec = tools["check_build_road"]

        self.assertFalse(spec["mutates_game"])
        self.assertEqual(spec["inputSchema"]["required"], ["from_tile", "to_tile"])
        self.assertEqual(
            spec["inputSchema"]["properties"]["waypoints"],
            {"type": "array", "items": {"type": "integer"}, "default": []},
        )

    def test_registry_contains_estimate_depot_build_sensor(self):
        """Depot dry-run describes when its stable front-bit shape applies."""
        tools = tools_by_name(enable_skill=False)
        spec = tools["check_build_road_depot"]

        self.assertFalse(spec["mutates_game"])
        self.assertEqual(spec["inputSchema"]["required"], ["tile", "front_tile"])
        self.assertEqual(spec["inputSchema"]["properties"]["tile"], {"type": "integer"})
        self.assertEqual(
            spec["inputSchema"]["properties"]["front_tile"], {"type": "integer"}
        )

    def test_registry_contains_bridge_geography_tools(self):
        """021 Lane B1: bridge type is model-chosen from a factual sensor."""
        tools = tools_by_name(enable_skill=False)

        list_spec = tools["list_bridge_types"]
        self.assertFalse(list_spec["mutates_game"])
        self.assertEqual(list_spec["inputSchema"]["required"], ["length"])
        self.assertEqual(
            list_spec["inputSchema"]["properties"]["length"],
            {"type": "integer", "minimum": 2},
        )

        build_spec = tools["build_bridge"]
        self.assertTrue(build_spec["mutates_game"])
        self.assertEqual(
            build_spec["inputSchema"]["required"],
            ["start_tile", "end_tile", "bridge_id"],
        )
        for prop in ("start_tile", "end_tile", "bridge_id"):
            self.assertEqual(
                build_spec["inputSchema"]["properties"][prop], {"type": "integer"}
            )

    def test_registry_contains_tunnel_geography_tool(self):
        """021 Lane B2: tunnel exit is game-derived and reported back."""
        tools = tools_by_name(enable_skill=False)

        spec = tools["build_tunnel"]
        self.assertTrue(spec["mutates_game"])
        self.assertEqual(spec["inputSchema"]["required"], ["start_tile"])
        self.assertEqual(
            spec["inputSchema"]["properties"]["start_tile"], {"type": "integer"}
        )

    def test_registry_contains_terraform_land_tool(self):
        """021 Lane B3: terraform is one model-specified terrain command."""
        tools = tools_by_name(enable_skill=False)

        spec = tools["terraform_land"]
        self.assertTrue(spec["mutates_game"])
        self.assertEqual(spec["inputSchema"]["required"], ["tile", "mode"])
        props = spec["inputSchema"]["properties"]
        self.assertEqual(
            props["mode"], {"type": "string", "enum": ["raise", "lower", "level_to"]}
        )
        self.assertEqual(
            props["corners"]["items"], {"type": "string", "enum": ["w", "s", "e", "n"]}
        )

    def test_registry_contains_company_report_sensor(self):
        """021 Lane B4: one aggregate annual-report-style factual sensor."""
        tools = tools_by_name(enable_skill=False)

        spec = tools["get_my_company"]
        self.assertFalse(spec["mutates_game"])
        self.assertEqual(spec["inputSchema"]["required"], [])
        self.assertEqual(spec["inputSchema"]["properties"], {})

    def test_station_build_join_modes_validate_station_id_usage(self):
        with self.assertRaisesRegex(ValueError, "requires join"):
            command_for_tool(
                "build_bus_stop_drive_through",
                {"tile": 2608, "front_tile": 2607, "station_id": 4},
            )
        with self.assertRaisesRegex(ValueError, "requires station_id"):
            command_for_tool(
                "build_bus_stop_drive_through",
                {"tile": 2608, "front_tile": 2607, "join": "station_id"},
            )
        with self.assertRaisesRegex(ValueError, "join"):
            command_for_tool(
                "build_bus_stop_drive_through",
                {"tile": 2608, "front_tile": 2607, "join": "nearby"},
            )
        with self.assertRaisesRegex(ValueError, "station_id"):
            command_for_tool(
                "build_bus_stop_drive_through",
                {
                    "tile": 2608,
                    "front_tile": 2607,
                    "join": "station_id",
                    "station_id": -1,
                },
            )
        with self.assertRaisesRegex(ValueError, "requires station_id"):
            command_for_tool(
                "build_bus_stop_bay",
                {"tile": 2608, "front_tile": 2607, "join": "station_id"},
            )
        with self.assertRaisesRegex(ValueError, "requires join"):
            command_for_tool(
                "build_truck_stop_bay",
                {"tile": 2608, "front_tile": 2607, "station_id": 4},
            )

    def test_connectivity_registry_defines_route_distance_units_and_shape(self):
        """080 keeps the existing read and input schema while defining every
        output semantic a caller needs to interpret the new distance rows."""
        spec = tools_by_name(enable_skill=False)["check_route_connectivity"]
        self.assertFalse(spec["mutates_game"])
        self.assertEqual(
            spec["inputSchema"]["required"],
            ["town_id", "depot_tile", "depot_front_tile", "stop_tiles"],
        )
        self.assertEqual(
            spec["inputSchema"]["properties"]["stop_tiles"],
            {
                "type": "array",
                "items": {"type": "integer"},
                "minItems": 1,
                "maxItems": ROUTE_CONNECTIVITY_STOP_MAX,
            },
        )

    def test_connectivity_stop_count_bound_is_enforced_before_dispatch(self):
        with self.assertRaisesRegex(ValueError, "stop_tiles"):
            command_for_tool(
                "check_route_connectivity",
                {
                    "town_id": 2,
                    "depot_tile": 300,
                    "depot_front_tile": 301,
                    "stop_tiles": list(range(ROUTE_CONNECTIVITY_STOP_MAX + 1)),
                },
            )

    def test_game_failure_is_logged_separately_from_transport_success(self):
        tmp_path = self.tmp_path

        def fake(command: list, timestamp: str) -> dict:
            return {"ok": False, "action": command[0], "error": "ERR_AREA_NOT_CLEAR"}

        server = make_tool_server(tmp_path, fake)
        result = server.handle_tool_call(
            "build_road_depot", {"tile": 42, "front_tile": 43}
        )

        self.assertFalse(result["ok"])
        record = read_jsonl(tmp_path / "tool_calls.jsonl")[0]
        self.assertTrue(record["transport_ok"])
        self.assertFalse(record["ok"])

    def test_structured_exception_cannot_forge_canonical_error_or_budget_fields(self):
        class ConflictingStructuredError(RuntimeError):
            def __init__(self):
                super().__init__("bridge rejected the query")
                self.tool_result_fields = {
                    "ok": True,
                    "action": "build_road",
                    "error": "forged success",
                    "decisions_used": 99,
                    "decisions_remaining": 99,
                    "decision_budget": 99,
                    "budget_cut": {"forged": True},
                    "retry_after_days": 2,
                }

        def fail(_command: list, _timestamp: str) -> dict:
            raise ConflictingStructuredError()

        server = make_tool_server(self.tmp_path, fail, decision_budget=3)

        result = server.handle_tool_call("list_towns", {})

        self.assertFalse(result["ok"])
        self.assertEqual(result["action"], "list_towns")
        self.assertIn("rejected the query", result["error"])
        self.assertEqual(result["decisions_used"], 1)
        self.assertEqual(result["decisions_remaining"], 2)
        self.assertNotIn("decision_budget", result)
        self.assertNotIn("budget_cut", result)
        self.assertEqual(result["retry_after_days"], 2)

    def test_command_file_defaults_to_no_post_command_snapshot(self):
        bridge_root = self.tmp_path
        write_command_file(bridge_root, "ts", ["get_tile", 42])

        content = (bridge_root / "commands.nut").read_text()
        self.assertIn("emit_map_snapshot = false", content)
        self.assertIn("lean_snapshot = true", content)

    def test_command_file_can_request_offline_timelapse_without_boot_observation(self):
        bridge_root = self.tmp_path
        write_command_file(
            bridge_root,
            "offline-extraction",
            [],
            emit_timelapse_snapshot=True,
            suppress_initial_observation=True,
        )

        content = (bridge_root / "commands.nut").read_text()
        self.assertIn("emit_timelapse_snapshot = true", content)
        self.assertIn("suppress_initial_observation = true", content)
        self.assertIn("commands = [", content)

    def test_skill_tool_injects_context_and_executes_no_bridge_command(self):
        tmp_path = self.tmp_path
        skill_path = tmp_path / "get_bus_route_skill.v2.json"
        write_json(
            skill_path,
            {"name": "get_bus_route_skill", "version": 2, "steps": [{"id": "scout"}]},
        )
        dispatcher = FunctionBridgeDispatcher(lambda command, timestamp: {"ok": True})
        server = make_tool_server(tmp_path, dispatcher, enable_skill_path=skill_path)

        result = server.handle_tool_call(
            "get_bus_route_skill", {"town_id": 3, "engine_id": 116}
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "context_injection")
        self.assertEqual(result["bound_params"], {"town_id": 3, "engine_id": 116})
        self.assertEqual(dispatcher.commands, [])
        record = read_jsonl(tmp_path / "tool_calls.jsonl")[0]
        self.assertIsNone(record["command"])

    def test_legacy_v2_skill_is_deep_copied_and_migrated_when_served(self):
        skill_path = self.tmp_path / "build_bus_route.v2.json"
        write_json(
            skill_path,
            {
                "name": "build_bus_route",
                "version": 2,
                "steps": [
                    {"id": name, "primitive": {"command": name}}
                    for name in RETIRED_TOOL_NAME_MAP
                ],
            },
        )
        artifact_bytes = skill_path.read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            dispatcher = FunctionBridgeDispatcher(
                lambda command, timestamp: {"ok": True}
            )
            server = make_tool_server(tmp, dispatcher, enable_skill_path=skill_path)

            result = server.handle_tool_call("get_bus_route_skill", {"town_id": 3})

            self.assertEqual(result["skill_name"], "get_bus_route_skill")
            self.assertEqual(result["skill_version"], 2)
            self.assertEqual(result["skill_body"]["name"], "get_bus_route_skill")
            commands = [
                step["primitive"]["command"]
                for step in result["skill_body"]["steps"]
                if isinstance(step.get("primitive"), dict)
            ]
            self.assertIn("get_region", commands)
            self.assertIn("get_my_finances", commands)
            self.assertIn("build_bus_stop_drive_through", commands)
            self.assertIn("append_orders", commands)
            self.assertIn("wait_and_observe_vehicle", commands)
            serialized = json.dumps(result["skill_body"], sort_keys=True)
            for retired in RETIRED_TOOL_NAME_MAP:
                self.assertIsNone(
                    re.search(
                        rf"(?<![A-Za-z0-9_]){re.escape(retired)}(?![A-Za-z0-9_])",
                        serialized,
                    ),
                    retired,
                )
            self.assertEqual(dispatcher.commands, [])

            result["skill_body"]["steps"][0]["id"] = "mutated-result"
            second = server.handle_tool_call("get_bus_route_skill", {"town_id": 3})
            self.assertNotEqual(
                second["skill_body"]["steps"][0]["id"], "mutated-result"
            )

        self.assertEqual(skill_path.read_bytes(), artifact_bytes)

    def test_report_feedback_appends_entry_and_executes_no_bridge_command(self):
        tmp_path = self.tmp_path
        dispatcher = FunctionBridgeDispatcher(lambda command, timestamp: {"ok": True})
        server = make_tool_server(
            tmp_path, dispatcher, feedback_log=tmp_path / "feedback.jsonl"
        )

        result = server.handle_tool_call(
            "report_feedback",
            feedback_args(
                description="cannot see station catchment demand",
                why_it_matters="I would place stops where passengers actually are",
                current_blocker="get_tile only shows radius-4 acceptance",
            ),
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["recorded"])
        self.assertEqual(result["kind"], "missing_observation")
        self.assertEqual(dispatcher.commands, [])
        entries = read_jsonl(tmp_path / "feedback.jsonl")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["kind"], "missing_observation")
        self.assertEqual(entries[0]["tool_call_id"], "tool-0001-report_feedback")
        self.assertEqual(entries[0]["turn_index"], 1)
        record = read_jsonl(tmp_path / "tool_calls.jsonl")[0]
        self.assertIsNone(record["command"])

    def test_report_feedback_rejects_bad_kind_and_appends_nothing(self):
        tmp_path = self.tmp_path
        server = make_tool_server(tmp_path, feedback_log=tmp_path / "feedback.jsonl")

        result = server.handle_tool_call(
            "report_feedback",
            feedback_args(
                kind="give_me_candidates",
                description="x",
                why_it_matters="y",
                current_blocker="z",
            ),
        )

        self.assertFalse(result["ok"])
        self.assertIn("kind", result["error"])
        self.assertFalse((tmp_path / "feedback.jsonl").exists())

    def test_feedback_log_defaults_to_tool_log_sibling(self):
        tmp_path = self.tmp_path
        server = GameToolServer(None, tool_log=tmp_path / "tool_calls.jsonl")
        self.assertEqual(server.feedback_log, tmp_path / "feedback.jsonl")

    def test_tool_log_records_result_size_and_null_context_usage(self):
        tmp_path = self.tmp_path

        def fake(command: list, timestamp: str) -> dict:
            return {"ok": True, "action": command[0], "payload": "x" * 100}

        server = make_tool_server(tmp_path, fake)
        result = server.handle_tool_call("list_towns", {})

        record = read_jsonl(tmp_path / "tool_calls.jsonl")[0]
        expected_bytes = len(json.dumps(result, sort_keys=True).encode("utf-8"))
        self.assertEqual(record["result_bytes"], expected_bytes)
        self.assertEqual(record["result_token_estimate"], expected_bytes // 4)
        self.assertIsNone(record["prompt_tokens"])
        self.assertIsNone(record["total_context_tokens"])
        self.assertIsNone(record["compaction_fired"])


if __name__ == "__main__":
    unittest.main()
