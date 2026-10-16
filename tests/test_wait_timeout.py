"""026 item 1: `wait` returns an honest result on a dispatcher timeout.

The root fix lives in Squirrel (`main.nut`: `wait` now emits the lean
observation like every other primitive, so it no longer stalls the bridge). The
Python side keeps `wait` a single dispatch but turns a `TimeoutError` into an
honest ``status:"timeout"`` result -- carrying a model-facing note to re-observe
-- instead of the bare ``{ok:false, error:...}`` of 024 ep-001 #2. These tests
drive the handler through a stub dispatcher (no OpenTTD / Squirrel needed).
"""

from __future__ import annotations

import unittest

from openttd_mcp.server import GameToolServer, command_for_tool
from tests.support import TempDirTestCase, make_tool_server, read_jsonl

# The pre-026 `wait` success token the bridge emits; the success result must be
# this unchanged plus the additive `status` field.
BRIDGE_WAIT_TOKEN = {
    "ok": True,
    "action": "wait",
    "error": "",
    "cost": 0,
    "requested_duration_days": 30,
    "duration_days": 30,
    "start_date": 1000,
    "end_date": 1030,
    "days_elapsed": 30,
    "bank_balance": 100_210,
    "vehicle_lost_events": 0,
    "vehicle_crashed_events": 0,
}


class StubWaitDispatcher:
    """Return a fixed `wait` token, or raise TimeoutError to force the timeout path."""

    def __init__(self, *, timeout: bool = False, token: dict | None = None) -> None:
        self.timeout = timeout
        self.token = token if token is not None else dict(BRIDGE_WAIT_TOKEN)
        self.calls: list[tuple[str, list]] = []

    def dispatch(self, command: list, timestamp: str) -> dict:
        self.calls.append((timestamp, list(command)))
        if self.timeout:
            raise TimeoutError(f"timed out waiting for MCP_RESULT|{timestamp}|")
        assert command[0] == "wait", command
        return dict(self.token)


class WaitTimeoutTests(TempDirTestCase):
    def setUp(self) -> None:
        super().setUp()

    def _server(self, dispatcher: StubWaitDispatcher) -> GameToolServer:
        return make_tool_server(self.tmp_path, dispatcher)

    def _records(self) -> list[dict]:
        return read_jsonl(self.tmp_path / "tool_calls.jsonl")

    def test_success_returns_bridge_token_plus_status_complete(self) -> None:
        disp = StubWaitDispatcher()
        result = self._server(disp).handle_tool_call("wait", {"duration_days": 30})

        # Single dispatch of the logical command; no chunking.
        self.assertEqual(len(disp.calls), 1)
        self.assertEqual(disp.calls[0][1], ["wait", 30])
        # Every pre-026 field survives unchanged.
        for key, value in BRIDGE_WAIT_TOKEN.items():
            self.assertEqual(result[key], value, key)
        # Plus the additive status field.
        self.assertEqual(result["status"], "complete")
        self.assertTrue(result["ok"])

    def test_timeout_returns_honest_status_not_bare_error(self) -> None:
        disp = StubWaitDispatcher(timeout=True)
        result = self._server(disp).handle_tool_call("wait", {"duration_days": 90})

        self.assertEqual(result["status"], "timeout")
        self.assertFalse(result["ok"])
        self.assertEqual(result["action"], "wait")
        self.assertEqual(result["requested_duration_days"], 90)
        # The underlying timeout message is preserved for evidence...
        self.assertIn("timed out", result["error"])
        # ...and a model-facing directive to re-observe is present (this is the
        # whole point vs. the pre-026 bare error). It must name a real, always-on
        # re-observe tool the model can actually call -- there is no tool named
        # `view` in text-only episodes, so the note points at get_my_company/
        # get_my_finances (both report the current date).
        self.assertIn("note", result)
        self.assertIn("get_my_company", result["note"])
        self.assertEqual(len(disp.calls), 1)

    def test_success_records_one_ok_true_row_with_logical_command(self) -> None:
        disp = StubWaitDispatcher()
        self._server(disp).handle_tool_call("wait", {"duration_days": 30})
        records = self._records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["command"], ["wait", 30])
        self.assertEqual(records[0]["name"], "wait")
        self.assertTrue(records[0]["ok"])
        self.assertEqual(records[0]["result"]["status"], "complete")

    def test_year_end_reports_pass_through_to_result_and_evidence_record(self) -> None:
        """084: Python preserves the bridge-owned report unchanged in both lanes."""
        reports = [
            {
                "economy_year": 1951,
                "date": {"raw": 1234, "year": 1952, "month": 1, "day": 1},
                "quarters": [
                    {
                        "quarter_of_year": 1,
                        "income": 2_000,
                        "expenses": -500,
                        "cargo_delivered": 80,
                        "performance_rating": 120,
                        "company_value": 101_000,
                    }
                ],
                "bank_balance": 100_210,
                "loan_amount": 0,
            }
        ]
        disp = StubWaitDispatcher(
            token=BRIDGE_WAIT_TOKEN | {"year_end_reports": reports}
        )

        result = self._server(disp).handle_tool_call("wait", {"duration_days": 30})
        record = self._records()[0]

        self.assertEqual(result["year_end_reports"], reports)
        self.assertEqual(record["result"]["year_end_reports"], reports)

    def test_timeout_records_one_ok_false_row_with_command(self) -> None:
        # The record must log the timeout as ok=False (the deterministic monitor/
        # review lanes key on `ok`) while still carrying the logical command.
        disp = StubWaitDispatcher(timeout=True)
        self._server(disp).handle_tool_call("wait", {"duration_days": 90})
        record = self._records()[0]
        self.assertFalse(record["ok"])
        self.assertEqual(record["command"], ["wait", 90])
        self.assertEqual(record["result"]["status"], "timeout")

    def test_command_for_tool_wait_unchanged(self) -> None:
        self.assertEqual(command_for_tool("wait", {"duration_days": 42}), ["wait", 42])

    def test_out_of_range_duration_is_rejected_not_clamped(self) -> None:
        # The bridge silently clamps to 1..90; if Python let an out-of-range value
        # through, the agent would be told it waited longer (or shorter) than it
        # did. The stated 1..90 range must be enforced as a rejection.
        for bad in (0, 91, -5, 365):
            with self.subTest(duration_days=bad):
                with self.assertRaises(ValueError):
                    command_for_tool("wait", {"duration_days": bad})
                disp = StubWaitDispatcher()
                result = self._server(disp).handle_tool_call(
                    "wait", {"duration_days": bad}
                )
                self.assertFalse(result["ok"])
                self.assertIn("between 1 and 90", result["error"])
                # Nothing reached the bridge, so nothing was clamped.
                self.assertEqual(disp.calls, [])

    def test_range_bounds_are_accepted(self) -> None:
        self.assertEqual(command_for_tool("wait", {"duration_days": 1}), ["wait", 1])
        self.assertEqual(command_for_tool("wait", {"duration_days": 90}), ["wait", 90])

    def test_observe_route_operation_advance_days_rejected_not_clamped(self) -> None:
        # Same silent-clamp defect as wait: the bridge clamps advance_days to
        # 1..90, so the stated schema range must be enforced as a rejection.
        for bad in (0, 91, 365):
            with self.subTest(advance_days=bad), self.assertRaises(ValueError):
                command_for_tool(
                    "wait_and_observe_vehicle", {"vehicle_id": 5, "advance_days": bad}
                )
        self.assertEqual(
            command_for_tool(
                "wait_and_observe_vehicle", {"vehicle_id": 5, "advance_days": 90}
            ),
            ["wait_and_observe_vehicle", 5, 90],
        )
        self.assertEqual(
            command_for_tool("wait_and_observe_vehicle", {"vehicle_id": 5}),
            ["wait_and_observe_vehicle", 5, 24],
        )


if __name__ == "__main__":
    unittest.main()
