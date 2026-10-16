#!/usr/bin/env python3
"""Replay a final save and report factual road-network telemetry.

The instrument makes no verdict, ranking, or diagnosis. Queue fields are an
operational proximity proxy rather than engine-exact bay occupancy, and the
replay measures the network in the final save rather than the episode history.
Ticks are AI-controller script ticks: one loop iteration targets one script tick,
and a game day is roughly 18.5 script ticks (empirical; see
``sampling.mean_ticks_per_loop_iteration`` for the achieved rate).
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal, TypeAlias

from openttd_mcp.admin import AdminClient, AdminLifecycleConfig, allocate_admin_port
from openttd_mcp.render_capture import (
    DEFAULT_CAPTURE_TIMEOUT_SEC,
    REVIEW_MAX_EDGE_PX,
    SCREENSHOT_BLITTER,
    capture_giant_screenshot,
    read_png_dimensions,
    require_pillow_for_rendering,
    write_review_png,
)
from openttd_mcp.runtime import ROOT
from openttd_mcp.verify._replay_aggregate import (
    DEFAULT_ARRIVAL_DEBOUNCE_TICKS,
    INSTRUMENT_MODULE,
    NO_STATION_SENTINEL,
    build_report,
    classify_utilization,
    derive_route_key,
    derive_trips,
    summarize_station_series,
)
from openttd_mcp.verify._scaffold import (
    DEDICATED_DRIVER,
    create_run_root,
    launch_openttd,
    read_until,
    resolve_verify_driver,
    teardown,
)
from openttd_mcp.verify._squirrel import (
    APPEND_UNIQUE,
    BOOL_STR,
    CONTAINS,
    LOAD,
    compose_ai_class,
    compose_info_nut,
)

__all__ = [
    "DEFAULT_ARRIVAL_DEBOUNCE_TICKS",
    "INSTRUMENT_MODULE",
    "RECORD_FIELDS",
    "build_report",
    "classify_utilization",
    "derive_route_key",
    "derive_trips",
    "emit_telemetry",
    "main",
    "parse_telemetry_line",
    "parse_telemetry_log",
    "summarize_station_series",
]

TELEMETRY_TIMEOUT_SEC = 900
DEFAULT_WINDOW_DAYS = 280
DEFAULT_QUEUE_RADIUS_TILES = 4
DEFAULT_STATION_SAMPLE_TICKS = 18
RECORD_PREFIX = "REPLAY_TELEMETRY"
DONE_MARKER = "REPLAY_TELEMETRY|done|"
RENDER_PREFIX = "REPLAY_RENDER"
RENDER_MANIFEST_SCHEMA_VERSION = 1
MAX_RENDER_TRIGGERS = 5
RENDER_TIMEOUT_BASE_SEC = 120.0
RENDER_TIMEOUT_PER_DAY_SEC = 4.0
RENDER_TIMEOUT_PER_FRAME_SEC = DEFAULT_CAPTURE_TIMEOUT_SEC * 2 + 15.0

INFO_NUT = compose_info_nut(
    short_name="BRTT",
    description="Replay telemetry instrument for saved McpBridge company.",
    date="2026-08-08",
)

_TELEMETRY_HELPERS = r"""function McpBridge::SanitizeName(value)
{
    local out = "";
    for (local i = 0; i < value.len(); i++) {
        local character = value.slice(i, i + 1);
        out += (character == "|" ? "/" : character);
    }
    return out;
}

function McpBridge::JoinInts(items)
{
    local out = "";
    for (local i = 0; i < items.len(); i++) {
        if (i != 0) out += ",";
        out += items[i];
    }
    return out;
}

function McpBridge::JoinPairs(items)
{
    local out = "";
    for (local i = 0; i < items.len(); i++) {
        if (i != 0) out += ",";
        out += items[i][0] + ":" + items[i][1];
    }
    return out;
}

function McpBridge::RoadStopTiles(station_id)
{
    local result = [];
    if (!AIStation.IsValidStation(station_id)) return result;
    local stop_types = [AIStation.STATION_TRUCK_STOP, AIStation.STATION_BUS_STOP];
    foreach (stop_type in stop_types) {
        local tiles = AITileList_StationType(station_id, stop_type);
        foreach (tile, value in tiles) this.AppendUnique(result, tile);
    }
    return result;
}

function McpBridge::StationAtTile(tile)
{
    if (!AIMap.IsValidTile(tile)) return -1;
    local station_id = AIStation.GetStationID(tile);
    return AIStation.IsValidStation(station_id) ? station_id : -1;
}

function McpBridge::VehicleLoadPairs(vehicle_id, cargo_ids)
{
    local result = [];
    if (!AIVehicle.IsValidVehicle(vehicle_id)) return result;
    foreach (cargo_id in cargo_ids) {
        result.append([cargo_id, AIVehicle.GetCargoLoad(vehicle_id, cargo_id)]);
    }
    return result;
}

function McpBridge::NearAnyStop(tile, stop_tiles, radius)
{
    if (!AIMap.IsValidTile(tile)) return false;
    local x = AIMap.GetTileX(tile);
    local y = AIMap.GetTileY(tile);
    foreach (stop_tile in stop_tiles) {
        local dx = x - AIMap.GetTileX(stop_tile);
        local dy = y - AIMap.GetTileY(stop_tile);
        if (dx < 0) dx = -dx;
        if (dy < 0) dy = -dy;
        if (dx + dy <= radius) return true;
    }
    return false;
}

function McpBridge::DrainTelemetryEvents()
{
    local emitted = 0;
    while (AIEventController.IsEventWaiting()) {
        local event = AIEventController.GetNextEvent();
        local event_type = event.GetEventType();
        local kind = "";
        local vehicle_id = -1;
        local station_id = -1;
        if (event_type == AIEvent.ET_STATION_FIRST_VEHICLE) {
            local station_event = AIEventStationFirstVehicle.Convert(event);
            kind = "station_first_vehicle";
            vehicle_id = station_event.GetVehicleID();
            local candidate = station_event.GetStationID();
            if (AIStation.IsValidStation(candidate)) station_id = candidate;
        } else if (event_type == AIEvent.ET_VEHICLE_LOST) {
            kind = "lost";
            vehicle_id = AIEventVehicleLost.Convert(event).GetVehicleID();
        } else if (event_type == AIEvent.ET_VEHICLE_CRASHED) {
            kind = "crashed";
            vehicle_id = AIEventVehicleCrashed.Convert(event).GetVehicleID();
        } else {
            continue;
        }
        local valid = AIVehicle.IsValidVehicle(vehicle_id);
        local tile = -1;
        local order_index = -1;
        if (valid) {
            tile = AIVehicle.GetLocation(vehicle_id);
            if (station_id == -1) station_id = this.StationAtTile(tile);
            if (AIOrder.GetOrderCount(vehicle_id) > 0) {
                order_index = AIOrder.ResolveOrderPosition(vehicle_id, AIOrder.ORDER_CURRENT);
            }
        }
        print("REPLAY_TELEMETRY|event|" + AIController.GetTick()
            + "|" + AIDate.GetCurrentDate()
            + "|" + kind
            + "|" + vehicle_id
            + "|" + station_id
            + "|" + tile
            + "|" + order_index
            + "|" + this.BoolStr(valid));
        emitted++;
    }
    return emitted;
}

"""

_START_TEMPLATE = r"""function McpBridge::Start()
{
    AIRoad.SetCurrentRoadType(AIRoad.ROADTYPE_ROAD);

    local queue_radius = __QUEUE_RADIUS__;
    local station_sample_ticks = __STATION_TICKS__;
    local window_days = __WINDOW_DAYS__;
    local backstop_iterations = window_days * 250;
    local stations_data = [];
    local seen_stations = {};
    local stop_types = [AIStation.STATION_TRUCK_STOP, AIStation.STATION_BUS_STOP];
    foreach (stop_type in stop_types) {
        local stations = AIStationList(stop_type);
        foreach (station_id, value in stations) {
            if (!AIStation.IsValidStation(station_id)) continue;
            local key = "" + station_id;
            if (seen_stations.rawin(key)) continue;
            seen_stations[key] <- true;
            local stop_tiles = this.RoadStopTiles(station_id);
            local has_truck = AIStation.HasStationType(station_id, AIStation.STATION_TRUCK_STOP);
            local has_bus = AIStation.HasStationType(station_id, AIStation.STATION_BUS_STOP);
            local location = AIStation.GetLocation(station_id);
            stations_data.append({ id = station_id, stop_tiles = stop_tiles });
            print("REPLAY_TELEMETRY|station|" + station_id
                + "|" + stop_tiles.len()
                + "|" + this.JoinInts(stop_tiles)
                + "|" + this.BoolStr(has_truck)
                + "|" + this.BoolStr(has_bus)
                + "|" + location
                + "|" + this.SanitizeName(AIStation.GetName(station_id)));
        }
    }

    local vehicle_ids = [];
    local vehicle_data = {};
    local vehicles = AIVehicleList();
    foreach (vehicle_id, value in vehicles) {
        if (!AIVehicle.IsValidVehicle(vehicle_id)) continue;
        if (AIVehicle.GetVehicleType(vehicle_id) != AIVehicle.VT_ROAD) continue;
        local order_count = AIOrder.GetOrderCount(vehicle_id);
        local order_stations = [];
        for (local i = 0; i < order_count; i++) {
            local station_id = -1;
            if (AIOrder.IsGotoStationOrder(vehicle_id, i)) {
                local destination = AIOrder.GetOrderDestination(vehicle_id, i);
                local candidate = AIStation.GetStationID(destination);
                if (AIStation.IsValidStation(candidate)) station_id = candidate;
            }
            order_stations.append(station_id);
        }
        local capacity_pairs = [];
        local cargo_ids = [];
        local primary_cargo = -1;
        local primary_capacity = -1;
        local cargoes = AICargoList();
        foreach (cargo_id, cargo_value in cargoes) {
            local capacity = AIVehicle.GetCapacity(vehicle_id, cargo_id);
            if (capacity <= 0) continue;
            capacity_pairs.append([cargo_id, capacity]);
            cargo_ids.append(cargo_id);
            if (capacity > primary_capacity) {
                primary_capacity = capacity;
                primary_cargo = cargo_id;
            }
        }
        local state = AIVehicle.GetState(vehicle_id);
        local tile = AIVehicle.GetLocation(vehicle_id);
        vehicle_ids.append(vehicle_id);
        vehicle_data["" + vehicle_id] <- {
            prev_state = state,
            cargo_ids = cargo_ids,
            samples = 0,
            n_running = 0,
            n_stopped = 0,
            n_in_depot = 0,
            n_at_station = 0,
            n_broken = 0,
            n_crashed = 0,
            n_invalid = 0,
            n_speed_positive = 0,
            n_speed_positive_in_depot = 0,
        };
        print("REPLAY_TELEMETRY|vehicle|" + vehicle_id
            + "|" + order_count
            + "|" + this.JoinInts(order_stations)
            + "|" + this.JoinPairs(capacity_pairs)
            + "|" + primary_cargo
            + "|" + state
            + "|" + tile
            + "|" + this.SanitizeName(AIVehicle.GetName(vehicle_id)));
    }

    local start_date = AIDate.GetCurrentDate();
    local target_date = start_date + window_days;
    print("REPLAY_TELEMETRY|meta|" + window_days
        + "|" + start_date
        + "|" + AIDate.GetYear(start_date)
        + "|" + AIDate.GetMonth(start_date)
        + "|" + AIDate.GetDayOfMonth(start_date)
        + "|" + target_date
        + "|1|" + station_sample_ticks
        + "|" + queue_radius
        + "|" + backstop_iterations
        + "|" + vehicle_ids.len()
        + "|" + stations_data.len());

    __RENDER_OVERVIEW__
    __RENDER_SETUP__
    local loop_iterations = 0;
    local vehicle_state_records = 0;
    local station_snapshots = 0;
    local event_records = 0;
    local snapshot_cargo_ids = [];
    local snapshot_cargoes = AICargoList();
    foreach (cargo_id, cargo_value in snapshot_cargoes) snapshot_cargo_ids.append(cargo_id);
    while (AIDate.GetCurrentDate() < target_date && loop_iterations < backstop_iterations) {
        event_records += this.DrainTelemetryEvents();
        foreach (vehicle_id in vehicle_ids) {
            local telemetry = vehicle_data["" + vehicle_id];
            telemetry.samples++;
            local valid = AIVehicle.IsValidVehicle(vehicle_id);
            local state = AIVehicle.VS_INVALID;
            local tile = -1;
            local speed = 0;
            local station_id = -1;
            local order_index = -1;
            local loads = [];
            if (valid) {
                state = AIVehicle.GetState(vehicle_id);
                tile = AIVehicle.GetLocation(vehicle_id);
                speed = AIVehicle.GetCurrentSpeed(vehicle_id);
                station_id = this.StationAtTile(tile);
                if (AIOrder.GetOrderCount(vehicle_id) > 0) {
                    order_index = AIOrder.ResolveOrderPosition(vehicle_id, AIOrder.ORDER_CURRENT);
                }
                loads = this.VehicleLoadPairs(vehicle_id, telemetry.cargo_ids);
                if (state == AIVehicle.VS_RUNNING) telemetry.n_running++;
                else if (state == AIVehicle.VS_STOPPED) telemetry.n_stopped++;
                else if (state == AIVehicle.VS_IN_DEPOT) telemetry.n_in_depot++;
                else if (state == AIVehicle.VS_AT_STATION) telemetry.n_at_station++;
                else if (state == AIVehicle.VS_BROKEN) telemetry.n_broken++;
                else if (state == AIVehicle.VS_CRASHED) telemetry.n_crashed++;
                else telemetry.n_invalid++;
                if (speed > 0) {
                    telemetry.n_speed_positive++;
                    if (AIVehicle.IsInDepot(vehicle_id)) telemetry.n_speed_positive_in_depot++;
                }
            } else {
                telemetry.n_invalid++;
            }
            if (state != telemetry.prev_state) {
                print("REPLAY_TELEMETRY|vstate|" + AIController.GetTick()
                    + "|" + AIDate.GetCurrentDate()
                    + "|" + vehicle_id
                    + "|" + telemetry.prev_state
                    + "|" + state
                    + "|" + station_id
                    + "|" + tile
                    + "|" + speed
                    + "|" + order_index
                    + "|" + this.JoinPairs(loads));
                telemetry.prev_state = state;
                vehicle_state_records++;
            }
        }

        if (loop_iterations % station_sample_ticks == 0) {
            local snapshot_tick = AIController.GetTick();
            local snapshot_date = AIDate.GetCurrentDate();
            foreach (station in stations_data) {
                if (!AIStation.IsValidStation(station.id)) continue;
                local near = 0;
                local near_stopped = 0;
                foreach (vehicle_id in vehicle_ids) {
                    if (!AIVehicle.IsValidVehicle(vehicle_id)) continue;
                    local tile = AIVehicle.GetLocation(vehicle_id);
                    if (!this.NearAnyStop(tile, station.stop_tiles, queue_radius)) continue;
                    near++;
                    if (AIVehicle.GetCurrentSpeed(vehicle_id) == 0) near_stopped++;
                }
                local waiting_total = 0;
                local waiting_pairs = [];
                foreach (cargo_id in snapshot_cargo_ids) {
                    local waiting = AIStation.GetCargoWaiting(station.id, cargo_id);
                    if (waiting <= 0) continue;
                    waiting_total += waiting;
                    waiting_pairs.append([cargo_id, waiting]);
                }
                print("REPLAY_TELEMETRY|station_sample|" + snapshot_tick
                    + "|" + snapshot_date
                    + "|" + station.id
                    + "|" + near
                    + "|" + near_stopped
                    + "|" + waiting_total
                    + "|" + this.JoinPairs(waiting_pairs));
                station_snapshots++;
            }
        }
        this.Sleep(1);
        loop_iterations++;
        __RENDER_CHECKPOINT__
    }
    event_records += this.DrainTelemetryEvents();

    foreach (vehicle_id in vehicle_ids) {
        local telemetry = vehicle_data["" + vehicle_id];
        local final_tile = -1;
        local final_state = AIVehicle.VS_INVALID;
        if (AIVehicle.IsValidVehicle(vehicle_id)) {
            final_tile = AIVehicle.GetLocation(vehicle_id);
            final_state = AIVehicle.GetState(vehicle_id);
        }
        print("REPLAY_TELEMETRY|vutil|" + vehicle_id
            + "|" + telemetry.samples
            + "|" + telemetry.n_running
            + "|" + telemetry.n_stopped
            + "|" + telemetry.n_in_depot
            + "|" + telemetry.n_at_station
            + "|" + telemetry.n_broken
            + "|" + telemetry.n_crashed
            + "|" + telemetry.n_invalid
            + "|" + telemetry.n_speed_positive
            + "|" + telemetry.n_speed_positive_in_depot
            + "|" + final_tile
            + "|" + final_state);
    }

    local end_date = AIDate.GetCurrentDate();
    local backstop_hit = loop_iterations >= backstop_iterations;
    print("REPLAY_TELEMETRY|done|" + start_date
        + "|" + target_date
        + "|" + end_date
        + "|" + (end_date - start_date)
        + "|" + loop_iterations
        + "|" + vehicle_state_records
        + "|" + station_snapshots
        + "|" + event_records
        + "|" + this.BoolStr(backstop_hit));
    while (true) this.Sleep(200);
}
"""


def _compose_main_nut(
    *,
    window_days: int,
    station_sample_ticks: int,
    queue_radius: int,
    render_offsets: Sequence[int] = (),
) -> str:
    if render_offsets:
        offsets = ",".join(str(offset) for offset in render_offsets)
        render_overview = (
            'print("REPLAY_RENDER|overview|0|" + AIController.GetTick()'
            ' + "|" + AIDate.GetCurrentDate());'
        )
        render_setup = (
            f"local render_offsets = [{offsets}];\n    local next_render_offset = 0;"
        )
        render_checkpoint = r"""local render_elapsed_days = AIDate.GetCurrentDate() - start_date;
        while (next_render_offset < render_offsets.len()
                && render_elapsed_days >= render_offsets[next_render_offset]) {
            print("REPLAY_RENDER|checkpoint|" + render_offsets[next_render_offset]
                + "|" + AIController.GetTick()
                + "|" + AIDate.GetCurrentDate());
            next_render_offset++;
        }"""
    else:
        render_overview = ""
        render_setup = ""
        render_checkpoint = ""
    start = (
        _START_TEMPLATE.replace("__WINDOW_DAYS__", str(window_days))
        .replace("__STATION_TICKS__", str(station_sample_ticks))
        .replace("__QUEUE_RADIUS__", str(queue_radius))
        .replace("__RENDER_OVERVIEW__", render_overview)
        .replace("__RENDER_SETUP__", render_setup)
        .replace("__RENDER_CHECKPOINT__", render_checkpoint)
    )
    return compose_ai_class(
        LOAD,
        BOOL_STR,
        CONTAINS,
        APPEND_UNIQUE,
        _TELEMETRY_HELPERS,
        start,
    )


MAIN_NUT = _compose_main_nut(
    window_days=DEFAULT_WINDOW_DAYS,
    station_sample_ticks=DEFAULT_STATION_SAMPLE_TICKS,
    queue_radius=DEFAULT_QUEUE_RADIUS_TILES,
)

FieldParser: TypeAlias = Callable[[str], object] | Literal["bool"]
FieldSpec: TypeAlias = tuple[str, FieldParser]


def _parse_bool(text: str) -> bool:
    if text == "true":
        return True
    if text == "false":
        return False
    raise ValueError(f"Expected boolean string, got {text!r}")


def _parse_int_list(text: str) -> list[int]:
    if not text:
        return []
    return [int(item) for item in text.split(",")]


def _parse_station_id_list(text: str) -> list[int]:
    if not text:
        return []
    return [
        (-1 if int(item) == NO_STATION_SENTINEL else int(item))
        for item in text.split(",")
    ]


def _parse_pairs(text: str) -> dict[int, int]:
    result: dict[int, int] = {}
    if not text:
        return result
    for item in text.split(","):
        key, value = item.split(":", 1)
        result[int(key)] = int(value)
    return result


def _parse_station_id(text: str) -> int:
    station_id = int(text)
    return -1 if station_id == NO_STATION_SENTINEL else station_id


def _parse_event_type(text: str) -> str:
    if text not in {"lost", "crashed", "station_first_vehicle"}:
        raise ValueError(f"Unexpected event type {text!r}")
    return text


RECORD_FIELDS: dict[str, Sequence[FieldSpec]] = {
    "meta": (
        ("window_days_requested", int),
        ("start_date", int),
        ("start_year", int),
        ("start_month", int),
        ("start_day", int),
        ("target_date", int),
        ("vehicle_sample_ticks", int),
        ("station_sample_ticks", int),
        ("queue_radius_tiles", int),
        ("backstop_iterations", int),
        ("road_vehicle_count", int),
        ("station_count", int),
    ),
    "station": (
        ("station_id", _parse_station_id),
        ("stop_tile_count", int),
        ("stop_tiles", _parse_int_list),
        ("has_truck_stop", "bool"),
        ("has_bus_stop", "bool"),
        ("location_tile", int),
        ("name", str),
    ),
    "vehicle": (
        ("vehicle_id", int),
        ("order_count", int),
        ("order_station_sequence", _parse_station_id_list),
        ("capacity_by_cargo", _parse_pairs),
        ("primary_cargo_id", int),
        ("initial_state", int),
        ("initial_tile", int),
        ("name", str),
    ),
    "vstate": (
        ("tick", int),
        ("date", int),
        ("vehicle_id", int),
        ("prev_state", int),
        ("state", int),
        ("station_id", _parse_station_id),
        ("tile", int),
        ("speed", int),
        ("order_index", int),
        ("load_by_cargo", _parse_pairs),
    ),
    "station_sample": (
        ("tick", int),
        ("date", int),
        ("station_id", _parse_station_id),
        ("vehicles_near", int),
        ("vehicles_near_stopped", int),
        ("waiting_total", int),
        ("waiting_by_cargo", _parse_pairs),
    ),
    "vutil": (
        ("vehicle_id", int),
        ("samples", int),
        ("n_running", int),
        ("n_stopped", int),
        ("n_in_depot", int),
        ("n_at_station", int),
        ("n_broken", int),
        ("n_crashed", int),
        ("n_invalid", int),
        ("n_speed_positive", int),
        ("n_speed_positive_in_depot", int),
        ("final_tile", int),
        ("final_state", int),
    ),
    "event": (
        ("tick", int),
        ("date", int),
        ("event_type", _parse_event_type),
        ("vehicle_id", int),
        ("station_id", _parse_station_id),
        ("tile", int),
        ("order_index", int),
        ("vehicle_still_valid", "bool"),
    ),
    "done": (
        ("start_date", int),
        ("target_date", int),
        ("end_date", int),
        ("elapsed_days", int),
        ("loop_iterations", int),
        ("vehicle_state_records", int),
        ("station_snapshots", int),
        ("event_records", int),
        ("backstop_hit", "bool"),
    ),
}


def parse_telemetry_line(line: str) -> tuple[str, dict] | None:
    """Parse one telemetry line, ignoring unrelated OpenTTD output."""
    marker = f"{RECORD_PREFIX}|"
    if marker not in line:
        return None
    payload = line.split(marker, 1)[1].strip()
    record_type, separator, fields_payload = payload.partition("|")
    if not separator or record_type not in RECORD_FIELDS:
        raise RuntimeError(
            f"Unexpected {record_type or 'unknown'} payload: {payload!r}"
        )
    fields = RECORD_FIELDS[record_type]
    parts = fields_payload.split("|", len(fields) - 1)
    if len(parts) != len(fields):
        raise RuntimeError(
            f"Unexpected {record_type} payload ({len(parts)} fields): {payload!r}"
        )
    record: dict = {}
    try:
        for (name, parser), raw in zip(fields, parts):
            record[name] = _parse_bool(raw) if parser == "bool" else parser(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Unexpected {record_type} payload: {payload!r}") from exc
    if record_type in {"station", "vehicle"} and "|" in record["name"]:
        raise RuntimeError(f"Unexpected {record_type} payload: {payload!r}")
    return record_type, record


def parse_telemetry_log(path: Path) -> dict[str, list[dict]]:
    """Parse every telemetry record from an OpenTTD log."""
    records = {record_type: [] for record_type in RECORD_FIELDS}
    with path.open() as source:
        for line in source:
            parsed = parse_telemetry_line(line)
            if parsed is not None:
                record_type, record = parsed
                records[record_type].append(record)
    return records


def emit_telemetry(
    *,
    save_path: Path,
    log_path: Path,
    report_path: Path,
    report: dict,
) -> None:
    """Write the dedicated report and print its compact marker block."""
    rendered = json.dumps(report, indent=2, sort_keys=True)
    report_path.write_text(rendered)
    summary = json.dumps(report["totals"], sort_keys=True, separators=(",", ":"))
    print(f"REPLAY_TELEMETRY_SAVE={save_path}")
    print(f"REPLAY_TELEMETRY_LOG={log_path}")
    print(f"REPLAY_TELEMETRY={report_path}")
    print(f"REPLAY_TELEMETRY_SUMMARY={summary}")


def _render_checkpoint_offsets(window_days: int) -> tuple[int, ...]:
    """Return de-duplicated first-at-or-after quarter-window day offsets."""
    offsets = ((window_days * quarter + 3) // 4 for quarter in range(1, 5))
    return tuple(dict.fromkeys(offsets))


def _relative_path(target: Path, directory: Path) -> str:
    return os.path.relpath(target.resolve(), start=directory.resolve())


def _write_render_manifest(path: Path, manifest: dict) -> None:
    """Atomically replace a manifest so readers never observe partial JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(json.dumps(manifest, indent=2, sort_keys=True))
            temporary.flush()
            os.fchmod(temporary.fileno(), 0o644)
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def _render_timeout_seconds(window_days: int, frame_count: int) -> float:
    """Return the dedicated replay budget, including per-day and frame slack."""
    return (
        RENDER_TIMEOUT_BASE_SEC
        + window_days * RENDER_TIMEOUT_PER_DAY_SEC
        + frame_count * RENDER_TIMEOUT_PER_FRAME_SEC
    )


def _parse_render_line(line: str) -> dict[str, int | str] | None:
    marker = f"{RENDER_PREFIX}|"
    if marker not in line:
        return None
    payload = line.split(marker, 1)[1].strip()
    parts = payload.split("|")
    if len(parts) != 4 or parts[0] not in {"overview", "checkpoint"}:
        raise RuntimeError(f"Unexpected replay render payload: {payload!r}")
    try:
        scheduled_offset, trigger_tick, trigger_date = map(int, parts[1:])
    except ValueError as exc:
        raise RuntimeError(f"Unexpected replay render payload: {payload!r}") from exc
    return {
        "kind": parts[0],
        "scheduled_offset_days": scheduled_offset,
        "trigger_tick": trigger_tick,
        "trigger_date": trigger_date,
    }


class _ReplayRenderer:
    """Queue replay markers and capture them outside the stdout-pump thread."""

    def __init__(
        self,
        *,
        client: AdminClient,
        runtime_screenshot_dir: Path,
        render_dir: Path,
        manifest_path: Path,
        manifest: dict,
        checkpoint_offsets: Sequence[int],
        capture_timeout_seconds: float,
        started_at: float,
    ) -> None:
        self.client = client
        self.runtime_screenshot_dir = runtime_screenshot_dir
        self.render_dir = render_dir
        self.manifest_path = manifest_path
        self.manifest = manifest
        self.capture_timeout_seconds = capture_timeout_seconds
        self.started_at = started_at
        self._expected_keys = {
            ("overview", 0),
            *(("checkpoint", offset) for offset in checkpoint_offsets),
        }
        if len(self._expected_keys) > MAX_RENDER_TRIGGERS:
            raise ValueError(
                f"render requested {len(self._expected_keys)} triggers; maximum is "
                f"{MAX_RENDER_TRIGGERS}"
            )
        self._accepted_keys: set[tuple[str, int]] = set()
        self._queue: queue.Queue[dict[str, int | str]] = queue.Queue(
            maxsize=len(self._expected_keys)
        )
        self._producer_done = threading.Event()
        self._lock = threading.RLock()
        self._finished = False
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def _write_locked(self) -> None:
        _write_render_manifest(self.manifest_path, self.manifest)

    def _record_error_locked(
        self, stage: str, error: str, *, frame: str | None = None
    ) -> None:
        record = {"stage": stage, "error": error}
        if frame is not None:
            record["frame"] = frame
        self.manifest["errors"].append(record)
        self._write_locked()

    def observe_line(self, line: str) -> None:
        """Validate and enqueue one marker without blocking stdout drainage."""
        if self._producer_done.is_set():
            return
        with self._lock:
            if self._producer_done.is_set():
                return
            try:
                trigger = _parse_render_line(line)
            except Exception as exc:  # noqa: BLE001 -- marker errors must not stop log drainage
                self._record_error_locked("marker", str(exc))
                return
            if trigger is None:
                return
            kind = str(trigger["kind"])
            offset = int(trigger["scheduled_offset_days"])
            key = (kind, offset)
            if key not in self._expected_keys:
                self._record_error_locked(
                    "marker", f"unexpected render trigger {kind}@{offset}"
                )
                return
            if key in self._accepted_keys:
                self._record_error_locked(
                    "marker", f"duplicate render trigger {kind}@{offset}"
                )
                return
            self._accepted_keys.add(key)
            try:
                self._queue.put_nowait(trigger)
            except queue.Full:
                self._accepted_keys.remove(key)
                self._record_error_locked(
                    "marker", "render trigger queue is full", frame=f"{kind}@{offset}"
                )

    def _capture_frame(self, trigger: dict[str, int | str]) -> None:
        kind = str(trigger["kind"])
        offset = int(trigger["scheduled_offset_days"])
        basename = "overview" if kind == "overview" else f"replay-day-{offset}"
        archival_path = self.render_dir / f"{basename}.png"
        review_path = self.render_dir / f"{basename}-review.png"
        frame = {
            **trigger,
            "id": basename,
            "status": "capturing",
            "archival": {
                "path": archival_path.name,
                "width": None,
                "height": None,
                "bytes": None,
                "scale": 1.0,
            },
            "review": None,
        }
        with self._lock:
            self.manifest["frames"].append(frame)
            self._write_locked()
        try:
            captured = capture_giant_screenshot(
                self.client,
                self.runtime_screenshot_dir,
                basename,
                timeout=self.capture_timeout_seconds,
            )
            read_png_dimensions(captured)
            shutil.copyfile(captured, archival_path)
            width, height = read_png_dimensions(archival_path)
            review: dict | None = None
            if max(width, height) > REVIEW_MAX_EDGE_PX:
                review_width, review_height, review_scale = write_review_png(
                    archival_path, review_path
                )
                settled_review_width, settled_review_height = read_png_dimensions(
                    review_path
                )
                if (review_width, review_height) != (
                    settled_review_width,
                    settled_review_height,
                ):
                    raise RuntimeError(
                        "review PNG dimensions changed after writing "
                        f"{review_path}: expected {review_width}x{review_height}, got "
                        f"{settled_review_width}x{settled_review_height}"
                    )
                review = {
                    "path": review_path.name,
                    "width": review_width,
                    "height": review_height,
                    "bytes": review_path.stat().st_size,
                    "scale": review_scale,
                }
        except Exception as exc:  # noqa: BLE001 -- preserve partial evidence per frame
            with self._lock:
                frame.update({"status": "failed", "error": str(exc)})
                self._record_error_locked("capture", str(exc), frame=basename)
            return
        with self._lock:
            archival = frame["archival"]
            assert isinstance(archival, dict)
            archival.update(
                {
                    "width": width,
                    "height": height,
                    "bytes": archival_path.stat().st_size,
                }
            )
            frame["review"] = review
            frame["status"] = "complete"
            self._write_locked()

    def _run(self) -> None:
        while True:
            try:
                trigger = self._queue.get(timeout=0.1)
            except queue.Empty:
                if self._producer_done.is_set():
                    return
                continue
            try:
                self._capture_frame(trigger)
            except Exception as exc:  # noqa: BLE001 -- preserve telemetry after worker bugs
                with self._lock:
                    self._record_error_locked("worker", str(exc))
            finally:
                self._queue.task_done()

    def finish(self) -> None:
        """Drain the worker before its administrative client is closed."""
        with self._lock:
            if self._finished:
                return
            self._producer_done.set()
        self._worker.join()
        with self._lock:
            missing = self._expected_keys - self._accepted_keys
            for kind, offset in sorted(missing):
                self._record_error_locked(
                    "marker", f"missing render trigger {kind}@{offset}"
                )
            actual_frames = len(self.manifest["frames"])
            expected_frames = len(self._expected_keys)
            if actual_frames != expected_frames:
                self._record_error_locked(
                    "frames",
                    "render frame count mismatch: "
                    f"expected {expected_frames}, captured {actual_frames}",
                )
            self.manifest["elapsed_wall_seconds"] = round(
                time.monotonic() - self.started_at, 3
            )
            self._finished = True
            self._write_locked()

    def finalize(self, replay_window: dict) -> None:
        with self._lock:
            self.manifest["replay_window"].update(
                {
                    "start_date": replay_window["start_date"],
                    "target_date": replay_window["target_date"],
                    "end_date": replay_window["end_date"],
                    "window_complete": replay_window["window_complete"],
                }
            )
            self.manifest["status"] = (
                "failed"
                if self.manifest["errors"]
                else (
                    "complete"
                    if replay_window["window_complete"]
                    else "telemetry_incomplete"
                )
            )
            self.manifest["elapsed_wall_seconds"] = round(
                time.monotonic() - self.started_at, 3
            )
            self._write_locked()

    def fail(self, stage: str, error: str) -> None:
        """Persist a non-worker failure without duplicating a finalized one."""
        with self._lock:
            if self.manifest["status"] != "failed":
                self.manifest["status"] = "failed"
                self.manifest["errors"].append({"stage": stage, "error": error})
            self.manifest["elapsed_wall_seconds"] = round(
                time.monotonic() - self.started_at, 3
            )
            self._write_locked()

    def failure(self) -> RuntimeError | None:
        with self._lock:
            return _render_failure_from_errors(self.manifest["errors"])


def _allocate_render_ports() -> tuple[int, int]:
    """Allocate distinct loopback game and administrative listener ports."""
    game_port = allocate_admin_port()
    admin_port = allocate_admin_port()
    while admin_port == game_port:
        admin_port = allocate_admin_port()
    return game_port, admin_port


def _create_render_dir(value: Path) -> Path:
    """Create a new evidence directory without accepting stale prior output."""
    render_dir = value.resolve()
    if render_dir.exists():
        raise ValueError(
            f"render evidence directory already exists and may contain stale files: "
            f"{render_dir}"
        )
    render_dir.mkdir(parents=True)
    return render_dir


def _finalize_render_manifest(
    manifest: dict, manifest_path: Path, replay_window: dict, started_at: float
) -> None:
    """Finalize a manifest when no capture worker could be started."""
    manifest["replay_window"].update(
        {
            "start_date": replay_window["start_date"],
            "target_date": replay_window["target_date"],
            "end_date": replay_window["end_date"],
            "window_complete": replay_window["window_complete"],
        }
    )
    manifest["status"] = (
        "failed"
        if manifest["errors"]
        else (
            "complete" if replay_window["window_complete"] else "telemetry_incomplete"
        )
    )
    manifest["elapsed_wall_seconds"] = round(time.monotonic() - started_at, 3)
    _write_render_manifest(manifest_path, manifest)


def _render_failure_from_errors(errors: list[dict]) -> RuntimeError | None:
    if not errors:
        return None
    first = errors[0]
    return RuntimeError(
        "rendered evidence failed after telemetry completed: "
        f"{len(errors)} error(s); first {first['stage']}: {first['error']}"
    )


def _positive_int(text: str) -> int:
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return value


def _nonnegative_int(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError("value must be a non-negative integer")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("save_path")
    parser.add_argument(
        "--window-days", type=_positive_int, default=DEFAULT_WINDOW_DAYS
    )
    parser.add_argument(
        "--station-sample-ticks",
        type=_positive_int,
        default=DEFAULT_STATION_SAMPLE_TICKS,
    )
    parser.add_argument(
        "--queue-radius", type=_nonnegative_int, default=DEFAULT_QUEUE_RADIUS_TILES
    )
    parser.add_argument("--out", type=Path)
    parser.add_argument("--render-dir", type=Path)
    args = parser.parse_args(argv)

    save_path = Path(args.save_path)
    if not save_path.is_absolute():
        save_path = (ROOT / save_path).resolve()
    render_dir: Path | None = None
    render_offsets: tuple[int, ...] = ()
    admin_config: AdminLifecycleConfig | None = None
    game_port = 3996
    render_started_at: float | None = None
    capture_timeout_seconds = DEFAULT_CAPTURE_TIMEOUT_SEC
    render_timeout_seconds: float | None = None
    driver = (
        DEDICATED_DRIVER if args.render_dir is not None else resolve_verify_driver()
    )
    if args.render_dir is not None:
        require_pillow_for_rendering()
        render_dir = _create_render_dir(args.render_dir)
        render_offsets = _render_checkpoint_offsets(args.window_days)
        render_timeout_seconds = _render_timeout_seconds(
            args.window_days, 1 + len(render_offsets)
        )
        render_started_at = time.monotonic()
        game_port, admin_port = _allocate_render_ports()
        admin_config = AdminLifecycleConfig(
            port=admin_port, client_name="replay-render-evidence"
        )
    root, log_path, _ignored_result_path, env = create_run_root(
        prefix="replay-telemetry",
        game_start_script=(
            "echo replay telemetry: using saved McpBridge AI\nunpause\n"
        ),
        info_nut=INFO_NUT,
        main_nut=_compose_main_nut(
            window_days=args.window_days,
            station_sample_ticks=args.station_sample_ticks,
            queue_radius=args.queue_radius,
            render_offsets=render_offsets,
        ),
        log_filename="replay_telemetry.log",
        admin_config=admin_config,
    )
    report_path = (
        render_dir / "replay_telemetry.json"
        if render_dir is not None and args.out is None
        else root / "replay_telemetry.json"
    )
    manifest_path: Path | None = None
    manifest: dict | None = None
    if render_dir is not None:
        manifest_path = render_dir / "manifest.json"
        telemetry_target = args.out.resolve() if args.out is not None else report_path
        manifest = {
            "schema_version": RENDER_MANIFEST_SCHEMA_VERSION,
            "status": "pending",
            "source_save": str(save_path),
            "telemetry_report": _relative_path(telemetry_target, render_dir),
            "driver": DEDICATED_DRIVER,
            "blitter": SCREENSHOT_BLITTER,
            "screenshot_kind": "giant",
            "replay_window": {
                "requested_days": args.window_days,
                "scheduled_offsets_days": list(render_offsets),
            },
            "capture_timeout_seconds": capture_timeout_seconds,
            "replay_timeout_seconds": render_timeout_seconds,
            "elapsed_wall_seconds": None,
            "frames": [],
            "errors": [],
        }
        _write_render_manifest(manifest_path, manifest)

    proc = None
    admin: AdminClient | None = None
    renderer: _ReplayRenderer | None = None
    failure_stage = "launch"
    try:
        proc = launch_openttd(
            root,
            save_path,
            env,
            port=game_port,
            driver=driver,
            blitter=SCREENSHOT_BLITTER if render_dir is not None else None,
        )
        if admin_config is not None:
            failure_stage = "connection"
            admin = AdminClient(admin_config)
            try:
                admin.connect(timeout=60)
            except Exception as exc:  # noqa: BLE001 -- emit telemetry after render connect failure
                assert manifest is not None
                assert manifest_path is not None
                manifest["errors"].append({"stage": "connection", "error": str(exc)})
                _write_render_manifest(manifest_path, manifest)
            else:
                assert render_dir is not None
                assert manifest_path is not None
                assert manifest is not None
                assert render_started_at is not None
                renderer = _ReplayRenderer(
                    client=admin,
                    runtime_screenshot_dir=root / "screenshot",
                    render_dir=render_dir,
                    manifest_path=manifest_path,
                    manifest=manifest,
                    checkpoint_offsets=render_offsets,
                    capture_timeout_seconds=capture_timeout_seconds,
                    started_at=render_started_at,
                )
        failure_stage = "replay"
        read_until(
            proc,
            log_path,
            DONE_MARKER,
            (
                render_timeout_seconds
                if render_timeout_seconds is not None
                else TELEMETRY_TIMEOUT_SEC
            ),
            on_line=renderer.observe_line if renderer is not None else None,
        )
        if renderer is not None:
            renderer.finish()
        failure_stage = "telemetry"
        records = parse_telemetry_log(log_path)
        report = build_report(records, save_path=save_path, driver=driver)
        emit_telemetry(
            save_path=save_path,
            log_path=log_path,
            report_path=report_path,
            report=report,
        )
        if args.out is not None:
            shutil.copyfile(report_path, args.out)
            print(f"REPLAY_TELEMETRY_OUT={args.out}")
        if renderer is not None and manifest_path is not None:
            renderer.finalize(report["replay_window"])
            print(f"REPLAY_RENDERED_EVIDENCE={manifest_path}")
            render_failure = renderer.failure()
            if render_failure is not None:
                failure_stage = "render"
                raise render_failure
        elif manifest is not None and manifest_path is not None:
            assert render_started_at is not None
            _finalize_render_manifest(
                manifest, manifest_path, report["replay_window"], render_started_at
            )
            print(f"REPLAY_RENDERED_EVIDENCE={manifest_path}")
            render_failure = _render_failure_from_errors(manifest["errors"])
            if render_failure is not None:
                failure_stage = "render"
                raise render_failure
        return 0 if report["replay_window"]["window_complete"] else 2
    except Exception as exc:
        if renderer is not None:
            renderer.finish()
            renderer.fail(failure_stage, str(exc))
        elif manifest is not None and manifest_path is not None:
            if manifest["status"] != "failed":
                manifest["status"] = "failed"
                manifest["errors"].append({"stage": failure_stage, "error": str(exc)})
            if render_started_at is not None:
                manifest["elapsed_wall_seconds"] = round(
                    time.monotonic() - render_started_at, 3
                )
            _write_render_manifest(manifest_path, manifest)
        raise
    finally:
        try:
            if renderer is not None:
                renderer.finish()
            if admin is not None:
                admin.close()
        finally:
            if proc is not None:
                teardown(proc, driver=driver)


if __name__ == "__main__":
    raise SystemExit(main())
