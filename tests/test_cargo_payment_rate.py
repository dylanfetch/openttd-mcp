from __future__ import annotations

import tempfile
import unittest

from openttd_mcp.server import MUTATING_TOOL_NAMES, command_for_tool
from tests.support import (
    fastmcp_tools_by_name,
    make_tool_server,
    tools_by_name,
)


class CargoPaymentRateTests(unittest.TestCase):
    def test_registry_contract_is_bounded_factual_and_read_only(self):
        tools = tools_by_name(enable_skill=False)
        spec = tools["get_cargo_payment_rate"]

        self.assertFalse(spec["mutates_game"])
        self.assertNotIn("get_cargo_payment_rate", MUTATING_TOOL_NAMES)
        self.assertEqual(
            spec["inputSchema"],
            {
                "type": "object",
                "properties": {
                    "cargo_id": {"type": "integer", "minimum": 0, "maximum": 63},
                    "distance": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 4_294_967_295,
                    },
                    "days_in_transit": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 163_838,
                    },
                },
                "required": ["cargo_id", "distance", "days_in_transit"],
                "additionalProperties": False,
            },
        )

    def test_python_mapping_accepts_only_documented_integer_ranges(self):
        self.assertEqual(
            command_for_tool(
                "get_cargo_payment_rate",
                {"cargo_id": 0, "distance": 0, "days_in_transit": 0},
            ),
            ["get_cargo_payment_rate", 0, 0, 0],
        )
        self.assertEqual(
            command_for_tool(
                "get_cargo_payment_rate",
                {"cargo_id": 63, "distance": 4_294_967_295, "days_in_transit": 163_838},
            ),
            ["get_cargo_payment_rate", 63, 4_294_967_295, 163_838],
        )
        invalid_arguments = (
            {},
            {"cargo_id": True, "distance": 1, "days_in_transit": 1},
            {"cargo_id": 0, "distance": 1.0, "days_in_transit": 1},
            {"cargo_id": 0, "distance": 1, "days_in_transit": "1"},
            {"cargo_id": -1, "distance": 1, "days_in_transit": 1},
            {"cargo_id": 64, "distance": 1, "days_in_transit": 1},
            {"cargo_id": 0, "distance": -1, "days_in_transit": 1},
            {"cargo_id": 0, "distance": 4_294_967_296, "days_in_transit": 1},
            {"cargo_id": 0, "distance": 1, "days_in_transit": -1},
            {"cargo_id": 0, "distance": 1, "days_in_transit": 163_839},
        )
        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                command_for_tool("get_cargo_payment_rate", arguments)

    def test_server_dispatches_exact_flat_command_without_mutating_arguments(self):
        calls: list[list[object]] = []

        def fake_dispatch(command: list[object], timestamp: str) -> dict[str, object]:
            calls.append(command)
            return {"ok": True, "action": command[0], "income": 1234}

        with tempfile.TemporaryDirectory() as tmp:
            server = make_tool_server(tmp, fake_dispatch)
            arguments = {"cargo_id": 0, "distance": 42, "days_in_transit": 9}
            result = server.handle_tool_call("get_cargo_payment_rate", arguments)

        self.assertTrue(result["ok"])
        self.assertEqual(result["income"], 1234)
        self.assertEqual(calls, [["get_cargo_payment_rate", 0, 42, 9]])
        self.assertEqual(
            arguments, {"cargo_id": 0, "distance": 42, "days_in_transit": 9}
        )

    def test_fastmcp_wrapper_is_registered_with_three_required_integers(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = make_tool_server(tmp)
            tools = fastmcp_tools_by_name(server)

        schema = tools["get_cargo_payment_rate"].inputSchema
        self.assertEqual(
            schema["required"], ["cargo_id", "distance", "days_in_transit"]
        )
        for argument in schema["required"]:
            self.assertEqual(schema["properties"][argument]["type"], "integer")


if __name__ == "__main__":
    unittest.main()
