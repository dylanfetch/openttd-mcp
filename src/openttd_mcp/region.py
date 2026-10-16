"""Pure get_region result compaction helpers.

The Squirrel bridge remains the source of all map facts: it emits the full
TileJson array. This module only validates opt-in model-facing presentation
arguments and mechanically projects, filters, or renders that observed tile set.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Literal, TypedDict, get_args

# One constant, many consumers (the TerraformMode pattern): the Literal alias
# annotates the served FastMCP wrapper, and the tuple derived from it feeds the
# registry JSON-schema enum and this module's validator, so the served schema,
# the recorded registry, and the runtime check cannot advertise different sets.
QueryRegionDetail = Literal["tiles", "grid"]
DETAIL_VALUES: tuple[str, ...] = get_args(QueryRegionDetail)
# One message for the two places that reject an unknown detail (this module's
# presentation validator and server.command_for_tool, which non-MCP callers
# reach directly), so the two cannot state different vocabularies.
DETAIL_ERROR = "get_region detail must be " + " or ".join(
    f"'{value}'" for value in DETAIL_VALUES
)
IDENTITY_TILE_FIELDS = ("tile", "x", "y")

# Sourced from McpBridge::TileJson and live-probed on seed 9973.
VALID_TILE_FIELDS: set[str] = {
    "tile",
    "x",
    "y",
    "terrain_type",
    "slope",
    "min_height",
    "max_height",
    "owner",
    "buildable",
    "water",
    "sea",
    "river",
    "coast",
    "rough",
    "rock",
    "farm",
    "tree",
    "house",
    "station_tile",
    "road_tile",
    "road_station_tile",
    "road_depot_tile",
    "drive_through_road_station",
    "road_station_front_tile",
    "drive_through_back_tile",
    "road_depot_front_tile",
    "road_connections",
    "road_bits",
    "closest_town_id",
    "in_largest_town_influence",
    "passenger_acceptance_radius4",
    "passenger_production_radius4",
    "passenger_acceptance",
    "passenger_production",
    "cargo_id",
    "cargo_label",
    "cargo_acceptance_radius4",
    "cargo_production_radius4",
    "cargo_acceptance",
    "cargo_production",
    "industry_id",
    "fact_errors",
}

# Fields the Squirrel bridge emits only when a cargo_id argument is supplied
# (main.nut TileJson: the cargo_json block is guarded by cargo_id != -1). Asking
# for any of these without cargo_id would validate cleanly, the bridge would emit
# nothing, and _identity_projection would silently drop them — so the model would
# read "no cargo facts" as "zero cargo everywhere". Validation rejects that below.
CARGO_TILE_FIELDS: set[str] = {
    "cargo_id",
    "cargo_label",
    "cargo_acceptance_radius4",
    "cargo_production_radius4",
    "cargo_acceptance",
    "cargo_production",
}

WHERE_TILE_FIELDS: set[str] = {
    "buildable",
    "road_tile",
    "water",
    "house",
    "station_tile",
    "road_depot_tile",
    "road_station_tile",
    "drive_through_road_station",
    "sea",
    "river",
    "coast",
    "rough",
    "rock",
    "farm",
    "tree",
    "in_largest_town_influence",
}

GRID_LEGEND: dict[str, str] = {
    "D": "road depot",
    "S": "road station or station tile",
    "r": "road",
    "~": "water",
    "I": "industry",
    "H": "house",
    "T": "tree",
    "f": "farm",
    "k": "rock",
    "u": "rough",
    ".": "buildable",
    "_": "other",
}
GRID_PRECEDENCE = (
    "road_depot_tile",
    "road_station_tile",
    "station_tile",
    "road_tile",
    "water",
    "industry_id",
    "house",
    "tree",
    "farm",
    "rock",
    "rough",
    "buildable",
)
TILE_ARITHMETIC = (
    "tile = row_start_tile + (x - x_min); consecutive x tiles have consecutive tile ids"
)


class QueryRegionOptions(TypedDict):
    detail: QueryRegionDetail
    fields: list[str] | None
    where: list[str] | None


def _string_list(value: Any, key: str) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError(f"get_region {key} must be an array of strings")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"get_region {key} must be an array of strings")
        out.append(item)
    return out


def _unknown_message(kind: str, unknown: Iterable[str], valid: Iterable[str]) -> str:
    unknown_text = ", ".join(sorted(set(unknown)))
    valid_text = ", ".join(sorted(valid))
    return f"unknown get_region {kind}: {unknown_text}; valid names: {valid_text}"


def validate_query_region_args(arguments: Mapping[str, Any]) -> QueryRegionOptions:
    """Validate Python-side presentation options before bridge dispatch."""
    detail = arguments.get("detail", "tiles")
    if detail not in DETAIL_VALUES:
        raise ValueError(DETAIL_ERROR)
    has_cargo_id = arguments.get("cargo_id") is not None
    fields = _string_list(arguments.get("fields"), "fields")
    where = _string_list(arguments.get("where"), "where")
    if detail == "grid" and fields:
        raise ValueError("get_region fields can only be used with detail='tiles'")
    if detail == "grid" and where:
        raise ValueError("get_region where can only be used with detail='tiles'")
    if detail == "grid" and has_cargo_id:
        # grid rendering discards the tile array entirely (GRID_PRECEDENCE has no
        # cargo categories), so a cargo_id here would spend the bridge's per-tile
        # cargo probes for nothing. Reject it rather than silently drop the facts.
        raise ValueError(
            "get_region cargo_id can only be used with detail='tiles'; grid mode reports no cargo facts"
        )
    if fields:
        unknown = set(fields) - VALID_TILE_FIELDS
        if unknown:
            raise ValueError(_unknown_message("fields", unknown, VALID_TILE_FIELDS))
        cargo_fields = set(fields) & CARGO_TILE_FIELDS
        if cargo_fields and not has_cargo_id:
            names = ", ".join(sorted(cargo_fields))
            raise ValueError(
                f"get_region cargo fields ({names}) require the cargo_id argument; "
                "without cargo_id the bridge emits no cargo facts and they would be silently dropped"
            )
    if where:
        unknown = set(where) - WHERE_TILE_FIELDS
        if unknown:
            raise ValueError(
                _unknown_message("where fields", unknown, WHERE_TILE_FIELDS)
            )
    return {"detail": detail, "fields": fields, "where": where}  # type: ignore[typeddict-item]


def _copy_region_shell(result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in result.items() if key != "tiles" and key != "grid"
    }


def _tiles(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_tiles = result.get("tiles")
    if not isinstance(raw_tiles, list):
        return []
    return [tile for tile in raw_tiles if isinstance(tile, dict)]


def _identity_projection(
    tile: Mapping[str, Any], fields: Iterable[str]
) -> dict[str, Any]:
    projected: dict[str, Any] = {}
    for key in IDENTITY_TILE_FIELDS:
        if key in tile:
            projected[key] = tile[key]
    for key in fields:
        if key in IDENTITY_TILE_FIELDS:
            continue
        if key in tile:
            projected[key] = tile[key]
    # Error qualifiers are sticky metadata for facts in this row. A fields
    # projection must never preserve a sentinel while hiding why it is not a
    # game fact.
    if "fact_errors" in tile:
        projected["fact_errors"] = tile["fact_errors"]
    return projected


def project_tiles(result: Mapping[str, Any], fields: Iterable[str]) -> dict[str, Any]:
    """Return ``result`` with each tile projected to identity + requested fields."""
    field_list = list(fields)
    unknown = set(field_list) - VALID_TILE_FIELDS
    if unknown:
        raise ValueError(_unknown_message("fields", unknown, VALID_TILE_FIELDS))
    projected = dict(result)
    projected["tiles"] = [
        _identity_projection(tile, field_list) for tile in _tiles(result)
    ]
    return projected


def _fact_errors_for_fields(
    tile: Mapping[str, Any], fields: Iterable[str]
) -> list[Mapping[str, Any]]:
    """Return bounded errors that qualify one of ``fields`` on this tile."""
    wanted = set(fields)
    errors = tile.get("fact_errors")
    if not isinstance(errors, list):
        return []
    return [
        error
        for error in errors
        if isinstance(error, Mapping) and error.get("field") in wanted
    ]


def _fact_error_tile(
    tile: Mapping[str, Any], fields: Iterable[str]
) -> dict[str, Any] | None:
    """Project identity plus only errors relevant to ``fields``."""
    errors = _fact_errors_for_fields(tile, fields)
    if not errors:
        return None
    return {
        **{key: tile[key] for key in IDENTITY_TILE_FIELDS if key in tile},
        "fact_errors": errors,
    }


def filter_tiles(result: Mapping[str, Any], where: Iterable[str]) -> dict[str, Any]:
    """Return ``result`` with only tiles whose named boolean fields are all true."""
    where_list = list(where)
    unknown = set(where_list) - WHERE_TILE_FIELDS
    if unknown:
        raise ValueError(_unknown_message("where fields", unknown, WHERE_TILE_FIELDS))
    filtered = dict(result)
    where_fields = set(where_list)
    uncertain = [
        entry
        for tile in _tiles(result)
        if (entry := _fact_error_tile(tile, where_fields)) is not None
    ]
    filtered["tiles"] = [
        tile
        for tile in _tiles(result)
        if all(tile.get(name) is True for name in where_list)
    ]
    filtered["where_fact_error_tiles"] = uncertain
    return filtered


def grid_cell(tile: Mapping[str, Any]) -> str:
    if tile.get("road_depot_tile") is True:
        return "D"
    if tile.get("road_station_tile") is True or tile.get("station_tile") is True:
        return "S"
    if tile.get("road_tile") is True:
        return "r"
    if tile.get("water") is True:
        return "~"
    if (
        isinstance(tile.get("industry_id"), int)
        and int(tile.get("industry_id", -1)) >= 0
    ):
        return "I"
    if tile.get("house") is True:
        return "H"
    if tile.get("tree") is True:
        return "T"
    if tile.get("farm") is True:
        return "f"
    if tile.get("rock") is True:
        return "k"
    if tile.get("rough") is True:
        return "u"
    if tile.get("buildable") is True:
        return "."
    return "_"


def grid_view(result: Mapping[str, Any]) -> dict[str, Any]:
    """Replace the verbose tile array with a compact, self-describing grid."""
    tiles = _tiles(result)
    compact = _copy_region_shell(result)
    fact_error_tiles = [
        entry
        for tile in tiles
        if (entry := _fact_error_tile(tile, GRID_PRECEDENCE)) is not None
    ]
    if not tiles:
        compact["grid"] = {
            "x_min": None,
            "x_max": None,
            "y_min": None,
            "y_max": None,
            "rows": [],
            "legend": dict(GRID_LEGEND),
            "category_precedence": list(GRID_PRECEDENCE),
            "tile_arithmetic": TILE_ARITHMETIC,
            "fact_error_tiles": fact_error_tiles,
        }
        return compact

    x_min = min(int(tile["x"]) for tile in tiles)
    x_max = max(int(tile["x"]) for tile in tiles)
    y_min = min(int(tile["y"]) for tile in tiles)
    y_max = max(int(tile["y"]) for tile in tiles)
    by_y: dict[int, list[dict[str, Any]]] = {}
    for tile in tiles:
        by_y.setdefault(int(tile["y"]), []).append(tile)
    rows: list[dict[str, Any]] = []
    for y in sorted(by_y):
        row_tiles = sorted(by_y[y], key=lambda tile: int(tile["x"]))
        rows.append(
            {
                "y": y,
                "row_start_tile": int(row_tiles[0]["tile"]),
                "cells": "".join(grid_cell(tile) for tile in row_tiles),
            }
        )
    compact["grid"] = {
        "x_min": x_min,
        "x_max": x_max,
        "y_min": y_min,
        "y_max": y_max,
        "rows": rows,
        "legend": dict(GRID_LEGEND),
        "category_precedence": list(GRID_PRECEDENCE),
        "tile_arithmetic": TILE_ARITHMETIC,
        "fact_error_tiles": fact_error_tiles,
    }
    return compact


def process_query_region_result(
    result: dict[str, Any], options: QueryRegionOptions
) -> dict[str, Any]:
    """Apply already-validated get_region presentation options.

    Default ``detail='tiles'`` without ``fields`` or ``where`` returns the exact
    dispatcher result object unchanged, preserving existing result shape and log
    comparability.
    """
    if not result.get("ok", True) or "tiles" not in result:
        return result
    detail = options["detail"]
    fields = options.get("fields")
    where = options.get("where")
    if detail == "tiles" and not fields and not where:
        return result
    processed: dict[str, Any] = result
    if detail == "grid":
        return grid_view(result)
    if where:
        processed = filter_tiles(processed, where)
    if fields:
        processed = project_tiles(processed, fields)
    return processed


def unknown_tile_fields(result: Mapping[str, Any]) -> set[str]:
    """Return live TileJson keys missing from VALID_TILE_FIELDS."""
    return {
        key for tile in _tiles(result) for key in tile if key not in VALID_TILE_FIELDS
    }
