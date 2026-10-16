#!/usr/bin/env python3
"""Load a save and verify the saved McpBridge company owns an executing bus route."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from openttd_mcp.runtime import ROOT
from openttd_mcp.verify._network import parse_bool
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

VERIFIER_MODULE = "openttd_mcp.verify.bus_route"

# 128x128 save reloads can spend ~140s before the verifier payload; 180s leaves
# little margin, so keep a bounded but roomier single-route verifier wait.
ROUTE_VERIFY_TIMEOUT_SEC = 300

INFO_NUT = compose_info_nut(
    short_name="MCPB",
    description="Verification shim for saved McpBridge company.",
    date="2026-06-27",
)

_TOWN_ROAD_COMPONENT_SIZE = compose_town_road_component_size(guard_invalid_town=False)

_MAIN_ROUTE_PRE_CONNECTIVITY = """function McpBridge::FirstBusStopTile(station_id)
{
    if (!AIStation.IsValidStation(station_id)) return -1;
    local station_tiles = AITileList_StationType(station_id, AIStation.STATION_BUS_STOP);
    foreach (station_tile, value in station_tiles) {
        if (AIRoad.IsRoadStationTile(station_tile)) return station_tile;
    }
    return -1;
}

"""

_MAIN_ROUTE_POST_CONNECTIVITY = """function McpBridge::ProcessEvents(vehicle_id, station_a, station_b, event_state)
{
    while (AIEventController.IsEventWaiting()) {
        local event = AIEventController.GetNextEvent();
        local event_type = event.GetEventType();
        if (event_type == AIEvent.ET_STATION_FIRST_VEHICLE) {
            local station_event = AIEventStationFirstVehicle.Convert(event);
            local event_vehicle = station_event.GetVehicleID();
            local event_station = station_event.GetStationID();
            print("BUS_ROUTE_TRACE|event|station_first_vehicle|" + event_station + "|" + event_vehicle);
            if (event_vehicle == vehicle_id && event_station == station_a) event_state.station_a_first_vehicle = true;
            if (event_vehicle == vehicle_id && event_station == station_b) event_state.station_b_first_vehicle = true;
        } else if (event_type == AIEvent.ET_VEHICLE_LOST) {
            local lost_event = AIEventVehicleLost.Convert(event);
            local lost_vehicle = lost_event.GetVehicleID();
            print("BUS_ROUTE_TRACE|event|vehicle_lost|" + lost_vehicle);
            if (lost_vehicle == vehicle_id) event_state.vehicle_lost = true;
        } else if (event_type == AIEvent.ET_VEHICLE_CRASHED) {
            local crash_event = AIEventVehicleCrashed.Convert(event);
            local crashed_vehicle = crash_event.GetVehicleID();
            print("BUS_ROUTE_TRACE|event|vehicle_crashed|" + crashed_vehicle);
            if (crashed_vehicle == vehicle_id) event_state.vehicle_crashed = true;
        } else if (event_type == AIEvent.ET_VEHICLE_WAITING_IN_DEPOT) {
            local depot_event = AIEventVehicleWaitingInDepot.Convert(event);
            local depot_vehicle = depot_event.GetVehicleID();
            print("BUS_ROUTE_TRACE|event|vehicle_waiting_in_depot|" + depot_vehicle);
        }
    }
}

function McpBridge::EmitTrace(sample, vehicle_id)
{
    local location = AIVehicle.GetLocation(vehicle_id);
    local speed = AIVehicle.GetCurrentSpeed(vehicle_id);
    local state = AIVehicle.GetState(vehicle_id);
    local in_depot = AIVehicle.IsInDepot(vehicle_id);
    local stopped = AIVehicle.IsStoppedInDepot(vehicle_id);
    local order_index = AIOrder.ResolveOrderPosition(vehicle_id, AIOrder.ORDER_CURRENT);
    local current_dest = -1;
    local current_dest_station = -1;
    if (AIOrder.GetOrderCount(vehicle_id) > 0) {
        current_dest = AIOrder.GetOrderDestination(vehicle_id, AIOrder.ORDER_CURRENT);
        if (AIMap.IsValidTile(current_dest) && AIRoad.IsRoadStationTile(current_dest)) current_dest_station = AIStation.GetStationID(current_dest);
    }
    local location_station = -1;
    if (AIMap.IsValidTile(location) && AIRoad.IsRoadStationTile(location)) location_station = AIStation.GetStationID(location);
    print("BUS_ROUTE_TRACE|sample|" + sample + "|" + location + "|" + speed + "|" + state + "|" + this.BoolStr(in_depot) + "|" + this.BoolStr(stopped) + "|" + order_index + "|" + current_dest + "|" + current_dest_station + "|" + location_station);
}

function McpBridge::Start()
{
    AIRoad.SetCurrentRoadType(AIRoad.ROADTYPE_ROAD);
    local town_id = this.FindLargestTown();
    local bus_station_count = 0;
    local bus_station_in_largest_town = 0;
    local road_access_bus_station_orders = 0;
    local connected_bus_station_orders = 0;
    local road_depot_count = 0;
    local road_depot_with_front_road_count = 0;
    local depot_connected_count = 0;

    local stations = AIStationList(AIStation.STATION_BUS_STOP);
    foreach (station_id, value in stations) {
        bus_station_count++;
        if (AIStation.IsWithinTownInfluence(station_id, town_id)) bus_station_in_largest_town++;
    }

    local depots = AIDepotList(AITile.TRANSPORT_ROAD);
    foreach (depot_tile, value in depots) {
        road_depot_count++;
        local depot_front = AIRoad.GetRoadDepotFrontTile(depot_tile);
        if (AIMap.IsValidTile(depot_front) && AIRoad.IsRoadTile(depot_front)) road_depot_with_front_road_count++;
        if (AIMap.IsValidTile(depot_front) && this.DirectConnected(depot_tile, depot_front) && this.DirectConnected(depot_front, depot_tile)) depot_connected_count++;
    }

    local road_vehicle_count = 0;
    local valid_bus_route_vehicle_count = 0;
    local first_vehicle = -1;
    local first_order_count = 0;
    local first_stopped_in_depot = true;
    local first_initial_tile = -1;
    local first_final_tile = -1;
    local first_vehicle_moved = false;
    local order_station_ids = [];
    local order_station_tiles = [];

    local vehicles = AIVehicleList();
    foreach (vehicle_id, value in vehicles) {
        if (AIVehicle.GetVehicleType(vehicle_id) != AIVehicle.VT_ROAD) continue;
        road_vehicle_count++;
        local orders = AIOrder.GetOrderCount(vehicle_id);
        local stopped = AIVehicle.IsStoppedInDepot(vehicle_id);
        local good_orders = 0;
        local road_access_orders = 0;
        local connected_orders = 0;
        local vehicle_station_ids = [];
        local vehicle_station_tiles = [];
        for (local i = 0; i < orders; i++) {
            if (!AIOrder.IsGotoStationOrder(vehicle_id, i)) continue;
            local dest = AIOrder.GetOrderDestination(vehicle_id, i);
            local station_id = AIStation.GetStationID(dest);
            if (AIStation.IsValidStation(station_id) && AIStation.HasStationType(station_id, AIStation.STATION_BUS_STOP)) {
                good_orders++;
                this.AppendUnique(vehicle_station_ids, station_id);
                local station_has_road_access = false;
                local station_connected = false;
                local station_tiles = AITileList_StationType(station_id, AIStation.STATION_BUS_STOP);
                foreach (station_tile, tile_value in station_tiles) {
                    if (!AIRoad.IsRoadStationTile(station_tile)) continue;
                    if (!this.Contains(vehicle_station_tiles, station_tile)) vehicle_station_tiles.append(station_tile);
                    local front = AIRoad.GetRoadStationFrontTile(station_tile);
                    if (AIRoad.IsRoadTile(front)) station_has_road_access = true;
                    if (AIRoad.IsDriveThroughRoadStationTile(station_tile)) {
                        local back = AIRoad.GetDriveThroughBackTile(station_tile);
                        if (AIRoad.IsRoadTile(back)) station_has_road_access = true;
                    }
                    if (this.StationTileConnected(station_tile)) station_connected = true;
                }
                if (station_has_road_access) road_access_orders++;
                if (station_connected) connected_orders++;
            }
        }
        if (orders >= 2 && good_orders >= 2 && vehicle_station_ids.len() >= 2) {
            road_access_bus_station_orders += road_access_orders;
            connected_bus_station_orders += connected_orders;
            valid_bus_route_vehicle_count++;
            if (first_vehicle == -1) {
                first_vehicle = vehicle_id;
                first_order_count = orders;
                first_stopped_in_depot = stopped;
                first_initial_tile = AIVehicle.GetLocation(vehicle_id);
                order_station_ids = vehicle_station_ids;
                order_station_tiles = vehicle_station_tiles;
            }
        }
    }

    local station_a = -1;
    local station_b = -1;
    local station_a_tile = -1;
    local station_b_tile = -1;
    if (order_station_ids.len() >= 2) {
        station_a = order_station_ids[0];
        station_b = order_station_ids[1];
        station_a_tile = this.FirstBusStopTile(station_a);
        station_b_tile = this.FirstBusStopTile(station_b);
    }

    local bus_stop_types = [AIStation.STATION_BUS_STOP];
    local station_a_tiles = this.ConnectedStationTiles(station_a, bus_stop_types);
    local station_b_tiles = this.ConnectedStationTiles(station_b, bus_stop_types);
    local station_a_connected = station_a_tiles.len() > 0;
    local station_b_connected = station_b_tiles.len() > 0;
    local station_a_to_station_b = this.ReachableAny(station_a_tiles, station_b_tiles);
    local station_b_to_station_a = this.ReachableAny(station_b_tiles, station_a_tiles);
    local station_a_town_road_component_tiles = station_a_tile != -1 ? this.TownRoadComponentSize(station_a_tile, town_id, 128) : 0;
    local station_b_town_road_component_tiles = station_b_tile != -1 ? this.TownRoadComponentSize(station_b_tile, town_id, 128) : 0;

    local depot = this.RouteDepot(station_a_tiles);
    local route_depot_tile = depot[0];
    local route_depot_front = depot[1];
    local depot_connected = depot[2];
    local depot_to_station_a = depot[3];
    local station_a_to_depot = depot[4];
    if (!depot_to_station_a && route_depot_tile != -1) {
        // Fallback depot (no mutually-reachable one found): report its one-way
        // reachability honestly instead of RouteDepot's blanket false/false.
        depot_to_station_a = this.ReachableAny([route_depot_tile], station_a_tiles);
        station_a_to_depot = this.ReachableAny(station_a_tiles, [route_depot_tile]);
    }

    local station_a_visited = false;
    local station_b_visited = false;
    local order_progressed = false;
    local last_order_index = -2;
    local last_current_dest = -1;
    local samples = 0;
    local idle_stopped_samples = 0;
    local event_state = {
        station_a_first_vehicle = false,
        station_b_first_vehicle = false,
        vehicle_lost = false,
        vehicle_crashed = false,
    };

    if (first_vehicle != -1) {
        for (local sample = 0; sample < 900; sample++) {
            samples = sample + 1;
            this.ProcessEvents(first_vehicle, station_a, station_b, event_state);
            local location = AIVehicle.GetLocation(first_vehicle);
            if (AIMap.IsValidTile(location) && AIRoad.IsRoadStationTile(location)) {
                local location_station = AIStation.GetStationID(location);
                if (location_station == station_a) station_a_visited = true;
                if (location_station == station_b) station_b_visited = true;
            }
            local current_order_index = AIOrder.ResolveOrderPosition(first_vehicle, AIOrder.ORDER_CURRENT);
            if (last_order_index != -2 && current_order_index != last_order_index) order_progressed = true;
            last_order_index = current_order_index;
            if (AIOrder.GetOrderCount(first_vehicle) > 0) last_current_dest = AIOrder.GetOrderDestination(first_vehicle, AIOrder.ORDER_CURRENT);
            if (AIVehicle.IsInDepot(first_vehicle) && AIVehicle.IsStoppedInDepot(first_vehicle) && AIVehicle.GetCurrentSpeed(first_vehicle) == 0) {
                idle_stopped_samples++;
            } else {
                idle_stopped_samples = 0;
            }
            local depot_stopped_terminal = idle_stopped_samples >= 5;
            if (sample == 0 || sample % 25 == 0 || station_a_visited || station_b_visited || event_state.vehicle_lost || event_state.vehicle_crashed) this.EmitTrace(sample, first_vehicle);
            if (station_a_visited && station_b_visited && order_progressed) break;
            if (event_state.vehicle_lost || event_state.vehicle_crashed) break;
            if (depot_stopped_terminal) break;
            this.Sleep(5);
        }
        this.ProcessEvents(first_vehicle, station_a, station_b, event_state);
        first_final_tile = AIVehicle.GetLocation(first_vehicle);
        first_vehicle_moved = first_final_tile != first_initial_tile;
        if (event_state.station_a_first_vehicle) station_a_visited = true;
        if (event_state.station_b_first_vehicle) station_b_visited = true;
    }

    print("BUS_ROUTE_VERIFIER|" + town_id + "|" + AITown.GetName(town_id) + "|" + AITown.GetPopulation(town_id)
        + "|" + bus_station_count
        + "|" + bus_station_in_largest_town
        + "|" + road_depot_count
        + "|" + road_depot_with_front_road_count
        + "|" + depot_connected_count
        + "|" + road_vehicle_count
        + "|" + valid_bus_route_vehicle_count
        + "|" + road_access_bus_station_orders
        + "|" + connected_bus_station_orders
        + "|" + first_vehicle
        + "|" + first_order_count
        + "|" + this.BoolStr(first_stopped_in_depot)
        + "|" + first_initial_tile
        + "|" + first_final_tile
        + "|" + this.BoolStr(first_vehicle_moved)
        + "|" + station_a
        + "|" + station_b
        + "|" + station_a_tile
        + "|" + station_b_tile
        + "|" + route_depot_tile
        + "|" + route_depot_front
        + "|" + this.BoolStr(station_a_connected)
        + "|" + this.BoolStr(station_b_connected)
        + "|" + this.BoolStr(depot_connected)
        + "|" + this.BoolStr(station_a_to_station_b)
        + "|" + this.BoolStr(station_b_to_station_a)
        + "|" + this.BoolStr(depot_to_station_a)
        + "|" + this.BoolStr(station_a_to_depot)
        + "|" + this.BoolStr(station_a_visited)
        + "|" + this.BoolStr(station_b_visited)
        + "|" + this.BoolStr(event_state.station_a_first_vehicle)
        + "|" + this.BoolStr(event_state.station_b_first_vehicle)
        + "|" + this.BoolStr(event_state.vehicle_lost)
        + "|" + this.BoolStr(event_state.vehicle_crashed)
        + "|" + this.BoolStr(order_progressed)
        + "|" + last_order_index
        + "|" + last_current_dest
        + "|" + samples
        + "|" + station_a_town_road_component_tiles
        + "|" + station_b_town_road_component_tiles);
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
    ROUTE_DEPOT,
    _TOWN_ROAD_COMPONENT_SIZE,
    _MAIN_ROUTE_PRE_CONNECTIVITY,
    STATION_TILE_CONNECTED,
    _MAIN_ROUTE_POST_CONNECTIVITY,
)


def parse_trace_line(line: str) -> dict | None:
    if "BUS_ROUTE_TRACE|" not in line:
        return None
    payload = line.split("BUS_ROUTE_TRACE|", 1)[1].strip()
    parts = payload.split("|")
    if not parts:
        return None
    if parts[0] == "sample" and len(parts) == 11:
        return {
            "type": "sample",
            "sample": int(parts[1]),
            "vehicle_tile": int(parts[2]),
            "speed": int(parts[3]),
            "state": int(parts[4]),
            "in_depot": parse_bool(parts[5]),
            "stopped_in_depot": parse_bool(parts[6]),
            "current_order_index": int(parts[7]),
            "current_order_destination": int(parts[8]),
            "current_order_station": int(parts[9]),
            "location_station": int(parts[10]),
        }
    if parts[0] == "event":
        event: dict[str, object] = {
            "type": "event",
            "event": parts[1] if len(parts) > 1 else "unknown",
        }
        if len(parts) > 2:
            if parts[1] == "station_first_vehicle" and len(parts) >= 4:
                event["station_id"] = int(parts[2])
                event["vehicle_id"] = int(parts[3])
            elif parts[1] in {
                "vehicle_lost",
                "vehicle_crashed",
                "vehicle_waiting_in_depot",
            }:
                event["vehicle_id"] = int(parts[2])
            else:
                event["raw"] = parts[2:]
        return event
    return {"type": "raw", "payload": payload}


def write_route_trace(log_path: Path, trace_path: Path) -> int:
    count = 0
    with log_path.open() as source, trace_path.open("w") as dest:
        for line in source:
            entry = parse_trace_line(line)
            if entry is None:
                continue
            dest.write(json.dumps(entry, sort_keys=True) + "\n")
            count += 1
    return count


def route_trace_shows_vehicle_movement(trace_path: Path, initial_tile: int) -> bool:
    """Return true when any verifier trace sample observes the vehicle away from its initial tile."""
    if initial_tile < 0 or not trace_path.exists():
        return False
    with trace_path.open() as source:
        for line in source:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") != "sample":
                continue
            try:
                vehicle_tile = int(entry.get("vehicle_tile", initial_tile))
            except (TypeError, ValueError):
                continue
            if vehicle_tile != initial_tile:
                return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "save_path", nargs="?", default=str(ROOT / "artifacts" / "llm_bus_route.sav")
    )
    args = parser.parse_args(argv)

    save_path = Path(args.save_path)
    if not save_path.is_absolute():
        save_path = (ROOT / save_path).resolve()
    root, log_path, verify_result_path, env = create_run_root(
        prefix="bus-route",
        game_start_script="echo verifier: using saved McpBridge AI\nunpause\n",
        info_nut=INFO_NUT,
        main_nut=MAIN_NUT,
        log_filename="verify_bus_route.log",
    )
    trace_path = root / "route_trace.jsonl"
    driver = resolve_verify_driver()
    proc = launch_openttd(root, save_path, env, port=3995, driver=driver)
    try:
        line = read_until(
            proc, log_path, "BUS_ROUTE_VERIFIER|", ROUTE_VERIFY_TIMEOUT_SEC
        )
        payload = line.split("BUS_ROUTE_VERIFIER|", 1)[1].strip()
        parts = payload.split("|")
        expected_parts = 43
        if len(parts) != expected_parts:
            raise RuntimeError(
                f"Unexpected verifier payload ({len(parts)} parts, expected {expected_parts}): {payload!r}"
            )
        result = {
            "largest_town_id": int(parts[0]),
            "largest_town_name": parts[1],
            "largest_town_population": int(parts[2]),
            "bus_station_count": int(parts[3]),
            "bus_station_in_largest_town": int(parts[4]),
            "road_depot_count": int(parts[5]),
            "road_depot_with_front_road_count": int(parts[6]),
            "road_depot_connected_count": int(parts[7]),
            "road_vehicle_count": int(parts[8]),
            "valid_bus_route_vehicle_count": int(parts[9]),
            "road_access_bus_station_orders": int(parts[10]),
            "connected_bus_station_orders": int(parts[11]),
            "first_vehicle": int(parts[12]),
            "first_order_count": int(parts[13]),
            "first_stopped_in_depot": parse_bool(parts[14]),
            "first_initial_tile": int(parts[15]),
            "first_final_tile": int(parts[16]),
            "first_vehicle_moved": parse_bool(parts[17]),
            "station_a": int(parts[18]),
            "station_b": int(parts[19]),
            "station_a_tile": int(parts[20]),
            "station_b_tile": int(parts[21]),
            "depot_tile": int(parts[22]),
            "depot_front_tile": int(parts[23]),
            "station_a_connected": parse_bool(parts[24]),
            "station_b_connected": parse_bool(parts[25]),
            "depot_connected": parse_bool(parts[26]),
            "station_a_to_station_b": parse_bool(parts[27]),
            "station_b_to_station_a": parse_bool(parts[28]),
            "depot_to_station_a": parse_bool(parts[29]),
            "station_a_to_depot": parse_bool(parts[30]),
            "station_a_visited_by_vehicle": parse_bool(parts[31]),
            "station_b_visited_by_vehicle": parse_bool(parts[32]),
            "station_a_first_vehicle_event": parse_bool(parts[33]),
            "station_b_first_vehicle_event": parse_bool(parts[34]),
            "vehicle_lost_event": parse_bool(parts[35]),
            "vehicle_crashed_event": parse_bool(parts[36]),
            "order_progressed": parse_bool(parts[37]),
            "last_order_index": int(parts[38]),
            "last_current_order_destination": int(parts[39]),
            "dynamic_sample_count": int(parts[40]),
            "station_a_town_road_component_tiles": int(parts[41]),
            "station_b_town_road_component_tiles": int(parts[42]),
        }
        result["orders_target_both_stations"] = (
            result["station_a"] != -1
            and result["station_b"] != -1
            and result["station_a"] != result["station_b"]
        )
        result["both_stations_same_road_component_as_depot"] = (
            result["station_a_connected"]
            and result["station_b_connected"]
            and result["depot_connected"]
            and result["station_a_to_station_b"]
            and result["station_b_to_station_a"]
            and result["depot_to_station_a"]
            and result["station_a_to_depot"]
        )
        result["stations_on_substantial_town_road_network"] = (
            result["station_a_town_road_component_tiles"] >= 10
            and result["station_b_town_road_component_tiles"] >= 10
        )
        trace_count = write_route_trace(log_path, trace_path)
        result["route_trace_entry_count"] = trace_count
        if route_trace_shows_vehicle_movement(trace_path, result["first_initial_tile"]):
            result["first_vehicle_moved"] = True
            result["first_vehicle_movement_evidence"] = (
                "route_trace_sampled_tile_change"
            )
        else:
            result["first_vehicle_movement_evidence"] = (
                "initial_final_tile_difference_only"
            )
        emit_result(
            proc,
            save_path=save_path,
            log_path=log_path,
            result_path=verify_result_path,
            result=result,
            verifier_module=VERIFIER_MODULE,
            extra_printed_paths=(("ROUTE_TRACE", trace_path),),
            driver=driver,
        )
        ok = (
            result["bus_station_in_largest_town"] >= 2
            and result["valid_bus_route_vehicle_count"] >= 1
            and result["road_access_bus_station_orders"] >= 2
            and result["connected_bus_station_orders"] >= 2
            and result["road_depot_connected_count"] >= 1
            and result["first_order_count"] >= 2
            and not result["first_stopped_in_depot"]
            and result["first_vehicle_moved"]
            and result["orders_target_both_stations"]
            and result["both_stations_same_road_component_as_depot"]
            and result["stations_on_substantial_town_road_network"]
            and result["station_a_visited_by_vehicle"]
            and result["station_b_visited_by_vehicle"]
            and not result["vehicle_lost_event"]
            and not result["vehicle_crashed_event"]
            and result["order_progressed"]
        )
        return 0 if ok else 2
    finally:
        teardown(proc, driver=driver)


if __name__ == "__main__":
    raise SystemExit(main())
