"""Independently verify that a saved McpBridge company owns a >=2-route bus network.

This is the network generalisation of ``openttd_mcp.verify.bus_route``. The single-route
verifier picks one arbitrary valid route vehicle and measures its stations against the
*globally largest* town's road network. That is wrong for a multi-route network whose
routes live in different towns: a route built in a smaller town has zero tiles inside the
largest town's influence and is rejected even though it is a perfectly good route.

This verifier instead enumerates *every* valid route vehicle, measures each station's
road-component size against that station's *own* closest town, and evaluates each route
independently. Acceptance requires at least ``--min-routes`` (default 2) independent
operating routes that each pass the per-route criteria.

Station-type scope: a "route stop" is any road stop, bus *or* truck, so a
route built from truck stops (``build_truck_stop_drive_through``) is recognised rather than scored as
zero routes. Known limitation — this remains a *road-route topology and operation* gate,
not a freight-economics gate: it confirms two connected stops on a substantial town road
network with an operating vehicle that visits both, but it does not check that cargo flows
from a producing industry to an accepting one. Its ``stations_on_substantial_town_road_network``
criterion is also town-bound, so an industry-to-industry freight route whose stops lie
outside any town's influence will still fail here. Validating cargo source->sink is the job
of ``openttd_mcp.verify.freight_network``, not this module.

The Squirrel side emits one ``BUS_ROUTE_NET|route|...`` line per valid route vehicle plus a
``BUS_ROUTE_NET|summary|...`` line. All aggregation lives in pure Python helpers
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
    FIND_LARGEST_TOWN,
    IS_ROAD_LIKE,
    LOAD,
    REACHABLE,
    REACHABLE_ANY,
    ROUTE_DEPOT,
    STATION_TILE_CONNECTED,
    compose_ai_class,
    compose_info_nut,
    compose_town_road_component_size,
)

VERIFIER_MODULE = "openttd_mcp.verify.bus_network"

MIN_TOWN_ROAD_COMPONENT_TILES = 10
# Large 128x128 saves can spend ~140s rebuilding the reload observation plus
# ~165s scanning ~80 vehicles; 300s was too tight, 600s leaves about 2x margin.
NETWORK_VERIFY_TIMEOUT_SEC = 600

INFO_NUT = compose_info_nut(
    short_name="MCPB",
    description="Network verification shim for saved McpBridge company.",
    date="2026-07-01",
)

_TOWN_ROAD_COMPONENT_SIZE = compose_town_road_component_size(guard_invalid_town=True)

_MAIN_NETWORK_PRE_CONNECTIVITY = """function McpBridge::FirstRoadStopTile(station_id)
{
    // Road stop tile for either a bus or a truck drive-through stop: the freight
    // fix means a route can be built from truck stops, whose tiles are absent from
    // the STATION_BUS_STOP tile list. Both stop kinds are IsRoadStationTile.
    if (!AIStation.IsValidStation(station_id)) return -1;
    local road_stop_types = [AIStation.STATION_BUS_STOP, AIStation.STATION_TRUCK_STOP];
    foreach (station_type in road_stop_types) {
        local station_tiles = AITileList_StationType(station_id, station_type);
        foreach (station_tile, value in station_tiles) {
            if (AIRoad.IsRoadStationTile(station_tile)) return station_tile;
        }
    }
    return -1;
}

"""

_MAIN_NETWORK_POST_CONNECTIVITY = """function McpBridge::StationOwnTown(station_tile)
{
    if (!AIMap.IsValidTile(station_tile)) return -1;
    return AITile.GetClosestTown(station_tile);
}

"""

_MAIN_NETWORK_START = """function McpBridge::Start()
{
    AIRoad.SetCurrentRoadType(AIRoad.ROADTYPE_ROAD);
    local largest_town = this.FindLargestTown();

    local bus_station_count = 0;
    local stations = AIStationList(AIStation.STATION_BUS_STOP);
    foreach (station_id, value in stations) bus_station_count++;
    local road_depot_count = 0;
    local depots = AIDepotList(AITile.TRANSPORT_ROAD);
    foreach (depot_tile, value in depots) road_depot_count++;

    // Enumerate every valid road route vehicle (>=2 distinct bus-stop station orders).
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
            // A route stop is any road stop -- bus OR truck. The old bus-only check
            // scored freight (truck-stop) routes as zero good_orders, so a working
            // truck route verified as no routes at all.
            local is_road_stop = AIStation.IsValidStation(station_id)
                && (AIStation.HasStationType(station_id, AIStation.STATION_BUS_STOP)
                    || AIStation.HasStationType(station_id, AIStation.STATION_TRUCK_STOP));
            if (is_road_stop) {
                good_orders++;
                this.AppendUnique(station_ids, station_id);
                local station_has_road_access = false;
                local station_connected = false;
                local station_stop_types = [AIStation.STATION_BUS_STOP, AIStation.STATION_TRUCK_STOP];
                foreach (station_type in station_stop_types) {
                    local tiles = AITileList_StationType(station_id, station_type);
                    foreach (station_tile, tile_value in tiles) {
                        if (!AIRoad.IsRoadStationTile(station_tile)) continue;
                        if (!this.Contains(station_tiles, station_tile)) station_tiles.append(station_tile);
                        local front = AIRoad.GetRoadStationFrontTile(station_tile);
                        if (AIRoad.IsRoadTile(front)) station_has_road_access = true;
                        if (AIRoad.IsDriveThroughRoadStationTile(station_tile)) {
                            local back = AIRoad.GetDriveThroughBackTile(station_tile);
                            if (AIRoad.IsRoadTile(back)) station_has_road_access = true;
                        }
                        if (this.StationTileConnected(station_tile)) station_connected = true;
                    }
                }
                if (station_has_road_access) road_access_orders++;
                if (station_connected) connected_orders++;
            }
        }
        if (orders >= 2 && good_orders >= 2 && station_ids.len() >= 2) {
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
            });
        }
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
                if (AIMap.IsValidTile(location) && AIRoad.IsRoadStationTile(location)) {
                    local location_station = AIStation.GetStationID(location);
                    if (location_station == route.station_a) route.visited_a = true;
                    if (location_station == route.station_b) route.visited_b = true;
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
                    && route.order_progressed;
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

    print("BUS_ROUTE_NET|summary|" + routes.len() + "|" + road_vehicle_count + "|" + bus_station_count
        + "|" + road_depot_count + "|" + largest_town + "|" + AITown.GetName(largest_town)
        + "|" + AITown.GetPopulation(largest_town));

    local idx = 0;
    foreach (route in routes) {
        local st = states[route.key];
        local station_a_tile = route.station_a_tile;
        local station_b_tile = route.station_b_tile;
        local road_stop_types = [AIStation.STATION_BUS_STOP, AIStation.STATION_TRUCK_STOP];
        local station_a_tiles = this.ConnectedStationTiles(route.station_a, road_stop_types);
        local station_b_tiles = this.ConnectedStationTiles(route.station_b, road_stop_types);
        local station_a_connected = station_a_tiles.len() > 0;
        local station_b_connected = station_b_tiles.len() > 0;
        local a_to_b = this.ReachableAny(station_a_tiles, station_b_tiles);
        local b_to_a = this.ReachableAny(station_b_tiles, station_a_tiles);
        local town_a = this.StationOwnTown(station_a_tile);
        local town_b = this.StationOwnTown(station_b_tile);
        local comp_a = station_a_tile != -1 ? this.TownRoadComponentSize(station_a_tile, town_a, 128) : 0;
        local comp_b = station_b_tile != -1 ? this.TownRoadComponentSize(station_b_tile, town_b, 128) : 0;
        local depot = this.RouteDepot(station_a_tiles);
        local visited_a = route.visited_a || st.a_first;
        local visited_b = route.visited_b || st.b_first;
        local moved = route.final_tile != route.initial_tile;
        print("BUS_ROUTE_NET|route|" + idx
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
            + "|" + town_a
            + "|" + town_b
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
            + "|" + comp_a
            + "|" + comp_b
            + "|" + route.road_access_orders
            + "|" + route.connected_orders);
        idx++;
    }
    print("BUS_ROUTE_NET|done|" + routes.len());
    while (true) this.Sleep(200);
}
"""

MAIN_NUT = compose_ai_class(
    LOAD,
    FIND_LARGEST_TOWN,
    BOOL_STR,
    CONTAINS,
    APPEND_UNIQUE,
    IS_ROAD_LIKE,
    DIRECT_CONNECTED,
    REACHABLE,
    CONNECTED_STATION_TILES,
    REACHABLE_ANY,
    _TOWN_ROAD_COMPONENT_SIZE,
    _MAIN_NETWORK_PRE_CONNECTIVITY,
    STATION_TILE_CONNECTED,
    _MAIN_NETWORK_POST_CONNECTIVITY,
    ROUTE_DEPOT,
    DRAIN_EVENTS,
    _MAIN_NETWORK_START,
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
    ("station_a_town", int),
    ("station_b_town", int),
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
    ("station_a_town_road_component_tiles", int),
    ("station_b_town_road_component_tiles", int),
    ("road_access_bus_station_orders", int),
    ("connected_bus_station_orders", int),
]

SUMMARY_FIELDS: Sequence[network_core.FieldSpec] = (
    ("valid_route_count", int),
    ("road_vehicle_count", int),
    ("bus_station_count", int),
    ("road_depot_count", int),
    ("largest_town_id", int),
    ("largest_town_name", str),
    ("largest_town_population", int),
)

_NETWORK_LOG_SCHEMA = network_core.NetworkLogSchema(
    prefix="BUS_ROUTE_NET",
    route_fields=ROUTE_FIELDS,
    summary_fields=SUMMARY_FIELDS,
)

parse_bool = network_core.parse_bool


def parse_route_line(line: str) -> dict | None:
    """Parse a ``BUS_ROUTE_NET|route|...`` line into a typed per-route dict."""
    return _NETWORK_LOG_SCHEMA.parse_route_line(line)


def parse_summary_line(line: str) -> dict | None:
    return _NETWORK_LOG_SCHEMA.parse_summary_line(line)


def evaluate_route(
    route: dict, min_component_tiles: int = MIN_TOWN_ROAD_COMPONENT_TILES
) -> dict:
    """Return the route with derived per-route acceptance booleans and ``route_ok``.

    Each route is judged against its *own* stations' town road networks, so a route in a
    smaller town is no longer penalised for having zero tiles in the largest town.
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
        "stations_on_substantial_town_road_network": (
            route["station_a_town_road_component_tiles"] >= min_component_tiles
            and route["station_b_town_road_component_tiles"] >= min_component_tiles
        ),
        "road_access_orders_ok": route["road_access_bus_station_orders"] >= 2,
        "connected_orders_ok": route["connected_bus_station_orders"] >= 2,
    }
    enriched = dict(route)
    enriched.update(checks)
    enriched["route_ok"] = bool(
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
        and checks["stations_on_substantial_town_road_network"]
        and checks["road_access_orders_ok"]
        and checks["connected_orders_ok"]
    )
    return enriched


def route_signature(route: dict) -> tuple[int, int]:
    """Order-independent station-tile signature used to deduplicate distinct routes."""
    return cast(tuple[int, int], network_core.route_signature(route))


def aggregate_network(
    routes: list[dict],
    summary: dict | None,
    min_routes: int = 2,
    min_component_tiles: int = MIN_TOWN_ROAD_COMPONENT_TILES,
) -> dict:
    """Aggregate per-route evaluations into a network verdict."""
    return network_core.aggregate_network(
        routes,
        summary,
        evaluator=lambda route: evaluate_route(route, min_component_tiles),
        route_signature=route_signature,
        min_routes=min_routes,
        configuration_metadata={
            "min_town_road_component_tiles": min_component_tiles,
        },
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
    parser.add_argument(
        "--min-component-tiles",
        type=int,
        default=MIN_TOWN_ROAD_COMPONENT_TILES,
        help="Minimum own-town road-component tiles per station (default 10).",
    )
    args = parser.parse_args(argv)

    save_path = Path(args.save_path)
    if not save_path.is_absolute():
        save_path = (ROOT / save_path).resolve()
    root, log_path, verify_result_path, env = create_run_root(
        prefix="bus-network",
        game_start_script="echo network verifier: using saved McpBridge AI\nunpause\n",
        info_nut=INFO_NUT,
        main_nut=MAIN_NUT,
        log_filename="verify_bus_network.log",
    )
    driver = resolve_verify_driver()
    proc = launch_openttd(root, save_path, env, driver=driver)
    try:
        read_until(proc, log_path, "BUS_ROUTE_NET|done|", NETWORK_VERIFY_TIMEOUT_SEC)
        routes, summary = parse_verifier_log(log_path)
        result = aggregate_network(
            routes, summary, args.min_routes, args.min_component_tiles
        )
        result["save_path"] = str(save_path)
        emit_result(
            proc,
            save_path=save_path,
            log_path=log_path,
            result_path=verify_result_path,
            result=result,
            verifier_module=VERIFIER_MODULE,
            driver=driver,
        )
        return 0 if result["network_ok"] else 2
    finally:
        teardown(proc, driver=driver)


if __name__ == "__main__":
    raise SystemExit(main())
