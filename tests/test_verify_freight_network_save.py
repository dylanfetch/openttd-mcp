"""Regression tests for the freight-network verifier aggregation logic (028-freight-verifier).

Seeded from ``artifacts/runs/024-scaled-frontier/episode-002/REVERIFY_NOTE.md``: the model
built 81 physically-functional truck routes that ``bus_network`` scored as zero passing
routes because its ``stations_on_substantial_town_road_network`` criterion anchors on
``AITown.IsWithinTownInfluence``, which industry-anchored truck stops sit outside. This
verifier drops that criterion and instead requires observed cargo pickup.

Behaviors shared with the bus verifier are parametrized in
``tests/test_verify_network_shared.py``; this file keeps only freight-specific policy.

All logic under test is pure Python (no OpenTTD launch required).
"""

from __future__ import annotations

import json
import unittest

import openttd_mcp.verify.freight_network as freight_network_module
from openttd_mcp.verify.freight_network import aggregate_network, evaluate_route
from tests.support import stub_verifier_run
from tests.test_verify_network_shared import FREIGHT
from tests.test_verify_network_shared import make_freight_route as make_route


class EvaluateRouteTests(unittest.TestCase):
    def test_zero_pickup_route_fails(self):
        # Baseline acceptance requires observed cargo pickup; a route with orders and
        # connectivity but no load ever observed is not confirmed to be moving freight.
        result = evaluate_route(make_route(max_load_observed=0))
        self.assertFalse(result["cargo_pickup_observed"])
        self.assertFalse(result["route_ok"])

    def test_strict_delivery_flag_independent_of_baseline_pass(self):
        # Baseline route_ok requires only pickup evidence; route_ok_strict additionally
        # requires an observed load drop (delivery). A route can pass baseline without
        # passing strict, but never the reverse.
        no_delivery = evaluate_route(make_route(load_drop_observed=False))
        self.assertTrue(no_delivery["route_ok"])
        self.assertFalse(no_delivery["route_ok_strict"])

        with_delivery = evaluate_route(make_route(load_drop_observed=True))
        self.assertTrue(with_delivery["route_ok"])
        self.assertTrue(with_delivery["route_ok_strict"])

    def test_strict_delivery_still_requires_baseline_topology(self):
        # A load drop alone (e.g. spurious) does not rescue a route that fails topology.
        broken = evaluate_route(
            make_route(load_drop_observed=True, depot_connected=False)
        )
        self.assertFalse(broken["route_ok"])
        self.assertFalse(broken["route_ok_strict"])


class DriveThroughDeadEndTests(unittest.TestCase):
    """ep-002 acceptance fix: drive-through truck stops legally terminate a road at an
    industry (vehicles reverse inside the stop), so ONE fully-connected end is serviceable.
    The shared bus and freight verifier policy now follows that game rule."""

    def test_dead_end_drive_through_route_passes(self):
        # A one-open-end drive-through stop is emitted by Squirrel as connected=true;
        # with the dynamic operation evidence intact the route passes baseline.
        dead_end_route = make_route(
            station_a_connected=True,  # one serviceable end suffices
            station_b_connected=True,
            road_access_truck_station_orders=2,
            connected_truck_station_orders=2,
        )
        self.assertTrue(evaluate_route(dead_end_route)["route_ok"])

    def test_fully_disconnected_station_still_fails(self):
        # A stop with NO serviceable end is emitted as connected=false and stays fatal.
        result = evaluate_route(make_route(station_a_connected=False))
        self.assertFalse(result["both_stations_same_road_component_as_depot"])
        self.assertFalse(result["route_ok"])


class AggregateNetworkTests(unittest.TestCase):
    def _two_truck_routes(self) -> list[dict]:
        route_a = make_route(
            route_index=0, vehicle_id=1, station_a_tile=2402, station_b_tile=2532
        )
        route_b = make_route(
            route_index=1, vehicle_id=2, station_a_tile=1037, station_b_tile=1167
        )
        return [route_a, route_b]

    def test_min_routes_aggregation(self):
        routes = self._two_truck_routes()
        routes[1]["max_load_observed"] = 0  # drop route b: no pickup evidence
        result = aggregate_network(routes, summary=None, min_routes=1)
        self.assertTrue(result["network_ok"])
        self.assertEqual(result["distinct_passing_route_count"], 1)

        result_min_two = aggregate_network(routes, summary=None, min_routes=2)
        self.assertFalse(result_min_two["network_ok"])

    def test_delivered_route_count_tracked_independently(self):
        routes = self._two_truck_routes()
        routes[0]["load_drop_observed"] = True
        result = aggregate_network(routes, summary=None)
        self.assertTrue(result["network_ok"])  # baseline unaffected by delivery
        self.assertEqual(result["delivered_route_count"], 1)
        self.assertEqual(result["distinct_delivered_route_count"], 1)


def test_aggregate_and_delivery_use_module_route_signature(monkeypatch):
    monkeypatch.setattr(
        freight_network_module,
        "route_signature",
        lambda route: (7, 7),
    )
    routes = [
        make_route(
            route_index=0,
            station_a_tile=10,
            station_b_tile=20,
            load_drop_observed=True,
        ),
        make_route(
            route_index=1,
            station_a_tile=30,
            station_b_tile=40,
            load_drop_observed=True,
        ),
    ]

    result = freight_network_module.aggregate_network(routes, summary=None)

    assert result["passing_route_signatures"] == [[7, 7]]
    assert result["distinct_passing_route_count"] == 1
    assert result["delivered_route_signatures"] == [[7, 7]]
    assert result["distinct_delivered_route_count"] == 1
    assert result["network_ok"] is False


def _freight_log(routes: list[dict]) -> str:
    return FREIGHT.network_log(routes)


def _run_main(
    monkeypatch, tmp_path, log_text: str, argv: list[str]
) -> tuple[int, dict]:
    stub = stub_verifier_run(
        monkeypatch, freight_network_module, tmp_path, log_text=log_text
    )
    exit_code = freight_network_module.main([str(tmp_path / "save.sav"), *argv])
    return exit_code, json.loads(stub.result_path.read_text())


def test_main_accepts_a_two_route_freight_network(monkeypatch, tmp_path, capsys):
    routes = [
        make_route(
            route_index=0,
            vehicle_id=1,
            station_a_tile=2402,
            station_b_tile=2532,
            load_drop_observed=True,
        ),
        make_route(
            route_index=1, vehicle_id=2, station_a_tile=1037, station_b_tile=1167
        ),
    ]
    exit_code, result = _run_main(monkeypatch, tmp_path, _freight_log(routes), [])
    capsys.readouterr()

    assert exit_code == 0
    assert result["network_ok"] is True
    assert result["distinct_passing_route_count"] == 2
    assert result["delivered_route_count"] == 1  # only route 0 was seen unloading
    assert result["summary"]["truck_station_count"] == 4
    assert result["save_path"].endswith("save.sav")
    assert result["verifier_module"] == "openttd_mcp.verify.freight_network"


def test_main_fails_a_network_with_no_observed_pickup(monkeypatch, tmp_path, capsys):
    routes = [
        make_route(route_index=0, vehicle_id=1, max_load_observed=0),
        make_route(
            route_index=1,
            vehicle_id=2,
            station_a_tile=1037,
            station_b_tile=1167,
            max_load_observed=0,
        ),
    ]
    exit_code, result = _run_main(monkeypatch, tmp_path, _freight_log(routes), [])
    capsys.readouterr()

    assert exit_code == 2
    assert result["network_ok"] is False
    assert result["passing_route_count"] == 0


def test_main_honours_the_min_routes_flag(monkeypatch, tmp_path, capsys):
    log_text = _freight_log([make_route(route_index=0, vehicle_id=1)])
    exit_code, result = _run_main(
        monkeypatch, tmp_path, log_text, ["--min-routes", "1"]
    )
    capsys.readouterr()

    assert exit_code == 0
    assert result["min_routes_required"] == 1


if __name__ == "__main__":
    unittest.main()
