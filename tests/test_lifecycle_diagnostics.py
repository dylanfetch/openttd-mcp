"""Bundle-0 lifecycle hardening: bounded reads + marker-specific diagnostics.

Drives partial OpenTTD output through ``read_until`` /
``read_observation_and_snapshot`` / ``save_game`` with a fake process over a
real OS pipe, asserting that deadlines are honored (a wedged partial line can
never hang the controller) and that timeout errors name the signal that never
arrived.
"""

from __future__ import annotations

import io
import json
import os
import threading
import time
import unittest
import unittest.mock

from openttd_mcp.lifecycle import (
    DEFAULT_TOWN_COUNT,
    assert_expected_town_count,
    read_map_snapshot,
    read_observation_and_snapshot,
    read_until,
    save_game,
)
from tests.support import TempDirTestCase


class FakeOpenTTDProc:
    """subprocess.Popen stand-in whose stdout is a real pipe fd."""

    def __init__(self) -> None:
        read_fd, self._write_fd = os.pipe()
        self.stdout = os.fdopen(read_fd, "r")
        self.stdin = io.StringIO()
        self.returncode: int | None = None

    def emit(self, text: str) -> None:
        os.write(self._write_fd, text.encode("utf-8"))

    def emit_later(self, delay: float, text: str) -> None:
        timer = threading.Timer(delay, self.emit, args=(text,))
        timer.daemon = True
        timer.start()

    def _close_write_fd(self) -> None:
        # Close exactly once: fd numbers are reused, so a second os.close on
        # the stale number could kill an unrelated descriptor.
        if self._write_fd >= 0:
            os.close(self._write_fd)
            self._write_fd = -1

    def exit(self, returncode: int) -> None:
        self.returncode = returncode
        self._close_write_fd()

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int | None:
        return self.returncode

    def close(self) -> None:
        self._close_write_fd()
        self.stdout.close()


class LifecycleDiagnosticsTests(TempDirTestCase):
    def setUp(self) -> None:
        self.proc = FakeOpenTTDProc()
        self.addCleanup(self.proc.close)
        super().setUp()
        self.run_dir = self.tmp_path

    def test_partial_line_never_hangs_past_deadline(self) -> None:
        self.proc.emit("stuck mid-line without a newline")
        started = time.monotonic()
        with self.assertRaises(TimeoutError) as ctx:
            read_until(self.proc, self.run_dir, "NEVER_ARRIVES", timeout=0.5)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 3.0, "readline-style hang: deadline not honored")
        self.assertIn("NEVER_ARRIVES", str(ctx.exception))
        self.assertIn("stuck mid-line", str(ctx.exception))

    def test_read_until_timeout_names_marker_and_recent_lines(self) -> None:
        self.proc.emit("dbg: alpha\ndbg: beta\n")
        with self.assertRaises(TimeoutError) as ctx:
            read_until(self.proc, self.run_dir, "MCP_BRIDGE_READY", timeout=0.4)
        message = str(ctx.exception)
        self.assertIn("MCP_BRIDGE_READY", message)
        self.assertIn("dbg: beta", message)

    def test_line_split_across_writes_is_reassembled(self) -> None:
        self.proc.emit("MCP_RES")
        self.proc.emit_later(0.1, "ULT|t1|{}\n")
        line = read_until(self.proc, self.run_dir, "MCP_RESULT|t1|", timeout=5)
        self.assertEqual(line, "MCP_RESULT|t1|{}\n")

    def test_partial_line_survives_across_calls(self) -> None:
        self.proc.emit("first\nMCP_RES")
        read_until(self.proc, self.run_dir, "first", timeout=5)
        self.proc.emit_later(0.1, "ULT|t2|{}\n")
        line = read_until(self.proc, self.run_dir, "MCP_RESULT|t2|", timeout=5)
        self.assertEqual(line, "MCP_RESULT|t2|{}\n")

    def test_process_exit_raises_with_returncode(self) -> None:
        self.proc.emit("last words\n")
        self.proc.exit(3)
        with self.assertRaises(RuntimeError) as ctx:
            read_until(self.proc, self.run_dir, "NEVER_ARRIVES", timeout=5)
        self.assertIn("rc=3", str(ctx.exception))

    def test_marker_written_before_exit_is_still_found(self) -> None:
        self.proc.emit("Map successfully saved\n")
        self.proc.exit(0)
        line = read_until(self.proc, self.run_dir, "Map successfully saved", timeout=5)
        self.assertIn("Map successfully saved", line)

    def test_unterminated_marker_line_before_exit_is_still_found(self) -> None:
        self.proc.emit("MCP_BRIDGE_READY")
        self.proc.exit(0)
        line = read_until(self.proc, self.run_dir, "MCP_BRIDGE_READY", timeout=5)
        self.assertIn("MCP_BRIDGE_READY", line)

    def test_snapshot_timeout_names_missing_objects_and_row_progress(self) -> None:
        self.proc.emit("MCP_OBSERVATION|" + json.dumps({"tick": 1}) + "\n")
        self.proc.emit(
            "MCP_MAP_SNAPSHOT_BEGIN|" + json.dumps({"map_size": [64, 64]}) + "\n"
        )
        for row in range(3):
            self.proc.emit(f"MCP_MAP_SNAPSHOT_TILES|{row}|[]\n")
        with self.assertRaises(TimeoutError) as ctx:
            read_observation_and_snapshot(self.proc, self.run_dir, timeout=0.4)
        message = str(ctx.exception)
        self.assertIn("observation received", message)
        self.assertIn("snapshot BEGIN received", message)
        self.assertIn("3 of 64", message)
        self.assertIn("OBJECTS never arrived", message)

    def test_snapshot_timeout_before_any_signal_names_both(self) -> None:
        with self.assertRaises(TimeoutError) as ctx:
            read_observation_and_snapshot(self.proc, self.run_dir, timeout=0.3)
        message = str(ctx.exception)
        self.assertIn("observation never arrived", message)
        self.assertIn("snapshot BEGIN never arrived", message)

    def test_snapshot_timeout_names_begin_without_any_tile_rows(self) -> None:
        self.proc.emit("MCP_OBSERVATION|" + json.dumps({"tick": 1}) + "\n")
        self.proc.emit(
            "MCP_MAP_SNAPSHOT_BEGIN|" + json.dumps({"map_size": [64, 64]}) + "\n"
        )
        with self.assertRaises(TimeoutError) as ctx:
            read_observation_and_snapshot(self.proc, self.run_dir, timeout=0.3)
        message = str(ctx.exception)
        self.assertIn("TILES rows never arrived", message)
        self.assertIn("64", message)

    def test_snapshot_before_observation_is_rejected(self) -> None:
        self.proc.emit(
            "MCP_MAP_SNAPSHOT_BEGIN|" + json.dumps({"map_size": [1, 1]}) + "\n"
        )
        self.proc.emit("MCP_MAP_SNAPSHOT_END|{}\n")
        self.proc.emit("MCP_MAP_SNAPSHOT_OBJECTS|{}\n")
        with self.assertRaisesRegex(
            RuntimeError, "snapshot received before any observation"
        ):
            read_observation_and_snapshot(self.proc, self.run_dir, timeout=5)

    def test_complete_snapshot_still_parses(self) -> None:
        self.proc.emit("MCP_OBSERVATION|" + json.dumps({"tick": 7}) + "\n")
        self.proc.emit(
            "MCP_MAP_SNAPSHOT_BEGIN|" + json.dumps({"map_size": [2, 2]}) + "\n"
        )
        self.proc.emit('MCP_MAP_SNAPSHOT_TILES|0|[{"tile": 0}]\n')
        self.proc.emit('MCP_MAP_SNAPSHOT_TILES|1|[{"tile": 2}]\n')
        self.proc.emit("MCP_MAP_SNAPSHOT_END|{}\n")
        self.proc.emit(
            "MCP_MAP_SNAPSHOT_OBJECTS|" + json.dumps({"towns": [{"town_id": 1}]}) + "\n"
        )
        observation, snapshot = read_observation_and_snapshot(
            self.proc, self.run_dir, timeout=5
        )
        self.assertEqual(observation, {"tick": 7})
        self.assertEqual(snapshot["tiles"], [{"tile": 0}, {"tile": 2}])
        self.assertEqual(snapshot["towns"], [{"town_id": 1}])

    def test_standalone_snapshot_preserves_saved_route_and_company_facts(self) -> None:
        metadata = {
            "type": "map_snapshot",
            "map_size": [2, 1],
            "company_id": 3,
        }
        self.proc.emit("MCP_MAP_SNAPSHOT_BEGIN|" + json.dumps(metadata) + "\n")
        self.proc.emit('MCP_MAP_SNAPSHOT_TILES|0|[{"tile":0},{"tile":1}]\n')
        self.proc.emit("MCP_MAP_SNAPSHOT_END|{}\n")
        self.proc.emit(
            "MCP_MAP_SNAPSHOT_OBJECTS|"
            + json.dumps(
                {
                    "stations": [{"station_id": 7}],
                    "routes": [{"station_tiles": [10, 11]}],
                }
            )
            + "\n"
        )

        snapshot = read_map_snapshot(self.proc, self.run_dir, timeout=5)

        self.assertEqual(snapshot["company_id"], 3)
        self.assertEqual(len(snapshot["tiles"]), 2)
        self.assertEqual(snapshot["stations"], [{"station_id": 7}])
        self.assertEqual(snapshot["routes"], [{"station_tiles": [10, 11]}])

    def test_standalone_snapshot_rejects_a_missing_tile_row(self) -> None:
        self.proc.emit(
            "MCP_MAP_SNAPSHOT_BEGIN|" + json.dumps({"map_size": [2, 2]}) + "\n"
        )
        self.proc.emit('MCP_MAP_SNAPSHOT_TILES|0|[{"tile":0}]\n')
        self.proc.emit("MCP_MAP_SNAPSHOT_END|{}\n")
        self.proc.emit("MCP_MAP_SNAPSHOT_OBJECTS|{}\n")

        with self.assertRaisesRegex(RuntimeError, "row set is incomplete"):
            read_map_snapshot(self.proc, self.run_dir, timeout=5)

    def test_expected_town_count_guard_raises_on_mismatch(self) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            assert_expected_town_count({"towns": [{"town_id": 1}]}, seed=7331429)
        message = str(ctx.exception)
        self.assertIn("7331429", message)
        self.assertIn(f"expected {DEFAULT_TOWN_COUNT}", message)

    def test_expected_town_count_guard_requires_a_towns_array(self) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            assert_expected_town_count({"towns": None}, seed=42)
        message = str(ctx.exception)
        self.assertIn("seed 42", message)
        self.assertIn("towns", message)

    def test_save_game_accepts_file_when_success_marker_is_missed(self) -> None:
        save_path = self.run_dir / "final.sav"
        save_path.write_bytes(b"save")
        with unittest.mock.patch(
            "openttd_mcp.lifecycle.read_until",
            side_effect=TimeoutError("marker missed"),
        ):
            save_game(self.proc, self.run_dir, save_path)
        self.assertEqual(self.proc.stdin.getvalue(), f"save {str(save_path)[:-4]}\n")

    def test_save_game_raises_when_file_never_appears(self) -> None:
        self.proc.emit("Map successfully saved onto disk.\n")
        save_path = self.run_dir / "saves" / "final.sav"
        with self.assertRaises(TimeoutError) as ctx:
            save_game(self.proc, self.run_dir, save_path, disk_timeout=0.5)
        message = str(ctx.exception)
        self.assertIn(str(save_path), message)
        self.assertIn("never appeared", message)


if __name__ == "__main__":
    unittest.main()
