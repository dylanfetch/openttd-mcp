"""Regression tests for the per-town bus-network verifier aggregation logic.

These are seeded from the frozen run 016 skill-eval shape, where the model built two
operating routes (Wadworth ``[2402, 2532]`` and Carnpool ``[1037, 1167]``) but the old
single-route verifier failed the save because it selected the Carnpool route and measured
its stations against the *largest* town (Wadworth) road network, yielding zero component
tiles. The new verifier measures each station against its own town, so the two-route
network is accepted.

Behaviors shared with the freight verifier are parametrized in
``tests/test_verify_network_shared.py``; this file keeps only bus-specific policy.

All logic under test is pure Python (no OpenTTD launch required).
"""

from __future__ import annotations

import json
import unittest

import openttd_mcp.verify.bus_network as bus_network_module
from openttd_mcp.verify.bus_network import aggregate_network, evaluate_route
from tests.support import stub_verifier_run
from tests.test_verify_network_shared import BUS
from tests.test_verify_network_shared import make_bus_route as make_route


class EvaluateRouteTests(unittest.TestCase):
    def test_route_measured_against_own_town_not_largest(self):
        # Carnpool route: real component tiles in its own (non-largest) town. The old
        # verifier reported 0 here because it used the largest town's influence.
        carnpool = make_route(
            vehicle_id=2,
            station_a_tile=1037,
            station_b_tile=1167,
            station_a_town=3,
            station_b_town=3,
            station_a_town_road_component_tiles=28,
            station_b_town_road_component_tiles=19,
        )
        result = evaluate_route(carnpool)
        self.assertTrue(result["stations_on_substantial_town_road_network"])
        self.assertTrue(result["route_ok"])

    def test_stub_road_route_fails_substantial_network_check(self):
        stub = make_route(
            station_a_town_road_component_tiles=2,
            station_b_town_road_component_tiles=1,
        )
        result = evaluate_route(stub)
        self.assertFalse(result["stations_on_substantial_town_road_network"])
        self.assertFalse(result["route_ok"])

    def test_component_tiles_boundary_is_exactly_the_minimum(self):
        at_minimum = make_route(
            station_a_town_road_component_tiles=10,
            station_b_town_road_component_tiles=10,
        )
        self.assertTrue(evaluate_route(at_minimum)["route_ok"])


class AggregateNetworkTests(unittest.TestCase):
    def _frozen_016_skill_eval_routes(self) -> list[dict]:
        wadworth = make_route(
            route_index=0,
            vehicle_id=1,
            station_a_tile=2402,
            station_b_tile=2532,
            station_a_town=0,
            station_b_town=0,
        )
        carnpool = make_route(
            route_index=1,
            vehicle_id=2,
            station_a_tile=1037,
            station_b_tile=1167,
            station_a_town=3,
            station_b_town=3,
        )
        return [wadworth, carnpool]

    def test_min_routes_one_accepts_single_route(self):
        routes = self._frozen_016_skill_eval_routes()
        routes[1]["order_progressed"] = False  # drop Carnpool
        result = aggregate_network(routes, summary=None, min_routes=1)
        self.assertTrue(result["network_ok"])


def test_aggregate_network_uses_module_route_signature(monkeypatch):
    monkeypatch.setattr(bus_network_module, "route_signature", lambda route: (7, 7))
    routes = [
        make_route(route_index=0, station_a_tile=10, station_b_tile=20),
        make_route(route_index=1, station_a_tile=30, station_b_tile=40),
    ]

    result = bus_network_module.aggregate_network(routes, summary=None)

    assert result["passing_route_signatures"] == [[7, 7]]
    assert result["distinct_passing_route_count"] == 1
    assert result["network_ok"] is False


def _network_log(routes: list[dict]) -> str:
    return BUS.network_log(routes)


def _run_main(
    monkeypatch, tmp_path, log_text: str, argv: list[str]
) -> tuple[int, dict]:
    stub = stub_verifier_run(
        monkeypatch, bus_network_module, tmp_path, log_text=log_text
    )
    exit_code = bus_network_module.main([str(tmp_path / "save.sav"), *argv])
    return exit_code, json.loads(stub.result_path.read_text())


def test_main_accepts_a_two_route_network(monkeypatch, tmp_path, capsys):
    routes = [
        make_route(
            route_index=0, vehicle_id=1, station_a_tile=2402, station_b_tile=2532
        ),
        make_route(
            route_index=1, vehicle_id=2, station_a_tile=1037, station_b_tile=1167
        ),
    ]
    exit_code, result = _run_main(monkeypatch, tmp_path, _network_log(routes), [])
    capsys.readouterr()

    assert exit_code == 0
    assert result["network_ok"] is True
    assert result["distinct_passing_route_count"] == 2
    assert result["summary"]["largest_town_name"] == "Wadworth"
    assert result["save_path"].endswith("save.sav")
    assert result["verifier_module"] == "openttd_mcp.verify.bus_network"


def test_main_fails_a_single_route_network(monkeypatch, tmp_path, capsys):
    log_text = _network_log([make_route(route_index=0, vehicle_id=1)])
    exit_code, result = _run_main(monkeypatch, tmp_path, log_text, [])
    capsys.readouterr()

    assert exit_code == 2
    assert result["network_ok"] is False


def test_main_honours_min_routes_and_min_component_tiles_flags(
    monkeypatch, tmp_path, capsys
):
    log_text = _network_log(
        [
            make_route(
                route_index=0,
                vehicle_id=1,
                station_a_town_road_component_tiles=6,
                station_b_town_road_component_tiles=6,
            )
        ]
    )

    strict, strict_result = _run_main(
        monkeypatch, tmp_path, log_text, ["--min-routes", "1"]
    )
    capsys.readouterr()
    assert strict == 2  # 6 tiles is below the default 10-tile floor
    assert strict_result["min_town_road_component_tiles"] == 10

    relaxed, relaxed_result = _run_main(
        monkeypatch,
        tmp_path,
        log_text,
        ["--min-routes", "1", "--min-component-tiles", "5"],
    )
    capsys.readouterr()
    assert relaxed == 0
    assert relaxed_result["min_routes_required"] == 1
    assert relaxed_result["min_town_road_component_tiles"] == 5


if __name__ == "__main__":
    unittest.main()
