from __future__ import annotations

import unittest

from openttd_mcp.server import FunctionBridgeDispatcher, command_for_tool
from tests.support import (
    TempDirTestCase,
    fastmcp_tools_by_name,
    make_tool_server,
    tools_by_name,
)


class GetVehicleRegistryTests(TempDirTestCase):
    def test_registry_and_fastmcp_expose_one_time_free_vehicle_id_read(self) -> None:
        registry = tools_by_name(enable_skill=False)["get_vehicle"]

        self.assertFalse(registry["mutates_game"])
        self.assertEqual(registry["inputSchema"]["required"], ["vehicle_id"])
        self.assertEqual(
            registry["inputSchema"]["properties"], {"vehicle_id": {"type": "integer"}}
        )

        server = make_tool_server(self.tmp_path)
        served = fastmcp_tools_by_name(server)["get_vehicle"]
        self.assertEqual(served.inputSchema["required"], ["vehicle_id"])
        self.assertEqual(
            served.inputSchema["properties"]["vehicle_id"]["type"], "integer"
        )

    def test_python_command_validation_and_dispatch_preserve_vehicle_id(self) -> None:
        self.assertEqual(
            command_for_tool("get_vehicle", {"vehicle_id": 17}),
            ["get_vehicle", 17],
        )
        for bad_arguments in ({}, {"vehicle_id": True}, {"vehicle_id": "17"}):
            with self.subTest(arguments=bad_arguments), self.assertRaises(ValueError):
                command_for_tool("get_vehicle", bad_arguments)

        dispatcher = FunctionBridgeDispatcher(
            lambda command, timestamp: {
                "ok": True,
                "action": command[0],
                "vehicle": {"vehicle_id": command[1]},
            }
        )
        server = make_tool_server(self.tmp_path, dispatcher.fn)
        result = server.handle_tool_call("get_vehicle", {"vehicle_id": 17})

        self.assertEqual(result["vehicle"]["vehicle_id"], 17)
        self.assertEqual(
            server.dispatcher.commands[0][1],
            ["get_vehicle", 17],
        )


if __name__ == "__main__":
    unittest.main()
