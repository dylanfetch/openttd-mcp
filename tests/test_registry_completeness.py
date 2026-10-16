"""021 Unit 3 — registry completeness lint (018 debrief item 4).

Every ID-typed tool argument must have a read-only sensor that produces that
ID. In 018 episode-001 the model had to guess ``buy_vehicle(engine_id)`` values
because no sensor produced engine IDs; this lint fails the suite the moment a
new ID-typed argument lands without its producer, so that blocker class cannot
recur. Episode-control tools (``end_episode``, ``report_feedback``) have no
ID-typed arguments and pass trivially.
"""

from __future__ import annotations

import unittest

from openttd_mcp.server import (
    _first_sentence,
    generate_server_instructions,
    registry_snapshot,
    tool_specs,
)

# The explicit producer map, maintained next to the lint. A new ID-typed
# argument must either appear here (pointing at the sensor that produces it)
# or the lint fails with "add the sensor or the mapping".
ID_PRODUCERS: dict[str, tuple[str, ...]] = {
    "engine_id": ("list_engines",),
    "bridge_id": ("list_bridge_types",),
    "vehicle_id": ("list_my_vehicles",),
    "source_vehicle_id": ("list_my_vehicles",),
    "group_id": ("list_my_vehicles",),
    "town_id": ("list_towns",),
    "industry_id": ("list_industries",),
    # The cargo_id *argument* (refit_vehicle/get_tile/get_region/
    # get_cargo_payment_rate) is produced by list_engines, which emits the value
    # under the field name `cargo_type` (also in list_industries/get_industry cargo
    # rows). Same integer, different field name; every consuming tool description
    # states the equivalence so the model does not scan for a nonexistent `cargo_id`
    # field.
    "cargo_id": ("list_engines",),
    # list_my_stations is the whole-inventory producer (routed or not);
    # list_my_vehicles still produces the subset a vehicle's orders point at
    # (routes[].station_ids), so both are live producers of the ID.
    "station_id": ("list_my_stations", "list_my_vehicles"),
}
# list_my_depots deliberately joins the general tile producer family: its tile
# and front_tile facts supply depot actions and depot connectivity arguments.
TILE_PRODUCERS: tuple[str, ...] = ("get_tile", "get_region", "list_my_depots")


def is_tile_family(prop: str) -> bool:
    """tile, *_tile, *_tiles (stop_tiles), and the waypoints tile list."""
    return prop == "tile" or prop.endswith(("_tile", "_tiles")) or prop == "waypoints"


def lint_registry(registry: dict) -> list[str]:
    """Return violations; an empty list means every ID arg has a live producer."""
    tools = {tool["name"]: tool for tool in registry["tools"]}

    def producer_ok(name: str) -> bool:
        return name in tools and not tools[name]["mutates_game"]

    problems: list[str] = []
    for tool in registry["tools"]:
        for prop in tool["inputSchema"]["properties"]:
            if is_tile_family(prop):
                producers = TILE_PRODUCERS
            elif prop.endswith(("_id", "_ids")):
                # A plural ID list (vehicle_ids, ...) needs the same producer as its singular.
                key = prop[:-1] if prop.endswith("_ids") else prop
                if key not in ID_PRODUCERS:
                    problems.append(
                        f"{tool['name']}.{prop}: ID-typed argument with no producer mapping — "
                        "add the sensor or the ID_PRODUCERS entry"
                    )
                    continue
                producers = ID_PRODUCERS[key]
            else:
                continue
            if not any(producer_ok(name) for name in producers):
                problems.append(
                    f"{tool['name']}.{prop}: no registered read-only producer among {producers} — "
                    "add the sensor or the mapping"
                )
    return problems


class RegistryCompletenessTests(unittest.TestCase):
    def test_generated_instructions_exactly_index_enabled_tools(self):
        combinations = (
            (False, "standard", False),
            (True, "standard", True),
            (False, "distress", False),
        )
        for enable_skill, end_episode_mode, enable_vision in combinations:
            with self.subTest(
                enable_skill=enable_skill,
                end_episode_mode=end_episode_mode,
                enable_vision=enable_vision,
            ):
                instructions = generate_server_instructions(
                    enable_skill=enable_skill,
                    end_episode_mode=end_episode_mode,
                    enable_vision=enable_vision,
                )
                tool_lines = instructions.splitlines()[1:]
                expected_names = [
                    spec.name
                    for spec in tool_specs(
                        enable_skill=enable_skill,
                        end_episode_mode=end_episode_mode,
                        enable_vision=enable_vision,
                    )
                ]

                self.assertEqual(
                    [line.split(": ", 1)[0] for line in tool_lines],
                    expected_names,
                )
                for name in expected_names:
                    self.assertEqual(
                        sum(line.startswith(f"{name}: ") for line in tool_lines),
                        1,
                    )
                self.assertEqual("get_bus_route_skill" in expected_names, enable_skill)
                self.assertEqual("view_map" in expected_names, enable_vision)

    def test_tool_index_order_is_lifecycle_ordered(self):
        """032: index position signals importance to the model, so flag-gated
        tools are inserted into their lifecycle block (vision after
        get_region, skill among the planning aids) and end_episode is
        always the final entry — never appended after the catalog."""
        names = [
            spec.name for spec in tool_specs(enable_skill=True, enable_vision=True)
        ]

        query_region_at = names.index("get_region")
        self.assertEqual(
            names[query_region_at + 1 : query_region_at + 3],
            ["view_map", "list_towns"],
        )

        skill_at = names.index("get_bus_route_skill")
        self.assertGreater(skill_at, names.index("check_build_road_depot"))
        self.assertLess(skill_at, names.index("build_road"))

        build_road_at = names.index("build_road")
        self.assertEqual(
            names[build_road_at : build_road_at + 2],
            ["build_road", "build_one_way_road"],
        )

        for enable_skill in (False, True):
            for enable_vision in (False, True):
                with self.subTest(
                    enable_skill=enable_skill, enable_vision=enable_vision
                ):
                    specs = tool_specs(
                        enable_skill=enable_skill, enable_vision=enable_vision
                    )
                    self.assertEqual(specs[-1].name, "end_episode")

    def test_registry_snapshot_records_generated_tool_index(self):
        """030: the snapshot records the generated index under `tool_index`
        with the delivery channel, and no longer carries an `instructions`
        key — pre-030 bundles have `instructions` (served in
        initialize.instructions), post-030 bundles have `tool_index`
        (appended to the episode prompt). Key presence is how series
        comparisons tell the eras apart, so both halves are pinned."""
        flags = {
            "enable_skill": True,
            "experiment": "030-instructions-channel",
            "end_episode_mode": "distress",
            "enable_vision": True,
            "tool_index_channel": "prompt",
        }
        snapshot = registry_snapshot(**flags)
        index_flags = {
            key: value
            for key, value in flags.items()
            # 039: the model-visible index is era-neutral, so the experiment
            # label is not one of its inputs; it stays recorded in the snapshot
            # for the evidence lane only.
            if key not in {"tool_index_channel", "experiment"}
        }
        self.assertEqual(
            snapshot["tool_index"], generate_server_instructions(**index_flags)
        )
        self.assertEqual(snapshot["tool_index_channel"], "prompt")
        self.assertNotIn("instructions", snapshot)
        self.assertEqual(snapshot["experiment"], "030-instructions-channel")
        self.assertNotIn("030-instructions-channel", snapshot["tool_index"])

    def test_registry_snapshot_does_not_invent_a_delivery_channel(self):
        self.assertNotIn("tool_index_channel", registry_snapshot())

    def test_first_sentence_extraction(self):
        cases = (
            (
                "stops at the first sentence boundary",
                "First sentence. Second sentence.",
                "First sentence.",
            ),
            (
                "text without a period is the whole (stripped) text",
                "  No period here  ",
                "No period here",
            ),
            (
                "a period at the end of the string still ends the sentence",
                "Ends here.",
                "Ends here.",
            ),
            # build_road/check_build_road write path semantics as
            # "from_tile -> waypoints... -> to_tile"; splitting at the ellipsis
            # dropped the destination from the always-visible index line.
            (
                "an ellipsis is not a sentence boundary",
                "Spans run from_tile -> waypoints... -> to_tile in order. Failures report.",
                "Spans run from_tile -> waypoints... -> to_tile in order.",
            ),
        )
        for label, text, expected in cases:
            with self.subTest(label):
                self.assertEqual(_first_sentence(text), expected)

    def test_every_id_typed_argument_has_a_registered_producer(self):
        for end_episode_mode in ("standard", "distress"):
            with self.subTest(end_episode_mode=end_episode_mode):
                problems = lint_registry(
                    registry_snapshot(
                        enable_skill=True, end_episode_mode=end_episode_mode
                    )
                )
                self.assertEqual(problems, [])

    def test_bay_station_id_and_tile_arguments_have_existing_producers(self):
        """021 bay/terminus builders add no guessed identifiers: their tile
        arguments come from map sensors and station_id from fleet facts."""
        registry = registry_snapshot(enable_skill=False)
        tools = {tool["name"]: tool for tool in registry["tools"]}
        for name in ("build_bus_stop_bay", "build_truck_stop_bay"):
            self.assertEqual(
                set(tools[name]["inputSchema"]["properties"]),
                {"tile", "front_tile", "join", "station_id"},
            )

    def test_mapped_producers_are_registered_read_only_tools(self):
        registry = registry_snapshot(enable_skill=True)
        tools = {tool["name"]: tool for tool in registry["tools"]}
        for producers in list(ID_PRODUCERS.values()) + [TILE_PRODUCERS]:
            for name in producers:
                self.assertIn(name, tools)
                self.assertFalse(tools[name]["mutates_game"], name)

    def test_episode_control_tools_have_no_id_typed_arguments(self):
        """022 coverage: end_episode/report_feedback pass the lint trivially."""
        registry = registry_snapshot(enable_skill=False)
        tools = {tool["name"]: tool for tool in registry["tools"]}
        for name in ("end_episode", "report_feedback"):
            props = tools[name]["inputSchema"]["properties"]
            self.assertFalse(
                [p for p in props if p.endswith("_id") or is_tile_family(p)], name
            )

    def test_lint_catches_the_018_engine_id_blocker(self):
        """Self-test on the pre-Unit-1 surface: buy_vehicle(engine_id), no list_engines."""
        registry = registry_snapshot(enable_skill=False)
        registry["tools"] = [
            tool for tool in registry["tools"] if tool["name"] != "list_engines"
        ]
        problems = lint_registry(registry)
        self.assertTrue(
            any("buy_vehicle.engine_id" in problem for problem in problems), problems
        )

    def test_lint_flags_a_new_id_argument_without_a_mapping(self):
        registry = registry_snapshot(enable_skill=False)
        registry["tools"].append(
            {
                "name": "build_signal",
                "description": "synthetic",
                "inputSchema": {
                    "type": "object",
                    "properties": {"signal_id": {"type": "integer"}},
                    "required": ["signal_id"],
                    "additionalProperties": False,
                },
                "mutates_game": True,
            }
        )
        problems = lint_registry(registry)
        self.assertTrue(
            any(
                "build_signal.signal_id" in problem and "add the sensor" in problem
                for problem in problems
            ),
            problems,
        )

    def test_lint_covers_plural_id_list_arguments(self):
        """A batch arg (vehicle_ids) must not slip past the lint unchecked."""
        registry = registry_snapshot(enable_skill=False)
        registry["tools"].append(
            {
                "name": "sell_vehicles",
                "description": "synthetic",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "vehicle_ids": {"type": "array", "items": {"type": "integer"}},
                        "signal_ids": {"type": "array", "items": {"type": "integer"}},
                    },
                    "required": ["vehicle_ids", "signal_ids"],
                    "additionalProperties": False,
                },
                "mutates_game": True,
            }
        )
        problems = lint_registry(registry)
        # vehicle_ids maps to vehicle_id's producer (list_my_vehicles) — no violation;
        # signal_ids has no mapping and must be flagged.
        self.assertFalse([p for p in problems if "vehicle_ids" in p], problems)
        self.assertTrue(
            any("sell_vehicles.signal_ids" in p for p in problems), problems
        )


if __name__ == "__main__":
    unittest.main()
