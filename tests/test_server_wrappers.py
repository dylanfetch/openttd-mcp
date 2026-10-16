from __future__ import annotations

import asyncio
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, call, patch

from mcp.server.fastmcp.exceptions import ToolError

from openttd_mcp.server import (
    EPISODE_STOP_SIGNAL,
    RESULT_PREFIX,
    FileLogBridgeDispatcher,
    build_fastmcp,
    main,
    registry_snapshot,
    run_scripted,
)
from tests.support import TempDirTestCase, fastmcp_tools_by_name, make_tool_server

SCHEMA_CONSTRAINT_KEYS = frozenset(
    {
        "const",
        "enum",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "maximum",
        "maxItems",
        "maxLength",
        "minimum",
        "minItems",
        "minLength",
        "multipleOf",
        "pattern",
    }
)


def schema_facts(value: object) -> list[tuple[str, str]]:
    """Collect types and constraints without depending on nullable schema shape."""
    facts: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in SCHEMA_CONSTRAINT_KEYS:
                facts.append((key, json.dumps(child, sort_keys=True)))
            elif key == "type":
                types = child if isinstance(child, list) else [child]
                facts.extend(
                    ("type", json.dumps(item, sort_keys=True))
                    for item in types
                    if item != "null"
                )
            elif key == "properties" and isinstance(child, dict):
                for property_schema in child.values():
                    facts.extend(schema_facts(property_schema))
            else:
                facts.extend(schema_facts(child))
    elif isinstance(value, list):
        for child in value:
            facts.extend(schema_facts(child))
    return sorted(facts)


class FileLogBridgeDispatcherTests(TempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.tmp_path
        self.log = self.root / "openttd.log"

    def test_dispatch_returns_the_parsed_result_and_writes_the_command_file(
        self,
    ) -> None:
        self.log.write_text("old log content\n")
        timestamp = "turn-1"
        result_line = f'noise {RESULT_PREFIX}{timestamp}|{{"ok": true, "cost": 12}}'
        dispatcher = FileLogBridgeDispatcher(
            self.root,
            self.log,
            timeout=1,
            poll_interval=0,
        )

        with (
            patch(
                "openttd_mcp.server._tail_lines_since", return_value=([result_line], 99)
            ),
            patch("openttd_mcp.server.time.time", side_effect=[0.0, 0.0]),
        ):
            result = dispatcher.dispatch(["list_towns"], timestamp)

        self.assertEqual(result, {"ok": True, "cost": 12})
        self.assertIn('timestamp = "turn-1"', (self.root / "commands.nut").read_text())

    def test_dispatch_acks_a_parsed_result_on_the_next_command_write(self) -> None:
        """084: the first write has no ack; a parsed result supplies the next one."""
        first_timestamp = "turn-ack-1"
        second_timestamp = "turn-ack-2"
        first_line = f'{RESULT_PREFIX}{first_timestamp}|{{"ok": true}}'
        second_line = f'{RESULT_PREFIX}{second_timestamp}|{{"ok": true}}'
        dispatcher = FileLogBridgeDispatcher(
            self.root,
            self.log,
            timeout=1,
            poll_interval=0,
        )

        with (
            patch(
                "openttd_mcp.server._tail_lines_since",
                side_effect=[([first_line], 10), ([second_line], 20)],
            ),
            patch(
                "openttd_mcp.server.time.time",
                side_effect=[0.0, 0.0, 0.0, 0.0],
            ),
        ):
            dispatcher.dispatch(["list_towns"], first_timestamp)
            first_command = (self.root / "commands.nut").read_text()
            dispatcher.dispatch(["list_towns"], second_timestamp)

        second_command = (self.root / "commands.nut").read_text()
        # The field is present from the very first write -- empty marks an
        # ack-capable writer with nothing parsed yet, so the session's first
        # dispatch is already protected against clear-on-emit loss.
        self.assertIn('ack_result_timestamp = "";', first_command)
        self.assertIn(f'ack_result_timestamp = "{first_timestamp}"', second_command)
        self.assertEqual(dispatcher.last_result_timestamp, second_timestamp)

    def test_dispatch_timeout_does_not_ack_the_timed_out_timestamp(self) -> None:
        """084: an unread result cannot replace the last proven acknowledgement."""
        dispatcher = FileLogBridgeDispatcher(
            self.root,
            self.log,
            timeout=1,
            poll_interval=0,
            last_result_timestamp="turn-read",
        )

        with (
            patch("openttd_mcp.server.time.time", side_effect=[0.0, 2.0]),
            self.assertRaises(TimeoutError),
        ):
            dispatcher.dispatch(["wait", 30], "turn-timeout")

        command = (self.root / "commands.nut").read_text()
        self.assertIn('ack_result_timestamp = "turn-read"', command)
        self.assertEqual(dispatcher.last_result_timestamp, "turn-read")

    def test_result_split_across_appends_and_never_terminated_is_matched(self) -> None:
        """TD-23 end to end: a partially flushed result must never be lost.

        The dispatcher polls the log file itself here (``_tail_lines_since`` is
        not patched). Under the lossy tail the first half was consumed as a
        line and the offset advanced past it, so neither poll ever saw a whole
        ``MCP_RESULT|`` marker and the dispatch died of timeout.

        The second half never gets its newline: when the process dies mid-line
        the harness log pump forwards the final fragment verbatim, so the
        marker can be complete while the terminator never arrives. The
        dispatcher peeks at that tail without consuming it.
        """
        self.log.write_text("old log content\n")
        timestamp = "turn-split"
        result_line = f'noise {RESULT_PREFIX}{timestamp}|{{"ok": true, "cost": 12}}'
        split = result_line.index(timestamp) + 3
        # The split falls inside the marker, so neither fragment matches it.
        self.assertNotIn(f"{RESULT_PREFIX}{timestamp}|", result_line[:split])
        self.assertNotIn(f"{RESULT_PREFIX}{timestamp}|", result_line[split:])
        appends = [result_line[:split], result_line[split:]]

        def flush_next_append(_seconds: float) -> None:
            if appends:
                with self.log.open("a") as handle:
                    handle.write(appends.pop(0))

        dispatcher = FileLogBridgeDispatcher(
            self.root,
            self.log,
            timeout=5,
            poll_interval=0,
        )
        with patch("openttd_mcp.server.time.sleep", side_effect=flush_next_append):
            result = dispatcher.dispatch(["list_towns"], timestamp)

        self.assertEqual(result, {"ok": True, "cost": 12})
        self.assertEqual(appends, [])
        # The tail was peeked at, never consumed: it is still on disk unended.
        self.assertFalse(self.log.read_text().endswith("\n"))

    def test_timeout_excerpt_is_bounded_and_keeps_the_unterminated_tail(self) -> None:
        """The model-visible timeout excerpt is bounded but keeps the crash tail.

        A log that stops mid-line is the signature of OpenTTD dying, so the
        unterminated fragment must appear in the excerpt even though the poll
        loop never consumes it -- while older lines and over-long lines are
        cut so a timeout cannot dump the whole recent log into model context.
        """
        lines = [f"line-{index}-" + "x" * 250 for index in range(4)]

        def flush_log(_seconds: float) -> None:
            if not self.log.exists() or not self.log.read_text():
                # Three terminated lines, then a final line that never ends.
                self.log.write_text("\n".join(lines[:3]) + "\n" + lines[3])

        dispatcher = FileLogBridgeDispatcher(
            self.root,
            self.log,
            timeout=1,
            poll_interval=0.25,
        )

        with (
            patch(
                "openttd_mcp.server.time.time",
                side_effect=[0.0, 0.0, 0.0, 2.0],
            ),
            patch("openttd_mcp.server.time.sleep", side_effect=flush_log),
            self.assertRaisesRegex(TimeoutError, "recent log lines") as raised,
        ):
            dispatcher.dispatch(["list_towns"], "turn-timeout")

        message = str(raised.exception)
        self.assertIn("line-3-", message)
        self.assertNotIn("line-0-", message)
        self.assertNotIn("x" * 201, message)

    def test_stop_sentinel_interrupts_an_in_flight_bridge_call(self) -> None:
        """A horizon reached during wait must not burn the bridge timeout."""
        stop_path = self.root / "budget_override.json"
        dispatcher = FileLogBridgeDispatcher(
            self.root,
            self.log,
            timeout=240,
            poll_interval=0.2,
            stop_path=stop_path,
        )
        server = make_tool_server(self.root, dispatcher, decision_budget=None)

        def reach_horizon(_seconds: float) -> None:
            stop_path.write_text(
                json.dumps(
                    {
                        "source": "game_time_limit",
                        "rule": "game_duration_reached",
                        "reason": "exact horizon reached",
                    }
                )
            )

        with patch("openttd_mcp.server.time.sleep", side_effect=reach_horizon):
            result = server.handle_tool_call("wait", {"duration_days": 30})

        self.assertEqual(result["error"], EPISODE_STOP_SIGNAL)
        serialized = json.dumps(result, sort_keys=True)
        self.assertNotIn("decision_budget", serialized)
        self.assertNotIn("decisions_used", serialized)
        records = [
            json.loads(line)
            for line in (self.root / "tool_calls.jsonl").read_text().splitlines()
        ]
        self.assertEqual(
            [record["name"] for record in records],
            ["budget_override_applied", "wait"],
        )
        self.assertTrue(records[0]["transport_ok"])
        self.assertTrue(records[1]["transport_ok"])


class FastMCPWrapperTests(TempDirTestCase):
    def test_live_surface_schema_arguments_match_registry(self) -> None:
        """The evidence snapshot must match live argument names and schema facts."""
        root = self.tmp_path
        skill_path = root / "skill.json"
        skill_path.write_text(json.dumps({"name": "test-skill", "version": 1}))
        server = make_tool_server(
            root,
            enable_skill_path=skill_path,
            vision_enabled=True,
        )
        served = fastmcp_tools_by_name(server)
        recorded = {
            tool["name"]: tool["inputSchema"]
            for tool in registry_snapshot(enable_skill=True, enable_vision=True)[
                "tools"
            ]
        }

        self.assertEqual(set(served), set(recorded))
        for name, tool in served.items():
            with self.subTest(name=name):
                live_schema = tool.inputSchema
                # Keep this independent of the forwarding test below: that
                # oracle derives its calls from the served schema and cannot
                # detect an argument deleted from both wrapper and call.
                self.assertEqual(
                    set(live_schema.get("properties", {})),
                    set(recorded[name]["properties"]),
                )
                self.assertEqual(
                    set(live_schema.get("required", [])),
                    set(recorded[name]["required"]),
                )
                for argument, recorded_schema in recorded[name]["properties"].items():
                    self.assertEqual(
                        schema_facts(live_schema["properties"][argument]),
                        schema_facts(recorded_schema),
                        argument,
                    )

    def test_operations_surface_integer_arguments_are_strict(self) -> None:
        """The served path must not coerce values the recorded schema rejects."""
        server = make_tool_server(self.tmp_path)
        mcp = build_fastmcp(server)
        cases = (
            (
                "build_one_way_road",
                {"from_tile": True, "to_tile": 20},
            ),
            ("reverse_vehicle", {"vehicle_id": "7"}),
            (
                "insert_conditional_order",
                {
                    "vehicle_id": 7,
                    "position": 2,
                    "condition": "RELIABILITY",
                    "compare": "LESS_THAN",
                    "value": True,
                    "jump_to": 0,
                },
            ),
            ("skip_to_order", {"vehicle_id": 7, "position": "0"}),
            (
                "copy_orders",
                {"vehicle_id": 8, "source_vehicle_id": True},
            ),
            (
                "share_orders",
                {"vehicle_id": 8.0, "source_vehicle_id": 7},
            ),
            ("unshare_orders", {"vehicle_id": True}),
        )

        with patch.object(server, "handle_tool_call") as handle:
            for name, arguments in cases:
                with (
                    self.subTest(name=name, arguments=arguments),
                    self.assertRaisesRegex(ToolError, "integer"),
                ):
                    asyncio.run(mcp.call_tool(name, arguments))
                handle.assert_not_called()


class ServerCliTests(TempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.tmp_path
        self.base_args = [
            "--bridge-root",
            str(self.root),
            "--tool-log",
            str(self.root / "tool_calls.jsonl"),
        ]

    def test_dump_registry_prints_json_without_requiring_a_dispatcher(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main([*self.base_args, "--dump-registry"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(output.getvalue())["transport"], "mcp-stdio")

    def test_main_names_missing_dispatchers_for_scripted_and_serve_modes(self) -> None:
        script = self.root / "script.jsonl"
        script.write_text("")
        cases = (
            ["--scripted", str(script)],
            [],
        )
        for extra_args in cases:
            with (
                self.subTest(extra_args=extra_args),
                self.assertRaisesRegex(SystemExit, "--openttd-log"),
            ):
                main([*self.base_args, *extra_args])

    def test_scripted_mode_marks_no_llm_and_honors_require_ok(self) -> None:
        script = self.root / "script.jsonl"
        marker = self.root / "evidence" / "no-gameplay-llm.txt"
        script.write_text(
            '{"name": "list_towns"}\n'
            '{"tool": "wait", "arguments": {"duration_days": 1}, "require_ok": false}\n'
        )
        server = Mock()
        server.handle_tool_call.side_effect = (
            {"ok": True},
            {"ok": False, "error": "ignored"},
        )

        self.assertEqual(run_scripted(server, script, marker), 0)
        self.assertEqual(marker.read_text(), "true\n")
        self.assertEqual(
            server.handle_tool_call.call_args_list,
            [
                call("list_towns", {}),
                call("wait", {"duration_days": 1}),
            ],
        )

        server.handle_tool_call.side_effect = None
        server.handle_tool_call.return_value = {"ok": False, "error": "stopped"}
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(run_scripted(server, script), 2)
        self.assertEqual(json.loads(output.getvalue())["failed_tool"], "list_towns")

        script.write_text('{"arguments": {}}\n')
        with self.assertRaisesRegex(ValueError, "missing tool name"):
            run_scripted(server, script)

        empty_script = self.root / "empty.jsonl"
        empty_script.write_text("")
        self.assertEqual(
            main(
                [
                    *self.base_args,
                    "--openttd-log",
                    str(self.root / "openttd.log"),
                    "--scripted",
                    str(empty_script),
                ]
            ),
            0,
        )

    def test_vision_serve_mode_wires_admin_credentials(self) -> None:
        password_file = self.root / "admin-password"
        password_file.write_text("secret\n")
        fake_mcp = Mock()

        with patch("openttd_mcp.server.build_fastmcp", return_value=fake_mcp) as build:
            exit_code = main(
                [
                    *self.base_args,
                    "--openttd-log",
                    str(self.root / "openttd.log"),
                    "--enable-vision",
                    "--admin-port",
                    "3977",
                    "--admin-password-file",
                    str(password_file),
                    "--screenshot-dir",
                    str(self.root / "screenshots"),
                ]
            )

        self.assertEqual(exit_code, 0)
        server = build.call_args.args[0]
        self.assertEqual(server.admin_config.port, 3977)
        self.assertEqual(server.admin_config.password, "secret")

    def test_vision_mode_rejects_incomplete_admin_configuration(self) -> None:
        with self.assertRaisesRegex(SystemExit, "--enable-vision"):
            main([*self.base_args, "--enable-vision"])

        server = make_tool_server(self.root, vision_enabled=True)
        with self.assertRaisesRegex(RuntimeError, "--admin-port"):
            server._ensure_screenshot_source()


if __name__ == "__main__":
    unittest.main()
