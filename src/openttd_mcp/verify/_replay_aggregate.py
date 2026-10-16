"""Pure aggregation for the replay-telemetry instrument."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

INSTRUMENT_MODULE = "openttd_mcp.verify.replay_telemetry"
DEFAULT_ARRIVAL_DEBOUNCE_TICKS = 5
VS_AT_STATION = 3
VS_INVALID = 255
NO_STATION_SENTINEL = 65535


def _unique_stations(order_station_sequence: Sequence[int]) -> list[int]:
    stations: list[int] = []
    for station_id in order_station_sequence:
        if station_id in (-1, NO_STATION_SENTINEL) or station_id in stations:
            continue
        stations.append(station_id)
    return stations


def _route_metadata(order_station_sequence: Sequence[int]) -> tuple[list[int], bool]:
    stations = _unique_stations(order_station_sequence)
    if len(stations) == 2:
        return sorted(stations), False
    if len(stations) > 2:
        rotations = [
            stations[index:] + stations[:index] for index in range(len(stations))
        ]
        return min(rotations), True
    return stations, False


def derive_route_key(order_station_sequence: Sequence[int]) -> str:
    """Derive a direction-aware, cyclically normalized station route key."""
    stations, ordered = _route_metadata(order_station_sequence)
    if len(stations) < 2:
        return "none"
    prefix = "ordered" if ordered else "unordered"
    return prefix + ":" + ",".join(map(str, stations))


def classify_utilization(vutil: dict) -> dict:
    """Classify mutually exclusive depot, moving, and stopped sample buckets."""
    samples = int(vutil["samples"])
    in_depot = int(vutil["n_in_depot"]) + int(vutil["n_speed_positive_in_depot"])
    moving = int(vutil["n_speed_positive"]) - int(vutil["n_speed_positive_in_depot"])
    stopped = samples - in_depot - moving
    divisor = samples if samples else 1
    raw_names = (
        "running",
        "stopped",
        "in_depot",
        "at_station",
        "broken",
        "crashed",
        "invalid",
        "speed_positive",
        "speed_positive_in_depot",
    )
    raw_counts = {name: int(vutil[f"n_{name}"]) for name in raw_names}
    return {
        "samples": samples,
        "in_depot": in_depot,
        "moving": moving,
        "stopped": stopped,
        "share_in_depot": in_depot / divisor if samples else 0.0,
        "share_moving": moving / divisor if samples else 0.0,
        "share_stopped": stopped / divisor if samples else 0.0,
        "raw_state_counts": raw_counts,
    }


def derive_trips(
    vstate_records: Sequence[dict], debounce_ticks: int
) -> dict[int, dict]:
    """Derive arrivals, departures, legs, and order cycles from state edges."""
    grouped: dict[int, list[dict]] = defaultdict(list)
    for record in vstate_records:
        grouped[int(record["vehicle_id"])].append(record)
    result: dict[int, dict] = {}
    for vehicle_id, rows in grouped.items():
        rows.sort(key=lambda row: int(row["tick"]))
        arrivals: list[dict] = []
        departures_by_visit: dict[int, dict] = {}
        legs: list[tuple[int, int]] = []
        inside_station = False
        current_visit: int | None = None
        last_exit_tick: int | None = None
        previous_arrival_station: int | None = None
        reference_order: int | None = None
        previous_order: int | None = None
        left_reference = False
        completed_cycles = 0
        for row_index, row in enumerate(rows):
            order_index = int(row["order_index"])
            state = int(row["state"])
            previous_state = int(row["prev_state"])
            tick = int(row["tick"])
            if (
                state == VS_AT_STATION
                and previous_state != VS_AT_STATION
                and not inside_station
            ):
                is_new = (
                    last_exit_tick is None or tick - last_exit_tick >= debounce_ticks
                )
                if is_new:
                    station_id = int(row["station_id"])
                    arrival = {
                        "tick": tick,
                        "date": int(row["date"]),
                        "station_id": station_id,
                        "order_index": order_index,
                        "load_by_cargo": dict(row["load_by_cargo"]),
                    }
                    arrivals.append(arrival)
                    current_visit = len(arrivals) - 1
                    if reference_order is None and order_index >= 0:
                        reference_order = order_index
                    if (
                        previous_arrival_station is not None
                        and station_id >= 0
                        and previous_arrival_station != station_id
                    ):
                        legs.append((previous_arrival_station, station_id))
                    previous_arrival_station = station_id if station_id >= 0 else None
                else:
                    current_visit = len(arrivals) - 1 if arrivals else None
                inside_station = True
            elif (
                previous_state == VS_AT_STATION
                and state != VS_AT_STATION
                and inside_station
            ):
                if current_visit is not None and state != VS_INVALID:
                    departures_by_visit[current_visit] = {
                        "tick": tick,
                        "date": int(row["date"]),
                        "station_id": int(arrivals[current_visit]["station_id"]),
                        "order_index": order_index,
                        "load_by_cargo": dict(row["load_by_cargo"]),
                    }
                last_exit_tick = tick
                inside_station = False
            elif (
                row_index == 0
                and previous_state == VS_AT_STATION
                and state != VS_AT_STATION
            ):
                last_exit_tick = tick
            if reference_order is not None and order_index >= 0:
                if order_index != reference_order:
                    left_reference = True
                elif left_reference and previous_order != reference_order:
                    completed_cycles += 1
                    left_reference = False
                previous_order = order_index
        result[vehicle_id] = {
            "arrivals": arrivals,
            "departures": [
                departures_by_visit[index] for index in sorted(departures_by_visit)
            ],
            "legs": legs,
            "completed_cycle_count": completed_cycles,
        }
    return result


def summarize_station_series(
    snapshots: Sequence[dict],
) -> tuple[list[dict], dict]:
    """Sort station snapshots and compute their factual summary statistics."""
    series_fields = (
        "tick",
        "date",
        "vehicles_near",
        "vehicles_near_stopped",
        "waiting_total",
        "waiting_by_cargo",
    )
    series = [
        {field: snapshot[field] for field in series_fields} for snapshot in snapshots
    ]
    series.sort(key=lambda snapshot: int(snapshot["tick"]))
    if not series:
        return [], {
            "sample_count": 0,
            "mean_vehicles_near": None,
            "max_vehicles_near": None,
            "mean_vehicles_near_stopped": None,
            "max_vehicles_near_stopped": None,
            "waiting_first": None,
            "waiting_last": None,
            "waiting_min": None,
            "waiting_max": None,
        }
    near = [int(snapshot["vehicles_near"]) for snapshot in series]
    stopped = [int(snapshot["vehicles_near_stopped"]) for snapshot in series]
    waiting = [int(snapshot["waiting_total"]) for snapshot in series]
    return series, {
        "sample_count": len(series),
        "mean_vehicles_near": sum(near) / len(near),
        "max_vehicles_near": max(near),
        "mean_vehicles_near_stopped": sum(stopped) / len(stopped),
        "max_vehicles_near_stopped": max(stopped),
        "waiting_first": waiting[0],
        "waiting_last": waiting[-1],
        "waiting_min": min(waiting),
        "waiting_max": max(waiting),
    }


def _load_factor(sample: dict, capacity_by_cargo: dict[int, int]) -> float | None:
    capacity = sum(capacity_by_cargo.values())
    if capacity <= 0:
        return None
    loads = sample["load_by_cargo"]
    if not isinstance(loads, dict):
        raise TypeError("load_by_cargo must be a dictionary")
    load = sum(int(loads.get(cargo_id, 0)) for cargo_id in capacity_by_cargo)
    return load / capacity


def _factor_summary(values: Sequence[float]) -> dict:
    if not values:
        return {"n": 0, "mean": None, "min": None, "max": None}
    return {
        "n": len(values),
        "mean": sum(values) / len(values),
        "min": min(values),
        "max": max(values),
    }


def _only_record(records: dict[str, list[dict]], record_type: str) -> dict:
    matches = records.get(record_type, [])
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one {record_type} record, got {len(matches)}"
        )
    return matches[0]


def _mean_ticks_per_iteration(
    station_samples: Sequence[dict], station_interval: int
) -> float | None:
    ticks = sorted({int(sample["tick"]) for sample in station_samples})
    if len(ticks) < 2:
        return None
    expected_iterations = (len(ticks) - 1) * station_interval
    return (ticks[-1] - ticks[0]) / expected_iterations


def build_report(
    records: dict[str, list[dict]],
    *,
    save_path: Path,
    driver: str,
    arrival_debounce_ticks: int = DEFAULT_ARRIVAL_DEBOUNCE_TICKS,
) -> dict:
    """Aggregate parsed replay records into the schema-version 1 report."""
    meta = _only_record(records, "meta")
    done = _only_record(records, "done")
    trips = derive_trips(records.get("vstate", []), arrival_debounce_ticks)
    station_samples: dict[int, list[dict]] = defaultdict(list)
    for sample in records.get("station_sample", []):
        station_samples[int(sample["station_id"])].append(sample)

    station_arrivals: dict[int, int] = defaultdict(int)
    for trip in trips.values():
        for arrival in trip["arrivals"]:
            station_id = int(arrival["station_id"])
            if station_id >= 0:
                station_arrivals[station_id] += 1

    stations: list[dict] = []
    for static in sorted(
        records.get("station", []), key=lambda row: int(row["station_id"])
    ):
        station_id = int(static["station_id"])
        series, summary = summarize_station_series(station_samples[station_id])
        stations.append(
            {
                "station_id": station_id,
                "name": static["name"],
                "station_types": [
                    station_type
                    for station_type, present in (
                        ("truck", bool(static["has_truck_stop"])),
                        ("bus", bool(static["has_bus_stop"])),
                    )
                    if present
                ],
                "stop_tiles": static["stop_tiles"],
                "stop_tile_count": static["stop_tile_count"],
                "series": series,
                "summary": summary,
                "arrivals_observed": station_arrivals[station_id],
            }
        )

    vutil_by_vehicle = {int(row["vehicle_id"]): row for row in records.get("vutil", [])}
    vehicle_static = {int(row["vehicle_id"]): row for row in records.get("vehicle", [])}
    vstate_counts: dict[int, int] = defaultdict(int)
    for row in records.get("vstate", []):
        vstate_counts[int(row["vehicle_id"])] += 1
    vehicles: list[dict] = []
    route_groups: dict[str, list[int]] = defaultdict(list)
    for vehicle_id, static in sorted(vehicle_static.items()):
        route_key = derive_route_key(static["order_station_sequence"])
        station_ids, _ordered = _route_metadata(static["order_station_sequence"])
        if len(station_ids) >= 2:
            route_groups[route_key].append(vehicle_id)
        raw_util = vutil_by_vehicle.get(
            vehicle_id,
            {
                "samples": 0,
                "n_running": 0,
                "n_stopped": 0,
                "n_in_depot": 0,
                "n_at_station": 0,
                "n_broken": 0,
                "n_crashed": 0,
                "n_invalid": 0,
                "n_speed_positive": 0,
                "n_speed_positive_in_depot": 0,
                "final_state": static["initial_state"],
            },
        )
        vehicles.append(
            {
                "vehicle_id": vehicle_id,
                "name": static["name"],
                "order_count": static["order_count"],
                "order_station_sequence": static["order_station_sequence"],
                "route_key": route_key,
                "capacity_by_cargo": static["capacity_by_cargo"],
                "primary_cargo_id": static["primary_cargo_id"],
                "utilization": classify_utilization(raw_util),
                "arrivals": len(trips.get(vehicle_id, {}).get("arrivals", [])),
                "state_change_count": vstate_counts[vehicle_id],
                "final_state": raw_util["final_state"],
            }
        )

    routes: list[dict] = []
    for route_key, vehicle_ids in sorted(route_groups.items()):
        sequence = vehicle_static[vehicle_ids[0]]["order_station_sequence"]
        station_ids, ordered = _route_metadata(sequence)
        leg_counts: dict[tuple[int, int], int] = defaultdict(int)
        arrivals: list[dict] = []
        departures: list[dict] = []
        completed_cycles = 0
        arrival_factors: list[float] = []
        departure_factors: list[float] = []
        for vehicle_id in vehicle_ids:
            vehicle_trips = trips.get(vehicle_id, {})
            vehicle_arrivals = vehicle_trips.get("arrivals", [])
            vehicle_departures = vehicle_trips.get("departures", [])
            arrivals.extend(vehicle_arrivals)
            departures.extend(vehicle_departures)
            completed_cycles += int(vehicle_trips.get("completed_cycle_count", 0))
            for leg in vehicle_trips.get("legs", []):
                leg_counts[leg] += 1
            capacity = vehicle_static[vehicle_id]["capacity_by_cargo"]
            for sample in vehicle_arrivals:
                factor = _load_factor(sample, capacity)
                if factor is not None:
                    arrival_factors.append(factor)
            for sample in vehicle_departures:
                factor = _load_factor(sample, capacity)
                if factor is not None:
                    departure_factors.append(factor)
        routes.append(
            {
                "route_key": route_key,
                "station_ids": station_ids,
                "stop_count": len(station_ids),
                "ordered": ordered,
                "vehicle_ids": vehicle_ids,
                "vehicle_count": len(vehicle_ids),
                "trip_count": len(arrivals),
                "completed_cycle_count": completed_cycles,
                "legs": [
                    {"from_station": leg[0], "to_station": leg[1], "trip_count": count}
                    for leg, count in sorted(leg_counts.items())
                ],
                "load_factor_at_arrival": _factor_summary(arrival_factors),
                "load_factor_at_departure": _factor_summary(departure_factors),
            }
        )

    route_by_vehicle = {
        vehicle_id: route["route_key"]
        for route in routes
        for vehicle_id in route["vehicle_ids"]
    }
    events = [
        {
            "tick": event["tick"],
            "date": event["date"],
            "type": event["event_type"],
            "vehicle_id": event["vehicle_id"],
            "station_id": event["station_id"],
            "tile": event["tile"],
            "route_key": route_by_vehicle.get(int(event["vehicle_id"]), "none"),
        }
        for event in sorted(records.get("event", []), key=lambda row: int(row["tick"]))
    ]
    waiting_first = sum(
        int(station["summary"]["waiting_first"] or 0) for station in stations
    )
    waiting_last = sum(
        int(station["summary"]["waiting_last"] or 0) for station in stations
    )
    end_date = int(done["end_date"])
    target_date = int(done["target_date"])
    backstop_hit = bool(done["backstop_hit"])
    window_complete = end_date >= target_date and not backstop_hit
    queue_radius = int(meta["queue_radius_tiles"])
    total_arrivals = sum(int(vehicle["arrivals"]) for vehicle in vehicles)
    return {
        "schema_version": 1,
        "instrument_module": INSTRUMENT_MODULE,
        "driver": driver,
        "save_path": str(save_path),
        "replay_window": {
            "requested_window_days": meta["window_days_requested"],
            "start_date": meta["start_date"],
            "start_year": meta["start_year"],
            "start_month": meta["start_month"],
            "start_day": meta["start_day"],
            "target_date": target_date,
            "end_date": end_date,
            "elapsed_days": done["elapsed_days"],
            "window_complete": window_complete,
            "backstop_hit": backstop_hit,
        },
        "sampling": {
            "vehicle_state_interval_iterations": meta["vehicle_sample_ticks"],
            "station_snapshot_interval_iterations": meta["station_sample_ticks"],
            "queue_proxy_radius_tiles": queue_radius,
            "arrival_debounce_ticks": arrival_debounce_ticks,
            "loop_iterations": done["loop_iterations"],
            "vehicle_state_records": done["vehicle_state_records"],
            "station_snapshots": done["station_snapshots"],
            "mean_ticks_per_loop_iteration": _mean_ticks_per_iteration(
                records.get("station_sample", []), int(meta["station_sample_ticks"])
            ),
        },
        "definitions": {
            "queue_proxy": (
                "vehicles_near and vehicles_near_stopped are an operational proxy — "
                f"company road vehicles within {queue_radius} tiles of any stop tile — "
                "and OpenTTD's script API exposes no engine-exact bay occupancy or queue length."
            ),
            "tick_units": (
                "Ticks in this report are AI-controller script ticks; one loop iteration "
                "targets one script tick, and a game day is roughly 18.5 script ticks "
                "(empirical; see sampling.mean_ticks_per_loop_iteration for the achieved rate)."
            ),
            "utilization_policy": (
                "Samples are classified in_depot first, including stopped-in-depot state "
                "samples and positive-speed samples physically inside a depot; moving is "
                "positive speed outside a depot, and every remaining sample is stopped."
            ),
            "trip_definition": (
                "An arrival is an observed transition into VS_AT_STATION (vehicle at "
                "station); re-entry counts "
                f"after at least {arrival_debounce_ticks} controller ticks outside that state. "
                "completed_cycle_count increments when the resolved order index returns to "
                "its reference position, which can occur at the final departure rather than "
                "the return arrival, so it is timing-dependent."
            ),
            "load_factor_definition": (
                "Load factor is summed cargo load divided by summed positive cargo capacity, "
                "reported separately at observed arrival and departure edges."
            ),
            "lost_event_semantics": (
                "A lost event means OpenTTD emitted a vehicle-lost edge during this replay "
                "window; it is not a current-state or historical-episode claim."
            ),
        },
        "stations": stations,
        "vehicles": vehicles,
        "routes": routes,
        "events": events,
        "totals": {
            "road_vehicle_count": meta["road_vehicle_count"],
            "station_count": meta["station_count"],
            "route_count": len(routes),
            "total_arrivals": total_arrivals,
            "vehicles_with_zero_arrivals": sum(
                int(vehicle["arrivals"]) == 0 for vehicle in vehicles
            ),
            "lost_event_count": sum(event["type"] == "lost" for event in events),
            "crashed_event_count": sum(event["type"] == "crashed" for event in events),
            "total_waiting_cargo_first": waiting_first,
            "total_waiting_cargo_last": waiting_last,
        },
    }
