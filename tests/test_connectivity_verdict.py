from __future__ import annotations

import unittest

from tests.support import TempDirTestCase, make_tool_server, read_jsonl


class ConnectivityVerdictTests(TempDirTestCase):
    def test_015_failure_shape_is_reported_not_connected_without_controller_repair(
        self,
    ):
        """The MCP layer must return the bridge's factual verdict, not repair it.

        The concrete 015 failure shape was a legal-looking depot/front/stops
        tuple whose depot mouth was not on the road component.  Here the fake
        bridge returns that factual verdict and the tool layer is required to log
        exactly one query command and no hidden build_road repair.
        """
        tmp_path = self.tmp_path
        commands: list[list] = []

        def fake(command: list, timestamp: str) -> dict:
            commands.append(command)
            return {
                "ok": True,
                "action": "check_route_connectivity",
                "depot_front_direct_connected": False,
                "stops_mutually_reachable": True,
                "all_on_one_component": False,
                "stop_to_stop_distances": [
                    {
                        "from_tile": 2833,
                        "to_tile": 3153,
                        "reachable": True,
                        "road_steps": 9,
                    },
                    {
                        "from_tile": 3153,
                        "to_tile": 2833,
                        "reachable": True,
                        "road_steps": 11,
                    },
                ],
            }

        server = make_tool_server(tmp_path, fake)
        result = server.handle_tool_call(
            "check_route_connectivity",
            {
                "town_id": 2,
                "depot_tile": 3151,
                "depot_front_tile": 3152,
                "stop_tiles": [2833, 3153],
            },
        )

        self.assertTrue(result["ok"])
        self.assertFalse(result["depot_front_direct_connected"])
        self.assertFalse(result["all_on_one_component"])
        self.assertEqual(
            [row["road_steps"] for row in result["stop_to_stop_distances"]],
            [9, 11],
        )
        self.assertEqual(
            commands, [["check_route_connectivity", 2, 3151, 3152, 2833, 3153]]
        )
        log_record = read_jsonl(tmp_path / "tool_calls.jsonl")[0]
        self.assertEqual(log_record["command"], commands[0])

    def test_connected_after_connector_is_reported_connected(self):
        def fake(command: list, timestamp: str) -> dict:
            return {
                "ok": True,
                "action": "check_route_connectivity",
                "depot_front_direct_connected": True,
                "stops_mutually_reachable": True,
                "all_on_one_component": True,
                "stop_to_stop_distances": [
                    {
                        "from_tile": 2833,
                        "to_tile": 3153,
                        "reachable": True,
                        "road_steps": 9,
                    },
                    {
                        "from_tile": 3153,
                        "to_tile": 2833,
                        "reachable": True,
                        "road_steps": 9,
                    },
                ],
            }

        server = make_tool_server(self.tmp_path, fake)
        result = server.handle_tool_call(
            "check_route_connectivity",
            {
                "town_id": 2,
                "depot_tile": 3151,
                "depot_front_tile": 3152,
                "stop_tiles": [2833, 3153],
            },
        )

        self.assertTrue(result["depot_front_direct_connected"])
        self.assertTrue(result["all_on_one_component"])
        self.assertEqual(result["stop_to_stop_distances"][0]["road_steps"], 9)


if __name__ == "__main__":
    unittest.main()
