"""Unit coverage for ``openttd_mcp.observation.general`` (045-test-suite).

The general observation is the candidate-free counterpart to the map-snapshot
observation: pure transformation (structured state) plus a render that must
carry no candidate overlays. Every test here is a plain data-in/data-out check
against the shared fixtures in ``tests.test_map_observation`` -- no process, no
runtime, no IO beyond a tmp dir for the PNG best-effort path.
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

from openttd_mcp.observation.general import (
    MINIMAL_WORLD_MODEL,
    build_general_observation,
    render_general_png,
    render_general_svg,
    serialize_general_state,
)
from openttd_mcp.observation.map_snapshot import (
    derive_world_model,
    render_map_snapshot_svg,
)
from tests.support import TempDirTestCase
from tests.test_map_observation import fixture_observation, fixture_snapshot

# Candidate overlay strokes emitted by render_map_snapshot_svg for
# candidate_stop_features / candidate_depot_features.
STOP_CANDIDATE_STROKE = 'stroke="#0066ff"'
DEPOT_CANDIDATE_STROKE = 'stroke="#ff7f00"'


def rich_observation() -> dict:
    """The bridge observation with the fields the general state reads that the
    shared map fixture does not carry (finances, date, warnings, routes)."""
    observation = fixture_observation()
    observation.update(
        observation_index=3,
        date={"raw": 1, "year": 1950, "month": 1, "day": 1},
        company={
            "bank_balance": 250000,
            "loan_amount": 100000,
            "max_loan_amount": 300000,
            "quarterly_income_0": 9000,
            "quarterly_expenses_0": 3500,
            "road_vehicle_count": 2,
        },
        routes=[{"route_index": 0, "vehicle_id": 11}],
        warnings={"lost_vehicles": []},
    )
    observation["engine_candidates"] = [
        {
            "engine_id": 116,
            "name": "MPS Regal Bus",
            "cargo_type": 0,
            "capacity": 31,
            "price": 5000,
            "max_speed": 56,
        }
    ]
    return observation


def snapshot_with_vehicle() -> dict:
    snapshot = fixture_snapshot()
    snapshot["industries"][0].update(tile=61, x=1, y=6)
    snapshot["vehicles"] = [
        {
            "vehicle_id": 11,
            "name": "Bus 1",
            "engine_id": 116,
            "tile": 42,
            "x": 2,
            "y": 4,
            "state": "running",
            "in_depot": False,
            "stopped_in_depot": False,
            "order_count": 2,
            "current_order_station": 501,
            "profit_this_year": 1234,
            "profit_last_year": 0,
        }
    ]
    snapshot["stations"][0].update(
        is_bus_stop=True, is_truck_stop=False, waiting_passengers=4, passenger_rating=60
    )
    return snapshot


class SerializeGeneralStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = serialize_general_state(
            rich_observation(), snapshot_with_vehicle()
        )

    def test_envelope_pins_the_tile_frame_and_map_size(self) -> None:
        self.assertEqual(self.state["type"], "general_state")
        self.assertEqual(self.state["schema_version"], 1)
        self.assertEqual(self.state["map_size"], [10, 10])
        self.assertEqual(self.state["tile_frame"], "tile_id = y * map_width + x")
        self.assertEqual(
            self.state["observation_index"], 0
        )  # snapshot wins over observation
        self.assertEqual(self.state["current_date"]["year"], 1950)

    def test_no_candidate_fields_survive_into_the_general_state(self) -> None:
        """The deletion *is* the experiment: no ranked options, no quality."""
        for banned in (
            "stop_candidates",
            "depot_candidates",
            "candidate_bus_routes",
            "quality_score",
            "recommended_action",
        ):
            self.assertNotIn(banned, self.state)

    def test_finances_derive_operating_profit_from_the_quarterly_pair(self) -> None:
        finances = self.state["company_finances"]
        self.assertEqual(finances["bank_balance"], 250000)
        self.assertEqual(finances["loan_amount"], 100000)
        self.assertEqual(finances["max_loan_amount"], 300000)
        self.assertEqual(finances["operating_profit"], 9000 - 3500)
        self.assertEqual(finances["road_vehicle_count"], 2)

    def test_towns_carry_a_derived_row_major_location_tile(self) -> None:
        town = self.state["towns"][0]
        self.assertEqual(town["town_id"], 7)
        self.assertEqual(town["name"], "Teston")
        self.assertEqual((town["x"], town["y"]), (4, 4))
        self.assertEqual(town["location_tile"], 4 * 10 + 4)
        self.assertEqual(town["population"], 100)

    def test_industries_and_cargo_types_are_projected_without_cargo_detail(
        self,
    ) -> None:
        industries = self.state["industries"]
        self.assertEqual(
            industries[0],
            {
                "industry_id": 10,
                "industry_type": 1,
                "industry_type_name": "Coal Mine",
                "tile": 61,
                "x": 1,
                "y": 6,
            },
        )
        self.assertEqual(
            self.state["cargo_types"],
            [{"cargo_type": 1, "label": "COAL", "name": "Coal"}],
        )

    def test_my_network_projects_stations_depots_vehicles_and_bridge_routes(
        self,
    ) -> None:
        network = self.state["my_network"]
        self.assertEqual(
            network["stations"][0],
            {
                "station_id": 501,
                "name": "Old Stop",
                "tile": 52,
                "x": 2,
                "y": 5,
                "is_bus_stop": True,
                "is_truck_stop": False,
                "waiting_passengers": 4,
                "passenger_rating": 60,
            },
        )
        self.assertEqual(
            network["depots"], [{"tile": 54, "x": 4, "y": 5, "front_tile": 44}]
        )
        self.assertEqual(
            network["vehicles"][0],
            {
                "vehicle_id": 11,
                "name": "Bus 1",
                "type": "road",
                "engine_id": 116,
                "tile": 42,
                "x": 2,
                "y": 4,
                "state": "running",
                "in_depot": False,
                "stopped_in_depot": False,
                "order_count": 2,
                "current_order_station": 501,
                "profit_this_year": 1234,
                "profit_last_year": 0,
            },
        )
        # routes come from the bridge observation, verbatim
        self.assertEqual(network["routes"], [{"route_index": 0, "vehicle_id": 11}])

    def test_passenger_engines_come_from_the_observation_engine_candidates(
        self,
    ) -> None:
        self.assertEqual(
            self.state["passenger_engines"],
            [
                {
                    "engine_id": 116,
                    "name": "MPS Regal Bus",
                    "cargo_type": 0,
                    "capacity": 31,
                    "price": 5000,
                    "max_speed": 56,
                }
            ],
        )

    def test_warnings_are_passed_through_from_the_observation(self) -> None:
        self.assertEqual(self.state["warnings"], {"lost_vehicles": []})


class SerializeGeneralStateFallbackTests(unittest.TestCase):
    def test_empty_inputs_fall_back_to_the_64x64_frame_and_zeroed_finances(
        self,
    ) -> None:
        state = serialize_general_state({}, {})
        self.assertEqual(state["map_size"], [64, 64])
        self.assertEqual(state["company_finances"]["bank_balance"], 0)
        self.assertEqual(state["company_finances"]["operating_profit"], 0)
        self.assertEqual(state["towns"], [])
        self.assertEqual(state["my_network"]["vehicles"], [])
        self.assertEqual(state["current_date"], {})

    def test_map_size_falls_back_to_the_observation_when_the_snapshot_omits_it(
        self,
    ) -> None:
        state = serialize_general_state(
            {"map_size": [32, 16]}, {"towns": [{"town_id": 1, "x": 3, "y": 2}]}
        )
        self.assertEqual(state["map_size"], [32, 16])
        self.assertEqual(state["towns"][0]["location_tile"], 2 * 32 + 3)

    def test_null_finance_fields_are_read_as_zero_not_crashed_on(self) -> None:
        state = serialize_general_state(
            {
                "company": {
                    "bank_balance": None,
                    "quarterly_income_0": None,
                    "loan_amount": None,
                }
            },
            {},
        )
        self.assertEqual(state["company_finances"]["bank_balance"], 0)
        self.assertEqual(state["company_finances"]["operating_profit"], 0)

    def test_bank_balance_falls_back_to_the_top_level_observation_field(self) -> None:
        state = serialize_general_state({"bank_balance": 4242}, {})
        self.assertEqual(state["company_finances"]["bank_balance"], 4242)

    def test_observation_index_falls_back_to_the_observation(self) -> None:
        state = serialize_general_state({"observation_index": 9}, {})
        self.assertEqual(state["observation_index"], 9)


class RenderGeneralSvgTests(unittest.TestCase):
    def test_render_is_candidate_free_where_the_map_snapshot_render_is_not(
        self,
    ) -> None:
        snapshot = fixture_snapshot()
        general = render_general_svg(snapshot)
        self.assertTrue(general.startswith("<svg"))
        self.assertIn("OpenTTD map snapshot 10x10", general)
        self.assertNotIn(STOP_CANDIDATE_STROKE, general)
        self.assertNotIn(DEPOT_CANDIDATE_STROKE, general)

        # Control: the same geometry rendered with the candidate world model
        # does carry the overlays, so the absence above is the deletion and
        # not an artifact of the fixture.
        candidate_svg = render_map_snapshot_svg(
            snapshot, derive_world_model(snapshot, fixture_observation())
        )
        self.assertIn(STOP_CANDIDATE_STROKE, candidate_svg)

    def test_minimal_world_model_stays_empty_after_a_render(self) -> None:
        render_general_svg(fixture_snapshot())
        self.assertEqual(MINIMAL_WORLD_MODEL["candidate_stop_features"], [])
        self.assertEqual(MINIMAL_WORLD_MODEL["candidate_depot_features"], [])
        self.assertEqual(MINIMAL_WORLD_MODEL["candidate_bus_routes"], [])


class RenderGeneralPngTests(TempDirTestCase):
    """PNG is best-effort: a missing or broken rasteriser reports False so the
    controller records honestly that PNG was unavailable, keeping the SVG."""

    def setUp(self) -> None:
        super().setUp()
        self.out = self.tmp_path / "map.png"
        self.saved = sys.modules.get("cairosvg")

        def restore() -> None:
            if self.saved is None:
                sys.modules.pop("cairosvg", None)
            else:
                sys.modules["cairosvg"] = self.saved

        self.addCleanup(restore)

    def _install(self, svg2png) -> None:
        module = types.ModuleType("cairosvg")
        module.svg2png = svg2png  # type: ignore[attr-defined]
        sys.modules["cairosvg"] = module

    def test_missing_rasteriser_returns_false_without_writing(self) -> None:
        sys.modules["cairosvg"] = None  # import raises ImportError for a None entry
        self.assertFalse(render_general_png("<svg/>", self.out))
        self.assertFalse(self.out.exists())

    def test_successful_rasterisation_returns_true(self) -> None:
        calls: list[dict] = []

        def svg2png(*, bytestring: bytes, write_to: str) -> None:
            calls.append({"bytestring": bytestring, "write_to": write_to})
            Path(write_to).write_bytes(b"PNG")

        self._install(svg2png)
        self.assertTrue(render_general_png("<svg/>", self.out))
        self.assertEqual(calls[0]["bytestring"], b"<svg/>")
        self.assertEqual(calls[0]["write_to"], str(self.out))
        self.assertEqual(self.out.read_bytes(), b"PNG")

    def test_broken_rasteriser_returns_false_instead_of_raising(self) -> None:
        def svg2png(**_kwargs) -> None:
            raise RuntimeError("no cairo library")

        self._install(svg2png)
        self.assertFalse(render_general_png("<svg/>", self.out))


class BuildGeneralObservationTests(unittest.TestCase):
    def test_bundle_carries_validation_state_and_svg(self) -> None:
        bundle = build_general_observation(
            rich_observation(), snapshot_with_vehicle(), expected_size=(10, 10)
        )
        self.assertEqual(
            sorted(bundle), ["general_state", "map_snapshot_validation", "map_svg"]
        )
        validation = bundle["map_snapshot_validation"]
        self.assertTrue(validation["valid"])
        self.assertEqual(validation["tile_count"], 100)
        self.assertTrue(validation["tile_index_matches_coordinates"])
        self.assertEqual(bundle["general_state"]["type"], "general_state")
        self.assertNotIn(STOP_CANDIDATE_STROKE, bundle["map_svg"])

    def test_a_snapshot_that_fails_validation_aborts_the_bundle(self) -> None:
        """Validation runs first: an untrustworthy coordinate frame must never
        reach the model as a rendered map."""
        snapshot = fixture_snapshot()
        snapshot["tiles"] = snapshot["tiles"][:-1]
        with self.assertRaisesRegex(ValueError, "expected 100 tiles"):
            build_general_observation(
                rich_observation(), snapshot, expected_size=(10, 10)
            )

    def test_default_expected_size_rejects_a_mismatched_map(self) -> None:
        with self.assertRaisesRegex(ValueError, "map_size"):
            build_general_observation(rich_observation(), fixture_snapshot())


if __name__ == "__main__":
    unittest.main()
