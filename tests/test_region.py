from __future__ import annotations

import copy
import json

import pytest

from openttd_mcp.region import (
    VALID_TILE_FIELDS,
    filter_tiles,
    grid_view,
    process_query_region_result,
    project_tiles,
    unknown_tile_fields,
    validate_query_region_args,
)
from openttd_mcp.server import QUERY_REGION_RADIUS_MAX_GRID
from tests.support import REGION_FIXTURE_PATH


def load_region() -> dict:
    return json.loads(REGION_FIXTURE_PATH.read_text())


def test_default_query_region_processing_returns_result_unchanged() -> None:
    result = load_region()
    processed = process_query_region_result(result, validate_query_region_args({}))
    assert processed == result
    assert processed is result


def test_project_tiles_keeps_identity_fields_and_requested_fields() -> None:
    result = load_region()
    projected = project_tiles(result, ["road_tile", "buildable"])

    assert projected is not result
    assert len(projected["tiles"]) == len(result["tiles"])
    assert set(projected["tiles"][0]) == {"tile", "x", "y", "road_tile", "buildable"}
    assert projected["tiles"][0]["tile"] == result["tiles"][0]["tile"]


def test_project_tiles_keeps_fact_errors_without_an_explicit_field_request() -> None:
    result = load_region()
    result["tiles"][0]["tree"] = False
    result["tiles"][0]["fact_errors"] = [{"code": "tree_unavailable", "field": "tree"}]

    projected = project_tiles(result, ["tree"])

    assert projected["tiles"][0]["tree"] is False
    assert projected["tiles"][0]["fact_errors"] == [
        {"code": "tree_unavailable", "field": "tree"}
    ]


def test_passenger_acceptance_fields_are_valid_opt_in_tile_fields() -> None:
    options = validate_query_region_args(
        {"fields": ["passenger_acceptance", "passenger_production"]}
    )
    assert options["fields"] == ["passenger_acceptance", "passenger_production"]

    result = load_region()
    result["tiles"][0]["passenger_acceptance"] = 38
    result["tiles"][0]["passenger_production"] = 6
    projected = project_tiles(result, ["passenger_acceptance", "passenger_production"])

    assert projected["tiles"][0] == {
        "tile": result["tiles"][0]["tile"],
        "x": result["tiles"][0]["x"],
        "y": result["tiles"][0]["y"],
        "passenger_acceptance": 38,
        "passenger_production": 6,
    }


def test_arbitrary_cargo_fields_are_valid_opt_in_tile_fields() -> None:
    options = validate_query_region_args(
        {"cargo_id": 4, "fields": ["cargo_id", "cargo_acceptance", "cargo_production"]}
    )
    assert options["fields"] == ["cargo_id", "cargo_acceptance", "cargo_production"]

    result = load_region()
    result["tiles"][0].update(
        {
            "cargo_id": 4,
            "cargo_label": "LVST",
            "cargo_acceptance": 8,
            "cargo_production": 24,
        }
    )
    projected = project_tiles(
        result, ["cargo_id", "cargo_label", "cargo_acceptance", "cargo_production"]
    )

    assert projected["tiles"][0] == {
        "tile": result["tiles"][0]["tile"],
        "x": result["tiles"][0]["x"],
        "y": result["tiles"][0]["y"],
        "cargo_id": 4,
        "cargo_label": "LVST",
        "cargo_acceptance": 8,
        "cargo_production": 24,
    }


def test_cargo_fields_without_cargo_id_fail_loudly() -> None:
    # The bridge only emits the cargo_json block when cargo_id != -1; requesting
    # cargo fields without it used to validate cleanly and then silently drop the
    # missing keys, so the model read "no cargo facts" as "zero cargo everywhere".
    with pytest.raises(ValueError, match="cargo fields.*require the cargo_id argument"):
        validate_query_region_args({"fields": ["cargo_acceptance", "cargo_production"]})
    # Non-cargo fields remain valid without cargo_id.
    assert validate_query_region_args({"fields": ["road_tile"]})["fields"] == [
        "road_tile"
    ]


def test_cargo_id_is_rejected_in_grid_mode() -> None:
    # grid mode discards the tile array, so cargo probes would be wasted work.
    with pytest.raises(
        ValueError, match="cargo_id can only be used with detail='tiles'"
    ):
        validate_query_region_args({"detail": "grid", "cargo_id": 8})
    # cargo_id stays valid in the default tiles mode.
    assert validate_query_region_args({"cargo_id": 8}) == {
        "detail": "tiles",
        "fields": None,
        "where": None,
    }


def test_filter_tiles_uses_and_semantics_for_boolean_fields() -> None:
    result = load_region()
    filtered = filter_tiles(result, ["road_tile", "in_largest_town_influence"])

    assert 0 < len(filtered["tiles"]) < len(result["tiles"])
    assert all(
        tile["road_tile"] and tile["in_largest_town_influence"]
        for tile in filtered["tiles"]
    )
    assert filtered["summary"] == result["summary"]
    assert filtered["where_fact_error_tiles"] == []


def test_filter_tiles_reports_rows_it_could_not_classify() -> None:
    result = load_region()
    result["tiles"][0]["tree"] = False
    result["tiles"][0]["fact_errors"] = [
        {"code": "tree_unavailable", "field": "tree"},
        {"code": "water_unavailable", "field": "water"},
    ]

    filtered = filter_tiles(result, ["tree"])

    assert result["tiles"][0] not in filtered["tiles"]
    assert filtered["where_fact_error_tiles"] == [
        {
            "tile": result["tiles"][0]["tile"],
            "x": result["tiles"][0]["x"],
            "y": result["tiles"][0]["y"],
            "fact_errors": [{"code": "tree_unavailable", "field": "tree"}],
        }
    ]


def test_projection_and_filtering_compose_in_tiles_mode() -> None:
    result = load_region()
    processed = process_query_region_result(
        result,
        validate_query_region_args({"fields": ["road_tile"], "where": ["road_tile"]}),
    )

    assert processed["tiles"]
    assert all(
        set(tile) == {"tile", "x", "y", "road_tile"} for tile in processed["tiles"]
    )
    assert all(tile["road_tile"] for tile in processed["tiles"])


def test_unknown_field_names_fail_loudly_with_valid_names() -> None:
    with pytest.raises(
        ValueError, match="unknown get_region fields.*buildable.*road_tile"
    ):
        validate_query_region_args({"fields": ["definitely_not_a_tile_field"]})
    with pytest.raises(
        ValueError, match="unknown get_region where fields.*buildable.*road_tile"
    ):
        validate_query_region_args({"where": ["terrain_type"]})


def test_grid_mode_rejects_fields_and_where_instead_of_ignoring_them() -> None:
    with pytest.raises(ValueError, match="fields can only be used with detail='tiles'"):
        validate_query_region_args({"detail": "grid", "fields": ["road_tile"]})
    with pytest.raises(ValueError, match="where can only be used with detail='tiles'"):
        validate_query_region_args({"detail": "grid", "where": ["road_tile"]})


def test_grid_view_is_self_describing_and_keeps_tile_id_derivability() -> None:
    result = load_region()
    compact = grid_view(result)

    assert "tiles" not in compact
    grid = compact["grid"]
    assert grid["x_min"] == min(tile["x"] for tile in result["tiles"])
    assert grid["x_max"] == max(tile["x"] for tile in result["tiles"])
    assert grid["y_min"] == min(tile["y"] for tile in result["tiles"])
    assert grid["y_max"] == max(tile["y"] for tile in result["tiles"])
    assert grid["legend"]["r"] == "road"
    assert grid["fact_error_tiles"] == []
    assert "tile = row_start_tile + (x - x_min)" in grid["tile_arithmetic"]
    by_y = {
        tile["y"]: sorted(
            [t for t in result["tiles"] if t["y"] == tile["y"]], key=lambda t: t["x"]
        )
        for tile in result["tiles"]
    }
    assert len(grid["rows"]) == len(by_y)
    for row in grid["rows"]:
        source_row = by_y[row["y"]]
        assert row["row_start_tile"] == source_row[0]["tile"]
        assert len(row["cells"]) == len(source_row)


def test_grid_view_preserves_category_fact_errors() -> None:
    result = load_region()
    result["tiles"][0]["tree"] = False
    result["tiles"][0]["fact_errors"] = [
        {"code": "tree_unavailable", "field": "tree"},
        {
            "code": "passenger_acceptance_unavailable",
            "field": "passenger_acceptance",
        },
    ]

    grid = grid_view(result)["grid"]

    assert grid["fact_error_tiles"] == [
        {
            "tile": result["tiles"][0]["tile"],
            "x": result["tiles"][0]["x"],
            "y": result["tiles"][0]["y"],
            "fact_errors": [{"code": "tree_unavailable", "field": "tree"}],
        }
    ]


def test_grid_category_precedence_prefers_stations_over_roads() -> None:
    result = load_region()
    synthetic = copy.deepcopy(result)
    synthetic["tiles"] = [
        {
            "tile": 1,
            "x": 1,
            "y": 1,
            "road_tile": True,
            "road_station_tile": True,
            "station_tile": True,
        },
        {
            "tile": 2,
            "x": 2,
            "y": 1,
            "road_tile": True,
            "road_station_tile": False,
            "station_tile": False,
        },
        {
            "tile": 3,
            "x": 3,
            "y": 1,
            "road_depot_tile": True,
            "road_tile": True,
            "road_station_tile": False,
            "station_tile": False,
        },
    ]

    grid = grid_view(synthetic)["grid"]

    assert grid["rows"][0]["cells"] == "SrD"


def _synthetic_region(radius: int, map_size_x: int = 128) -> dict:
    """A full square region at an arbitrary radius, built from the live
    fixture's tile records so the shapes are real; only the extent is invented
    (the fixture is a radius-2 capture and the grid maximum is far wider)."""
    result = copy.deepcopy(load_region())
    source = result["tiles"]
    center_x = center_y = map_size_x // 2
    tiles = []
    for index_y, y in enumerate(range(center_y - radius, center_y + radius + 1)):
        for index_x, x in enumerate(range(center_x - radius, center_x + radius + 1)):
            tile = dict(source[(index_y * (2 * radius + 1) + index_x) % len(source)])
            tile.update({"tile": y * map_size_x + x, "x": x, "y": y})
            tiles.append(tile)
    result["tiles"] = tiles
    result["center_tile"] = center_y * map_size_x + center_x
    result["center_x"] = center_x
    result["center_y"] = center_y
    result["radius"] = radius
    result["summary"] = dict(result["summary"], tile_count=len(tiles))
    return result


def test_grid_view_is_well_formed_at_the_grid_radius_maximum() -> None:
    # 042 Unit 6 raised the grid maximum above the tiles maximum; the renderer
    # must cover the wider span row-for-row and stay a compact payload.
    radius = QUERY_REGION_RADIUS_MAX_GRID
    side = 2 * radius + 1
    result = _synthetic_region(radius)

    compact = process_query_region_result(
        result, validate_query_region_args({"detail": "grid"})
    )
    grid = compact["grid"]

    assert "tiles" not in compact
    assert len(grid["rows"]) == side
    assert all(len(row["cells"]) == side for row in grid["rows"])
    assert grid["x_max"] - grid["x_min"] == side - 1
    assert grid["y_max"] - grid["y_min"] == side - 1
    for row in grid["rows"]:
        source_row = sorted(
            (t for t in result["tiles"] if t["y"] == row["y"]), key=lambda t: t["x"]
        )
        assert row["row_start_tile"] == source_row[0]["tile"]
        assert source_row[-1]["tile"] == row["row_start_tile"] + (side - 1)
    # The measured radius-12 grid is ~2.6KB (.local/analysis/042-unit6-grid-measurements.md),
    # which is what the description's payload statement rests on.
    assert len(json.dumps(compact).encode()) < 4000


def test_valid_tile_fields_cover_the_live_fixture_inventory() -> None:
    result = load_region()
    assert unknown_tile_fields(result) == set()
    assert {
        "tile",
        "x",
        "y",
        "road_bits",
        "road_connections",
        "passenger_acceptance",
        "passenger_production",
        "cargo_id",
        "cargo_label",
        "cargo_acceptance",
        "cargo_production",
        "cargo_acceptance_radius4",
        "cargo_production_radius4",
    } <= VALID_TILE_FIELDS
