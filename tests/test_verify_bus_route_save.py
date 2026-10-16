from __future__ import annotations

import json
import sys
import unittest

import pytest

from openttd_mcp.verify import bus_route
from openttd_mcp.verify.bus_route import (
    parse_bool,
    parse_trace_line,
    route_trace_shows_vehicle_movement,
    write_route_trace,
)
from tests.support import TempDirTestCase, stub_verifier_run

# The 43 ``BUS_ROUTE_VERIFIER|`` payload tokens, in the order ``main`` unpacks
# them. Pinning the order here guards against drift between the Squirrel print
# and the Python parser: a field inserted on one side only would shift every
# later value silently.
PAYLOAD_FIELDS: list[tuple[str, str]] = [
    ("largest_town_id", "int"),
    ("largest_town_name", "str"),
    ("largest_town_population", "int"),
    ("bus_station_count", "int"),
    ("bus_station_in_largest_town", "int"),
    ("road_depot_count", "int"),
    ("road_depot_with_front_road_count", "int"),
    ("road_depot_connected_count", "int"),
    ("road_vehicle_count", "int"),
    ("valid_bus_route_vehicle_count", "int"),
    ("road_access_bus_station_orders", "int"),
    ("connected_bus_station_orders", "int"),
    ("first_vehicle", "int"),
    ("first_order_count", "int"),
    ("first_stopped_in_depot", "bool"),
    ("first_initial_tile", "int"),
    ("first_final_tile", "int"),
    ("first_vehicle_moved", "bool"),
    ("station_a", "int"),
    ("station_b", "int"),
    ("station_a_tile", "int"),
    ("station_b_tile", "int"),
    ("depot_tile", "int"),
    ("depot_front_tile", "int"),
    ("station_a_connected", "bool"),
    ("station_b_connected", "bool"),
    ("depot_connected", "bool"),
    ("station_a_to_station_b", "bool"),
    ("station_b_to_station_a", "bool"),
    ("depot_to_station_a", "bool"),
    ("station_a_to_depot", "bool"),
    ("station_a_visited_by_vehicle", "bool"),
    ("station_b_visited_by_vehicle", "bool"),
    ("station_a_first_vehicle_event", "bool"),
    ("station_b_first_vehicle_event", "bool"),
    ("vehicle_lost_event", "bool"),
    ("vehicle_crashed_event", "bool"),
    ("order_progressed", "bool"),
    ("last_order_index", "int"),
    ("last_current_order_destination", "int"),
    ("dynamic_sample_count", "int"),
    ("station_a_town_road_component_tiles", "int"),
    ("station_b_town_road_component_tiles", "int"),
]

PASSING_PAYLOAD: dict[str, object] = {
    "largest_town_id": 0,
    "largest_town_name": "Wadworth",
    "largest_town_population": 1200,
    "bus_station_count": 2,
    "bus_station_in_largest_town": 2,
    "road_depot_count": 1,
    "road_depot_with_front_road_count": 1,
    "road_depot_connected_count": 1,
    "road_vehicle_count": 1,
    "valid_bus_route_vehicle_count": 1,
    "road_access_bus_station_orders": 2,
    "connected_bus_station_orders": 2,
    "first_vehicle": 1,
    "first_order_count": 2,
    "first_stopped_in_depot": False,
    "first_initial_tile": 2404,
    "first_final_tile": 2402,
    "first_vehicle_moved": True,
    "station_a": 0,
    "station_b": 1,
    "station_a_tile": 2402,
    "station_b_tile": 2532,
    "depot_tile": 2341,
    "depot_front_tile": 2340,
    "station_a_connected": True,
    "station_b_connected": True,
    "depot_connected": True,
    "station_a_to_station_b": True,
    "station_b_to_station_a": True,
    "depot_to_station_a": True,
    "station_a_to_depot": True,
    "station_a_visited_by_vehicle": True,
    "station_b_visited_by_vehicle": True,
    "station_a_first_vehicle_event": True,
    "station_b_first_vehicle_event": True,
    "vehicle_lost_event": False,
    "vehicle_crashed_event": False,
    "order_progressed": True,
    "last_order_index": 1,
    "last_current_order_destination": 2532,
    "dynamic_sample_count": 37,
    "station_a_town_road_component_tiles": 42,
    "station_b_town_road_component_tiles": 37,
}


def verifier_line(**overrides) -> str:
    """Render one ``BUS_ROUTE_VERIFIER|`` stdout line from field overrides."""
    values = {**PASSING_PAYLOAD, **overrides}
    tokens = []
    for name, kind in PAYLOAD_FIELDS:
        value = values[name]
        tokens.append(("true" if value else "false") if kind == "bool" else str(value))
    return "dbg: BUS_ROUTE_VERIFIER|" + "|".join(tokens) + "\n"


def run_main(
    monkeypatch, tmp_path, *, line: str, log_text: str = ""
) -> tuple[int, dict]:
    stub = stub_verifier_run(
        monkeypatch, bus_route, tmp_path, marker_line=line, log_text=log_text
    )
    exit_code = bus_route.main([str(tmp_path / "save.sav")])
    return exit_code, json.loads(stub.result_path.read_text())


class VerifyBusRouteSaveTests(TempDirTestCase):
    def test_route_trace_counts_any_sampled_tile_change_as_vehicle_movement(self):
        trace_path = self.tmp_path / "route_trace.jsonl"
        entries = [
            {"type": "sample", "vehicle_tile": 908, "sample": 0},
            {"type": "sample", "vehicle_tile": 853, "sample": 18},
            {"type": "sample", "vehicle_tile": 908, "sample": 60},
        ]
        trace_path.write_text("".join(json.dumps(entry) + "\n" for entry in entries))

        self.assertTrue(
            route_trace_shows_vehicle_movement(trace_path, initial_tile=908)
        )

    def test_route_trace_movement_helper_stays_false_when_all_samples_same_tile(self):
        trace_path = self.tmp_path / "route_trace.jsonl"
        entries = [
            {"type": "sample", "vehicle_tile": 908, "sample": 0},
            {"type": "sample", "vehicle_tile": 908, "sample": 1},
        ]
        trace_path.write_text("".join(json.dumps(entry) + "\n" for entry in entries))

        self.assertFalse(
            route_trace_shows_vehicle_movement(trace_path, initial_tile=908)
        )


class ParseBoolTests(unittest.TestCase):
    def test_boolean_words_round_trip(self):
        self.assertTrue(parse_bool("true"))
        self.assertFalse(parse_bool("false"))

    def test_non_boolean_token_raises(self):
        # A shifted payload shows up here first; it must fail loudly rather
        # than coerce to a verdict-bearing False.
        with self.assertRaises(ValueError):
            parse_bool("True")


class ParseTraceLineTests(unittest.TestCase):
    def test_sample_line_is_typed(self):
        line = "dbg: BUS_ROUTE_TRACE|sample|3|908|22|1|false|false|1|2532|1|0\n"
        self.assertEqual(
            parse_trace_line(line),
            {
                "type": "sample",
                "sample": 3,
                "vehicle_tile": 908,
                "speed": 22,
                "state": 1,
                "in_depot": False,
                "stopped_in_depot": False,
                "current_order_index": 1,
                "current_order_destination": 2532,
                "current_order_station": 1,
                "location_station": 0,
            },
        )

    def test_station_first_vehicle_event_carries_ids(self):
        entry = parse_trace_line("BUS_ROUTE_TRACE|event|station_first_vehicle|7|41")
        self.assertEqual(
            entry,
            {
                "type": "event",
                "event": "station_first_vehicle",
                "station_id": 7,
                "vehicle_id": 41,
            },
        )

    def test_vehicle_events_carry_vehicle_id(self):
        for event in ("vehicle_lost", "vehicle_crashed", "vehicle_waiting_in_depot"):
            with self.subTest(event=event):
                self.assertEqual(
                    parse_trace_line(f"BUS_ROUTE_TRACE|event|{event}|41"),
                    {"type": "event", "event": event, "vehicle_id": 41},
                )

    def test_unknown_event_keeps_raw_tail(self):
        entry = parse_trace_line("BUS_ROUTE_TRACE|event|mystery|a|b")
        self.assertEqual(
            entry, {"type": "event", "event": "mystery", "raw": ["a", "b"]}
        )

    def test_bare_event_keyword_has_no_payload_keys(self):
        self.assertEqual(
            parse_trace_line("BUS_ROUTE_TRACE|event"),
            {"type": "event", "event": "unknown"},
        )

    def test_unrecognised_shape_is_kept_as_raw(self):
        self.assertEqual(
            parse_trace_line("BUS_ROUTE_TRACE|sample|1|2"),
            {"type": "raw", "payload": "sample|1|2"},
        )

    def test_non_trace_line_returns_none(self):
        self.assertIsNone(parse_trace_line("dbg: BUS_ROUTE_VERIFIER|0|Wadworth|1200"))


class WriteRouteTraceTests(TempDirTestCase):
    def test_only_trace_lines_are_written_and_counted(self):
        log_path = self.tmp_path / "verify.log"
        trace_path = self.tmp_path / "route_trace.jsonl"
        log_path.write_text(
            "dbg: boot noise\n"
            "dbg: BUS_ROUTE_TRACE|sample|0|908|0|0|false|false|0|2402|0|0\n"
            "dbg: BUS_ROUTE_TRACE|event|vehicle_lost|41\n"
            "dbg: BUS_ROUTE_VERIFIER|unrelated\n"
        )

        self.assertEqual(write_route_trace(log_path, trace_path), 2)
        written = [json.loads(line) for line in trace_path.read_text().splitlines()]
        self.assertEqual([entry["type"] for entry in written], ["sample", "event"])


class RouteTraceMovementEdgeTests(TempDirTestCase):
    def test_negative_initial_tile_is_never_movement(self):
        trace_path = self.tmp_path / "route_trace.jsonl"
        trace_path.write_text(json.dumps({"type": "sample", "vehicle_tile": 5}) + "\n")
        self.assertFalse(
            route_trace_shows_vehicle_movement(trace_path, initial_tile=-1)
        )

    def test_missing_trace_file_is_never_movement(self):
        self.assertFalse(
            route_trace_shows_vehicle_movement(
                self.tmp_path / "absent.jsonl", initial_tile=908
            )
        )

    def test_malformed_and_non_sample_entries_are_skipped(self):
        trace_path = self.tmp_path / "route_trace.jsonl"
        trace_path.write_text(
            "not json\n"
            + json.dumps({"type": "event", "event": "vehicle_lost", "vehicle_tile": 1})
            + "\n"
            + json.dumps({"type": "sample", "vehicle_tile": "n/a"})
            + "\n"
            + json.dumps({"type": "sample"})
            + "\n"
        )
        # No usable sample disagrees with the initial tile, so the helper
        # must not upgrade the verdict to "moved".
        self.assertFalse(
            route_trace_shows_vehicle_movement(trace_path, initial_tile=908)
        )


def test_main_accepts_a_fully_operating_route(monkeypatch, tmp_path, capsys):
    exit_code, result = run_main(monkeypatch, tmp_path, line=verifier_line())
    capsys.readouterr()

    assert exit_code == 0
    assert result["largest_town_name"] == "Wadworth"
    assert result["station_b_town_road_component_tiles"] == 37
    assert result["orders_target_both_stations"] is True
    assert result["both_stations_same_road_component_as_depot"] is True
    assert result["stations_on_substantial_town_road_network"] is True
    assert (
        result["first_vehicle_movement_evidence"]
        == "initial_final_tile_difference_only"
    )
    assert result["route_trace_entry_count"] == 0
    assert result["verifier_module"] == "openttd_mcp.verify.bus_route"
    # Every parsed field must match the emitted payload exactly - a transposed
    # or shifted parts[i] shows up here even when the verdict is unaffected.
    parsed = {name: result[name] for name, _ in PAYLOAD_FIELDS if name in result}
    expected = {
        name: value for name, value in PASSING_PAYLOAD.items() if name in parsed
    }
    assert parsed == expected


def test_main_default_argv_reads_sys_argv(monkeypatch, tmp_path):
    stub_verifier_run(monkeypatch, bus_route, tmp_path, marker_line=verifier_line())
    monkeypatch.setattr(sys, "argv", ["prog", str(tmp_path / "save.sav")])

    assert bus_route.main() == 0


def test_main_accepts_component_tiles_at_the_exact_minimum(
    monkeypatch, tmp_path, capsys
):
    exit_code, _ = run_main(
        monkeypatch,
        tmp_path,
        line=verifier_line(
            station_a_town_road_component_tiles=10,
            station_b_town_road_component_tiles=10,
        ),
    )
    capsys.readouterr()
    assert exit_code == 0


def test_main_fails_a_route_the_vehicle_never_worked(monkeypatch, tmp_path, capsys):
    exit_code, result = run_main(
        monkeypatch,
        tmp_path,
        line=verifier_line(station_b_visited_by_vehicle=False, order_progressed=False),
    )
    capsys.readouterr()

    assert exit_code == 2
    assert result["station_b_visited_by_vehicle"] is False


def test_main_fails_a_stub_road_network(monkeypatch, tmp_path, capsys):
    exit_code, result = run_main(
        monkeypatch,
        tmp_path,
        line=verifier_line(station_a_town_road_component_tiles=3),
    )
    capsys.readouterr()

    assert exit_code == 2
    assert result["stations_on_substantial_town_road_network"] is False


def test_main_upgrades_movement_evidence_from_the_route_trace(
    monkeypatch, tmp_path, capsys
):
    # Same initial and final tile (a vehicle back where it started) but the
    # sampled trace saw it elsewhere: the verdict must use the trace evidence.
    exit_code, result = run_main(
        monkeypatch,
        tmp_path,
        line=verifier_line(
            first_initial_tile=2402, first_final_tile=2402, first_vehicle_moved=False
        ),
        log_text=(
            "dbg: BUS_ROUTE_TRACE|sample|0|2402|0|0|false|false|0|2402|0|0\n"
            "dbg: BUS_ROUTE_TRACE|sample|9|2450|30|1|false|false|1|2532|1|-1\n"
        ),
    )
    capsys.readouterr()

    assert result["route_trace_entry_count"] == 2
    assert result["first_vehicle_moved"] is True
    assert (
        result["first_vehicle_movement_evidence"] == "route_trace_sampled_tile_change"
    )
    assert exit_code == 0


def test_main_rejects_a_payload_with_the_wrong_field_count(monkeypatch, tmp_path):
    with pytest.raises(RuntimeError, match="Unexpected verifier payload"):
        run_main(
            monkeypatch, tmp_path, line="dbg: BUS_ROUTE_VERIFIER|0|Wadworth|1200\n"
        )


def test_main_tears_the_process_down_even_when_parsing_fails(monkeypatch, tmp_path):
    stub = stub_verifier_run(
        monkeypatch, bus_route, tmp_path, marker_line="dbg: BUS_ROUTE_VERIFIER|0|1\n"
    )
    with pytest.raises(RuntimeError):
        bus_route.main([str(tmp_path / "save.sav")])

    assert stub.proc.kill_count == 1


def test_main_resolves_a_relative_save_path_against_the_repo_root(
    monkeypatch, tmp_path, capsys
):
    stub_verifier_run(monkeypatch, bus_route, tmp_path, marker_line=verifier_line())
    assert bus_route.main(["artifacts/llm_bus_route.sav"]) == 0
    assert (
        f"VERIFY_SAVE={bus_route.ROOT}/artifacts/llm_bus_route.sav"
        in capsys.readouterr().out
    )


SINGLE_DEFECTS: list[tuple[str, object]] = [
    # One conjunct of main()'s verdict broken at a time, everything else passing —
    # a masked multi-defect payload cannot prove any individual check exists.
    ("bus_station_in_largest_town", 1),
    ("valid_bus_route_vehicle_count", 0),
    ("road_access_bus_station_orders", 1),
    ("connected_bus_station_orders", 1),
    ("road_depot_connected_count", 0),
    ("first_order_count", 1),
    ("first_stopped_in_depot", True),
    ("first_vehicle_moved", False),
    # These three verdict conjuncts are derived in main() from raw payload
    # fields, so the defect is injected through the raw field itself.
    # orders_target_both_stations, per raw conjunct
    ("station_b", -1),
    ("station_a", -1),
    ("station_b", 0),  # equal to station_a
    # both_stations_same_road_component_as_depot, per raw conjunct
    ("station_a_connected", False),
    ("station_b_connected", False),
    ("depot_connected", False),
    ("station_a_to_station_b", False),
    ("station_b_to_station_a", False),
    ("depot_to_station_a", False),
    ("station_a_to_depot", False),
    # stations_on_substantial_town_road_network, both sides at the boundary
    ("station_a_town_road_component_tiles", 9),
    ("station_b_town_road_component_tiles", 9),
    ("station_a_visited_by_vehicle", False),
    ("station_b_visited_by_vehicle", False),
    ("vehicle_lost_event", True),
    ("vehicle_crashed_event", True),
    ("order_progressed", False),
]


@pytest.mark.parametrize(
    "field,bad_value", SINGLE_DEFECTS, ids=[f"{f}={v}" for f, v in SINGLE_DEFECTS]
)
def test_main_rejects_each_single_defect(
    monkeypatch, tmp_path, capsys, field, bad_value
):
    exit_code, _ = run_main(
        monkeypatch, tmp_path, line=verifier_line(**{field: bad_value})
    )
    capsys.readouterr()
    assert exit_code == 2, f"single defect in {field} must fail the verdict"


if __name__ == "__main__":
    unittest.main()
