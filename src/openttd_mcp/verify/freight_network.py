"""Independently verify that a saved McpBridge company owns a >=2-route freight network.

This is the freight sibling of ``openttd_mcp.verify.bus_network``. ``bus_network`` validates passenger-bus
topology anchored on ``AITown.IsWithinTownInfluence``: a route built between two industry-
anchored truck stops that sit outside any town's influence measures zero town-road-component
tiles there and is rejected even though it is a perfectly functional freight route. One
large save had 81 physically-functional truck routes that ``bus_network`` credited as zero
passing routes for exactly this reason.

This verifier drops the town-influence criterion entirely and instead validates road-graph
topology (BFS reachability station<->station and depot<->station, reused unchanged from
``bus_network``) plus observed cargo flow. A drive-through stop counts as serviceable when
either end is fully road-connected because the game can route to the stop tile from that end
and reverse the road vehicle at the dead end; a bay stop still requires its single front
entrance. Cargo flow: the vehicle's primary (non-passenger) cargo must
show pickup evidence (``AIVehicle.GetCargoLoad`` > 0 at some sample) during the bounded
dynamic-operation window. Known limitation — pickup, not confirmed delivery, is the baseline
acceptance bar: a bounded sample window may not reliably observe a full unload-at-destination
for every route in a large network, so ``load_drop_observed`` (the load decreasing after a
station visit) is recorded and surfaced as a *stricter* per-route flag
(``route_ok_strict``/``delivered_route_count``) rather than required for the baseline verdict.
This is a *road-route topology and observed cargo-pickup* gate, not a full source->sink
delivery-economics gate; confirming reliable delivery evidence on large saves is the basis
for tightening this criterion later (not pre-emptively guessed at here).

The Squirrel side emits one ``FREIGHT_NET|route|...`` line per valid route vehicle plus a
``FREIGHT_NET|summary|...`` line. All aggregation lives in pure Python helpers
(:func:`parse_route_line`, :func:`evaluate_route`, :func:`aggregate_network`) so it can be
unit-tested without launching OpenTTD.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from openttd_mcp.runtime import ROOT
from openttd_mcp.verify import _network as network_core
from openttd_mcp.verify._scaffold import (
    create_run_root,
    emit_result,
    launch_openttd,
    read_until,
    resolve_verify_driver,
    teardown,
)
from openttd_mcp.verify._squirrel import (
    APPEND_UNIQUE,
    BOOL_STR,
    CONNECTED_STATION_TILES,
    CONTAINS,
    DIRECT_CONNECTED,
    DRAIN_EVENTS,
    IS_ROAD_LIKE,
    LOAD,
    REACHABLE,
    REACHABLE_ANY,
    ROUTE_DEPOT,
    STATION_TILE_CONNECTED,
    compose_ai_class,
    compose_info_nut,
)

VERIFIER_MODULE = "openttd_mcp.verify.freight_network"

# 128x128 saves spend ~140s rebuilding the reload observation plus a comparable scan window
# for a large vehicle roster (bus_network needed ~310s on one such save at cap 600); the
# freight window adds per-sample cargo-load polling on top of that scan, so the cap is raised
# further here rather than reusing bus_network's 600s.
FREIGHT_VERIFY_TIMEOUT_SEC = 900

INFO_NUT = compose_info_nut(
    short_name="BRTF",
    description="Freight network verification shim for saved McpBridge company.",
    date="2026-07-14",
)

_MAIN_FREIGHT_PRE_DEPOT = """function McpBridge::FirstRoadStopTile(station_id)
{
    // Truck-stop tile for this station -- freight routes are built exclusively from
    // truck stops (STATION_TRUCK_STOP), unlike bus_network which also scans bus stops.
    if (!AIStation.IsValidStation(station_id)) return -1;
    local station_tiles = AITileList_StationType(station_id, AIStation.STATION_TRUCK_STOP);
    foreach (station_tile, value in station_tiles) {
        if (AIRoad.IsRoadStationTile(station_tile)) return station_tile;
    }
    return -1;
}

"""

_MAIN_FREIGHT_CARGO = """function McpBridge::PrimaryCargo(vehicle_id)
{
    // Primary cargo = the cargo type with the largest capacity on this vehicle. Returns
    // [cargo_id, cargo_label, capacity, is_passenger_cargo]; cargo_id -1 if none found.
    local best_cargo = -1;
    local best_capacity = -1;
    local cargoes = AICargoList();
    foreach (cargo_id, value in cargoes) {
        local capacity = AIVehicle.GetCapacity(vehicle_id, cargo_id);
        if (capacity > 0 && capacity > best_capacity) {
            best_capacity = capacity;
            best_cargo = cargo_id;
        }
    }
    if (best_cargo == -1) return [-1, "", 0, false];
    local label = AICargo.GetCargoLabel(best_cargo);
    local is_passenger = AICargo.HasCargoClass(best_cargo, AICargo.CC_PASSENGERS);
    return [best_cargo, label, best_capacity, is_passenger];
}

"""

_MAIN_FREIGHT_START = """function McpBridge::Start()
{
    AIRoad.SetCurrentRoadType(AIRoad.ROADTYPE_ROAD);

    local truck_station_count = 0;
    local stations = AIStationList(AIStation.STATION_TRUCK_STOP);
    foreach (station_id, value in stations) truck_station_count++;
    local road_depot_count = 0;
    local depots = AIDepotList(AITile.TRANSPORT_ROAD);
    foreach (depot_tile, value in depots) road_depot_count++;

    // Enumerate every valid road freight route vehicle (>=2 distinct truck-stop station
    // orders, primary cargo not a passenger cargo).
    local routes = [];
    local road_vehicle_count = 0;
    local vehicles = AIVehicleList();
    foreach (vehicle_id, value in vehicles) {
        if (AIVehicle.GetVehicleType(vehicle_id) != AIVehicle.VT_ROAD) continue;
        road_vehicle_count++;
        local orders = AIOrder.GetOrderCount(vehicle_id);
        local good_orders = 0;
        local road_access_orders = 0;
        local connected_orders = 0;
        local station_ids = [];
        local station_tiles = [];
        for (local i = 0; i < orders; i++) {
            if (!AIOrder.IsGotoStationOrder(vehicle_id, i)) continue;
            local dest = AIOrder.GetOrderDestination(vehicle_id, i);
            local station_id = AIStation.GetStationID(dest);
            local is_truck_stop = AIStation.IsValidStation(station_id)
                && AIStation.HasStationType(station_id, AIStation.STATION_TRUCK_STOP);
            if (is_truck_stop) {
                good_orders++;
                this.AppendUnique(station_ids, station_id);
                local station_has_road_access = false;
                local station_connected = false;
                local tiles = AITileList_StationType(station_id, AIStation.STATION_TRUCK_STOP);
                foreach (station_tile, tile_value in tiles) {
                    if (!AIRoad.IsRoadStationTile(station_tile)) continue;
                    if (!this.Contains(station_tiles, station_tile)) station_tiles.append(station_tile);
                    // Either end carrying traffic counts: the designated front may be the
                    // dead end of a road-terminating drive-through stop, or a chained
                    // station/depot tile (road-like, not strictly IsRoadTile).
                    local front = AIRoad.GetRoadStationFrontTile(station_tile);
                    if (this.IsRoadLike(front)) station_has_road_access = true;
                    if (AIRoad.IsDriveThroughRoadStationTile(station_tile)) {
                        local back = AIRoad.GetDriveThroughBackTile(station_tile);
                        if (this.IsRoadLike(back)) station_has_road_access = true;
                    }
                    if (this.StationTileConnected(station_tile)) station_connected = true;
                }
                if (station_has_road_access) road_access_orders++;
                if (station_connected) connected_orders++;
            }
        }
        if (orders < 2 || good_orders < 2 || station_ids.len() < 2) continue;
        local cargo_info = this.PrimaryCargo(vehicle_id);
        if (cargo_info[0] == -1 || cargo_info[3]) continue; // no cargo capacity, or a passenger cargo
        local station_a = station_ids[0];
        local station_b = station_ids[1];
        routes.append({
            vid = vehicle_id,
            key = "" + vehicle_id,
            order_count = orders,
            road_access_orders = road_access_orders,
            connected_orders = connected_orders,
            stopped = AIVehicle.IsStoppedInDepot(vehicle_id),
            station_a = station_a,
            station_b = station_b,
            station_a_tile = this.FirstRoadStopTile(station_a),
            station_b_tile = this.FirstRoadStopTile(station_b),
            initial_tile = AIVehicle.GetLocation(vehicle_id),
            final_tile = -1,
            visited_a = false,
            visited_b = false,
            order_progressed = false,
            last_order_index = -2,
            idle_stopped_samples = 0,
            cargo_id = cargo_info[0],
            cargo_label = cargo_info[1],
            capacity = cargo_info[2],
            max_load_observed = 0,
            load_drop_observed = false,
            station_a_cargo_waiting_observed = false,
            last_load = -1,
            last_at_station = false,
        });
    }

    // Dynamic operation loop shared across all routes so events are attributed by vehicle.
    local states = {};
    foreach (route in routes) {
        states[route.key] <- { a_first = false, b_first = false, lost = false, crashed = false };
    }
    if (routes.len() > 0) {
        for (local sample = 0; sample < 1200; sample++) {
            this.DrainEvents(routes, states);
            local all_done = true;
            foreach (route in routes) {
                local location = AIVehicle.GetLocation(route.vid);
                local at_station = AIMap.IsValidTile(location) && AIRoad.IsRoadStationTile(location);
                if (at_station) {
                    local location_station = AIStation.GetStationID(location);
                    if (location_station == route.station_a) route.visited_a = true;
                    if (location_station == route.station_b) route.visited_b = true;
                }
                local load = AIVehicle.GetCargoLoad(route.vid, route.cargo_id);
                if (load > route.max_load_observed) route.max_load_observed = load;
                // Delivery evidence: load dropped since the previous sample after a station
                // visit -- the game only unloads cargo where a station accepts it, so a
                // drop right after a stop is honest sink evidence rather than a proxy.
                if (route.last_load != -1 && route.last_at_station && load < route.last_load) {
                    route.load_drop_observed = true;
                }
                route.last_load = load;
                route.last_at_station = at_station;
                if (!route.station_a_cargo_waiting_observed && route.station_a != -1
                        && AIStation.GetCargoWaiting(route.station_a, route.cargo_id) > 0) {
                    route.station_a_cargo_waiting_observed = true;
                }
                local current_order_index = AIOrder.ResolveOrderPosition(route.vid, AIOrder.ORDER_CURRENT);
                if (route.last_order_index != -2 && current_order_index != route.last_order_index) route.order_progressed = true;
                route.last_order_index = current_order_index;
                if (AIVehicle.IsInDepot(route.vid) && AIVehicle.IsStoppedInDepot(route.vid) && AIVehicle.GetCurrentSpeed(route.vid) == 0) {
                    route.idle_stopped_samples++;
                } else {
                    route.idle_stopped_samples = 0;
                }
                local st = states[route.key];
                local stopped_terminal = route.stopped || route.idle_stopped_samples >= 5;
                local done = (route.visited_a || st.a_first)
                    && (route.visited_b || st.b_first)
                    && route.order_progressed
                    && route.max_load_observed > 0;
                if (stopped_terminal) done = true;
                if (!done && !st.lost && !st.crashed) all_done = false;
            }
            if (all_done) break;
            this.Sleep(5);
        }
        this.DrainEvents(routes, states);
        foreach (route in routes) {
            route.final_tile = AIVehicle.GetLocation(route.vid);
        }
    }

    print("FREIGHT_NET|summary|" + routes.len() + "|" + road_vehicle_count + "|" + truck_station_count
        + "|" + road_depot_count);

    local idx = 0;
    foreach (route in routes) {
        local st = states[route.key];
        local station_a_tile = route.station_a_tile;
        local station_b_tile = route.station_b_tile;
        local truck_stop_types = [AIStation.STATION_TRUCK_STOP];
        local station_a_tiles = this.ConnectedStationTiles(route.station_a, truck_stop_types);
        local station_b_tiles = this.ConnectedStationTiles(route.station_b, truck_stop_types);
        local station_a_connected = station_a_tiles.len() > 0;
        local station_b_connected = station_b_tiles.len() > 0;
        local a_to_b = this.ReachableAny(station_a_tiles, station_b_tiles);
        local b_to_a = this.ReachableAny(station_b_tiles, station_a_tiles);
        local depot = this.RouteDepot(station_a_tiles);
        local visited_a = route.visited_a || st.a_first;
        local visited_b = route.visited_b || st.b_first;
        local moved = route.final_tile != route.initial_tile;
        print("FREIGHT_NET|route|" + idx
            + "|" + route.vid
            + "|" + route.order_count
            + "|" + this.BoolStr(route.stopped)
            + "|" + route.initial_tile
            + "|" + route.final_tile
            + "|" + this.BoolStr(moved)
            + "|" + route.station_a
            + "|" + route.station_b
            + "|" + station_a_tile
            + "|" + station_b_tile
            + "|" + depot[0]
            + "|" + depot[1]
            + "|" + this.BoolStr(station_a_connected)
            + "|" + this.BoolStr(station_b_connected)
            + "|" + this.BoolStr(depot[2])
            + "|" + this.BoolStr(a_to_b)
            + "|" + this.BoolStr(b_to_a)
            + "|" + this.BoolStr(depot[3])
            + "|" + this.BoolStr(depot[4])
            + "|" + this.BoolStr(visited_a)
            + "|" + this.BoolStr(visited_b)
            + "|" + this.BoolStr(st.lost)
            + "|" + this.BoolStr(st.crashed)
            + "|" + this.BoolStr(route.order_progressed)
            + "|" + route.road_access_orders
            + "|" + route.connected_orders
            + "|" + route.cargo_id
            + "|" + route.cargo_label
            + "|" + route.capacity
            + "|" + route.max_load_observed
            + "|" + this.BoolStr(route.load_drop_observed)
            + "|" + this.BoolStr(route.station_a_cargo_waiting_observed));
        idx++;
    }
    print("FREIGHT_NET|done|" + routes.len());
    while (true) this.Sleep(200);
}
"""

MAIN_NUT = compose_ai_class(
    LOAD,
    BOOL_STR,
    CONTAINS,
    APPEND_UNIQUE,
    IS_ROAD_LIKE,
    DIRECT_CONNECTED,
    REACHABLE,
    CONNECTED_STATION_TILES,
    REACHABLE_ANY,
    _MAIN_FREIGHT_PRE_DEPOT,
    STATION_TILE_CONNECTED,
    ROUTE_DEPOT,
    _MAIN_FREIGHT_CARGO,
    DRAIN_EVENTS,
    _MAIN_FREIGHT_START,
)

ROUTE_FIELDS: Sequence[network_core.FieldSpec] = [
    ("route_index", int),
    ("vehicle_id", int),
    ("order_count", int),
    ("stopped_in_depot", "bool"),
    ("initial_tile", int),
    ("final_tile", int),
    ("vehicle_moved", "bool"),
    ("station_a", int),
    ("station_b", int),
    ("station_a_tile", int),
    ("station_b_tile", int),
    ("depot_tile", int),
    ("depot_front_tile", int),
    ("station_a_connected", "bool"),
    ("station_b_connected", "bool"),
    ("depot_connected", "bool"),
    ("station_a_to_station_b", "bool"),
    ("station_b_to_station_a", "bool"),
    ("depot_to_station_a", "bool"),
    ("station_a_to_depot", "bool"),
    ("station_a_visited_by_vehicle", "bool"),
    ("station_b_visited_by_vehicle", "bool"),
    ("vehicle_lost_event", "bool"),
    ("vehicle_crashed_event", "bool"),
    ("order_progressed", "bool"),
    ("road_access_truck_station_orders", int),
    ("connected_truck_station_orders", int),
    ("cargo_id", int),
    ("cargo_label", str),
    ("capacity", int),
    ("max_load_observed", int),
    ("load_drop_observed", "bool"),
    ("station_a_cargo_waiting_observed", "bool"),
]

SUMMARY_FIELDS: Sequence[network_core.FieldSpec] = (
    ("valid_route_count", int),
    ("road_vehicle_count", int),
    ("truck_station_count", int),
    ("road_depot_count", int),
)

_NETWORK_LOG_SCHEMA = network_core.NetworkLogSchema(
    prefix="FREIGHT_NET",
    route_fields=ROUTE_FIELDS,
    summary_fields=SUMMARY_FIELDS,
)

parse_bool = network_core.parse_bool


def parse_route_line(line: str) -> dict | None:
    """Parse a ``FREIGHT_NET|route|...`` line into a typed per-route dict."""
    return _NETWORK_LOG_SCHEMA.parse_route_line(line)


def parse_summary_line(line: str) -> dict | None:
    return _NETWORK_LOG_SCHEMA.parse_summary_line(line)


def evaluate_route(route: dict) -> dict:
    """Return the route with derived per-route acceptance booleans and ``route_ok``.

    Baseline acceptance requires topology (road-graph reachability, both stations visited,
    order progressed, no lost/crash) plus observed cargo *pickup* (``max_load_observed > 0``).
    Confirmed delivery (``load_drop_observed``) is recorded and rolled into the stricter
    ``route_ok_strict`` flag but is not required for the baseline verdict -- see module
    docstring for why.
    """
    checks = {
        "orders_target_both_stations": route["station_a"] != -1
        and route["station_b"] != -1
        and route["station_a"] != route["station_b"],
        "both_stations_same_road_component_as_depot": (
            route["station_a_connected"]
            and route["station_b_connected"]
            and route["depot_connected"]
            and route["station_a_to_station_b"]
            and route["station_b_to_station_a"]
            and route["depot_to_station_a"]
            and route["station_a_to_depot"]
        ),
        "road_access_orders_ok": route["road_access_truck_station_orders"] >= 2,
        "connected_orders_ok": route["connected_truck_station_orders"] >= 2,
        "cargo_pickup_observed": route["max_load_observed"] > 0,
    }
    enriched = dict(route)
    enriched.update(checks)
    baseline_topology_ok = bool(
        route["order_count"] >= 2
        and not route["stopped_in_depot"]
        and route["vehicle_moved"]
        and route["station_a_visited_by_vehicle"]
        and route["station_b_visited_by_vehicle"]
        and route["order_progressed"]
        and not route["vehicle_lost_event"]
        and not route["vehicle_crashed_event"]
        and checks["orders_target_both_stations"]
        and checks["both_stations_same_road_component_as_depot"]
        and checks["road_access_orders_ok"]
        and checks["connected_orders_ok"]
    )
    enriched["route_ok"] = bool(
        baseline_topology_ok and checks["cargo_pickup_observed"]
    )
    enriched["route_ok_strict"] = bool(
        enriched["route_ok"] and route["load_drop_observed"]
    )
    return enriched


def route_signature(route: dict) -> tuple[int, int]:
    """Order-independent station-tile signature used to deduplicate distinct routes."""
    return cast(tuple[int, int], network_core.route_signature(route))


def _freight_result_additions(
    evaluated_routes: list[dict],
    signature: network_core.RouteSignatureFactory,
) -> dict[str, object]:
    strict_passing = [route for route in evaluated_routes if route["route_ok_strict"]]
    strict_signatures = sorted({signature(route) for route in strict_passing})
    return {
        "delivered_route_count": len(strict_passing),
        "distinct_delivered_route_count": len(strict_signatures),
        "delivered_route_signatures": [list(sig) for sig in strict_signatures],
    }


def aggregate_network(
    routes: list[dict], summary: dict | None, min_routes: int = 2
) -> dict:
    """Aggregate per-route evaluations into a network verdict."""
    return network_core.aggregate_network(
        routes,
        summary,
        evaluator=evaluate_route,
        route_signature=route_signature,
        min_routes=min_routes,
        derived_additions=_freight_result_additions,
    )


def parse_verifier_log(log_path: Path) -> tuple[list[dict], dict | None]:
    return _NETWORK_LOG_SCHEMA.parse_log(
        log_path,
        parse_route_line=parse_route_line,
        parse_summary_line=parse_summary_line,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "save_path", nargs="?", default=str(ROOT / "artifacts" / "llm_bus_route.sav")
    )
    parser.add_argument(
        "--min-routes",
        type=int,
        default=2,
        help="Minimum distinct passing routes required for acceptance (default 2).",
    )
    args = parser.parse_args(argv)

    save_path = Path(args.save_path)
    if not save_path.is_absolute():
        save_path = (ROOT / save_path).resolve()
    root, log_path, verify_result_path, env = create_run_root(
        prefix="freight-network",
        game_start_script="echo freight verifier: using saved McpBridge AI\nunpause\n",
        info_nut=INFO_NUT,
        main_nut=MAIN_NUT,
        log_filename="verify_freight_network.log",
    )
    driver = resolve_verify_driver()
    proc = launch_openttd(root, save_path, env, driver=driver)
    try:
        read_until(proc, log_path, "FREIGHT_NET|done|", FREIGHT_VERIFY_TIMEOUT_SEC)
        routes, summary = parse_verifier_log(log_path)
        result = aggregate_network(routes, summary, args.min_routes)
        result["save_path"] = str(save_path)
        emit_result(
            proc,
            save_path=save_path,
            log_path=log_path,
            result_path=verify_result_path,
            result=result,
            verifier_module=VERIFIER_MODULE,
            tolerate_slow_shutdown=True,
            driver=driver,
        )
        return 0 if result["network_ok"] else 2
    finally:
        teardown(proc, driver=driver)


if __name__ == "__main__":
    raise SystemExit(main())
