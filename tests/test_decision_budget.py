"""022 Bundle 1: harness-enforced decision budget, end_episode, budget override.

All episode endings are one mechanism — remaining budget -> 0 — with three
triggers: natural exhaustion at N, the model-invoked end_episode tool, and the
out-of-band budget_override.json sentinel (monitor abort). Exhaustion is a
factual logged result, never hidden state, and report_feedback stays callable
after it.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from openttd_mcp.server import (
    END_EPISODE_TOOL_NAME,
    EPISODE_STOP_SIGNAL,
    PRIMITIVE_TOOLS,
    QUERY_TOOLS,
    GameToolServer,
    command_for_tool,
    registry_snapshot,
)
from tests.support import (
    TempDirTestCase,
    feedback_args,
    make_tool_server,
    read_jsonl,
)

FEEDBACK_ARGS = feedback_args()
BAD_FEEDBACK_KIND_ARGS = feedback_args(kind="give_me_candidates")


def make_server(
    tmp_path: Path,
    budget: int | None,
    *,
    budget_echo: bool = True,
    end_episode_mode: str = "standard",
) -> GameToolServer:
    return make_tool_server(
        tmp_path,
        lambda command, timestamp: {"ok": True, "action": command[0]},
        decision_budget=budget,
        budget_echo=budget_echo,
        end_episode_mode=end_episode_mode,
    )


class DecisionBudgetTests(TempDirTestCase):
    def setUp(self) -> None:
        super().setUp()

    def test_budget_exhaustion_returns_factual_result_and_stops_dispatch(self) -> None:
        server = make_server(self.tmp_path, budget=3)
        for _ in range(3):
            self.assertTrue(server.handle_tool_call("list_towns", {})["ok"])
        refused = server.handle_tool_call("list_towns", {})
        refused_again = server.handle_tool_call("get_my_finances", {})

        for result in (refused, refused_again):
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"], EPISODE_STOP_SIGNAL)
            self.assertEqual(result["decisions_used"], 3)
            self.assertEqual(result["decision_budget"], 3)
            # No contradiction: an exhausted refusal always says 0 remaining.
            self.assertEqual(result["decisions_remaining"], 0)
        self.assertEqual(len(server.dispatcher.commands), 3)
        records = read_jsonl(self.tmp_path / "tool_calls.jsonl")
        self.assertEqual(len(records), 5)
        self.assertIsNone(records[3]["command"])
        self.assertEqual(records[3]["result"]["error"], EPISODE_STOP_SIGNAL)

    def test_results_expose_post_call_budget_counters(self) -> None:
        """022: every result under an enforced budget carries decisions_used /
        decisions_remaining, counted AFTER the current call (episode-002
        hidden_information:remaining_decision_budget)."""
        server = make_server(self.tmp_path, budget=4)
        for i in range(3):
            result = server.handle_tool_call("list_towns", {})
            self.assertEqual(result["decisions_used"], i + 1)
            self.assertEqual(result["decisions_remaining"], 4 - (i + 1))
        # Errored calls consume a decision and report it too.
        bad = server.handle_tool_call("buy_vehicle", {"depot_tile": 1})
        self.assertFalse(bad["ok"])
        self.assertNotEqual(bad["error"], EPISODE_STOP_SIGNAL)
        self.assertEqual(bad["decisions_used"], 4)
        self.assertEqual(bad["decisions_remaining"], 0)
        # The jsonl evidence records carry the same fields as the MCP results.
        records = read_jsonl(self.tmp_path / "tool_calls.jsonl")
        self.assertEqual(
            [
                (r["result"]["decisions_used"], r["result"]["decisions_remaining"])
                for r in records
            ],
            [(1, 3), (2, 2), (3, 1), (4, 0)],
        )

    def test_budget_echo_off_hides_counters_on_normal_error_and_exhaustion_results(
        self,
    ) -> None:
        server = make_server(self.tmp_path, budget=2, budget_echo=False)

        normal = server.handle_tool_call("list_towns", {})
        bad = server.handle_tool_call("buy_vehicle", {"depot_tile": 1})
        refused = server.handle_tool_call("list_towns", {})

        self.assertTrue(normal["ok"])
        self.assertFalse(bad["ok"])
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["error"], EPISODE_STOP_SIGNAL)
        for result in (normal, bad, refused):
            self.assertNotIn("decisions_used", result)
            self.assertNotIn("decisions_remaining", result)
            self.assertNotIn("decision_budget", result)
            self.assertNotIn("budget_cut", result)

        # Evidence honesty: the log stores the same model-visible result dict,
        # so harness decision accounting must never depend on echoed counters.
        for record in read_jsonl(self.tmp_path / "tool_calls.jsonl"):
            self.assertNotIn("decisions_used", record["result"])
            self.assertNotIn("decisions_remaining", record["result"])

    def test_no_budget_means_unlimited(self) -> None:
        server = make_server(self.tmp_path, budget=None)
        for _ in range(50):
            result = server.handle_tool_call("list_towns", {})
            self.assertTrue(result["ok"])
            # No enforced budget: results carry no budget fields at all.
            self.assertNotIn("decisions_used", result)
            self.assertNotIn("decisions_remaining", result)
        self.assertIsNone(server.budget_remaining())
        self.assertEqual(len(server.dispatcher.commands), 50)

    def test_report_feedback_stays_callable_after_exhaustion(self) -> None:
        server = make_server(self.tmp_path, budget=1)
        server.handle_tool_call("list_towns", {})
        self.assertFalse(server.handle_tool_call("list_towns", {})["ok"])

        result = server.handle_tool_call("report_feedback", FEEDBACK_ARGS)

        self.assertTrue(result["ok"])
        self.assertTrue(result["recorded"])
        entries = read_jsonl(self.tmp_path / "feedback.jsonl")
        self.assertEqual(len(entries), 1)
        # The carve-out consumes nothing: used never exceeds the budget.
        self.assertEqual(server.decisions_used, 1)
        # The carve-out result still reports the factual (exhausted) counters.
        self.assertEqual(result["decisions_used"], 1)
        self.assertEqual(result["decisions_remaining"], 0)

    def test_budget_echo_off_also_hides_feedback_counters_after_exhaustion(
        self,
    ) -> None:
        server = make_server(self.tmp_path, budget=1, budget_echo=False)
        server.handle_tool_call("list_towns", {})
        self.assertEqual(
            server.handle_tool_call("list_towns", {})["error"], EPISODE_STOP_SIGNAL
        )

        result = server.handle_tool_call("report_feedback", FEEDBACK_ARGS)

        self.assertTrue(result["ok"])
        self.assertNotIn("decisions_used", result)
        self.assertNotIn("decisions_remaining", result)

    def test_report_feedback_before_exhaustion_does_not_consume_budget(self) -> None:
        server = make_server(self.tmp_path, budget=3)
        accepted = server.handle_tool_call("report_feedback", FEEDBACK_ARGS)
        rejected = server.handle_tool_call("report_feedback", BAD_FEEDBACK_KIND_ARGS)

        self.assertTrue(accepted["ok"])
        self.assertFalse(rejected["ok"])
        self.assertIn("kind", rejected["error"])
        self.assertEqual(server.decisions_used, 0)
        self.assertEqual(server.budget_remaining(), 3)
        for result in (accepted, rejected):
            self.assertEqual(result["decisions_used"], 0)
            self.assertEqual(result["decisions_remaining"], 3)

    def test_feedback_interleaved_with_gameplay_does_not_shift_exhaustion(self) -> None:
        server = make_server(self.tmp_path, budget=3)

        server.handle_tool_call("report_feedback", FEEDBACK_ARGS)
        self.assertTrue(server.handle_tool_call("list_towns", {})["ok"])
        server.handle_tool_call("report_feedback", BAD_FEEDBACK_KIND_ARGS)
        self.assertTrue(server.handle_tool_call("get_my_finances", {})["ok"])
        server.handle_tool_call("report_feedback", FEEDBACK_ARGS)
        third_gameplay = server.handle_tool_call("list_towns", {})
        refused = server.handle_tool_call("list_towns", {})

        self.assertTrue(third_gameplay["ok"])
        self.assertEqual(third_gameplay["decisions_used"], 3)
        self.assertEqual(third_gameplay["decisions_remaining"], 0)
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["error"], EPISODE_STOP_SIGNAL)
        self.assertEqual(refused["decisions_used"], 3)
        self.assertEqual(server.decisions_used, 3)
        self.assertEqual(len(server.dispatcher.commands), 3)

    def test_end_episode_requires_reason_and_cuts_budget(self) -> None:
        server = make_server(self.tmp_path, budget=100)
        server.handle_tool_call("list_towns", {})

        missing = server.handle_tool_call(END_EPISODE_TOOL_NAME, {})
        self.assertFalse(missing["ok"])
        self.assertIn("reason", missing["error"])
        self.assertIsNone(server.budget_cut)

        ended = server.handle_tool_call(
            END_EPISODE_TOOL_NAME, {"reason": "blocked on missing engine catalog"}
        )
        self.assertTrue(ended["ok"])
        self.assertTrue(ended["episode_ended"])
        self.assertEqual(server.budget_remaining(), 0)
        # end_episode's own result reflects the cut it just applied.
        self.assertEqual(ended["decisions_remaining"], 0)
        self.assertEqual(ended["decisions_used"], server.decisions_used)

        refused = server.handle_tool_call(
            "buy_vehicle", {"depot_tile": 1, "engine_id": 2}
        )
        self.assertEqual(refused["error"], EPISODE_STOP_SIGNAL)
        self.assertEqual(refused["decisions_remaining"], 0)
        self.assertEqual(refused["budget_cut"]["source"], END_EPISODE_TOOL_NAME)
        self.assertEqual(
            refused["budget_cut"]["reason"], "blocked on missing engine catalog"
        )

        feedback = server.handle_tool_call("report_feedback", FEEDBACK_ARGS)
        self.assertTrue(feedback["ok"])

        records = read_jsonl(self.tmp_path / "tool_calls.jsonl")
        end_record = next(
            r for r in records if r["name"] == END_EPISODE_TOOL_NAME and r["ok"]
        )
        self.assertIsNone(end_record["command"])
        self.assertEqual(
            end_record["result"]["reason"], "blocked on missing engine catalog"
        )
        # No gameplay command was dispatched by end_episode or after the cut.
        self.assertEqual([c[1][0] for c in server.dispatcher.commands], ["list_towns"])

    def test_distress_end_episode_result_has_no_budget_mechanism_payload(self) -> None:
        server = make_server(
            self.tmp_path, budget=100, budget_echo=False, end_episode_mode="distress"
        )
        ended = server.handle_tool_call(
            END_EPISODE_TOOL_NAME, {"reason": "cannot make meaningful progress"}
        )

        self.assertTrue(ended["ok"])
        self.assertTrue(ended["episode_ended"])
        self.assertNotIn("decisions_used", ended)
        self.assertNotIn("decisions_remaining", ended)
        self.assertNotIn("decision_budget", ended)
        self.assertNotIn("budget", ended["note"].lower())

        refused = server.handle_tool_call("list_towns", {})
        self.assertEqual(refused["error"], EPISODE_STOP_SIGNAL)
        self.assertNotIn("budget_cut", refused)

    def test_end_episode_works_without_an_enforced_budget(self) -> None:
        server = make_server(self.tmp_path, budget=None)
        ended = server.handle_tool_call(END_EPISODE_TOOL_NAME, {"reason": "done"})
        self.assertTrue(ended["ok"])
        self.assertEqual(server.budget_remaining(), 0)
        self.assertEqual(
            server.handle_tool_call("list_towns", {})["error"], EPISODE_STOP_SIGNAL
        )

    def test_budget_override_sentinel_cuts_budget_and_is_logged(self) -> None:
        server = make_server(self.tmp_path, budget=100)
        server.handle_tool_call("list_towns", {})

        override = {
            "reason": "monitor abort: 12 consecutive buy_vehicle failures",
            "source": "monitor",
        }
        (self.tmp_path / "budget_override.json").write_text(json.dumps(override))

        refused = server.handle_tool_call("list_towns", {})
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["error"], EPISODE_STOP_SIGNAL)
        self.assertEqual(refused["budget_cut"]["source"], "budget_override")
        self.assertEqual(refused["budget_cut"]["reason"], override["reason"])
        self.assertEqual(len(server.dispatcher.commands), 1)

        records = read_jsonl(self.tmp_path / "tool_calls.jsonl")
        self.assertEqual(
            [r["name"] for r in records],
            ["list_towns", "budget_override_applied", "list_towns"],
        )
        applied = records[1]
        self.assertTrue(applied["ok"])
        self.assertIsNone(applied["command"])
        self.assertEqual(applied["arguments"], override)
        self.assertEqual(applied["result"]["decisions_used"], 1)
        self.assertTrue(applied["timestamp"].startswith(f"{server.experiment}-"))
        self.assertEqual(applied["started_at_unix"], applied["finished_at_unix"])

    def test_unparseable_override_still_cuts_with_recorded_reason(self) -> None:
        server = make_server(self.tmp_path, budget=100)
        (self.tmp_path / "budget_override.json").write_text("{not json")
        refused = server.handle_tool_call("list_towns", {})
        self.assertEqual(refused["error"], EPISODE_STOP_SIGNAL)
        self.assertIn("unparseable", refused["budget_cut"]["reason"])


class RegistryBrightLineTests(TempDirTestCase):
    """Bundle-1 additions must not touch the gameplay tool surface."""

    GAMEPLAY_TOOLS = PRIMITIVE_TOOLS | QUERY_TOOLS

    def test_gameplay_registry_entries_are_unchanged(self) -> None:
        registry = {
            tool["name"]: tool
            for tool in registry_snapshot(enable_skill=False)["tools"]
        }
        self.assertEqual(
            set(registry) - {"report_feedback", END_EPISODE_TOOL_NAME},
            self.GAMEPLAY_TOOLS,
        )

    def test_end_episode_has_no_bridge_command_and_no_id_typed_args(self) -> None:
        with self.assertRaises(ValueError):
            command_for_tool(END_EPISODE_TOOL_NAME, {"reason": "x"})
        spec = next(
            tool
            for tool in registry_snapshot(enable_skill=False)["tools"]
            if tool["name"] == END_EPISODE_TOOL_NAME
        )
        self.assertFalse(spec["mutates_game"])
        self.assertEqual(spec["inputSchema"]["required"], ["reason"])
        self.assertEqual(
            spec["inputSchema"]["properties"], {"reason": {"type": "string"}}
        )

    def test_distress_registry_records_mode(self) -> None:
        registry = registry_snapshot(enable_skill=False, end_episode_mode="distress")
        self.assertEqual(registry["end_episode_mode"], "distress")

    def test_no_budget_keys_in_reachable_results(self) -> None:
        """Hidden-horizon leak audit, result half: with a budget enforced but
        echo off (distress mode), no reachable result — gameplay, compacted
        sensor, error, feedback, end_episode, post-cut refusal — may carry a
        budget-mechanism key anywhere in its payload."""
        tmp_path = self.tmp_path

        def fake_dispatch(command: list, timestamp: str) -> dict:
            if command[0] == "get_region":
                return {
                    "ok": True,
                    "action": "get_region",
                    "tiles": [
                        {
                            "tile": 1,
                            "x": 1,
                            "y": 0,
                            "passenger_acceptance": 38,
                            "passenger_production": 6,
                        }
                    ],
                }
            return {"ok": True, "action": command[0]}

        server = make_tool_server(
            tmp_path,
            fake_dispatch,
            feedback_log=tmp_path / "feedback.jsonl",
            decision_budget=10,
            budget_echo=False,
            end_episode_mode="distress",
        )
        results = [
            server.handle_tool_call("list_towns", {}),
            server.handle_tool_call(
                "get_region",
                {
                    "center_tile": 1,
                    "radius": 1,
                    "fields": ["passenger_acceptance", "passenger_production"],
                },
            ),
            server.handle_tool_call("buy_vehicle", {"depot_tile": 1}),
            server.handle_tool_call("report_feedback", FEEDBACK_ARGS),
            server.handle_tool_call(END_EPISODE_TOOL_NAME, {"reason": "stuck"}),
            server.handle_tool_call("list_towns", {}),
        ]
        for result in results:
            self.assert_budget_keys_absent(result)

    def assert_budget_keys_absent(self, payload: object) -> None:
        if isinstance(payload, dict):
            for key, value in payload.items():
                self.assertNotIn(
                    key,
                    {
                        "decision_budget",
                        "decisions_used",
                        "decisions_remaining",
                        "budget_cut",
                    },
                )
                self.assert_budget_keys_absent(value)
        elif isinstance(payload, list):
            for item in payload:
                self.assert_budget_keys_absent(item)


if __name__ == "__main__":
    unittest.main()
