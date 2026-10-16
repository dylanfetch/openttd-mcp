"""Shared Squirrel fragments and composition for the save verifiers."""

from __future__ import annotations

import re

AI_INFO_TEMPLATE = """class McpBridge extends AIInfo {
    function GetAuthor() { return "openttd-mcp"; }
    function GetName() { return "McpBridge"; }
    function GetShortName() { return "__SHORT_NAME__"; }
    function GetDescription() { return "__DESCRIPTION__"; }
    function GetVersion() { return 1; }
    function MinVersionToLoad() { return 1; }
    function GetAPIVersion() { return "15"; }
    function GetDate() { return "__DATE__"; }
    function CreateInstance() { return "McpBridge"; }
    function UseAsRandomAI() { return false; }
}
RegisterAI(McpBridge());

class LegacyBridgeInfo extends McpBridge {
    function GetName() { return "BusRouteBridge"; }
    function GetShortName() { return "BRTE"; }
    function GetDescription() { return "Compatibility registration for saved games."; }
    function GetDate() { return "__DATE__"; }
}
RegisterAI(LegacyBridgeInfo());
"""

LOAD = """function McpBridge::Load(version, data) {}

"""

FIND_LARGEST_TOWN = """function McpBridge::FindLargestTown()
{
    local towns = AITownList();
    local best = -1;
    local best_pop = -1;
    foreach (town_id, value in towns) {
        local pop = AITown.GetPopulation(town_id);
        if (pop > best_pop) { best = town_id; best_pop = pop; }
    }
    return best;
}

"""

BOOL_STR = """function McpBridge::BoolStr(value)
{
    return value ? "true" : "false";
}

"""

CONTAINS = """function McpBridge::Contains(items, value)
{
    foreach (item in items) {
        if (item == value) return true;
    }
    return false;
}

"""

APPEND_UNIQUE = """function McpBridge::AppendUnique(items, value)
{
    if (!this.Contains(items, value)) items.append(value);
}

"""

IS_ROAD_LIKE = """function McpBridge::IsRoadLike(tile)
{
    if (!AIMap.IsValidTile(tile)) return false;
    if (AIRoad.IsRoadTile(tile)) return true;
    if (AIRoad.IsRoadStationTile(tile)) return true;
    if (AIRoad.IsRoadDepotTile(tile)) return true;
    return false;
}

"""

DIRECT_CONNECTED = """function McpBridge::DirectConnected(tile_from, tile_to)
{
    if (!AIMap.IsValidTile(tile_from) || !AIMap.IsValidTile(tile_to)) return false;
    local dx = AIMap.GetTileX(tile_from) - AIMap.GetTileX(tile_to);
    if (dx < 0) dx = -dx;
    local dy = AIMap.GetTileY(tile_from) - AIMap.GetTileY(tile_to);
    if (dy < 0) dy = -dy;
    if (dx + dy != 1) return false;
    if (!this.IsRoadLike(tile_from) || !this.IsRoadLike(tile_to)) return false;
    return AIRoad.AreRoadTilesConnected(tile_from, tile_to);
}

"""

# Source-only fragment, not a standalone Squirrel function. Callers must define
# ``x``, ``y``, ``max_x``, and ``max_y``; the fragment provides ``neighbours``.
CARDINAL_NEIGHBOURS = """        local neighbours = [];
        if (x > 0) neighbours.append(AIMap.GetTileIndex(x - 1, y));
        if (x + 1 < max_x) neighbours.append(AIMap.GetTileIndex(x + 1, y));
        if (y > 0) neighbours.append(AIMap.GetTileIndex(x, y - 1));
        if (y + 1 < max_y) neighbours.append(AIMap.GetTileIndex(x, y + 1));
"""


def compose_town_road_component_size(*, guard_invalid_town: bool) -> str:
    """Compose the town-bounded road BFS with its one verifier-specific guard."""
    invalid_town_guard = (
        "    if (town_id < 0) return 0;\n" if guard_invalid_town else ""
    )
    return (
        """function McpBridge::TownRoadComponentSize(tile, town_id, max_visits)
{
"""
        + invalid_town_guard
        + """    if (!this.IsRoadLike(tile) || !AITown.IsWithinTownInfluence(town_id, tile)) return 0;
    local queue = [tile];
    local visited = {};
    visited["" + tile] <- true;
    local count = 0;
    local max_x = AIMap.GetMapSizeX();
    local max_y = AIMap.GetMapSizeY();
    for (local i = 0; i < queue.len() && count < max_visits; i++) {
        local current = queue[i];
        count++;
        local x = AIMap.GetTileX(current);
        local y = AIMap.GetTileY(current);
"""
        + CARDINAL_NEIGHBOURS
        + """        foreach (next_tile in neighbours) {
            local key = "" + next_tile;
            if (visited.rawin(key)) continue;
            if (!AITown.IsWithinTownInfluence(town_id, next_tile)) continue;
            if (!this.DirectConnected(current, next_tile)) continue;
            visited[key] <- true;
            queue.append(next_tile);
        }
    }
    return count;
}

"""
    )


REACHABLE = (
    """function McpBridge::Reachable(tile_from, tile_to)
{
    if (!this.IsRoadLike(tile_from) || !this.IsRoadLike(tile_to)) return false;
    local queue = [tile_from];
    local visited = {};
    visited["" + tile_from] <- true;
    local max_x = AIMap.GetMapSizeX();
    local max_y = AIMap.GetMapSizeY();
    for (local i = 0; i < queue.len(); i++) {
        local tile = queue[i];
        if (tile == tile_to) return true;
        local x = AIMap.GetTileX(tile);
        local y = AIMap.GetTileY(tile);
"""
    + CARDINAL_NEIGHBOURS
    + """        foreach (next_tile in neighbours) {
            local key = "" + next_tile;
            if (visited.rawin(key)) continue;
            if (!this.DirectConnected(tile, next_tile)) continue;
            visited[key] <- true;
            queue.append(next_tile);
        }
    }
    return false;
}

"""
)

CONNECTED_STATION_TILES = """function McpBridge::ConnectedStationTiles(station_id, station_types)
{
    local connected_tiles = [];
    if (!AIStation.IsValidStation(station_id)) return connected_tiles;
    // YAPF accepts any matching stop tile of the ordered station as the destination.
    foreach (station_type in station_types) {
        local station_tiles = AITileList_StationType(station_id, station_type);
        foreach (station_tile, value in station_tiles) {
            if (this.StationTileConnected(station_tile)) connected_tiles.append(station_tile);
        }
    }
    return connected_tiles;
}

"""

REACHABLE_ANY = (
    """function McpBridge::ReachableAny(tiles_from, tiles_to)
{
    // One multi-source BFS seeded with every from-tile, succeeding on the first
    // to-tile dequeued: equivalent to any-pair Reachable at the cost of a single
    // flood instead of |from| x |to| of them. Edges stay directional, so this
    // must not be collapsed further into an undirected component check.
    local targets = {};
    foreach (tile_to in tiles_to) {
        if (this.IsRoadLike(tile_to)) targets["" + tile_to] <- true;
    }
    if (targets.len() == 0) return false;
    local queue = [];
    local visited = {};
    foreach (tile_from in tiles_from) {
        if (!this.IsRoadLike(tile_from)) continue;
        local key = "" + tile_from;
        if (visited.rawin(key)) continue;
        visited[key] <- true;
        queue.append(tile_from);
    }
    local max_x = AIMap.GetMapSizeX();
    local max_y = AIMap.GetMapSizeY();
    for (local i = 0; i < queue.len(); i++) {
        local tile = queue[i];
        if (targets.rawin("" + tile)) return true;
        local x = AIMap.GetTileX(tile);
        local y = AIMap.GetTileY(tile);
"""
    + CARDINAL_NEIGHBOURS
    + """        foreach (next_tile in neighbours) {
            local key = "" + next_tile;
            if (visited.rawin(key)) continue;
            if (!this.DirectConnected(tile, next_tile)) continue;
            visited[key] <- true;
            queue.append(next_tile);
        }
    }
    return false;
}

"""
)

STATION_TILE_CONNECTED = """function McpBridge::StationTileConnected(station_tile)
{
    if (!AIRoad.IsRoadStationTile(station_tile)) return false;
    local front = AIRoad.GetRoadStationFrontTile(station_tile);
    local front_ok = AIMap.IsValidTile(front) && this.IsRoadLike(front)
        && this.DirectConnected(front, station_tile)
        && this.DirectConnected(station_tile, front);
    if (!AIRoad.IsDriveThroughRoadStationTile(station_tile)) return front_ok;
    if (front_ok) return true;
    local back = AIRoad.GetDriveThroughBackTile(station_tile);
    return AIMap.IsValidTile(back) && this.IsRoadLike(back)
        && this.DirectConnected(back, station_tile)
        && this.DirectConnected(station_tile, back);
}

"""

ROUTE_DEPOT = """function McpBridge::RouteDepot(station_a_tiles)
{
    // Return [depot_tile, depot_front, connected, depot_to_a, a_to_depot] for the best
    // depot serving this route: a connected road depot mutually reachable to station A.
    local best = [-1, -1, false, false, false];
    local depots = AIDepotList(AITile.TRANSPORT_ROAD);
    foreach (depot_tile, value in depots) {
        local front = AIRoad.GetRoadDepotFrontTile(depot_tile);
        local connected = AIMap.IsValidTile(front)
            && this.DirectConnected(depot_tile, front)
            && this.DirectConnected(front, depot_tile);
        if (best[0] == -1) best = [depot_tile, front, connected, false, false];
        if (!connected) continue;
        foreach (station_a_tile in station_a_tiles) {
            if (this.Reachable(depot_tile, station_a_tile)
                    && this.Reachable(station_a_tile, depot_tile)) {
                return [depot_tile, front, true, true, true];
            }
        }
    }
    return best;
}

"""

DRAIN_EVENTS = """function McpBridge::DrainEvents(routes, states)
{
    while (AIEventController.IsEventWaiting()) {
        local event = AIEventController.GetNextEvent();
        local event_type = event.GetEventType();
        if (event_type == AIEvent.ET_STATION_FIRST_VEHICLE) {
            local station_event = AIEventStationFirstVehicle.Convert(event);
            local ev_vehicle = station_event.GetVehicleID();
            local ev_station = station_event.GetStationID();
            foreach (route in routes) {
                if (route.vid != ev_vehicle) continue;
                if (ev_station == route.station_a) states[route.key].a_first = true;
                if (ev_station == route.station_b) states[route.key].b_first = true;
            }
        } else if (event_type == AIEvent.ET_VEHICLE_LOST) {
            local lost_event = AIEventVehicleLost.Convert(event);
            local lost_vehicle = lost_event.GetVehicleID();
            foreach (route in routes) {
                if (route.vid == lost_vehicle) states[route.key].lost = true;
            }
        } else if (event_type == AIEvent.ET_VEHICLE_CRASHED) {
            local crash_event = AIEventVehicleCrashed.Convert(event);
            local crashed_vehicle = crash_event.GetVehicleID();
            foreach (route in routes) {
                if (route.vid == crashed_vehicle) states[route.key].crashed = true;
            }
        }
    }
}

"""


def compose_ai_class(*fragments: str) -> str:
    """Compose a verifier's main.nut from exact source fragments.

    The class's forward-declaration block is derived from the fragments' own
    ``function McpBridge::Name(args)`` definitions, so a signature change
    in any fragment cannot drift out of sync with a hand-maintained header.
    """
    declarations = []
    for fragment in fragments:
        for name, params in re.findall(
            r"function McpBridge::(\w+)\(([^)]*)\)", fragment
        ):
            declarations.append(f"    function {name}({params});")
    header = (
        "class McpBridge extends AIController {\n" + "\n".join(declarations) + "\n}\n\n"
    )
    return header + "".join(fragments)


def compose_info_nut(*, short_name: str, description: str, date: str) -> str:
    """Fill the common AIInfo source template without changing its whitespace."""
    replacements = {
        "__SHORT_NAME__": short_name,
        "__DESCRIPTION__": description,
        "__DATE__": date,
    }
    result = AI_INFO_TEMPLATE
    for marker, value in replacements.items():
        result = result.replace(marker, value)
    return result
