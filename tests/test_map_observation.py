from __future__ import annotations

import json
import unittest
from copy import deepcopy

import pytest

from openttd_mcp.observation.map_snapshot import (
    derive_world_model,
    prompt_preview_text,
    render_map_snapshot_svg,
    validate_map_snapshot,
)


def make_tile(tile_id: int, width: int = 10, **overrides):
    x = tile_id % width
    y = tile_id // width
    data = {
        "tile": tile_id,
        "x": x,
        "y": y,
        "terrain_type": 0,
        "slope": 0,
        "min_height": 0,
        "max_height": 0,
        "owner": -1,
        "buildable": True,
        "water": False,
        "sea": False,
        "river": False,
        "coast": False,
        "rough": False,
        "rock": False,
        "farm": False,
        "tree": False,
        "house": False,
        "station_tile": False,
        "road_tile": False,
        "road_station_tile": False,
        "road_depot_tile": False,
        "drive_through_road_station": False,
        "road_station_front_tile": -1,
        "drive_through_back_tile": -1,
        "road_depot_front_tile": -1,
        "road_connections": {"n": False, "e": False, "s": False, "w": False},
        "closest_town_id": 7,
        "in_largest_town_influence": True,
        "passenger_acceptance_radius4": 0,
        "passenger_production_radius4": 0,
        "industry_id": -1,
    }
    data.update(overrides)
    return data


def fixture_snapshot():
    tiles = [make_tile(i) for i in range(100)]
    by_id = {tile["tile"]: tile for tile in tiles}
    for tile_id in range(41, 49):
        by_id[tile_id].update(
            road_tile=True,
            road_connections={
                "n": False,
                "e": tile_id < 48,
                "s": tile_id == 44,
                "w": tile_id > 41,
            },
            passenger_acceptance_radius4=tile_id - 35,
            passenger_production_radius4=tile_id - 39,
        )
    by_id[54].update(
        road_depot_tile=True,
        road_connections={"n": True, "e": False, "s": False, "w": False},
        road_depot_front_tile=44,
    )
    for tile_id in (31, 32, 33, 42, 43, 46, 47, 52, 57):
        by_id[tile_id].update(
            house=True,
            passenger_acceptance_radius4=by_id[tile_id]["passenger_acceptance_radius4"]
            + 3,
            passenger_production_radius4=by_id[tile_id]["passenger_production_radius4"]
            + 2,
        )
    return {
        "type": "map_snapshot",
        "schema_version": 1,
        "observation_index": 0,
        "map_size": [10, 10],
        "date": {"raw": 1, "year": 1950, "month": 1, "day": 1},
        "tiles": tiles,
        "towns": [{"town_id": 7, "name": "Teston", "population": 100, "x": 4, "y": 4}],
        "industries": [
            {
                "industry_id": 10,
                "name": "Coal Mine",
                "industry_type": 1,
                "industry_type_name": "Coal Mine",
                "tile": 0,
                "x": 0,
                "y": 0,
                "produced_cargo": [
                    {
                        "cargo_type": 1,
                        "label": "COAL",
                        "name": "Coal",
                        "last_month_production": 50,
                    }
                ],
                "accepted_cargo": [],
                "built_on_water": False,
            },
            {
                "industry_id": 11,
                "name": "Power Station",
                "industry_type": 2,
                "industry_type_name": "Power Station",
                "tile": 15,
                "x": 3,
                "y": 3,
                "produced_cargo": [],
                "accepted_cargo": [
                    {
                        "cargo_type": 1,
                        "label": "COAL",
                        "name": "Coal",
                        "accept_state": 1,
                        "stockpiled": 0,
                    }
                ],
                "built_on_water": False,
            },
        ],
        "stations": [
            {
                "station_id": 501,
                "name": "Old Stop",
                "tile": 52,
                "x": 2,
                "y": 5,
                "station_type": "bus_stop",
            }
        ],
        "vehicles": [],
        "depots": [
            {"tile": 54, "x": 4, "y": 5, "front_tile": 44, "front_x": 4, "front_y": 4}
        ],
        "cargoes": [{"cargo_type": 1, "label": "COAL", "name": "Coal"}],
    }


def fixture_observation():
    return {
        "type": "observation",
        "map_size": [10, 10],
        "actions": ["build_bus_route_blueprint", "wait"],
        "stop_candidates": [
            {
                "candidate_id": "s42-f41",
                "town_id": 7,
                "town_name": "Teston",
                "tile": 42,
                "x": 2,
                "y": 4,
                "front_tile": 41,
                "front_x": 1,
                "front_y": 4,
                "back_tile": 43,
                "back_x": 3,
                "back_y": 4,
            },
            {
                "candidate_id": "s47-f46",
                "town_id": 7,
                "town_name": "Teston",
                "tile": 47,
                "x": 7,
                "y": 4,
                "front_tile": 46,
                "front_x": 6,
                "front_y": 4,
                "back_tile": 48,
                "back_x": 8,
                "back_y": 4,
            },
        ],
        "depot_candidates": [
            {
                "candidate_id": "d54-f44",
                "town_id": 7,
                "town_name": "Teston",
                "tile": 54,
                "x": 4,
                "y": 5,
                "front_tile": 44,
                "front_x": 4,
                "front_y": 4,
            },
        ],
        "engine_candidates": [
            {"engine_id": 116, "price": 5000, "capacity": 31, "name": "MPS Regal Bus"}
        ],
        "stations": [
            {
                "station_id": 501,
                "name": "Old Stop",
                "tile": 52,
                "x": 2,
                "y": 5,
                "waiting_passengers": 4,
                "passenger_rating": 60,
            }
        ],
        "routes": [],
        "vehicles": [],
    }


class MapObservationTests(unittest.TestCase):
    def test_validator_rejects_tile_count_mismatch(self):
        snapshot = fixture_snapshot()
        snapshot["tiles"] = snapshot["tiles"][:-1]
        with self.assertRaisesRegex(ValueError, "expected 100 tiles"):
            validate_map_snapshot(snapshot, expected_size=(10, 10))

    def test_road_component_derivation_follows_directional_connections(self):
        world_model = derive_world_model(fixture_snapshot(), fixture_observation())
        components = world_model["road_components"]
        self.assertEqual(len(components), 1)
        self.assertEqual(components[0]["tile_count"], 9)
        self.assertEqual(set(components[0]["tiles"]), set(range(41, 49)) | {54})

    def test_candidate_bus_routes_name_their_stops_depot_and_engine(self):
        world_model = derive_world_model(fixture_snapshot(), fixture_observation())
        routes = world_model["candidate_bus_routes"]
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]["stop_a_tile"], 42)
        self.assertEqual(routes[0]["stop_b_tile"], 47)
        self.assertEqual(routes[0]["depot_tile"], 54)
        self.assertEqual(routes[0]["cheapest_passenger_engine_id"], 116)

    def test_stop_features_include_catchment_and_validity_details(self):
        world_model = derive_world_model(fixture_snapshot(), fixture_observation())
        stop = next(
            item
            for item in world_model["candidate_stop_features"]
            if item["candidate_id"] == "s42-f41"
        )

        self.assertEqual(stop["catchment_radius_tiles"], 4)
        self.assertGreater(stop["catchment_tile_count_radius4"], 0)
        self.assertGreater(stop["covered_house_tiles_radius4"], 0)
        self.assertGreater(stop["covered_town_house_tiles_radius4"], 0)
        self.assertIs(stop["simple_drive_through_road_tile"], True)
        self.assertEqual(stop["validity"]["road_neighbour_count"], 2)
        self.assertEqual(stop["validity"]["town_road_component_tiles"], 9)
        self.assertIs(stop["validity"]["slope_flat"], True)
        self.assertIs(stop["validity"]["existing_road_tile"], True)

    def test_candidate_bus_routes_include_incremental_overlap_cost_and_quality_features(
        self,
    ):
        world_model = derive_world_model(fixture_snapshot(), fixture_observation())
        route = world_model["candidate_bus_routes"][0]

        # Nothing anywhere in the model recommends a route: the whole
        # serialized model is in scope, not just the keys of one level.
        self.assertNotIn("recommended_route", json.dumps(world_model))
        self.assertGreater(route["route_catchment_tile_count_radius4"], 0)
        self.assertGreater(route["route_covered_house_tiles_radius4"], 0)
        self.assertGreater(route["route_covered_town_house_tiles_radius4"], 0)
        self.assertGreater(route["route_passenger_acceptance_radius4_total"], 0)
        self.assertGreater(route["route_passenger_production_radius4_total"], 0)
        self.assertGreater(route["stop_catchment_overlap_tile_count_radius4"], 0)
        self.assertGreater(route["stop_catchment_overlap_ratio_radius4"], 0)
        self.assertGreater(route["existing_station_overlap_tile_count_radius4"], 0)
        self.assertGreater(route["existing_station_overlap_ratio_radius4"], 0)
        self.assertEqual(route["overlapping_existing_station_ids"], [501])
        self.assertGreater(route["incremental_catchment_tile_count_radius4"], 0)
        self.assertGreater(route["incremental_covered_house_tiles_radius4"], 0)
        self.assertGreater(route["incremental_passenger_acceptance_proxy"], 0)
        self.assertGreater(route["incremental_passenger_production_proxy"], 0)
        self.assertEqual(route["depot_total_distance_to_stops"], 7)
        self.assertEqual(route["depot_min_distance_to_stop"], 3)
        self.assertEqual(route["depot_max_distance_to_stop"], 4)
        self.assertEqual(
            route["estimated_cost_basis"]["formula"],
            "cheapest_engine_price_plus_proxy_construction_units",
        )
        self.assertEqual(route["estimated_cost_basis"]["proxy_stop_count"], 2)
        self.assertEqual(route["estimated_cost_basis"]["proxy_depot_count"], 1)
        self.assertTrue(
            route["estimated_cost_basis"]["proxy_values_are_not_exact_openttd_costs"]
        )
        self.assertIn("score", route["placement_quality_proxy"])
        self.assertTrue(
            route["placement_quality_proxy"][
                "score_is_decision_support_not_recommendation"
            ]
        )
        self.assertTrue(route["validity"]["stops_same_component"])
        self.assertTrue(route["validity"]["depot_connected_to_stops"])
        self.assertTrue(route["validity"]["stop_a_simple_drive_through_road_tile"])
        self.assertTrue(route["validity"]["stop_b_simple_drive_through_road_tile"])
        self.assertTrue(route["validity"]["minimum_stop_distance_met"])

    def test_svg_renderer_returns_svg_with_road_and_town_labels(self):
        snapshot = fixture_snapshot()
        world_model = derive_world_model(snapshot, fixture_observation())
        svg = render_map_snapshot_svg(snapshot, world_model)
        self.assertTrue(svg.lstrip().startswith("<svg"))
        self.assertIn("Teston", svg)
        self.assertIn("black lines: roads", svg)


def test_snapshot_validation_rejects_structurally_ambiguous_maps() -> None:
    """Invalid tile identity or object tables must fail before planning features are derived."""
    cases = [
        (lambda value: value.update(type="observation"), "snapshot.type"),
        (lambda value: value.update(tiles={}), "tiles must be a list"),
        (lambda value: value["tiles"].__setitem__(0, []), "not an object"),
        (lambda value: value["tiles"][0].pop("x"), "missing 'x'"),
        (lambda value: value["tiles"][1].update(tile=0), "duplicate tile id"),
        (lambda value: value["tiles"][0].update(x=10), "out of bounds"),
        (
            lambda value: value["tiles"][1].update(x=0, y=0),
            "duplicate tile coordinates",
        ),
        (lambda value: value.pop("towns"), "missing object tables"),
        (lambda value: value.update(towns={}), "towns must be a list"),
    ]
    for mutation, fragment in cases:
        snapshot = fixture_snapshot()
        mutation(snapshot)
        with pytest.raises(ValueError, match=fragment):
            validate_map_snapshot(snapshot, expected_size=(10, 10))


def test_non_row_major_tile_identity_is_reported_without_discarding_coordinates() -> (
    None
):
    """A usable coordinate map with alternate tile identifiers must remain inspectable."""
    snapshot = fixture_snapshot()
    snapshot["tiles"][0]["tile"] = 1000
    summary = validate_map_snapshot(snapshot, expected_size=(10, 10))
    assert summary["valid"] is True
    assert summary["tile_index_matches_coordinates"] is False


def test_prompt_preview_names_actions_and_transparent_route_features() -> None:
    """The preview must expose candidate facts while preserving model choice attribution."""
    observation = fixture_observation()
    model = derive_world_model(fixture_snapshot(), observation)
    preview = prompt_preview_text(observation, model)

    assert "- build_bus_route_blueprint" in preview
    assert model["candidate_bus_routes"][0]["candidate_id"] in preview
    assert "not a recommendation" in preview
    assert "does not request or execute an LLM action" in preview


def test_prompt_preview_explicitly_reports_no_candidate_routes() -> None:
    """An empty planner result must appear as empty instead of implying a hidden selection."""
    preview = prompt_preview_text(
        {"actions": ["wait"]},
        {
            "prompt_summary": {"candidate_bus_route_count": 0},
            "candidate_bus_routes": [],
        },
    )
    assert "Observation actions:\n- wait" in preview
    assert "features (first 12, not a recommendation):\n- none" in preview


def test_existing_route_components_are_derived_from_station_tiles() -> None:
    """A persisted route must identify every distinct road component touched by its stops."""
    observation = deepcopy(fixture_observation())
    observation["routes"] = [{"route_id": "r", "station_tiles": [42, 999]}]
    model = derive_world_model(fixture_snapshot(), observation)
    assert model["existing_company"]["routes"][0]["station_road_component_ids"] == [
        -1,
        0,
    ]


if __name__ == "__main__":
    unittest.main()
