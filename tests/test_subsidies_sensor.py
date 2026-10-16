from __future__ import annotations

import tempfile
import unittest

from openttd_mcp.server import FunctionBridgeDispatcher, command_for_tool
from tests.support import (
    TempDirTestCase,
    fastmcp_tools_by_name,
    make_tool_server,
    read_jsonl,
    tools_by_name,
)


class SubsidiesSensorTests(TempDirTestCase):
    def test_registry_exposes_factual_complete_list_with_no_inputs(self):
        tools = tools_by_name(enable_skill=False)
        spec = tools["list_subsidies"]

        self.assertFalse(spec["mutates_game"])
        self.assertEqual(spec["inputSchema"]["properties"], {})
        self.assertEqual(spec["inputSchema"]["required"], [])

    def test_python_dispatch_is_strictly_argumentless(self):
        self.assertEqual(command_for_tool("list_subsidies", {}), ["list_subsidies"])
        for invalid in ({"subsidy_id": 1}, {"open_only": True}, {"unexpected": None}):
            with (
                self.subTest(invalid=invalid),
                self.assertRaisesRegex(ValueError, "no arguments"),
            ):
                command_for_tool("list_subsidies", invalid)

    def test_server_dispatches_and_logs_flat_bridge_command(self):
        tmp_path = self.tmp_path
        dispatcher = FunctionBridgeDispatcher(
            lambda command, timestamp: {
                "ok": True,
                "action": command[0],
                "subsidy_count": 0,
                "subsidies": [],
            }
        )
        server = make_tool_server(tmp_path, dispatcher)

        result = server.handle_tool_call("list_subsidies", {})

        self.assertTrue(result["ok"])
        self.assertEqual(dispatcher.commands[0][1], ["list_subsidies"])
        record = read_jsonl(tmp_path / "tool_calls.jsonl")[0]
        self.assertEqual(record["name"], "list_subsidies")
        self.assertEqual(record["command"], ["list_subsidies"])

    def test_fastmcp_wrapper_has_empty_input_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = make_tool_server(tmp)
            tools = fastmcp_tools_by_name(server)

        self.assertIn("list_subsidies", tools)
        self.assertEqual(tools["list_subsidies"].inputSchema["properties"], {})
        self.assertEqual(tools["list_subsidies"].inputSchema.get("required", []), [])


if __name__ == "__main__":
    unittest.main()
