from __future__ import annotations

import unittest

from openttd_mcp.server import registry_snapshot
from openttd_mcp.tool_names import RETIRED_TOOL_NAME_MAP, migrate_retired_tool_names


class RetiredToolNameMigrationTests(unittest.TestCase):
    def test_map_is_one_step(self):
        """No value is itself a key: migration is idempotent in one pass."""
        self.assertEqual(
            set(RETIRED_TOOL_NAME_MAP.values()) & set(RETIRED_TOOL_NAME_MAP), set()
        )

    def test_every_retired_name_maps_to_a_live_all_features_registry_tool(self):
        live_names = {
            tool["name"]
            for tool in registry_snapshot(enable_skill=True, enable_vision=True)[
                "tools"
            ]
        }
        self.assertLessEqual(set(RETIRED_TOOL_NAME_MAP.values()), live_names)

    def test_identifier_shaped_names_migrate_anywhere_in_a_string(self):
        migrated = migrate_retired_tool_names(
            {
                "steps": [{"primitive": {"command": "query_region"}}],
                "guidance": "Call query_region first, then observe_route_operation.",
            }
        )
        self.assertEqual(migrated["steps"][0]["primitive"]["command"], "get_region")
        self.assertEqual(
            migrated["guidance"],
            "Call get_region first, then wait_and_observe_vehicle.",
        )

    def test_word_shaped_name_migrates_as_a_value_but_not_inside_prose(self):
        """`demolish` is also an ordinary English verb: substituting it inside
        prose would rewrite model-visible text into something false."""
        migrated = migrate_retired_tool_names(
            {
                "steps": [{"primitive": {"command": "demolish"}}],
                "guidance": "You may need to demolish the blocking house before building.",
                "code_span": "Call `demolish` on the blocking tile.",
            }
        )
        self.assertEqual(migrated["steps"][0]["primitive"]["command"], "demolish_tile")
        self.assertEqual(
            migrated["guidance"],
            "You may need to demolish the blocking house before building.",
        )
        self.assertEqual(
            migrated["code_span"], "Call `demolish_tile` on the blocking tile."
        )

    def test_current_names_and_substrings_are_left_alone(self):
        migrated = migrate_retired_tool_names(
            [
                "demolish_tile",
                "get_region",
                "predemolish",
                "query_regions",
                "demolisher",
            ]
        )
        self.assertEqual(
            migrated,
            [
                "demolish_tile",
                "get_region",
                "predemolish",
                "query_regions",
                "demolisher",
            ],
        )

    def test_input_is_deep_copied(self):
        original = {"steps": [{"command": "query_tile"}]}
        migrated = migrate_retired_tool_names(original)
        self.assertEqual(original["steps"][0]["command"], "query_tile")
        self.assertEqual(migrated["steps"][0]["command"], "get_tile")


if __name__ == "__main__":
    unittest.main()
