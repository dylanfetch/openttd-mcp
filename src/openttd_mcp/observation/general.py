"""General observation for the general action surface.

This is the deliberately *candidate-free* counterpart to ``openttd_mcp.observation.map_snapshot``.
It serialises a general world state and renders a viewable map for the model,
with **no** bus-candidate features, no quality score, and no pre-ranked option
list. That deletion is the point: the bridge stops
deciding the move; the model finds its own tiles from the image, the structured
state, and the ``query_*`` primitives.

What is reused from the map-snapshot observation module:
  - ``validate_map_snapshot`` -- so the coordinate frame the model reasons over
    is trustworthy (keeps the ``tile = y*width + x`` invariant check).
  - ``render_map_snapshot_svg`` -- the SVG geometry (terrain, towns, roads, own
    stations/depots). It is called with a *minimal* world model whose candidate
    lists are empty, so the render carries no candidate information.

What is NOT imported (the bright line): ``derive_world_model``,
``_candidate_features``, ``quality_score``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openttd_mcp.observation.map_snapshot import (
    render_map_snapshot_svg,
    validate_map_snapshot,
)

# render_map_snapshot_svg only reads these keys off the world_model. Empty lists
# mean "no candidate overlays" -> a general, candidate-free render.
MINIMAL_WORLD_MODEL: dict[str, Any] = {
    "candidate_stop_features": [],
    "candidate_depot_features": [],
    "candidate_bus_routes": [],
    "road_components": [],
    "prompt_summary": {},
}


def _operating_profit(company: dict[str, Any]) -> int:
    income = int(company.get("quarterly_income_0", 0) or 0)
    expenses = int(company.get("quarterly_expenses_0", 0) or 0)
    return income - expenses


def serialize_general_state(
    observation: dict[str, Any], snapshot: dict[str, Any]
) -> dict[str, Any]:
    """Build the spec-§1.2 general structured state. No candidate fields.

    ``observation`` is the parsed ``MCP_OBSERVATION|`` JSON (it carries
    finances, date, and the passenger-engine list). ``snapshot`` is the
    assembled map snapshot (it carries the tile raster and the object tables).
    Both describe the same tick; we read each field from whichever already
    proves it in the accepted runs.
    """
    map_size = snapshot.get("map_size") or observation.get("map_size") or [64, 64]
    width = int(map_size[0])
    company = observation.get("company", {})

    finances = {
        "bank_balance": int(
            company.get("bank_balance", observation.get("bank_balance", 0)) or 0
        ),
        "loan_amount": int(company.get("loan_amount", 0) or 0),
        "max_loan_amount": int(company.get("max_loan_amount", 0) or 0),
        "quarterly_income_0": int(company.get("quarterly_income_0", 0) or 0),
        "quarterly_expenses_0": int(company.get("quarterly_expenses_0", 0) or 0),
        "operating_profit": _operating_profit(company),
        "road_vehicle_count": int(company.get("road_vehicle_count", 0) or 0),
    }

    towns: list[dict[str, Any]] = []
    for town in snapshot.get("towns", []):
        x = int(town.get("x", 0))
        y = int(town.get("y", 0))
        towns.append(
            {
                "town_id": town.get("town_id"),
                "name": town.get("name"),
                "location_tile": y * width + x,
                "x": x,
                "y": y,
                "population": town.get("population"),
            }
        )

    industries: list[dict[str, Any]] = []
    for industry in snapshot.get("industries", []):
        industries.append(
            {
                "industry_id": industry.get("industry_id"),
                "industry_type": industry.get("industry_type"),
                "industry_type_name": industry.get("industry_type_name"),
                "tile": industry.get("tile"),
                "x": industry.get("x"),
                "y": industry.get("y"),
            }
        )

    stations: list[dict[str, Any]] = []
    for station in snapshot.get("stations", []):
        stations.append(
            {
                "station_id": station.get("station_id"),
                "name": station.get("name"),
                "tile": station.get("tile"),
                "x": station.get("x"),
                "y": station.get("y"),
                "is_bus_stop": station.get("is_bus_stop"),
                "is_truck_stop": station.get("is_truck_stop"),
                "waiting_passengers": station.get("waiting_passengers"),
                "passenger_rating": station.get("passenger_rating"),
            }
        )

    depots: list[dict[str, Any]] = []
    for depot in snapshot.get("depots", []):
        depots.append(
            {
                "tile": depot.get("tile"),
                "x": depot.get("x"),
                "y": depot.get("y"),
                "front_tile": depot.get("front_tile"),
            }
        )

    vehicles: list[dict[str, Any]] = []
    for vehicle in snapshot.get("vehicles", []):
        vehicles.append(
            {
                "vehicle_id": vehicle.get("vehicle_id"),
                "name": vehicle.get("name"),
                "type": "road",
                "engine_id": vehicle.get("engine_id"),
                "tile": vehicle.get("tile"),
                "x": vehicle.get("x"),
                "y": vehicle.get("y"),
                "state": vehicle.get("state"),
                "in_depot": vehicle.get("in_depot"),
                "stopped_in_depot": vehicle.get("stopped_in_depot"),
                "order_count": vehicle.get("order_count"),
                "current_order_station": vehicle.get("current_order_station"),
                "profit_this_year": vehicle.get("profit_this_year"),
                "profit_last_year": vehicle.get("profit_last_year"),
            }
        )

    cargo_types: list[dict[str, Any]] = []
    for cargo in snapshot.get("cargoes", []):
        cargo_types.append(
            {
                "cargo_type": cargo.get("cargo_type"),
                "label": cargo.get("label"),
                "name": cargo.get("name"),
            }
        )

    passenger_engines: list[dict[str, Any]] = []
    for engine in observation.get("engine_candidates", []):
        passenger_engines.append(
            {
                "engine_id": engine.get("engine_id"),
                "name": engine.get("name"),
                "cargo_type": engine.get("cargo_type"),
                "capacity": engine.get("capacity"),
                "price": engine.get("price"),
                "max_speed": engine.get("max_speed"),
            }
        )

    return {
        "type": "general_state",
        "schema_version": 1,
        "objective": "build a single coherent, verifiable passenger bus route from the blank primitive surface",
        "observation_index": snapshot.get(
            "observation_index", observation.get("observation_index")
        ),
        "map_size": [int(map_size[0]), int(map_size[1])],
        "tile_frame": "tile_id = y * map_width + x",
        "current_date": observation.get("date", snapshot.get("date", {})),
        "company_finances": finances,
        "towns": towns,
        "industries": industries,
        "my_network": {
            "stations": stations,
            "depots": depots,
            "vehicles": vehicles,
            "routes": observation.get("routes", []),
        },
        "cargo_types": cargo_types,
        "passenger_engines": passenger_engines,
        "warnings": observation.get("warnings", {}),
    }


def render_general_svg(snapshot: dict[str, Any]) -> str:
    """Render the candidate-free map SVG (terrain, towns, roads, own assets)."""
    return render_map_snapshot_svg(snapshot, MINIMAL_WORLD_MODEL)


def render_general_png(svg: str, out_path: Path) -> bool:
    """Rasterise the SVG to PNG. Best-effort: returns True on success.

    PNG is the model's primary visual input in Slice 2. If no rasteriser is
    installed (cairosvg), we return False and the caller keeps the SVG artifact
    -- no silent failure, the controller records that PNG was unavailable.
    """
    try:
        import cairosvg  # type: ignore
    except Exception:  # noqa: BLE001 -- missing or broken cairo install, any error
        return False
    try:
        cairosvg.svg2png(bytestring=svg.encode("utf-8"), write_to=str(out_path))
        return True
    except Exception:  # noqa: BLE001 -- rasterisation is best-effort, SVG is kept
        return False


def build_general_observation(
    observation: dict[str, Any],
    snapshot: dict[str, Any],
    expected_size: tuple[int, int] = (64, 64),
) -> dict[str, Any]:
    """Validate the snapshot and assemble the full general-observation bundle.

    Returns a dict with the validated-snapshot summary, the general state, and
    the rendered SVG. PNG rasterisation is left to the caller (it needs an
    output path) via ``render_general_png``.
    """
    validation = validate_map_snapshot(snapshot, expected_size=expected_size)
    state = serialize_general_state(observation, snapshot)
    svg = render_general_svg(snapshot)
    return {
        "map_snapshot_validation": validation,
        "general_state": state,
        "map_svg": svg,
    }
