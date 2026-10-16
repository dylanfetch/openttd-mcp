"""Parametrized tests shared by the bus_network and freight_network save verifiers.

110-test-suite-cleanup: these behaviors were exact-name duplicate tests across
``tests/test_verify_bus_network_save.py`` and
``tests/test_verify_freight_network_save.py``; each now runs once per verifier
lane. The lanes' acceptance policies differ only in the row data that encodes
them: bus anchors stations on a substantial town road network (the
component-tile floor rows and the ``min_town_road_component_tiles`` result
key), freight drops that anchor and instead requires observed cargo pickup
(the ``max_load_observed`` row and the delivery-count result keys).

All logic under test is pure Python (no OpenTTD launch required).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from types import ModuleType

import pytest

import openttd_mcp.verify.bus_network as bus_network_module
import openttd_mcp.verify.freight_network as freight_network_module
from tests.support import network_route, stub_verifier_run


def make_bus_route(**overrides) -> dict:
    """A fully-connected, operating, on-substantial-town-road bus route."""
    route = network_route(
        station_a_town=0,
        station_b_town=0,
        station_a_town_road_component_tiles=42,
        station_b_town_road_component_tiles=37,
        road_access_bus_station_orders=2,
        connected_bus_station_orders=2,
    )
    route.update(overrides)
    return route


def make_freight_route(**overrides) -> dict:
    """A fully-connected, operating, cargo-pickup-observed truck route."""
    route = network_route(
        road_access_truck_station_orders=2,
        connected_truck_station_orders=2,
        cargo_id=3,
        cargo_label="GOOD",
        capacity=30,
        max_load_observed=12,
        load_drop_observed=False,
        station_a_cargo_waiting_observed=True,
    )
    route.update(overrides)
    return route


# One route_ok conjunct broken per row, everything else passing (evaluate_route
# recomputes the derived checks, so derived conjuncts flip via their raw fields).
COMMON_SINGLE_DEFECTS: tuple[tuple[str, object], ...] = (
    ("order_count", 1),
    ("stopped_in_depot", True),
    ("vehicle_moved", False),
    ("station_a_visited_by_vehicle", False),
    ("station_b_visited_by_vehicle", False),
    ("order_progressed", False),
    ("vehicle_lost_event", True),
    ("vehicle_crashed_event", True),
    ("station_b", 0),  # orders_target_both_stations
    ("station_a_connected", False),  # the seven raw reachability conjuncts
    ("station_b_connected", False),
    ("depot_connected", False),
    ("station_a_to_station_b", False),
    ("station_b_to_station_a", False),
    ("depot_to_station_a", False),
    ("station_a_to_depot", False),
)


@dataclass(frozen=True)
class VerifierCase:
    """One verifier lane: the module plus the row data its policy differs by."""

    label: str
    module: ModuleType
    tag: str
    make_route: Callable[..., dict]
    single_defects: tuple[tuple[str, object], ...]
    summary_template: str  # ``{n}`` = valid route / vehicle count
    expected_summary: dict
    result_key_order: tuple[str, ...]

    @property
    def summary_line(self) -> str:
        return self.summary_template.format(n=2)

    def route_line(self, route: dict) -> str:
        """Render one ``<TAG>|route|`` stdout line from a route dict."""
        tokens = [
            ("true" if route[name] else "false") if kind == "bool" else str(route[name])
            for name, kind in self.module.ROUTE_FIELDS
        ]
        return f"dbg: {self.tag}|route|" + "|".join(tokens) + "\n"

    def network_log(self, routes: list[dict]) -> str:
        return (
            "".join(self.route_line(route) for route in routes)
            + self.summary_template.format(n=len(routes))
            + "\n"
            + f"dbg: {self.tag}|done|\n"
        )


BUS = VerifierCase(
    label="bus",
    module=bus_network_module,
    tag="BUS_ROUTE_NET",
    make_route=make_bus_route,
    single_defects=COMMON_SINGLE_DEFECTS
    + (
        ("station_a_town_road_component_tiles", 9),  # substantial-network floor
        ("station_b_town_road_component_tiles", 9),
        ("road_access_bus_station_orders", 1),
        ("connected_bus_station_orders", 1),
    ),
    summary_template="dbg: BUS_ROUTE_NET|summary|{n}|{n}|4|2|0|Wadworth|1200",
    expected_summary={
        "valid_route_count": 2,
        "road_vehicle_count": 2,
        "bus_station_count": 4,
        "road_depot_count": 2,
        "largest_town_id": 0,
        "largest_town_name": "Wadworth",
        "largest_town_population": 1200,
    },
    result_key_order=(
        "min_routes_required",
        "min_town_road_component_tiles",
        "valid_route_count",
        "passing_route_count",
        "distinct_passing_route_count",
        "passing_route_signatures",
        "network_ok",
        "routes",
        "summary",
    ),
)

FREIGHT = VerifierCase(
    label="freight",
    module=freight_network_module,
    tag="FREIGHT_NET",
    make_route=make_freight_route,
    single_defects=COMMON_SINGLE_DEFECTS
    + (
        ("road_access_truck_station_orders", 1),
        ("connected_truck_station_orders", 1),
        ("max_load_observed", 0),  # cargo_pickup_observed
    ),
    summary_template="dbg: FREIGHT_NET|summary|{n}|{n}|4|2",
    expected_summary={
        "valid_route_count": 2,
        "road_vehicle_count": 2,
        "truck_station_count": 4,
        "road_depot_count": 2,
    },
    result_key_order=(
        "min_routes_required",
        "valid_route_count",
        "passing_route_count",
        "distinct_passing_route_count",
        "passing_route_signatures",
        "delivered_route_count",
        "distinct_delivered_route_count",
        "delivered_route_signatures",
        "network_ok",
        "routes",
        "summary",
    ),
)

CASES = [BUS, FREIGHT]
each_case = pytest.mark.parametrize("case", CASES, ids=[case.label for case in CASES])


def two_distinct_routes(case: VerifierCase) -> list[dict]:
    """Two passing routes on distinct station pairs (the frozen run 016
    skill-eval shape: Wadworth ``[2402, 2532]`` and Carnpool ``[1037, 1167]``)."""
    return [
        case.make_route(
            route_index=0, vehicle_id=1, station_a_tile=2402, station_b_tile=2532
        ),
        case.make_route(
            route_index=1, vehicle_id=2, station_a_tile=1037, station_b_tile=1167
        ),
    ]


@each_case
def test_fully_connected_operating_route_passes(case):
    assert case.module.evaluate_route(case.make_route())["route_ok"] is True


@each_case
def test_each_single_defect_fails_route_ok(case):
    for field, bad_value in case.single_defects:
        route = case.make_route(**{field: bad_value})
        assert not case.module.evaluate_route(route)["route_ok"], field


@each_case
def test_two_distinct_passing_routes_are_accepted(case):
    result = case.module.aggregate_network(two_distinct_routes(case), summary=None)
    assert result["network_ok"] is True
    assert result["distinct_passing_route_count"] == 2
    assert result["passing_route_signatures"] == [[1037, 1167], [2402, 2532]]


@each_case
def test_single_passing_route_does_not_satisfy_min_two(case):
    routes = two_distinct_routes(case)
    routes[1]["depot_connected"] = False  # only route 0 survives
    result = case.module.aggregate_network(routes, summary=None)
    assert result["network_ok"] is False
    assert result["distinct_passing_route_count"] == 1


@each_case
def test_duplicate_route_signature_counts_once(case):
    routes = two_distinct_routes(case)
    # Two vehicles on the same physical route must not count as a two-route network.
    routes[1]["station_a_tile"] = 2402
    routes[1]["station_b_tile"] = 2532
    result = case.module.aggregate_network(routes, summary=None)
    assert result["distinct_passing_route_count"] == 1
    assert result["network_ok"] is False


@each_case
def test_signature_is_order_independent(case):
    forward = case.make_route(station_a_tile=2402, station_b_tile=2532)
    reverse = case.make_route(station_a_tile=2532, station_b_tile=2402)
    assert case.module.route_signature(forward) == case.module.route_signature(reverse)


@each_case
def test_non_route_line_returns_none(case):
    assert case.module.parse_route_line(case.summary_line) is None


@each_case
def test_non_summary_line_returns_none(case):
    assert case.module.parse_summary_line(f"dbg: {case.tag}|route|0|1") is None


@each_case
@pytest.mark.parametrize(
    "parser,payload",
    [("parse_route_line", "route|0|1|2"), ("parse_summary_line", "summary|2")],
)
def test_wrong_field_count_raises(case, parser, payload):
    with pytest.raises(RuntimeError):
        getattr(case.module, parser)(f"{case.tag}|{payload}")


@each_case
def test_non_boolean_token_raises(case):
    parse_bool = case.module.parse_bool
    assert parse_bool("true") is True
    assert parse_bool("false") is False
    for token in ("yes", "0"):
        with pytest.raises(ValueError):
            parse_bool(token)


@each_case
def test_summary_line_is_typed(case):
    assert case.module.parse_summary_line(case.summary_line) == case.expected_summary


@each_case
def test_summary_is_attached_when_present(case):
    summary = {"valid_route_count": 1}
    result = case.module.aggregate_network([case.make_route()], summary=summary)
    assert result["summary"] == summary


@each_case
def test_no_summary_key_when_absent(case):
    result = case.module.aggregate_network([case.make_route()], summary=None)
    assert "summary" not in result


@each_case
def test_result_key_order_matches_pre_refactor_contract(case):
    result = case.module.aggregate_network(
        [case.make_route()], summary={"valid_route_count": 1}
    )
    assert tuple(result) == case.result_key_order


@each_case
def test_parse_verifier_log_sorts_routes_by_index_and_captures_the_summary(
    case, tmp_path
):
    log_path = tmp_path / "verify.log"
    log_path.write_text(
        "dbg: boot noise\n"
        + case.route_line(case.make_route(route_index=1, vehicle_id=2))
        + case.route_line(case.make_route(route_index=0, vehicle_id=1))
        + case.summary_line
        + "\n"
        + f"dbg: {case.tag}|done|\n"
    )

    routes, summary = case.module.parse_verifier_log(log_path)

    assert [route["route_index"] for route in routes] == [0, 1]
    assert [route["vehicle_id"] for route in routes] == [1, 2]
    assert summary == case.expected_summary


@each_case
def test_parse_verifier_log_uses_module_parser_hooks(case, monkeypatch, tmp_path):
    log_path = tmp_path / "verify.log"
    log_path.write_text(
        f"{case.tag}|route|second\n{case.tag}|summary|summary\n{case.tag}|route|first\n"
    )
    calls = []

    def parse_route(line):
        calls.append(("route", line.strip()))
        return {"route_index": 0 if line.endswith("first\n") else 1}

    def parse_summary(line):
        calls.append(("summary", line.strip()))
        return {"hooked": True}

    monkeypatch.setattr(case.module, "parse_route_line", parse_route)
    monkeypatch.setattr(case.module, "parse_summary_line", parse_summary)

    routes, summary = case.module.parse_verifier_log(log_path)

    assert [route["route_index"] for route in routes] == [0, 1]
    assert summary == {"hooked": True}
    assert calls == [
        ("route", f"{case.tag}|route|second"),
        ("summary", f"{case.tag}|summary|summary"),
        ("route", f"{case.tag}|route|first"),
    ]


@each_case
def test_main_default_argv_reads_sys_argv(case, monkeypatch, tmp_path):
    log_text = case.network_log(two_distinct_routes(case))
    stub_verifier_run(monkeypatch, case.module, tmp_path, log_text=log_text)
    monkeypatch.setattr(sys, "argv", ["prog", str(tmp_path / "save.sav")])

    assert case.module.main() == 0


@each_case
def test_main_resolves_a_relative_save_path_against_the_repo_root(
    case, monkeypatch, tmp_path, capsys
):
    log_text = case.network_log([case.make_route(route_index=0, vehicle_id=1)])
    stub = stub_verifier_run(monkeypatch, case.module, tmp_path, log_text=log_text)
    case.module.main(["artifacts/llm_bus_route.sav"])
    capsys.readouterr()

    expected = str(case.module.ROOT / "artifacts" / "llm_bus_route.sav")
    assert json.loads(stub.result_path.read_text())["save_path"] == expected


@each_case
def test_main_tears_the_process_down_on_a_malformed_route_line(
    case, monkeypatch, tmp_path
):
    stub = stub_verifier_run(
        monkeypatch, case.module, tmp_path, log_text=f"dbg: {case.tag}|route|1|2|3\n"
    )
    with pytest.raises(RuntimeError, match="Unexpected route payload"):
        case.module.main([str(tmp_path / "save.sav")])

    assert stub.proc.kill_count == 1
