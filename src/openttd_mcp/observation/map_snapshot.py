#!/usr/bin/env python3
"""Validate and render structured OpenTTD map observation artifacts.

This module deliberately contains no OpenTTD control logic. It consumes the
read-only compact observation plus a full Squirrel-emitted map snapshot, then
produces transparent planning features and deterministic visual audit artifacts.
"""

from __future__ import annotations

from collections import Counter, deque
from html import escape
from itertools import combinations
from typing import Any

Direction = tuple[int, int, str]
DIRECTIONS: dict[str, Direction] = {
    "n": (0, -1, "s"),
    "e": (1, 0, "w"),
    "s": (0, 1, "n"),
    "w": (-1, 0, "e"),
}
OBJECT_TABLE_KEYS = ("towns", "industries", "stations", "vehicles", "depots", "cargoes")


def validate_map_snapshot(
    snapshot: dict[str, Any], expected_size: tuple[int, int] = (64, 64)
) -> dict[str, Any]:
    """Validate the full map snapshot and return validation summary metadata.

    The OpenTTD tile ID layout is expected to be row-major on current 64x64
    runs, but the validator treats that as a reported invariant rather than a
    hard failure so the artifact remains useful if a future map representation
    differs while coordinates stay unique and in bounds.
    """
    if snapshot.get("type") != "map_snapshot":
        raise ValueError(
            f"snapshot.type must be 'map_snapshot', got {snapshot.get('type')!r}"
        )
    map_size = snapshot.get("map_size")
    if not isinstance(map_size, list) or map_size != [
        expected_size[0],
        expected_size[1],
    ]:
        raise ValueError(
            f"snapshot.map_size must be {list(expected_size)!r}, got {map_size!r}"
        )
    width, height = int(map_size[0]), int(map_size[1])
    tiles_value = snapshot.get("tiles")
    if not isinstance(tiles_value, list):
        raise ValueError("snapshot.tiles must be a list")
    tiles: list[Any] = tiles_value
    expected_count = width * height
    if len(tiles) != expected_count:
        raise ValueError(f"expected {expected_count} tiles, got {len(tiles)}")

    ids: set[int] = set()
    coords: set[tuple[int, int]] = set()
    tile_index_matches_coordinates = True
    for index, tile in enumerate(tiles):
        if not isinstance(tile, dict):
            raise ValueError(f"tile at index {index} is not an object")
        for key in ("tile", "x", "y"):
            if key not in tile:
                raise ValueError(f"tile at index {index} missing {key!r}")
        tile_id = int(tile["tile"])
        x = int(tile["x"])
        y = int(tile["y"])
        if tile_id in ids:
            raise ValueError(f"duplicate tile id {tile_id}")
        ids.add(tile_id)
        if not (0 <= x < width and 0 <= y < height):
            raise ValueError(f"tile {tile_id} coordinates out of bounds: {(x, y)!r}")
        coord = (x, y)
        if coord in coords:
            raise ValueError(f"duplicate tile coordinates {coord!r}")
        coords.add(coord)
        if tile_id != y * width + x:
            tile_index_matches_coordinates = False

    missing_tables = [key for key in OBJECT_TABLE_KEYS if key not in snapshot]
    if missing_tables:
        raise ValueError(f"snapshot missing object tables: {missing_tables!r}")
    for key in OBJECT_TABLE_KEYS:
        if not isinstance(snapshot[key], list):
            raise ValueError(f"snapshot.{key} must be a list")

    return {
        "valid": True,
        "map_size": [width, height],
        "tile_count": len(tiles),
        "unique_tile_count": len(ids),
        "unique_coordinate_count": len(coords),
        "tile_index_matches_coordinates": tile_index_matches_coordinates,
        "object_table_counts": {key: len(snapshot[key]) for key in OBJECT_TABLE_KEYS},
    }


def _tile_maps(
    snapshot: dict[str, Any],
) -> tuple[dict[int, dict[str, Any]], dict[tuple[int, int], int]]:
    tile_by_id: dict[int, dict[str, Any]] = {}
    tile_by_coord: dict[tuple[int, int], int] = {}
    for tile in snapshot.get("tiles", []):
        tile_id = int(tile["tile"])
        tile_by_id[tile_id] = tile
        tile_by_coord[(int(tile["x"]), int(tile["y"]))] = tile_id
    return tile_by_id, tile_by_coord


def _is_roadish(tile: dict[str, Any]) -> bool:
    return bool(
        tile.get("road_tile")
        or tile.get("road_station_tile")
        or tile.get("road_depot_tile")
    )


def _build_road_components(
    snapshot: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[int, int]]:
    width, height = snapshot["map_size"]
    tile_by_id, tile_by_coord = _tile_maps(snapshot)
    road_tiles = {tile_id for tile_id, tile in tile_by_id.items() if _is_roadish(tile)}
    visited: set[int] = set()
    component_by_tile: dict[int, int] = {}
    components: list[dict[str, Any]] = []

    for start in sorted(road_tiles):
        if start in visited:
            continue
        component_id = len(components)
        queue: deque[int] = deque([start])
        visited.add(start)
        tiles: list[int] = []
        while queue:
            tile_id = queue.popleft()
            tile = tile_by_id[tile_id]
            tiles.append(tile_id)
            component_by_tile[tile_id] = component_id
            conns = tile.get("road_connections") or {}
            x = int(tile["x"])
            y = int(tile["y"])
            for name, (dx, dy, opposite) in DIRECTIONS.items():
                if not conns.get(name):
                    continue
                nx, ny = x + dx, y + dy
                if not (0 <= nx < width and 0 <= ny < height):
                    continue
                neighbour_id = tile_by_coord.get((nx, ny))
                if neighbour_id is None or neighbour_id not in road_tiles:
                    continue
                neighbour = tile_by_id[neighbour_id]
                neighbour_conns = neighbour.get("road_connections") or {}
                if not neighbour_conns.get(opposite):
                    continue
                if neighbour_id in visited:
                    continue
                visited.add(neighbour_id)
                queue.append(neighbour_id)

        xs = [int(tile_by_id[t]["x"]) for t in tiles]
        ys = [int(tile_by_id[t]["y"]) for t in tiles]
        town_counts = Counter(tile_by_id[t].get("closest_town_id", -1) for t in tiles)
        components.append(
            {
                "component_id": component_id,
                "tile_count": len(tiles),
                "tiles": sorted(tiles),
                "bbox": {
                    "min_x": min(xs),
                    "min_y": min(ys),
                    "max_x": max(xs),
                    "max_y": max(ys),
                },
                "closest_town_id_mode": town_counts.most_common(1)[0][0]
                if town_counts
                else -1,
                "road_station_tiles": sorted(
                    t for t in tiles if tile_by_id[t].get("road_station_tile")
                ),
                "road_depot_tiles": sorted(
                    t for t in tiles if tile_by_id[t].get("road_depot_tile")
                ),
            }
        )
    return components, component_by_tile


def _manhattan(a: dict[str, Any], b: dict[str, Any]) -> int:
    return abs(int(a.get("x", 0)) - int(b.get("x", 0))) + abs(
        int(a.get("y", 0)) - int(b.get("y", 0))
    )


def _component_for_candidate(
    candidate: dict[str, Any], component_by_tile: dict[int, int]
) -> int:
    for key in ("tile", "front_tile", "back_tile"):
        value = candidate.get(key)
        if value is None:
            continue
        component = component_by_tile.get(int(value))
        if component is not None:
            return component
    return -1


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except Exception:  # noqa: BLE001
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:  # noqa: BLE001
        return default


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 3) if denominator else 0.0


def _catchment_tile_ids(
    center: dict[str, Any], tile_by_coord: dict[tuple[int, int], int], radius: int = 4
) -> set[int]:
    """Return square radius-N tile IDs around a candidate/station for proxy catchment accounting."""
    if "x" not in center or "y" not in center:
        return set()
    x = _safe_int(center.get("x"))
    y = _safe_int(center.get("y"))
    tile_ids: set[int] = set()
    for cy in range(y - radius, y + radius + 1):
        for cx in range(x - radius, x + radius + 1):
            tile_id = tile_by_coord.get((cx, cy))
            if tile_id is not None:
                tile_ids.add(tile_id)
    return tile_ids


def _count_house_tiles(
    tile_ids: set[int],
    tile_by_id: dict[int, dict[str, Any]],
    town_id: int | None = None,
) -> int:
    """Count snapshot tiles marked as houses; optionally restrict to closest_town_id."""
    count = 0
    for tile_id in tile_ids:
        tile = tile_by_id.get(tile_id, {})
        if not tile.get("house"):
            continue
        if (
            town_id is not None
            and _safe_int(tile.get("closest_town_id"), -1) != town_id
        ):
            continue
        count += 1
    return count


def _sum_tile_field(
    tile_ids: set[int], tile_by_id: dict[int, dict[str, Any]], field: str
) -> int:
    return sum(
        _safe_int(tile_by_id.get(tile_id, {}).get(field), 0) for tile_id in tile_ids
    )


def _station_catchments(
    stations: list[dict[str, Any]], tile_by_coord: dict[tuple[int, int], int]
) -> dict[int, set[int]]:
    """Map existing station_id to proxy catchment tile IDs."""
    catchments: dict[int, set[int]] = {}
    for station in stations:
        station_id = station.get("station_id", station.get("tile"))
        if station_id is None:
            continue
        catchments[_safe_int(station_id)] = _catchment_tile_ids(station, tile_by_coord)
    return catchments


def _component_sizes(component_by_tile: dict[int, int]) -> dict[int, int]:
    counts = Counter(component_by_tile.values())
    return {int(component_id): int(count) for component_id, count in counts.items()}


def _road_neighbour_count(tile: dict[str, Any]) -> int:
    return sum(1 for value in (tile.get("road_connections") or {}).values() if value)


def _simple_drive_through_road_tile(
    tile: dict[str, Any], road_neighbour_count: int
) -> bool:
    if not tile.get("road_tile"):
        return False
    conns = tile.get("road_connections") or {}
    return bool(
        road_neighbour_count == 2
        and ((conns.get("n") and conns.get("s")) or (conns.get("e") and conns.get("w")))
    )


def _occupied_by_station_or_depot(tile: dict[str, Any]) -> bool:
    return bool(
        tile.get("station_tile")
        or tile.get("road_station_tile")
        or tile.get("road_depot_tile")
    )


def _flat_tile(tile: dict[str, Any]) -> bool:
    if "slope" in tile:
        return _safe_int(tile.get("slope"), 0) == 0
    return _safe_int(tile.get("min_height"), 0) == _safe_int(tile.get("max_height"), 0)


def _enrich_with_component(
    item: dict[str, Any], component_by_tile: dict[int, int]
) -> dict[str, Any]:
    enriched = dict(item)
    tile = enriched.get("tile")
    enriched["road_component_id"] = (
        component_by_tile.get(int(tile), -1) if tile is not None else -1
    )
    return enriched


def _cargo_opportunities(industries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    acceptors_by_cargo: dict[int, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for industry in industries:
        for accepted in industry.get("accepted_cargo", []) or []:
            cargo_type = accepted.get("cargo_type")
            if cargo_type is None:
                continue
            acceptors_by_cargo.setdefault(int(cargo_type), []).append(
                (industry, accepted)
            )

    opportunities: list[dict[str, Any]] = []
    for producer in industries:
        for produced in producer.get("produced_cargo", []) or []:
            cargo_type = produced.get("cargo_type")
            if cargo_type is None:
                continue
            for consumer, accepted in acceptors_by_cargo.get(int(cargo_type), []):
                if consumer.get("industry_id") == producer.get("industry_id"):
                    continue
                opportunities.append(
                    {
                        "cargo_type": int(cargo_type),
                        "cargo_label": produced.get("label", accepted.get("label")),
                        "cargo_name": produced.get("name", accepted.get("name")),
                        "producer_industry_id": producer.get("industry_id"),
                        "producer_name": producer.get("name"),
                        "consumer_industry_id": consumer.get("industry_id"),
                        "consumer_name": consumer.get("name"),
                        "manhattan_distance": _manhattan(producer, consumer),
                        "last_month_production": produced.get(
                            "last_month_production", -1
                        ),
                        "accept_state": accepted.get("accept_state", -1),
                        "stockpiled": accepted.get("stockpiled", -1),
                    }
                )
    opportunities.sort(
        key=lambda item: (
            item["cargo_type"],
            item["manhattan_distance"],
            item["producer_industry_id"],
            item["consumer_industry_id"],
        )
    )
    return opportunities


def _candidate_features(
    observation: dict[str, Any],
    tile_by_id: dict[int, dict[str, Any]],
    tile_by_coord: dict[tuple[int, int], int],
    component_by_tile: dict[int, int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    stops = [dict(item) for item in observation.get("stop_candidates", [])]
    depots = [dict(item) for item in observation.get("depot_candidates", [])]
    engines = [dict(item) for item in observation.get("engine_candidates", [])]
    stations = [dict(item) for item in observation.get("stations", [])]
    cheapest_engine = (
        min(engines, key=lambda item: _safe_int(item.get("price"), 0))
        if engines
        else None
    )
    component_sizes = _component_sizes(component_by_tile)
    station_catchments = _station_catchments(stations, tile_by_coord)
    existing_station_catchment_union: set[int] = (
        set().union(*station_catchments.values()) if station_catchments else set()
    )

    stop_features: list[dict[str, Any]] = []
    stop_catchments: dict[str, set[int]] = {}
    for stop in stops:
        component_id = _component_for_candidate(stop, component_by_tile)
        tile = tile_by_id.get(_safe_int(stop.get("tile"), -1), {})
        catchment_tiles = _catchment_tile_ids(stop, tile_by_coord)
        road_neighbour_count = _safe_int(
            stop.get("road_neighbour_count"), _road_neighbour_count(tile)
        )
        town_id = stop.get("town_id")
        town_id_int = _safe_int(town_id, -1) if town_id is not None else None
        town_component_tiles = _safe_int(
            stop.get("town_road_component_tiles"), component_sizes.get(component_id, 0)
        )

        feature = dict(stop)
        feature["road_component_id"] = component_id
        feature["passenger_acceptance_radius4"] = tile.get(
            "passenger_acceptance_radius4", 0
        )
        feature["passenger_production_radius4"] = tile.get(
            "passenger_production_radius4", 0
        )
        feature["catchment_radius_tiles"] = 4
        feature["catchment_tile_count_radius4"] = len(catchment_tiles)
        feature["covered_house_tiles_radius4"] = _count_house_tiles(
            catchment_tiles, tile_by_id
        )
        feature["covered_town_house_tiles_radius4"] = _count_house_tiles(
            catchment_tiles, tile_by_id, town_id_int
        )
        feature["simple_drive_through_road_tile"] = _simple_drive_through_road_tile(
            tile, road_neighbour_count
        )
        feature["validity"] = {
            "road_neighbour_count": road_neighbour_count,
            "town_road_component_tiles": town_component_tiles,
            "slope_flat": _flat_tile(tile),
            "existing_road_tile": bool(tile.get("road_tile")),
        }
        stop_features.append(feature)
        stop_catchments[str(stop.get("candidate_id"))] = catchment_tiles

    depot_features: list[dict[str, Any]] = []
    for depot in depots:
        component_id = _component_for_candidate(depot, component_by_tile)
        tile = tile_by_id.get(_safe_int(depot.get("tile"), -1), {})
        front_tile = tile_by_id.get(_safe_int(depot.get("front_tile"), -1), {})
        town_component_tiles = _safe_int(
            depot.get("town_road_component_tiles"), component_sizes.get(component_id, 0)
        )
        feature = dict(depot)
        feature["road_component_id"] = component_id
        feature["buildable_flat"] = bool(tile.get("buildable", True)) and _flat_tile(
            tile
        )
        feature["depot_front_existing_road_tile"] = _is_roadish(front_tile)
        feature["depot_front_tile_open_road"] = _is_roadish(
            front_tile
        ) and not _occupied_by_station_or_depot(front_tile)
        feature["town_road_component_tiles"] = town_component_tiles
        feature["estimated_connector_road_tiles"] = _safe_int(
            depot.get("estimated_connector_road_tiles"), 1
        )
        depot_features.append(feature)

    stop_by_id = {item.get("candidate_id"): item for item in stop_features}
    routes: list[dict[str, Any]] = []
    for stop_a_raw, stop_b_raw in combinations(stops, 2):
        if stop_a_raw.get("tile") == stop_b_raw.get("tile"):
            continue
        if stop_a_raw.get("town_id") != stop_b_raw.get("town_id"):
            continue
        stop_a = stop_by_id.get(stop_a_raw.get("candidate_id"), stop_a_raw)
        stop_b = stop_by_id.get(stop_b_raw.get("candidate_id"), stop_b_raw)
        stop_a_component = _safe_int(stop_a.get("road_component_id"), -1)
        stop_b_component = _safe_int(stop_b.get("road_component_id"), -1)
        stops_same_component = (
            stop_a_component != -1 and stop_a_component == stop_b_component
        )
        if not stops_same_component:
            continue
        for depot in depot_features:
            if depot.get("town_id") != stop_a.get("town_id"):
                continue
            depot_component = _safe_int(depot.get("road_component_id"), -1)
            depot_connected = (
                depot_component != -1 and depot_component == stop_a_component
            )
            if not depot_connected:
                continue

            stop_a_catchment = stop_catchments.get(
                str(stop_a.get("candidate_id")), set()
            )
            stop_b_catchment = stop_catchments.get(
                str(stop_b.get("candidate_id")), set()
            )
            route_catchment = stop_a_catchment | stop_b_catchment
            stop_catchment_overlap = stop_a_catchment & stop_b_catchment
            overlapping_station_ids = sorted(
                station_id
                for station_id, catchment in station_catchments.items()
                if route_catchment & catchment
            )
            existing_station_overlap_tiles = (
                route_catchment & existing_station_catchment_union
            )
            incremental_catchment = route_catchment - existing_station_catchment_union

            catchment_proxy = (
                _safe_int(stop_a.get("passenger_acceptance_radius4"))
                + _safe_int(stop_a.get("passenger_production_radius4"))
                + _safe_int(stop_b.get("passenger_acceptance_radius4"))
                + _safe_int(stop_b.get("passenger_production_radius4"))
            )
            stop_distance = _manhattan(stop_a, stop_b)
            depot_distance_to_stop_a = _manhattan(depot, stop_a)
            depot_distance_to_stop_b = _manhattan(depot, stop_b)
            depot_total_distance = depot_distance_to_stop_a + depot_distance_to_stop_b
            depot_min_distance = min(depot_distance_to_stop_a, depot_distance_to_stop_b)
            depot_max_distance = max(depot_distance_to_stop_a, depot_distance_to_stop_b)
            route_acceptance_total = _sum_tile_field(
                route_catchment, tile_by_id, "passenger_acceptance_radius4"
            )
            route_production_total = _sum_tile_field(
                route_catchment, tile_by_id, "passenger_production_radius4"
            )
            incremental_acceptance = _sum_tile_field(
                incremental_catchment, tile_by_id, "passenger_acceptance_radius4"
            )
            incremental_production = _sum_tile_field(
                incremental_catchment, tile_by_id, "passenger_production_radius4"
            )
            incremental_houses = _count_house_tiles(incremental_catchment, tile_by_id)
            connector_road_tiles = _safe_int(
                depot.get("estimated_connector_road_tiles"), 1
            )
            cheapest_engine_price = (
                cheapest_engine.get("price") if cheapest_engine else None
            )
            proxy_cash_outlay = None
            if cheapest_engine_price is not None:
                proxy_cash_outlay = (
                    _safe_int(cheapest_engine_price)
                    + (2 * 2000)
                    + 2000
                    + (connector_road_tiles * 1000)
                )
            quality_score = (
                incremental_production * 4
                + incremental_houses * 3
                + stop_distance * 2
                - len(existing_station_overlap_tiles)
                - depot_total_distance
                - (_safe_float(proxy_cash_outlay) / 1000.0)
            )
            stop_a_road_neighbour_count = _safe_int(
                stop_a.get("validity", {}).get("road_neighbour_count"), 0
            )
            stop_b_road_neighbour_count = _safe_int(
                stop_b.get("validity", {}).get("road_neighbour_count"), 0
            )
            town_component_tiles = min(
                _safe_int(
                    stop_a.get("validity", {}).get("town_road_component_tiles"), 0
                ),
                _safe_int(
                    stop_b.get("validity", {}).get("town_road_component_tiles"), 0
                ),
                _safe_int(depot.get("town_road_component_tiles"), 0),
            )

            routes.append(
                {
                    "candidate_id": f"route-{stop_a.get('candidate_id')}-{stop_b.get('candidate_id')}-{depot.get('candidate_id')}",
                    "town_id": stop_a.get("town_id"),
                    "town_name": stop_a.get("town_name"),
                    "stop_a_candidate_id": stop_a.get("candidate_id"),
                    "stop_b_candidate_id": stop_b.get("candidate_id"),
                    "depot_candidate_id": depot.get("candidate_id"),
                    "stop_a_tile": stop_a.get("tile"),
                    "stop_a_front_tile": stop_a.get("front_tile"),
                    "stop_b_tile": stop_b.get("tile"),
                    "stop_b_front_tile": stop_b.get("front_tile"),
                    "depot_tile": depot.get("tile"),
                    "depot_front_tile": depot.get("front_tile"),
                    "road_component_id": stop_a_component,
                    "stops_same_component": stops_same_component,
                    "depot_connected_to_stops": depot_connected,
                    "depot_front_tile_open_road": depot.get(
                        "depot_front_tile_open_road"
                    ),
                    "stop_distance": stop_distance,
                    "depot_distance_to_stop_a": depot_distance_to_stop_a,
                    "depot_distance_to_stop_b": depot_distance_to_stop_b,
                    "depot_total_distance_to_stops": depot_total_distance,
                    "depot_min_distance_to_stop": depot_min_distance,
                    "depot_max_distance_to_stop": depot_max_distance,
                    "stop_a_passenger_acceptance_radius4": stop_a.get(
                        "passenger_acceptance_radius4", 0
                    ),
                    "stop_a_passenger_production_radius4": stop_a.get(
                        "passenger_production_radius4", 0
                    ),
                    "stop_b_passenger_acceptance_radius4": stop_b.get(
                        "passenger_acceptance_radius4", 0
                    ),
                    "stop_b_passenger_production_radius4": stop_b.get(
                        "passenger_production_radius4", 0
                    ),
                    "combined_catchment_proxy": catchment_proxy,
                    "existing_station_overlap_proxy": len(overlapping_station_ids),
                    "route_catchment_tile_count_radius4": len(route_catchment),
                    "route_covered_house_tiles_radius4": _count_house_tiles(
                        route_catchment, tile_by_id
                    ),
                    "route_covered_town_house_tiles_radius4": _count_house_tiles(
                        route_catchment,
                        tile_by_id,
                        _safe_int(stop_a.get("town_id"), -1),
                    ),
                    "route_passenger_acceptance_radius4_total": route_acceptance_total,
                    "route_passenger_production_radius4_total": route_production_total,
                    "stop_catchment_overlap_tile_count_radius4": len(
                        stop_catchment_overlap
                    ),
                    "stop_catchment_overlap_ratio_radius4": _ratio(
                        len(stop_catchment_overlap), len(route_catchment)
                    ),
                    "existing_station_overlap_tile_count_radius4": len(
                        existing_station_overlap_tiles
                    ),
                    "existing_station_overlap_ratio_radius4": _ratio(
                        len(existing_station_overlap_tiles), len(route_catchment)
                    ),
                    "overlapping_existing_station_ids": overlapping_station_ids,
                    "incremental_catchment_tile_count_radius4": len(
                        incremental_catchment
                    ),
                    "incremental_covered_house_tiles_radius4": incremental_houses,
                    "incremental_passenger_acceptance_proxy": incremental_acceptance,
                    "incremental_passenger_production_proxy": incremental_production,
                    "cheapest_passenger_engine_id": cheapest_engine.get("engine_id")
                    if cheapest_engine
                    else None,
                    "cheapest_passenger_engine_price": cheapest_engine_price,
                    "estimated_cost_basis": {
                        "formula": "cheapest_engine_price_plus_proxy_construction_units",
                        "cheapest_engine_price": cheapest_engine_price,
                        "proxy_stop_count": 2,
                        "proxy_depot_count": 1,
                        "proxy_connector_road_tiles": connector_road_tiles,
                        "proxy_cash_outlay": proxy_cash_outlay,
                        "proxy_values_are_not_exact_openttd_costs": True,
                    },
                    "placement_quality_proxy": {
                        "formula": "incremental_passenger_production_proxy*4 + incremental_covered_house_tiles_radius4*3 + stop_distance*2 - existing_station_overlap_tile_count_radius4 - depot_total_distance_to_stops - proxy_cash_outlay/1000",
                        "score": round(quality_score, 3),
                        "score_is_decision_support_not_recommendation": True,
                    },
                    "validity": {
                        "stops_same_component": stops_same_component,
                        "depot_connected_to_stops": depot_connected,
                        "depot_front_tile_open_road": bool(
                            depot.get("depot_front_tile_open_road")
                        ),
                        "stop_a_simple_drive_through_road_tile": bool(
                            stop_a.get("simple_drive_through_road_tile")
                        ),
                        "stop_b_simple_drive_through_road_tile": bool(
                            stop_b.get("simple_drive_through_road_tile")
                        ),
                        "stop_a_road_neighbour_count": stop_a_road_neighbour_count,
                        "stop_b_road_neighbour_count": stop_b_road_neighbour_count,
                        "town_road_component_tiles": town_component_tiles,
                        "minimum_stop_distance_met": stop_distance >= 4,
                    },
                }
            )

    routes.sort(
        key=lambda item: (
            item["town_id"],
            -float(item["placement_quality_proxy"]["score"]),
            int(item["existing_station_overlap_tile_count_radius4"]),
            -int(item["incremental_passenger_production_proxy"]),
            item["candidate_id"],
        )
    )
    return stop_features, depot_features, routes


def derive_world_model(
    snapshot: dict[str, Any], observation: dict[str, Any]
) -> dict[str, Any]:
    """Derive transparent planning features from the snapshot and observation."""
    validation = validate_map_snapshot(
        snapshot, tuple(snapshot.get("map_size", [64, 64]))
    )
    tile_by_id, tile_by_coord = _tile_maps(snapshot)
    road_components, component_by_tile = _build_road_components(snapshot)
    stop_features, depot_features, candidate_bus_routes = _candidate_features(
        observation, tile_by_id, tile_by_coord, component_by_tile
    )

    stations = [
        _enrich_with_component(item, component_by_tile)
        for item in observation.get("stations", [])
    ]
    vehicles = [
        _enrich_with_component(item, component_by_tile)
        for item in observation.get("vehicles", [])
    ]
    depots = [
        _enrich_with_component(item, component_by_tile)
        for item in snapshot.get("depots", [])
    ]
    routes: list[dict[str, Any]] = []
    for route in observation.get("routes", []):
        enriched = dict(route)
        station_tiles = [int(tile) for tile in route.get("station_tiles", [])]
        enriched["station_road_component_ids"] = sorted(
            {component_by_tile.get(tile, -1) for tile in station_tiles}
        )
        routes.append(enriched)

    industries = list(snapshot.get("industries", []))
    world_model = {
        "type": "world_model",
        "schema_version": 1,
        "map_size": snapshot.get("map_size"),
        "source_observation_index": snapshot.get("observation_index"),
        "towns": list(snapshot.get("towns", [])),
        "industries": industries,
        "cargo_opportunities": _cargo_opportunities(industries),
        "road_components": road_components,
        "existing_company": {
            "stations": stations,
            "routes": routes,
            "vehicles": vehicles,
            "depots": depots,
        },
        "candidate_stop_features": stop_features,
        "candidate_depot_features": depot_features,
        "candidate_bus_routes": candidate_bus_routes,
        "prompt_summary": {
            "map_size": snapshot.get("map_size"),
            "tile_count": validation["tile_count"],
            "town_count": len(snapshot.get("towns", [])),
            "industry_count": len(industries),
            "cargo_opportunity_count": len(_cargo_opportunities(industries)),
            "road_component_count": len(road_components),
            "candidate_stop_count": len(stop_features),
            "candidate_depot_count": len(depot_features),
            "candidate_bus_route_count": len(candidate_bus_routes),
            "world_model_role": "decision_support_only_not_an_auto_selected_action",
        },
    }
    return world_model


def _tile_colour(tile: dict[str, Any]) -> str:
    if tile.get("sea") or tile.get("water") or tile.get("river"):
        return "#77aee6"
    if tile.get("road_depot_tile"):
        return "#c0392b"
    if tile.get("road_station_tile") or tile.get("station_tile"):
        return "#8e44ad"
    if int(tile.get("industry_id", -1)) != -1:
        return "#b87333"
    if tile.get("house"):
        return "#d7a36b"
    if tile.get("tree"):
        return "#4f9d4f"
    if tile.get("farm"):
        return "#d9d384"
    if tile.get("rock"):
        return "#9a9a9a"
    if tile.get("rough"):
        return "#b2a66b"
    if tile.get("buildable"):
        return "#91c778"
    return "#b8c8a0"


def render_map_snapshot_svg(
    snapshot: dict[str, Any], world_model: dict[str, Any]
) -> str:
    """Render a deterministic SVG audit map from the structured snapshot."""
    width, height = snapshot["map_size"]
    tile_size = 8
    legend_width = 250
    canvas_width = width * tile_size + legend_width
    canvas_height = height * tile_size + 80
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas_width}" height="{canvas_height}" viewBox="0 0 {canvas_width} {canvas_height}">',
        '<rect width="100%" height="100%" fill="#f5f1e8"/>',
        f'<text x="8" y="18" font-family="monospace" font-size="14" fill="#222">OpenTTD map snapshot {width}x{height} date={escape(str(snapshot.get("date", {})))}</text>',
    ]

    y_offset = 28
    for tile in sorted(
        snapshot.get("tiles", []), key=lambda item: (int(item["y"]), int(item["x"]))
    ):
        x = int(tile["x"]) * tile_size
        y = y_offset + int(tile["y"]) * tile_size
        parts.append(
            f'<rect x="{x}" y="{y}" width="{tile_size}" height="{tile_size}" fill="{_tile_colour(tile)}"/>'
        )

    for tile in sorted(
        snapshot.get("tiles", []), key=lambda item: (int(item["y"]), int(item["x"]))
    ):
        if not _is_roadish(tile):
            continue
        x = int(tile["x"]) * tile_size
        y = y_offset + int(tile["y"]) * tile_size
        cx = x + tile_size / 2
        cy = y + tile_size / 2
        conns = tile.get("road_connections") or {}
        if not any(conns.values()):
            parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="1.8" fill="#333"/>')
            continue
        for name, (dx, dy, _opposite) in DIRECTIONS.items():
            if not conns.get(name):
                continue
            x2 = cx + dx * tile_size / 2
            y2 = cy + dy * tile_size / 2
            parts.append(
                f'<line x1="{cx:.1f}" y1="{cy:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="#3b3528" stroke-width="2" stroke-linecap="round"/>'
            )

    for stop in world_model.get("candidate_stop_features", []):
        x = int(stop.get("x", 0)) * tile_size + tile_size / 2
        y = y_offset + int(stop.get("y", 0)) * tile_size + tile_size / 2
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.2" fill="none" stroke="#0066ff" stroke-width="1.4"/>'
        )
    for depot in world_model.get("candidate_depot_features", []):
        x = int(depot.get("x", 0)) * tile_size + 1
        y = y_offset + int(depot.get("y", 0)) * tile_size + 1
        parts.append(
            f'<rect x="{x}" y="{y}" width="6" height="6" fill="none" stroke="#ff7f00" stroke-width="1.4"/>'
        )

    for town in sorted(
        snapshot.get("towns", []),
        key=lambda item: (
            int(item.get("y", 0)),
            int(item.get("x", 0)),
            item.get("town_id", 0),
        ),
    ):
        x = int(town.get("x", 0)) * tile_size + 3
        y = y_offset + int(town.get("y", 0)) * tile_size - 3
        label = f"{town.get('name', 'town')} ({town.get('population', '?')})"
        parts.append(
            f'<text x="{x}" y="{y}" font-family="monospace" font-size="9" fill="#111" stroke="#f5f1e8" stroke-width="2" paint-order="stroke">{escape(label)}</text>'
        )

    for industry in sorted(
        snapshot.get("industries", []),
        key=lambda item: (
            int(item.get("y", 0)),
            int(item.get("x", 0)),
            item.get("industry_id", 0),
        ),
    )[:24]:
        x = int(industry.get("x", 0)) * tile_size + tile_size / 2
        y = y_offset + int(industry.get("y", 0)) * tile_size + tile_size / 2
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="#5e3517"/>')
        parts.append(
            f'<text x="{x + 4:.1f}" y="{y + 3:.1f}" font-family="monospace" font-size="7" fill="#222">{escape(str(industry.get("name", "industry"))[:18])}</text>'
        )

    legend_x = width * tile_size + 16
    legend_y = y_offset + 10
    summary = world_model.get("prompt_summary", {})
    legend_lines = [
        "Legend / summary",
        "green: buildable land",
        "blue: water",
        "brown: industry",
        "purple: station",
        "red: depot",
        "black lines: roads",
        "blue rings: stop candidates",
        "orange boxes: depot candidates",
        f"towns: {summary.get('town_count', len(snapshot.get('towns', [])))}",
        f"industries: {summary.get('industry_count', len(snapshot.get('industries', [])))}",
        f"road components: {summary.get('road_component_count', len(world_model.get('road_components', [])))}",
        f"candidate routes: {summary.get('candidate_bus_route_count', len(world_model.get('candidate_bus_routes', [])))}",
    ]
    for idx, line in enumerate(legend_lines):
        size = 12 if idx == 0 else 10
        weight = "bold" if idx == 0 else "normal"
        parts.append(
            f'<text x="{legend_x}" y="{legend_y + idx * 16}" font-family="monospace" font-size="{size}" font-weight="{weight}" fill="#222">{escape(line)}</text>'
        )

    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def prompt_preview_text(
    observation: dict[str, Any], world_model: dict[str, Any]
) -> str:
    """Return a no-LLM prompt preview preserving final-choice attribution."""
    summary = world_model.get("prompt_summary", {})
    examples = world_model.get("candidate_bus_routes", [])[:12]
    lines = [
        "Map observation spike prompt preview (no LLM call was made).",
        "",
        "The compact observation.json remains the authoritative action basis.",
        "world_model.json provides planning context and transparent candidate features only.",
        "world_model.candidate_bus_routes is decision support; no route is auto-selected by the controller.",
        "Final action must be one of observation.actions.",
        "Final tile/engine IDs must come from observation.stop_candidates, observation.depot_candidates, observation.engine_candidates, and other compact observation IDs.",
        "",
        "World-model summary:",
    ]
    for key in (
        "map_size",
        "tile_count",
        "town_count",
        "industry_count",
        "cargo_opportunity_count",
        "road_component_count",
        "candidate_stop_count",
        "candidate_depot_count",
        "candidate_bus_route_count",
    ):
        lines.append(f"- {key}: {summary.get(key)}")
    lines.extend(["", "Observation actions:"])
    for action in observation.get("actions", []):
        lines.append(f"- {action}")
    lines.extend(
        [
            "",
            "Example transparent candidate route features (first 12, not a recommendation):",
        ]
    )
    if not examples:
        lines.append("- none")
    for item in examples:
        lines.append(
            "- {candidate_id}: town={town_id} stops=({stop_a_tile},{stop_b_tile}) depot={depot_tile} "
            "component={road_component_id} catchment_proxy={combined_catchment_proxy} stop_distance={stop_distance} "
            "overlap_proxy={existing_station_overlap_proxy} engine={cheapest_passenger_engine_id}".format(
                **item
            )
        )
    lines.extend(
        [
            "",
            "Guardrail: this preview intentionally does not request or execute an LLM action.",
            "A future real episode prompt may reference these summaries, but execution must still validate the LLM-selected typed action against observation.json.",
        ]
    )
    return "\n".join(lines) + "\n"
