#!/usr/bin/env python3
"""OpenTTD native-tool/MCP bridge (the served experiment label is wired per run
via --experiment).

This module keeps the OpenTTD side effect surface small and auditable:

* primitive tools are the legality-only commands in
  ``src/openttd_mcp/squirrel/mcp_bridge/main.nut``;
* factual connectivity/operation tools dispatch to additive Squirrel observation
  commands;
* the optional ``get_bus_route_skill`` skill tool is context-only and never writes a
  bridge command.

The pure Python ``GameToolServer`` is intentionally usable without the ``mcp``
package so unit tests can exercise schemas, dispatch, logging, and the skill
bright line.  The stdio MCP transport is enabled only when ``serve``/default
mode imports ``mcp.server.fastmcp`` (run with ``uv run --with mcp ...``).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, get_args

from openttd_mcp.budget_override import BUDGET_OVERRIDE_FILENAME
from openttd_mcp.feedback import append_feedback, validate_report_feedback
from openttd_mcp.log_tailer import (
    tail_fragment_since as _tail_fragment_since,
)
from openttd_mcp.log_tailer import tail_lines_since as _tail_lines_since
from openttd_mcp.region import (
    DETAIL_ERROR,
    DETAIL_VALUES,
    VALID_TILE_FIELDS,
    WHERE_TILE_FIELDS,
    QueryRegionDetail,
    process_query_region_result,
    validate_query_region_args,
)
from openttd_mcp.tool_names import migrate_retired_tool_names
from openttd_mcp.vision import (
    DEFAULT_REFRESH,
    DEFAULT_WINDOW,
    WINDOW_MAX,
    WINDOW_MIN,
    ScreenshotSource,
)

RESULT_PREFIX = "MCP_RESULT|"
PRIMITIVE_TOOLS = {
    "get_tile",
    "get_region",
    "list_towns",
    "list_my_vehicles",
    "get_vehicle",
    "get_my_finances",
    "build_road",
    "build_one_way_road",
    "build_bridge",
    "build_tunnel",
    "terraform_land",
    "remove_road",
    "build_bus_stop_drive_through",
    "build_truck_stop_drive_through",
    "build_bus_stop_bay",
    "build_truck_stop_bay",
    "build_road_depot",
    "buy_vehicle",
    "clone_vehicle",
    "create_group",
    "move_vehicle_to_group",
    "set_group_autoreplace",
    "insert_order",
    "delete_order",
    "set_order_flags",
    "append_orders",
    "insert_conditional_order",
    "skip_to_order",
    "copy_orders",
    "share_orders",
    "unshare_orders",
    "refit_vehicle",
    "start_vehicle",
    "stop_vehicle",
    "reverse_vehicle",
    "send_vehicle_to_depot",
    "sell_vehicle",
    "set_loan",
    "demolish_tile",
    "wait",
}
QUERY_TOOLS = {
    "list_my_stations",
    "list_my_depots",
    "check_route_connectivity",
    "wait_and_observe_vehicle",
    "wait_and_observe_station",
    "list_engines",
    "list_bridge_types",
    "get_my_company",
    "list_industries",
    "list_subsidies",
    "get_industry",
    "get_town",
    "get_my_station",
    "check_truck_stop_catchment",
    "list_my_route_economics",
    "get_cargo_payment_rate",
    "check_build_road",
    "check_build_road_depot",
}
SKILL_TOOL_NAME = "get_bus_route_skill"
FEEDBACK_TOOL_NAME = "report_feedback"
END_EPISODE_TOOL_NAME = "end_episode"
VIEW_MAP_TOOL_NAME = "view_map"
SUBJECT_SCREENSHOT_TOOLS = frozenset({"get_tile", "get_vehicle", "get_my_station"})
SUBJECT_SCREENSHOT_DESCRIPTION = (
    " Optional screenshot=true returns these JSON facts plus one fresh 384-pixel image requested around "
    "the subject tile reported by this read. At map pixel edges, the same clamping as view_map can move "
    "the actual crop center; rendered_center_tile reports it. It remains one tool call: the subject is read first, and "
    "no image is attempted when that read fails. If the image cannot be captured, the facts remain "
    "available and screenshot reports its own bounded failure. The image is the same unannotated "
    "OpenTTD rendering and crop used by view_map. The game runs in real time: rendering "
    "happens after the factual read, so a moving vehicle can leave the reported subject tile before "
    "the image is drawn."
)
# The admin password is a per-run local secret. It reaches this server only via
# the environment (never on argv), because argv is written verbatim into the
# run's codex_command.json evidence and the password must never land there
# (same discipline as lifecycle_metadata.json's redaction). The launching client
# exports this variable and the MCP client propagates it to this subprocess.
ADMIN_PASSWORD_ENV = "OPENTTD_ADMIN_PASSWORD"
# Neutral end-of-episode signal the model sees once its decisions run out
# (natural exhaustion, end_episode, and monitor override all funnel here). The
# token is deliberately budget-free: "decision_budget_exhausted" leaked that a
# decision budget existed at all, which shaped endgame play. The episode monitor
# and episode-analytics readers key off this same literal to keep post-cut
# refusals out of the failure streak and failure summaries.
EPISODE_STOP_SIGNAL = "stop"
END_EPISODE_MODES = ("standard", "distress")
_CANONICAL_ERROR_RESULT_FIELDS = frozenset(
    {
        "ok",
        "action",
        "error",
        "decisions_used",
        "decisions_remaining",
        "decision_budget",
        "budget_cut",
    }
)
# Single source of truth for the terraform_land enums: the Literal aliases drive
# the FastMCP annotations directly, and the tuples (derived via get_args) feed the
# registry JSON-schema enums and the command_for_tool validators, so the schema,
# validator, and bridge signature cannot advertise a value the others reject
# (END_EPISODE_MODES is the same one-constant-many-consumers pattern).
TerraformMode = Literal["raise", "lower", "level_to"]
TerraformCorner = Literal["w", "s", "e", "n"]
TERRAFORM_MODES: tuple[str, ...] = get_args(TerraformMode)
TERRAFORM_CORNERS: tuple[str, ...] = get_args(TerraformCorner)
StationJoinMode = Literal["new", "adjacent", "station_id"]
STATION_JOIN_MODES: tuple[str, ...] = get_args(StationJoinMode)
CARGO_ID_MIN = 0
CARGO_ID_MAX = 63
CARGO_DISTANCE_MIN = 0
CARGO_DISTANCE_MAX = 4_294_967_295
CARGO_TRANSIT_DAYS_MIN = 0
CARGO_TRANSIT_DAYS_MAX = 163_838
WAIT_DAYS_MIN = 1
WAIT_DAYS_MAX_ENV = "OPENTTD_MCP_WAIT_DAYS_MAX"
WAIT_DAYS_DEFAULT_MAX = 90


def _configured_wait_days_max() -> int:
    """Return the served time-advance ceiling selected by the client.

    Cursor Agent CLI has a hard-coded approximately 60-second MCP tool-call
    timeout. A populated 128 by 128 game needs about two wall-clock seconds per
    game day, so its adapter selects 20 days while clients with configurable
    300-second MCP deadlines retain the established 90-day surface.
    """
    raw = os.environ.get(WAIT_DAYS_MAX_ENV)
    if raw is None:
        return WAIT_DAYS_DEFAULT_MAX
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{WAIT_DAYS_MAX_ENV} must be an integer") from exc
    if not WAIT_DAYS_MIN <= value <= WAIT_DAYS_DEFAULT_MAX:
        raise RuntimeError(
            f"{WAIT_DAYS_MAX_ENV} must be between {WAIT_DAYS_MIN} and "
            f"{WAIT_DAYS_DEFAULT_MAX}"
        )
    return value


WAIT_DAYS_MAX = _configured_wait_days_max()
WAIT_VEHICLE_DEFAULT_DAYS = min(24, WAIT_DAYS_MAX)
WAIT_STATION_DEFAULT_DAYS = min(14, WAIT_DAYS_MAX)
CONDITIONAL_VALUE_MAX = 2047
# Radius bounds are per detail mode because the two modes differ in what the
# model receives, not in what the bridge scans: the bridge emits the same full
# per-tile array either way, and grid mode re-renders it here (region.grid_view).
# tiles is capped at 6 by its own payload (~120KB); grid returns ~1-4KB over the
# same span, so its cap is set by the measured bridge scan cost. Out-of-range
# rejects, same as WAIT_DAYS — neither this layer nor the bridge clamps.
QUERY_REGION_RADIUS_MIN = 0
QUERY_REGION_RADIUS_MAX_TILES = 6
QUERY_REGION_RADIUS_MAX_GRID = 12
QUERY_REGION_RADIUS_MAX_BY_DETAIL: dict[str, int] = {
    "tiles": QUERY_REGION_RADIUS_MAX_TILES,
    "grid": QUERY_REGION_RADIUS_MAX_GRID,
}
ROUTE_CONNECTIVITY_STOP_MAX = 32
# The served annotation carries one bound, so it carries the widest one and the
# per-mode maximum is enforced (and named) in command_for_tool.
QUERY_REGION_RADIUS_MAX = max(QUERY_REGION_RADIUS_MAX_BY_DETAIL.values())

try:  # pydantic ships with the optional `mcp` extra; the base package is dependency-free.
    from pydantic import Field as _PydanticField
except (
    ModuleNotFoundError
):  # pragma: no cover - no mcp extra means no served schema at all
    _PydanticField: Any = None


def _bounded_int(minimum: int, maximum: int | None = None) -> Any:
    """An ``int`` annotation carrying its bounds into the served FastMCP schema.

    FastMCP derives the live tool schema from these wrapper annotations alone,
    so a bare ``int`` drops every minimum/maximum the registry ToolSpec schema
    declares and the served surface silently under-specifies the tool. Bounds
    stated here are validated by pydantic before the wrapper body runs;
    ``command_for_tool`` re-checks them for the non-MCP callers.
    """
    if _PydanticField is None:
        return int
    return Annotated[int, _PydanticField(ge=minimum, le=maximum, strict=True)]


BRIDGE_LENGTH_MIN = 2
QueryRegionRadius = _bounded_int(QUERY_REGION_RADIUS_MIN, QUERY_REGION_RADIUS_MAX)
WaitDays = _bounded_int(WAIT_DAYS_MIN, WAIT_DAYS_MAX)
CargoId = _bounded_int(CARGO_ID_MIN, CARGO_ID_MAX)
CargoDistance = _bounded_int(CARGO_DISTANCE_MIN, CARGO_DISTANCE_MAX)
CargoTransitDays = _bounded_int(CARGO_TRANSIT_DAYS_MIN, CARGO_TRANSIT_DAYS_MAX)
BridgeLength = _bounded_int(BRIDGE_LENGTH_MIN)
NonNegativeInt = _bounded_int(0)
ConditionalOrderValue = _bounded_int(0, CONDITIONAL_VALUE_MAX)
ViewMapWindow = _bounded_int(WINDOW_MIN, WINDOW_MAX)
StationJoinId = _bounded_int(0)
NonEmptyIntList = (
    list[int]
    if _PydanticField is None
    else Annotated[list[int], _PydanticField(min_length=1)]
)
RouteConnectivityStopList = (
    list[int]
    if _PydanticField is None
    else Annotated[
        list[int],
        _PydanticField(min_length=1, max_length=ROUTE_CONNECTIVITY_STOP_MAX),
    ]
)
FeedbackKind = Literal[
    "missing_observation",
    "missing_action",
    "ambiguous_tool_result",
    "strategic_note",
]
# Effect-first and literally true: end_episode pauses nothing. The handler only
# records the cut, so the game keeps running until the owning process takes the
# final save at episode shutdown (openttd-mcp play's PlayOwner.close() saves,
# then quits). An
# earlier wording promised "no further game time passes" and "the game exactly
# as it stands at this call is what the final save records"; both were false.
STANDARD_END_EPISODE_DESCRIPTION = (
    f"End the run: this is irreversible, and all gameplay tools return {EPISODE_STOP_SIGNAL} from then "
    "on. The run's final save is taken when the episode shuts down, and the game goes on running until "
    "that moment, so the saved state can differ slightly from the state at this call. Requires your "
    "reason (recorded as run evidence). report_feedback stays available and your closing POSTMORTEM "
    "message is still expected."
)
DISTRESS_END_EPISODE_DESCRIPTION = (
    f"End the run: this is irreversible, and all gameplay tools return {EPISODE_STOP_SIGNAL} from then "
    "on. The run's final save is taken when the episode shuts down, and the game goes on running until "
    "that moment, so the saved state can differ slightly from the state at this call. This is the only "
    "tool that ends the episode, and it applies when no action available to you would change the game "
    "state. Requires your reason (recorded as run evidence). report_feedback stays available and your "
    "closing POSTMORTEM message is still expected."
)
# Historical default only: invocations without --experiment keep stamping this
# label so older evidence and tests stay consistent. Callers that label their
# runs must pass --experiment explicitly.
DEFAULT_EXPERIMENT = "016-native-tool-skill-library"
# Flag names are the game's own AIOrder.OF_* names minus the OF_ prefix, so a
# name the model already knows from OpenTTD validates as typed. The Literal
# drives the FastMCP-served enum; ORDER_FLAG_BITS must key exactly its values.
OrderFlagName = Literal["FULL_LOAD", "FULL_LOAD_ANY", "NO_LOAD", "NON_STOP"]
ORDER_FLAG_BITS = {
    "FULL_LOAD": 64,  # AIOrder.OF_FULL_LOAD (2 << 5)
    "FULL_LOAD_ANY": 96,  # AIOrder.OF_FULL_LOAD_ANY (3 << 5)
    "NO_LOAD": 128,  # AIOrder.OF_NO_LOAD (1 << 7)
    "NON_STOP": 1,  # AIOrder.OF_NON_STOP_INTERMEDIATE (1 << 0)
}
MUTUALLY_EXCLUSIVE_LOAD_FLAGS = frozenset({"FULL_LOAD", "FULL_LOAD_ANY", "NO_LOAD"})

# OpenTTD 15.1 exposes eight non-sentinel AIOrder.OC_* constants, but its live
# SetOrderCondition precondition rejects OC_MAX_RELIABILITY. OC_INVALID is a
# read sentinel, not a selectable condition. Keep the model-visible enum to the
# seven values the live API can actually set (probed live).
ConditionalOrderCondition = Literal[
    "LOAD_PERCENTAGE",
    "RELIABILITY",
    "MAX_SPEED",
    "AGE",
    "REQUIRES_SERVICE",
    "UNCONDITIONALLY",
    "REMAINING_LIFETIME",
]
ConditionalOrderCompare = Literal[
    "EQUALS",
    "NOT_EQUALS",
    "LESS_THAN",
    "LESS_EQUALS",
    "MORE_THAN",
    "MORE_EQUALS",
    "IS_TRUE",
    "IS_FALSE",
]
CONDITIONAL_ORDER_CONDITIONS: dict[str, int] = {
    name: index for index, name in enumerate(get_args(ConditionalOrderCondition))
}
CONDITIONAL_ORDER_COMPARES: dict[str, int] = {
    name: index for index, name in enumerate(get_args(ConditionalOrderCompare))
}
CONDITIONAL_NUMERIC_COMPARES = frozenset(
    {"EQUALS", "NOT_EQUALS", "LESS_THAN", "LESS_EQUALS", "MORE_THAN", "MORE_EQUALS"}
)
CONDITIONAL_BOOLEAN_COMPARES = frozenset({"IS_TRUE", "IS_FALSE"})
CONDITIONAL_PERCENT_CONDITIONS = frozenset({"LOAD_PERCENTAGE", "RELIABILITY"})
CONDITIONAL_BOOLEAN_CONDITION = "REQUIRES_SERVICE"
CONDITIONAL_UNCONDITIONAL_CONDITION = "UNCONDITIONALLY"


class BridgeError(RuntimeError):
    """Raised when the file/log bridge cannot round-trip a command."""


class EpisodeStopRequested(RuntimeError):
    """Raised when an out-of-band stop interrupts an in-flight bridge call."""


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


# Generic JSONL append; canonical implementation lives in feedback.py because
# the server imports feedback and never the reverse.
append_jsonl = append_feedback

TOKEN_BYTES_HEURISTIC = 4


def payload_bytes(value: Any) -> int:
    """Byte size of a tool result as serialized into the log/transcript."""
    try:
        return len(json.dumps(value, sort_keys=True).encode("utf-8"))
    except (TypeError, ValueError):
        return len(str(value).encode("utf-8"))


def squirrel_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def squirrel_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return f'"{squirrel_string(value)}"'
    raise TypeError(f"Unsupported command literal {value!r}")


def write_command_file(
    bridge_root: Path,
    timestamp: str,
    command: list[Any],
    *,
    emit_map_snapshot: bool = False,
    emit_timelapse_snapshot: bool = False,
    suppress_initial_observation: bool = False,
    lean_snapshot: bool = True,
    ack_result_timestamp: str | None = None,
) -> None:
    command_literal = "[" + ", ".join(squirrel_literal(item) for item in command) + "]"
    # None means a writer that does not track delivery (legacy: the bridge
    # clears attachments on emit). A modern writer always sends the field --
    # empty until its first parsed result -- so field absence alone marks the
    # writer kind and the first dispatch of a session is already ack-protected.
    ack_line = (
        f'    ack_result_timestamp = "{squirrel_string(ack_result_timestamp)}";\n'
        if ack_result_timestamp is not None
        else ""
    )
    content = (
        "class Commands {\n"
        f'    timestamp = "{squirrel_string(timestamp)}";\n'
        f"{ack_line}"
        f"    emit_map_snapshot = {'true' if emit_map_snapshot else 'false'};\n"
        f"    emit_timelapse_snapshot = {'true' if emit_timelapse_snapshot else 'false'};\n"
        "    suppress_initial_observation = "
        f"{'true' if suppress_initial_observation else 'false'};\n"
        f"    lean_snapshot = {'true' if lean_snapshot else 'false'};\n"
        "    commands = [\n"
        f"        {command_literal}\n"
        "    ];\n"
        "}\n"
    )
    target = bridge_root / "commands.nut"
    tmp = bridge_root / "commands.nut.tmp"
    tmp.write_text(content)
    tmp.replace(target)


def parse_result_line(line: str, timestamp: str) -> dict[str, Any]:
    marker = f"{RESULT_PREFIX}{timestamp}|"
    if marker not in line:
        raise BridgeError(
            f"result marker for {timestamp} not found in line: {line[:200]!r}"
        )
    return json.loads(line.split(marker, 1)[1].strip())


# Bounds on the log excerpt a dispatch timeout carries into the model-visible
# error string (the full log stays on disk).
_TIMEOUT_EXCERPT_LINES = 3
_TIMEOUT_EXCERPT_CHARS = 200


@dataclass
class FileLogBridgeDispatcher:
    """Write ``commands.nut`` and read result markers from an OpenTTD log file.

    In live Codex/MCP runs, the controller owns the OpenTTD process and streams
    stdout to ``openttd.log``.  The MCP subprocess cannot access that stdout pipe,
    so it tails the log for ``MCP_RESULT|<timestamp>|...`` after each write.
    """

    bridge_root: Path
    openttd_log: Path
    timeout: float = 240.0
    poll_interval: float = 0.2
    stop_path: Path | None = None
    last_result_timestamp: str | None = None

    def dispatch(self, command: list[Any], timestamp: str) -> dict[str, Any]:
        start_offset = (
            self.openttd_log.stat().st_size if self.openttd_log.exists() else 0
        )
        write_command_file(
            self.bridge_root,
            timestamp,
            command,
            ack_result_timestamp=self.last_result_timestamp or "",
        )
        marker = f"{RESULT_PREFIX}{timestamp}|"
        deadline = time.time() + self.timeout
        offset = start_offset
        last_lines: list[str] = []
        while time.time() < deadline:
            lines, offset = _tail_lines_since(self.openttd_log, offset)
            if lines:
                last_lines = (last_lines + lines)[-20:]
            for line in lines:
                if marker in line:
                    result = parse_result_line(line, timestamp)
                    self.last_result_timestamp = timestamp
                    return result
            # OpenTTD can die mid-line, so the result may be sitting in a tail
            # that never gets its newline.  Peek at it without consuming it and
            # accept it only when the payload is already complete: a marker
            # whose JSON is still truncated stays unparsed here and is matched
            # for real once `_tail_lines_since` delivers the finished line.
            fragment = _tail_fragment_since(self.openttd_log, offset)
            if marker in fragment:
                try:
                    result = parse_result_line(fragment, timestamp)
                    self.last_result_timestamp = timestamp
                    return result
                except (BridgeError, ValueError):
                    pass
            # An exact game-time horizon pauses the simulation before writing
            # this sentinel. A bridge command that sleeps across the horizon
            # can therefore never emit its marker; return through the normal
            # neutral-stop funnel instead of burning the 240-second timeout.
            if self.stop_path is not None and self.stop_path.exists():
                raise EpisodeStopRequested("episode stop requested during bridge call")
            time.sleep(self.poll_interval)
        # This message becomes the model-visible `error` of the timed-out tool
        # result (see GameToolServer._dispatch_wait and the generic exception
        # path), so the log excerpt is bounded: the raw tail can be 20 whole
        # bridge emissions, which would dump tens of KB of bridge internals
        # into the model's context on every timeout. The full log is on disk.
        # An unterminated tail is never consumed by the poll loop, so without
        # this it would be missing from the excerpt exactly when it matters
        # most: a log that stops mid-line is the signature of OpenTTD dying,
        # and that half-line is the last thing it managed to say.
        recent = list(last_lines)
        fragment = _tail_fragment_since(self.openttd_log, offset)
        if fragment:
            recent = (recent + [fragment])[-20:]
        excerpt = [
            line[:_TIMEOUT_EXCERPT_CHARS] for line in recent[-_TIMEOUT_EXCERPT_LINES:]
        ]
        raise TimeoutError(
            f"timed out waiting for {marker} after {self.timeout:.0f}s; "
            f"last {len(excerpt)} of {len(recent)} recent log lines (truncated)={excerpt!r}"
        )


class FunctionBridgeDispatcher:
    """Adapter used by the controller/tests when they already own stdout reading."""

    def __init__(self, fn: Callable[[list[Any], str], dict[str, Any]]):
        self.fn = fn
        self.commands: list[tuple[str, list[Any]]] = []

    def dispatch(self, command: list[Any], timestamp: str) -> dict[str, Any]:
        self.commands.append((timestamp, list(command)))
        return self.fn(command, timestamp)


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    mutates_game: bool = False

    def as_registry_entry(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
            "mutates_game": self.mutates_game,
        }


def object_schema(
    properties: dict[str, Any], required: list[str] | None = None
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def _station_build_schema() -> dict[str, Any]:
    # All road-station builders take identical placement/join arguments; one
    # builder keeps the drive-through and bay/terminus schemas from drifting.
    return object_schema(
        {
            "tile": {"type": "integer"},
            "front_tile": {"type": "integer"},
            "join": {
                "type": "string",
                "enum": list(STATION_JOIN_MODES),
                "default": "new",
            },
            "station_id": {"type": "integer", "minimum": 0},
        },
        ["tile", "front_tile"],
    )


# Shared between the registry (tool_specs) and the FastMCP decorator so the
# model-visible text cannot drift from what the evidence bundle records.
BOUNDED_FACT_ERRORS_DESCRIPTION = (
    " If an unexpected bridge/API failure affects only some returned facts, the enclosing row carries "
    "fact_errors entries with bounded semantic code and field names; a listed fallback value is not a game fact."
)
WHOLE_SENSOR_ERROR_DESCRIPTION = (
    " If an unexpected bridge/API failure prevents this record from being read confidently, the call fails "
    "with error sensor_api_error and a sensor_error object containing bounded semantic code and field names."
)
ACTION_FACT_ERRORS_DESCRIPTION = (
    " If a post-command diagnostic cannot be read, fact_errors names its bounded semantic code and field; "
    "that diagnostic failure does not change the command's own ok or error result."
)
INVALID_CARGO_DESCRIPTION = (
    " A numeric cargo slot unavailable in the current game returns invalid_cargo_id before any cargo facts or "
    "mutation are attempted."
)
REGION_FACT_ERRORS_DESCRIPTION = (
    " Unexpected per-tile API failures keep stable placeholder types but qualify them with bounded semantic "
    "errors: tiles mode preserves each row's fact_errors through fields projection, where_fact_error_tiles "
    "names rows a where filter could not classify, and grid.fact_error_tiles names category inputs that were "
    "unavailable. Each entry carries only code and field, never raw exception text."
)

QUERY_TILE_DESCRIPTION = (
    "Read the full per-tile facts for one tile id: the same per-tile record get_region returns "
    "with detail='tiles'. Optional cargo_id adds cargo_id/cargo_label plus "
    "cargo_acceptance, cargo_production, cargo_acceptance_radius4, and cargo_production_radius4 "
    "measured for that cargo; omitting cargo_id returns only the default passenger fields. "
    "cargo_id is the integer cargo type reported as the "
    "cargo_type field by list_engines and by list_industries/get_industry produced_cargo/"
    "accepted_cargo rows (same integer, different field name). Plain road tiles additionally report per-direction "
    'road_bits facts (true / false / "unknown"), distinct from road_connections: a one-sided '
    "half-road stub toward a roadless neighbour is road_bits true while road_connections stays false. "
    '"unknown" means road material exists on the shared edge but the game cannot attribute the side. '
    "industry_id identifies the industry occupying the tile anywhere on its footprint; a tile no industry "
    "occupies reports industry_id -1. The station tile of an industry's own dock or heliport reports no industry."
    + INVALID_CARGO_DESCRIPTION
    + BOUNDED_FACT_ERRORS_DESCRIPTION
)
QUERY_REGION_DESCRIPTION = (
    "Survey a square region around a center tile: detail='grid' returns a one-character-per-tile "
    "category map of the whole region (about 1.5-2.7KB), and detail='tiles' (the default) returns the full "
    "per-tile facts (about 120KB at radius 6). "
    f"radius starts at {QUERY_REGION_RADIUS_MIN} and its maximum depends on the mode: "
    f"{QUERY_REGION_RADIUS_MAX_GRID} with detail='grid' (a {2 * QUERY_REGION_RADIUS_MAX_GRID + 1}x"
    f"{2 * QUERY_REGION_RADIUS_MAX_GRID + 1} region) and {QUERY_REGION_RADIUS_MAX_TILES} with detail='tiles' "
    f"(a {2 * QUERY_REGION_RADIUS_MAX_TILES + 1}x{2 * QUERY_REGION_RADIUS_MAX_TILES + 1} region), because "
    "tiles carries every per-tile record while grid carries one character per tile. A radius above the "
    "mode's maximum is rejected and the error names that maximum; nothing is silently reduced. The region "
    "is cropped at the map edge. "
    "In grid mode each row reports its y, a row_start_tile, and a cells string, with "
    "tile = row_start_tile + (x - x_min); the result carries its own legend and the "
    "category_precedence that decides which single category a tile in several of them shows. "
    "In tiles mode, where=[...] keeps only the tiles whose named fields are all true and fields=[...] "
    "keeps only the named fields plus tile/x/y; the filter runs before the projection, so a where name "
    "need not also be listed in fields. "
    "fields and where draw on two different name lists and are not interchangeable: fields accepts any "
    "name under 'Valid fields' below, while where accepts only the boolean category names under "
    "'Valid where filters', a strict subset of that list. Measurement and identifier fields such as "
    "cargo_acceptance, passenger_production, industry_id, or slope can be selected with fields but are "
    "rejected as where filters, and the rejection names every filter where does accept. "
    "fields, where, and cargo_id are rejected with detail='grid'. "
    "Optional cargo_id adds cargo_id/cargo_label/cargo_acceptance/cargo_production and the radius4 cargo "
    "fields for that cargo on top of the passenger_* fields every tile already carries; naming a cargo_* "
    "field in fields without cargo_id is rejected rather than silently returning nothing. cargo_id is the "
    "integer cargo type reported as the cargo_type field by list_engines and by list_industries/"
    "get_industry produced_cargo/accepted_cargo rows (same integer, different field name). "
    "passenger_acceptance and passenger_production are the raw passenger-cargo acceptance and production "
    "units a single-tile bus-stop catchment at that tile collects. A station accepts a cargo when the "
    "acceptance summed over its catchment is at least 8 units, and the game recomputes that sum "
    "periodically from the surrounding tiles, so a measurement near 8 units can start and stop being "
    "accepted over time as town buildings appear and disappear. "
    "cargo_acceptance and cargo_production are the same measurement over a single-tile truck-stop "
    "catchment, and the *_radius4 variants measure a fixed radius-4 catchment instead. "
    "get_tile reports these same per-tile facts for one tile id. "
    "Valid fields: " + ", ".join(sorted(VALID_TILE_FIELDS)) + ". "
    "Valid where filters: "
    + ", ".join(sorted(WHERE_TILE_FIELDS))
    + "."
    + INVALID_CARGO_DESCRIPTION
    + REGION_FACT_ERRORS_DESCRIPTION
)
BUILD_ROAD_DESCRIPTION = (
    "Build road between from_tile and to_tile, optionally via waypoint tiles; "
    "the path is built as straight spans in order from_tile -> waypoints... -> to_tile. "
    "Failures report the game's own error name plus where it failed: the failing segment "
    "(failed_segment_from/failed_segment_to), the exact straight span attempted "
    "(failed_span_from/failed_span_to), and first_blocked_tile (+x/y) — the first tile at which a "
    "test-mode replay of the failing span stops succeeding (-1 when the failure does not reproduce "
    "in test mode, e.g. a vehicle transiently in the way)."
)
BUILD_ONE_WAY_ROAD_DESCRIPTION = (
    "Build road that permits travel only from from_tile toward to_tile, as one "
    "straight span along a single axis: the endpoints must share a row or column, "
    "there are no waypoints and no corner fallback, so each straight segment is its "
    "own call and the call succeeds or fails as a whole. On empty land it lays road "
    "and marking together at plain road cost. The marking sits only on full straight "
    "road pieces; the two endpoint tiles receive half pieces that cannot carry it, "
    "so a fresh span's from_tile boundary still reads connected in both directions, "
    "and side roads join legally at those unmarked endpoint tiles while joining a "
    "marked tile is refused. Chaining collinear calls that share an anchor tile "
    "merges the anchor's half pieces into a marked full piece, so a chained route is "
    "seamless. Over existing straight road whose covered tiles are all full pieces "
    "the call costs nothing and toggles marking state per direction: repeating the "
    "same call restores two-way, and calling against an existing opposite marking "
    "blocks travel in both directions rather than flipping it, so a flip is two "
    "calls — the same-direction call to clear, then the reversed call — or "
    "remove_road followed by a reversed rebuild. When any covered tile cannot carry "
    "the marking (a junction tile, or a half-piece tile such as this tool's own "
    "endpoints), the whole call fails with "
    "ERR_ROAD_ONE_WAY_ROADS_CANNOT_HAVE_JUNCTIONS, the game's one generic one-way "
    "conflict error, raised for these non-junction conflicts too. Results report "
    "real cost, endpoints_connected_forward and endpoints_connected_reverse "
    "(directional road-graph reachability from_tile->to_tile and "
    "to_tile->from_tile)."
)
ESTIMATE_ROAD_BUILD_DESCRIPTION = (
    "Answer whether a road path would build and what it would cost, without laying any road. "
    "Read-only dry-run of build_road for the same from_tile -> waypoints... -> "
    "to_tile path semantics. Reports buildable, total_estimated_cost, per-segment/spans attempted, and on "
    "blockage the failing segment/span plus first_blocker_localized, first_blocker_tile and "
    "blocker_error_code/blocker_error_name. first_blocker_localized is true exactly when a test-mode replay of "
    "the failing span stopped succeeding at one tile. first_blocker_tile (+x/y) is then that replay's last tile "
    "— the span cannot be built that far, and the obstacle is that tile or the one before it — except when the "
    "first replayed pair is the failing one and failed_span_from is not buildable and is not classified as a "
    "road tile, a road station tile or a road depot tile, where the blockage is attributed to failed_span_from. "
    "blocker_error_code/blocker_error_name are the game's own error for that replay. When it is false the replay did not "
    'reproduce the failure, and first_blocker_tile=-1, blocker_error_code=-1 and blocker_error_name="" are '
    "stable placeholders, not game-reported values. Uses "
    "OpenTTD test mode: it builds nothing, spends no money, and advances no game time."
)
ESTIMATE_DEPOT_BUILD_DESCRIPTION = (
    "Answer whether the depot-construction phase would accept tile/front_tile and what that one "
    "phase would cost, without building it. front_tile is the same distinct, aligned direction "
    "selector accepted by build_road_depot, but OpenTTD test mode does not materialize the depot, "
    "so this sensor cannot report the resulting adjacent actual_front_tile or preview the automatic "
    "connector's legality or cost. "
    "Reports buildable, estimated_cost, the game's own error_code/error_name when not buildable, front_is_road, "
    "front_road_bits_applicable (true exactly when front_tile is a plain road tile to which the directional-bit "
    "sensor applies), front_road_connected, and front_road_bits. When front_road_bits_applicable is true, "
    "front_road_connected=true positively observes a road bit facing the depot tile; false means the bit was "
    'not positively observed, so consult that direction in front_road_bits, which can be "unknown" when the '
    "game cannot attribute a half-road to one side. When applicability is false, front_road_connected=false and the all-false "
    "front_road_bits object are stable compatibility placeholders, not observed topology facts. These front "
    "facts describe the supplied selector's current tile, not a hypothetical physical entrance. Uses OpenTTD "
    "test mode: it builds nothing, spends no money, and advances no game time."
    + BOUNDED_FACT_ERRORS_DESCRIPTION
)
LIST_BRIDGE_TYPES_DESCRIPTION = (
    "List the road bridge types that can span the given length, each with its bridge_id, max_speed, "
    "price, and length limits. A type whose own min_length/max_length limits do not cover the "
    "requested length is absent from the list. "
    "length is the total number of tiles the bridge occupies, which equals "
    "the Manhattan distance between the two bridge-head tiles + 1 (both ramps included) — the same "
    "length build_bridge derives and reports for that start/end pair, so query the length you will "
    "actually build."
)
BUILD_BRIDGE_DESCRIPTION = (
    "Build a road bridge between start_tile and end_tile bridge-head tiles using a "
    "bridge_id from list_bridge_types. The bridge length is the Manhattan distance between "
    "start_tile and end_tile + 1 (both ramps included); pass that same length to list_bridge_types "
    "so the chosen bridge_id's max_length covers this span. For a road bridge the game also tries "
    "to lay a road stub on the tile just outside each bridge head facing the bridge; joining each "
    "head to the rest of your road network is a separate build. The result reports cost, bank_balance, "
    "factual start/end bridge observations, other_end_tile, and the derived length."
)
BUILD_TUNNEL_DESCRIPTION = (
    "Dig a road tunnel under the hill at start_tile; the exit tile cannot be chosen, because the "
    "game derives it from the start tile's slope, running the tunnel in the direction that slope "
    "rises and surfacing at the first tile whose ground is back at the start tile's height. "
    "The start tile must carry a simple inclined slope rising toward exactly one of north, south, "
    "east or west; anything else is rejected as ERR_TUNNEL_START_SITE_UNSUITABLE, and a tunnel "
    "whose exit cannot be excavated, that meets another tunnel, or that would run off the map "
    "fails with the game's corresponding error. Cost grows with the length the game derives. "
    "For a road tunnel the game also tries to lay a road stub on the tile just outside each mouth "
    "facing the tunnel, and the tunnel still counts as built when those stubs fail; joining each "
    "mouth to the rest of your road network is a separate build. The result reports the derived "
    "exit as other_end_tile, the exit predicted from the pre-build slope as other_end_tile_before, "
    "slope_before, and whether each mouth is a tunnel tile afterwards; ok is true only when the "
    "command succeeded and both mouths were observed."
)
TERRAFORM_LAND_DESCRIPTION = (
    "Reshape terrain: mode raise and lower move the named corners of one tile (w/s/e/n are that "
    "tile's west, south, east and north corners) up or down by one height step, while mode "
    "level_to flattens whole tiles, levelling every tile in the rectangle spanned by tile and "
    "to_tile to a single height. Several corners can be named in one raise/lower call; the game "
    "applies them in the order west, south, east, north, and moving one corner can pull an "
    "adjacent corner with it, so a tile can end up changed by more than one step. level_to levels "
    "its rectangle to the height of tile's north corner, raising some tiles and lowering others as "
    "needed, and can level part of the rectangle while failing on the rest. raise and lower "
    "require at least one corner and reject to_tile; level_to requires to_tile and rejects "
    "corners. Reports real cost and the game's own error name on failure (land that is not clear, "
    "or a corner already at the game's height limit, is refused) plus slope_before/slope_after "
    "and height_before/height_after min and max for tile, so the terrain change that actually "
    "happened is visible."
)
# Shared join-mechanics facts and ERR_UNKNOWN tail for the two road-stop build
# tools: the game rules are identical for bus and truck stops, so the wording is
# kept in one place and cannot drift between the descriptions.
STATION_JOIN_FACTS = (
    'The default join:"new" creates a separate new station with its own station_id and '
    "per-station cargo pool; it does not join an existing station even on an adjacent tile. "
    'join:"adjacent" uses the game\'s adjacent join mode: when exactly one own station has tiles '
    "adjoining the new stop's area expanded by 1 tile, the stop joins it; when no own station "
    "adjoins, the game builds a new station; when more than one distinct own station adjoins, "
    'OpenTTD 15.1 rejects the build and reports ERR_UNKNOWN. join:"station_id" joins the '
    "supplied existing station id regardless of distance; it requires the distant_join_stations "
    "game setting (on by default), and the joined station's bounding rectangle is capped by the "
    "station_spread setting (default 12), with excess spread failing as ERR_STATION_TOO_SPREAD_OUT. "
    "Joined stops share one station_id and one per-station cargo pool, and a road stop may join a "
    "company-owned station of a different facility type. "
)
# Shared placement geometry for the two drive-through builders: bus and truck
# stops take the same tile/front_tile pair, so the wording is kept in one place
# and cannot drift between the two descriptions.
DRIVE_THROUGH_FRONT_TILE_FACTS = (
    "front_tile picks the axis the stop is driven along: it must differ from tile and share its row "
    "or column, and the stop tile carries road along that line only, open at both ends. "
)
STATION_ERR_UNKNOWN_FACTS = (
    "Failures report the game's own error name; OpenTTD 15.1 reports corner/junction "
    "rejections (road bits off the station axis) as the literal ERR_UNKNOWN, and the result then "
    "also carries the tile's factual per-direction tile_road_bits."
)
BUILD_BUS_STATION_DESCRIPTION = (
    "Build a drive-through bus stop on tile: vehicles drive straight through it instead of pulling "
    "into a dead end. "
    + DRIVE_THROUGH_FRONT_TILE_FACTS
    + STATION_JOIN_FACTS
    + "The result reports station_id, join, and drive_through; get_my_station reads a station's "
    "cargo state by station_id or tile. "
    + STATION_ERR_UNKNOWN_FACTS
    + ACTION_FACT_ERRORS_DESCRIPTION
)
BUILD_TRUCK_STATION_DESCRIPTION = (
    "Build a drive-through truck stop on tile: vehicles drive straight through it instead of pulling "
    "into a dead end. "
    "This is not a bus stop; it is the freight road-station counterpart to build_bus_stop_drive_through. "
    + DRIVE_THROUGH_FRONT_TILE_FACTS
    + STATION_JOIN_FACTS
    + "Result fields include station_id, join, drive_through, and is_truck_stop. "
    + STATION_ERR_UNKNOWN_FACTS
    + ACTION_FACT_ERRORS_DESCRIPTION
)
BUILD_BUS_BAY_STATION_DESCRIPTION = (
    "Build a one-tile bay bus station on tile: a dead end whose only road opening is on the "
    "front_tile side. A vehicle both enters and leaves through that one side, unlike the "
    "drive-through build_bus_stop_drive_through, which vehicles pass through. front_tile is a "
    "direction selector: it must differ from tile and share its row or column; the station entrance "
    "faces the adjacent side in that direction. "
    + STATION_JOIN_FACTS
    + "Result fields include station_id, join, drive_through=false, bay_station, and is_bus_stop."
    + ACTION_FACT_ERRORS_DESCRIPTION
)
BUILD_TRUCK_BAY_STATION_DESCRIPTION = (
    "Build a one-tile bay truck station on tile: a dead end whose only road opening is on the "
    "front_tile side. A vehicle both enters and leaves through that one side, unlike the "
    "drive-through build_truck_stop_drive_through, which vehicles pass through. front_tile is a "
    "direction selector: it must differ from tile and share its row or column; the station entrance "
    "faces the adjacent side in that direction. "
    + STATION_JOIN_FACTS
    + "Result fields include station_id, join, drive_through=false, bay_station, and is_truck_stop."
    + ACTION_FACT_ERRORS_DESCRIPTION
)
BUILD_ROAD_DEPOT_DESCRIPTION = (
    "Build a road depot at tile, facing toward front_tile, and leave its physical entrance "
    "connected in both directions. front_tile is a direction selector: it must differ from tile "
    "and share its row or column, but it need not be adjacent. After the depot builds, the game-selected "
    "adjacent entrance is read as actual_front_tile; if either direction is disconnected, the action "
    "issues one road-edge build between tile and actual_front_tile. Road vehicles use depots for purchase, "
    "servicing, and autoreplacement: buy_vehicle and clone_vehicle build a vehicle inside a depot "
    "tile, and a vehicle the game finds due for service, or carrying a pending autoreplacement, is "
    "sent to the nearest depot its road network reaches, and is not sent at all when no reachable "
    "depot is within the game's maximum go-to-depot path limit. A vehicle visiting for service "
    "leaves its orders temporarily, enters the depot, and then resumes its route where it left "
    "off; an autoreplacement is likewise carried out on a vehicle standing inside a depot. "
    "Results retain front_tile as the input selector and report actual_front_tile, the final observed "
    "is_road_depot, front_is_road, and bidirectional front_road_connected facts, whether the connector "
    "was attempted and succeeded, and the combined real cost. If the connector fails, ok is false but "
    "the paid depot is retained: game_mutated and the observed fields make that partial mutation explicit. "
    "If reading a present depot's physical entrance fails or returns an invalid tile, the action returns "
    "ok=false: actual_front_tile=-1 with a bounded fact_errors entry means that entrance was unavailable; "
    "actual_front_tile=-1 with is_road_depot=false and no fact_errors means no depot resulted. "
    "is_road_depot=true with game_mutated=false can identify a pre-existing depot after this build attempt "
    "was refused. A refused depot command never attempts the automatic connector, even when a pre-existing "
    "depot is observed. This is only the local depot-front fact; use check_route_connectivity for network-level "
    "reachability."
)
CLONE_VEHICLE_DESCRIPTION = (
    "Buy a copy of an existing vehicle in a depot: same engine, same cargo, that vehicle's order "
    "list, and that vehicle's group. buy_vehicle, by contrast, builds a new vehicle from an "
    "engine_id alone, with no orders. "
    "share_orders=false gives the clone its own copied order list; "
    "share_orders=true makes the source and clone share one order list, so later append_orders appends "
    "on either sharer change the order list for both. The result returns the new clone as vehicle_id "
    "and the copied vehicle as source_vehicle_id. The clone is built stopped in the depot and is not "
    "auto-started; call start_vehicle when you want it running."
)
GET_FINANCES_DESCRIPTION = (
    "Report your company's money right now: bank balance, loan, maximum loan and loan step, plus income "
    "and expenses for the quarter in progress and the one before it. "
    "interest_rate_percent is the game's configured loan interest rate, the same integer percent the "
    "game's finance window shows; a normally generated game charges loan interest at this yearly rate "
    "on the loan and on a negative bank balance. interest_rate_percent is -1 only if the game "
    "reports no such setting. "
    "loan_amount must be a multiple of loan_interval and no more than max_loan_amount; set_loan rejects "
    "any other amount. quarterly_income_0/quarterly_expenses_0 cover the quarter in progress and "
    "quarterly_income_1/quarterly_expenses_1 the quarter before it. Those quarterly figures count only "
    "recurring money — vehicle income on one side, vehicle running costs, maintenance and loan interest "
    "on the other — so construction spending, vehicle purchases and vehicle sales are absent from them "
    "and a quarter can show income above expenses while the bank balance falls. Expense figures are "
    "reported as negative numbers. "
    "Also reports the current in-game date. get_my_company returns these same balance and loan facts "
    "alongside every quarter the game still keeps, per-quarter cargo delivered, performance rating and "
    "company value, and your vehicle counts. Read-only."
)
GET_COMPANY_REPORT_DESCRIPTION = (
    "Report your company's quarter-by-quarter history: income, expenses, cargo delivered, performance "
    "rating and company value for every quarter the game still keeps, plus company identity, vehicle "
    "counts, and the same balance and loan facts get_my_finances reports. "
    "quarters[] runs from quarter 0 (current_quarter, the quarter in progress, whose income, expenses and "
    "cargo_delivered are partial) back to earliest_quarter, the oldest quarter OpenTTD retains. The "
    "quarter 0 row reports performance_rating -1 because the game computes a rating only when a quarter "
    "closes, so the newest real rating is in quarter 1; quarters[0].company_value is instead the live "
    "company value, while older rows hold the value recorded when that quarter closed. "
    "Quarterly income and expenses count only recurring money — vehicle income on one side, vehicle "
    "running costs, maintenance and loan interest on the other — so construction spending, vehicle "
    "purchases and vehicle sales are absent from them. "
    "Also reports company_id and company_name, vehicle_count (every vehicle you own) with "
    "road_vehicle_count (the road subset), bank_balance, loan_amount, max_loan_amount, loan_interval, "
    "and the current in-game date. Read-only."
)
BUY_VEHICLE_DESCRIPTION = (
    "Buy a road vehicle in a depot; the vehicle type follows the chosen engine_id, so this "
    "buys either a bus or a freight truck depending on the engine. It is the only vehicle-purchase "
    "path. Engine ids come from list_engines, whose is_passenger flag "
    "and cargo fields distinguish bus from truck engines."
)
REFIT_VEHICLE_DESCRIPTION = (
    "Refit an existing vehicle to a new cargo_id while it is stopped in a depot. Anywhere else the "
    "game rejects the command and the result reports the game's own error name. "
    "cargo_id is the integer cargo type reported as the cargo_type field by list_engines (and by "
    "list_industries/get_industry produced_cargo/accepted_cargo rows) — same integer, different field name. "
    "Result facts include engine_id, engine_valid, can_refit, stopped_in_depot, and "
    "capacity_before/capacity_after. "
    "Both capacities are measured FOR the target cargo_id before "
    "and after the refit, so capacity_before is typically 0 unless the vehicle already carries cargo_id; it is "
    "not the vehicle's current carrying capacity in its present cargo."
    + INVALID_CARGO_DESCRIPTION
    + ACTION_FACT_ERRORS_DESCRIPTION
)
INSERT_ORDER_DESCRIPTION = (
    "Insert one station order into a vehicle's order list before a 0-based position, leaving the "
    "rest of the list in place; a position equal to the current order_count inserts at the end, so "
    "this also seeds the first order of an empty list. If the vehicle shares its order list, the "
    "insert changes every vehicle sharing it. Optional flags is a list of flag names: FULL_LOAD "
    "(wait until the vehicle is fully loaded), FULL_LOAD_ANY (wait until at least one cargo type is "
    "fully loaded), NO_LOAD (load no cargo), NON_STOP (do not stop at stations passed on the way to "
    "this order's station). FULL_LOAD, FULL_LOAD_ANY, and NO_LOAD are mutually exclusive; any other "
    "name, a duplicate, or two of those three in one list is rejected without touching the vehicle. "
    "An order's destination is the station that owns stop_tile, never the tile itself: the game "
    "stores only the station, so two tiles of one station create two orders to the same station. "
    "The result echoes order_count, order_stations — the station_id each order resolved to, -1 for "
    "a position with no station destination — order_destinations, and order_flags — the flags of every order "
    "after the command, as the game's integer bitmask values rather than names. Each entry of "
    "order_destinations is one representative tile of that order's station, re-computed by the game "
    "on every read; it can differ from the tile the order was created with."
)
DELETE_ORDER_DESCRIPTION = (
    "Delete one order at a 0-based order position, leaving the rest of the vehicle's order list in "
    "place. Only that one order is removed, and only on the named vehicle/position; shared orders "
    "mutate every vehicle sharing that order list. The result echoes order_count, order_stations "
    "(the station_id each remaining order resolved to, -1 for a position with no station "
    "destination), and order_destinations after the command; each entry of order_destinations is "
    "one representative tile of that order's station, re-computed by the game on every read."
)
SET_ORDER_FLAGS_DESCRIPTION = (
    "Set the loading and stopping behaviour of one station order a vehicle already has: FULL_LOAD, "
    "FULL_LOAD_ANY, NO_LOAD, NON_STOP. The order is picked by 0-based position and which station it "
    "goes to does not change. flags is a list of flag names: FULL_LOAD "
    "(wait until the vehicle is fully loaded), FULL_LOAD_ANY (wait until at least one cargo type is "
    "fully loaded), NO_LOAD (load no cargo), NON_STOP (do not stop at stations passed on the way to "
    "this order's station). The list replaces the order's flags rather than adding to them, so an "
    "empty list clears them; FULL_LOAD, FULL_LOAD_ANY, and NO_LOAD are mutually exclusive, and any "
    "other name, a duplicate, or two of those three in one list is rejected without touching the "
    "vehicle. The result echoes the flags of every order after the command as the game's integer "
    "bitmask values rather than names, plus order_stations (the station_id each order resolved to, "
    "-1 for a position with no station destination) and order_destinations (one representative tile "
    "of each order's station, re-computed by the game on every read)."
)
APPEND_ORDERS_DESCRIPTION = (
    "Append one station order per stop tile to the end of a vehicle's order list, keeping any "
    "orders it already has. Optional flags is either one list of flag names applied to every "
    "appended order, or a list of one such list per stop tile, in the same order as stop_tiles. "
    "The flag names are FULL_LOAD (wait until the vehicle is fully loaded), FULL_LOAD_ANY (wait "
    "until at least one cargo type is fully loaded), NO_LOAD (load no cargo), and NON_STOP (do not "
    "stop at stations passed on the way to that order's station); FULL_LOAD, FULL_LOAD_ANY, and "
    "NO_LOAD are mutually exclusive. set_order_flags changes the flags of an order that already "
    "exists, and insert_order adds one order at a chosen position. "
    "An order's destination is the station that owns the stop tile, never the tile itself: the game "
    "stores only the station, so two tiles of one station create two orders to the same station, "
    "and the vehicle picks whichever of the station's compatible stops to use as it drives. "
    "The result echoes orders_appended, order_count, order_stations — the station_id each order "
    "resolved to, -1 for a position with no station destination — order_destinations, and "
    "order_flags. Each entry of order_destinations is one representative tile of that order's "
    "station, re-computed by the game on every read; it can differ from the stop_tiles entry the "
    "order was created with and need not be a stop this vehicle's type can use."
)
INSERT_CONDITIONAL_ORDER_DESCRIPTION = (
    "Insert one fully configured conditional order before a 0-based position; position equal to the "
    "current order_count appends it. jump_to names a position in the resulting list after insertion, "
    "including the new conditional's own position; the list must already contain at least one order. "
    "The condition tests this vehicle when execution reaches the conditional and jumps to that "
    "position when the comparison is true. The tool creates exactly one branch. The seven "
    "conditions the OpenTTD 15.1 AI API can set are LOAD_PERCENTAGE and "
    "RELIABILITY (percentage value 0 through 100), MAX_SPEED (value 0 through 2047 in the same "
    "internal speed unit as list_engines max_speed), AGE and REMAINING_LIFETIME (calendar-year "
    "value 0 through 2047), REQUIRES_SERVICE (compare IS_TRUE or IS_FALSE and value 0), and UNCONDITIONALLY "
    "(compare EQUALS and value 0). The numeric conditions accept EQUALS, NOT_EQUALS, LESS_THAN, "
    "LESS_EQUALS, MORE_THAN, and MORE_EQUALS. OpenTTD converts a MAX_SPEED value internally, so the "
    "read-back value can be one lower than requested. MAX_RELIABILITY is absent because the live API exposes its constant "
    "but rejects it in SetOrderCondition; INVALID is only a read sentinel. A conditional order has "
    "no station destination, so its entries in order_stations and order_destinations are both -1. "
    "The result reports requested_compare_value beside the actual condition, compare, compare_value, "
    "and jump_to read back from the new order, plus the complete orders array, order_count, "
    "order_stations, and order_destinations."
)
SKIP_TO_ORDER_DESCRIPTION = (
    "Make a vehicle begin with the order at a 0-based position instead of its current "
    "order. This changes only the current execution position; it does not move, add, remove, or "
    "rewrite any order. The result reports previous_position and current_position plus the complete "
    "orders array, order_count, order_stations, and order_destinations."
)
COPY_ORDERS_DESCRIPTION = (
    "Replace one vehicle's entire order list with an independent copy of another vehicle's list. "
    "Later edits through either vehicle do not affect the other. This is distinct from clone_vehicle "
    "with share_orders=false because it operates on an existing vehicle, and from unshare_orders "
    "because OpenTTD empties the departing vehicle's list when unsharing. The game requires compatible "
    "vehicle types and destinations and reports its own error when they are incompatible. The result "
    "reports both vehicles' order counts, orders arrays, order_stations, order_destinations, and "
    "has_shared_orders facts after the command."
)
SHARE_ORDERS_DESCRIPTION = (
    "Replace one vehicle's entire order list with the source vehicle's shared order list. Later order "
    "edits through either sharer change the same list for every sharing vehicle. The game requires "
    "compatible vehicle types and destinations and reports its own error when they are incompatible. "
    "The result reports both vehicles' order counts, orders arrays, order_stations, "
    "order_destinations, and has_shared_orders facts after the command."
)
UNSHARE_ORDERS_DESCRIPTION = (
    "Remove one vehicle from a shared order list. OpenTTD leaves that vehicle with an empty order list; "
    "it does not retain a private copy, while the remaining sharers keep the shared list. Use "
    "copy_orders instead when the destination vehicle must retain an independent copy. The result "
    "reports the vehicle's empty orders array, order_count, order_stations, order_destinations, and "
    "has_shared_orders fact after the command."
)
LIST_TOWNS_DESCRIPTION = (
    "List every town on the map with its town_id, name, population, and the x/y of its location "
    "tile. The location tile is reported as x/y only, not as a tile id; tile id = y * map width + x."
)
QUERY_TOWN_DESCRIPTION = (
    "Read one town's cargo statistics by town_id: what its houses produced last month, how much of "
    "that reached station waiting queues, and what it received toward growth — where list_towns only "
    "enumerates every town's id, name, population, and location. "
    "The envelope repeats that town's town_id, name, population, and x/y with the same meanings "
    "list_towns gives them. cargo_stats carries one row per cargo type, with cargo_type, "
    "cargo_label, last_month_production (units the town's houses produced), last_month_to_stations "
    "(how much of that production was moved into the waiting queues of stations in range — cargo "
    "entering a queue, not cargo loaded onto a vehicle and not cargo delivered anywhere), and "
    "last_month_transported_percentage (0-100, the game's own percentage of that month's production "
    "counted under last_month_to_stations). Rows whose three figures are all zero are left out, so a "
    "cargo the town has no record for is absent rather than listed. growth_cargo carries one row per "
    "town effect the cargoes in this game map to, with towneffect_id, towneffect_name (passengers, "
    "mail, goods, water, food), last_month_received (units of that effect's cargo the town accepted) "
    "and cargo_goal (units per month of that effect the game requires for the town to grow); several "
    "cargo types can share one town effect, and cargoes with no town effect are not listed. Every "
    "last_month_* figure covers the last fully-closed economy month and never the current partial "
    "month, a different period than a station or vehicle reading taken now. A town founded or "
    "expanded within the last month can report a seeded population-based estimate for passenger and "
    "mail production rather than a completed month's count. Return values are passed through as the "
    "game reports them: -1 means the game reports nothing for that town or cargo, 0 from a valid town "
    "means the town holds no record in that slot, and the two are not merged. A row carries "
    "metric_error when a figure could not be read at all. An unknown or stale town_id comes back as "
    "invalid_town_id."
    + BOUNDED_FACT_ERRORS_DESCRIPTION
    + WHOLE_SENSOR_ERROR_DESCRIPTION
)
LIST_MY_VEHICLES_DESCRIPTION = (
    "List every road vehicle your company owns, with its current state, age, and group. Each vehicle "
    "row carries the game's raw vehicle-state integer as state plus a stable state_name enum string "
    "(running, stopped, in_depot, at_station, broken, crashed, invalid/unknown), the in_depot and "
    "stopped_in_depot booleans, running_cost, has_shared_orders, "
    "age_days/max_age_days/age_left_days/reliability, the derived needs_replacement flag with the "
    "age_left threshold it uses, and group_id/group_name. When has_shared_orders is true, an order "
    "edit made through any vehicle sharing that order list applies to every vehicle sharing it. The routes "
    "array restates those same vehicles by the station stops their orders visit (station_ids and "
    "station_tiles) with per-vehicle profit this year and last year; the groups array reports your "
    "road-vehicle groups and their autoreplace rules. Stations appear here only as the stops a "
    "vehicle's orders point at — list_my_stations lists the stations your company owns."
)
GET_VEHICLE_DESCRIPTION = (
    "Read one road-class vehicle (bus, truck, or tram) your company owns by vehicle_id, "
    "without advancing game time. "
    "The result carries a vehicle object with the vehicle row fields from list_my_vehicles, plus "
    "top-level detailed orders and cargo_capacities arrays. The vehicle row fields include identity and "
    "engine, current tile/x/y, "
    "raw state and state_name, group, speed, depot state, current-order facts, profit, current value, "
    "running_cost, has_shared_orders, age, reliability, and the derived needs_replacement flag. When "
    "has_shared_orders is true, an order edit made through any vehicle sharing that order list applies "
    "to every vehicle sharing it. The orders array carries one object per 0-based order position. Every "
    "object carries position, is_conditional, and is_void. A conditional position also carries "
    "condition and compare names with their condition_code and compare_code OpenTTD values, plus "
    "compare_value and jump_to. A position that is neither conditional nor void also carries "
    "is_goto_station, is_goto_depot, destination, flags, and station_id when the destination maps to "
    "a valid station. Void positions carry only position and the two booleans, so they omit "
    "destination and flags. A station order stores only the station, so "
    "station_id is its stable identity; destination is one representative tile of that station, "
    "re-computed by the game on every read, and on a station with several stops it need not be the "
    "tile the order was created with nor a stop this vehicle's type uses. A destination of -1 means the game exposes no "
    "concrete tile for that order: the ordered station or depot no longer exists, or the order is a "
    "nearest-depot order. The vehicle row's cargo_type and cargo_load describe the engine's "
    "default cargo and the load of that cargo. cargo_capacities reports the vehicle's actual per-cargo "
    "capacity and load as cargo_type, cargo_label, capacity, and load, omitting cargo types with zero "
    "capacity. "
    "current_order_index resolves the manual order-list position, while "
    "current_order_destination reports the active order's destination when OpenTTD exposes "
    "a concrete tile; an automatic or manual depot trip can therefore make them refer to "
    "different orders, and a nearest-depot order reports -1. The result also "
    "reports the age-left threshold used by needs_replacement. An unknown, stale, other-company, "
    "or non-road vehicle_id returns invalid_road_vehicle_id instead of another subject's "
    "facts."
)
LIST_MY_STATIONS_DESCRIPTION = (
    "List every road station your company owns, including ones no vehicle is routed through. Each "
    "row reports station_id, name, the station's location tile (the tile its sign stands on — not "
    "guaranteed to be a station tile) with x/y, and "
    "the is_bus_stop/is_truck_stop flags — one station id can carry both stop kinds. "
    "waiting_passengers and passenger_rating cover the passenger cargo only. Both are -1 when no "
    "passenger cargo resolves, which happens when this game has no buildable passenger road engine to "
    "read one from; otherwise waiting_passengers is the real count (0 at a station nothing is waiting "
    "at) and passenger_rating alone is -1 until the game tracks a rating for that station. "
    "get_my_station reports one station in full: every cargo it holds, what it accepts, its stop "
    "tiles, and the vehicles with orders to it." + BOUNDED_FACT_ERRORS_DESCRIPTION
)
LIST_MY_DEPOTS_DESCRIPTION = (
    "List your company's road-depot inventory; vehicles and stations have their own "
    "list_my_vehicles and list_my_stations inventories. Depots are addressed by tile here: the "
    "script API this server is built on exposes no depot object, so a depot carries no id and no "
    "name on this surface. Each depot row reports tile, x, y, front_tile, front_x, front_y, and "
    "vehicles_inside. tile is the depot tile buy_vehicle takes as its depot_tile; front_tile is "
    "the physical entrance that build_road_depot returns as actual_front_tile and check_route_connectivity "
    "can take. It is not the build_road_depot or check_build_road_depot front_tile direction selector, "
    "which can be distant. send_vehicle_to_depot names no depot at all — the game picks the closest one the vehicle can "
    "drive to. vehicles_inside is the number of your "
    "vehicles physically inside that depot right now, counted by scanning your vehicles. Read-only."
    + BOUNDED_FACT_ERRORS_DESCRIPTION
)
CREATE_GROUP_DESCRIPTION = (
    "Create an empty named road-vehicle group: the container move_vehicle_to_group moves vehicles into and "
    "set_group_autoreplace scopes its engine-replacement rules to. A group is bookkeeping only — membership "
    "does not change how a vehicle drives, what orders it holds, or what it earns and costs. The name "
    "must be non-empty, short enough for the game, and not already used by another group; a group whose "
    "name is rejected is deleted again rather than left behind, and the call reports created/name_set "
    "with group_id -1. On success the result returns group_id, group_name, and the current groups array "
    "(group_id, name, vehicle_count, autoreplace_rules) that list_my_vehicles also reports."
)
ASSIGN_TO_GROUP_DESCRIPTION = (
    "Move one road vehicle you own into an existing road-vehicle group. A vehicle belongs to exactly "
    "one group, so this also takes it out of the group it was in. "
    "The result echoes the requested group_id alongside vehicle_group_id/vehicle_group_name — the group "
    "the vehicle is actually in after the attempt — plus the groups array."
)
SET_AUTOREPLACE_DESCRIPTION = (
    "Set a standing replacement rule on one group: whenever a vehicle in that group built from "
    "engine_id_old enters a depot, the game attempts to sell it and put a newly bought engine_id_new in "
    "its place. Nothing changes at the moment you set the rule — an attempt happens only once a vehicle "
    "is in a depot — and the rule keeps applying to vehicles that join the group later. An attempt can "
    "fail (not enough money for the new engine, for one), and a failed attempt leaves the old vehicle in "
    "service with the rule still set, so the game tries again on the next depot visit. A successful swap "
    "charges the new engine's price less the old vehicle's sale value, and the replacement keeps the old "
    "vehicle's orders and group membership but is a "
    "new vehicle with a new vehicle_id. group_id must be an existing road group and both engines must be "
    "road engines; the new engine must be buildable at the current date, and OpenTTD refits the replacement "
    "toward the old vehicles' cargo where it can, so cross-cargo pairs are accepted or rejected by the game "
    "rather than pre-filtered here. The result echoes replacement_engine_id/replacement_engine_name and the "
    "group's autoreplace_rules; list_my_vehicles groups[].autoreplace_rules reports the rules still in force."
)
REVERSE_VEHICLE_DESCRIPTION = (
    "Turn one road vehicle around so it heads back the way it came. A true result reports only "
    "that the vehicle has started to turn — the game reports nothing about the turn completing, so "
    "where the vehicle actually ends up is read later from get_vehicle or wait_and_observe_vehicle, "
    "never from this result. The game refuses the call for a road vehicle that is stopped or inside "
    "a depot: the refusal changes nothing and reports the game's own error name, which for both of "
    "those situations is only the generic ERR_UNKNOWN — the state_name and stopped_in_depot facts "
    "in the result describe the circumstances but do not identify why the game refused the call."
)
SEND_TO_DEPOT_DESCRIPTION = (
    "Send a vehicle to halt in a depot the game picks: the closest one it can drive to. On arrival "
    "the vehicle stops inside that depot instead of carrying on with its orders; its order list is "
    "left as it is, and start_vehicle sends the vehicle back out to continue those orders. Calling "
    "this again while the vehicle is already on its way to a depot cancels that trip, and the "
    "vehicle goes back to its orders. The game refuses the command with its own error name when the "
    "vehicle has crashed, is already stopped in a depot, or can drive to no depot at all."
)
SELL_VEHICLE_DESCRIPTION = (
    "Sell one vehicle that is stopped inside a depot, for its current value. That value (the same "
    "current_value list_my_route_economics reports) is credited to the company and appears in the "
    "result as a negative cost, and the vehicle ceases to exist, so its vehicle_id becomes invalid. "
    "The vehicle must be stopped inside a depot; otherwise the game rejects the sale with "
    "ERR_VEHICLE_NOT_IN_DEPOT and nothing is sold."
)
SET_LOAN_DESCRIPTION = (
    "Set the company's loan to amount — an absolute figure, not a change to the current loan. "
    "Raising the loan pays the difference into the bank balance; lowering it repays the difference "
    "out of the bank balance. amount must be a whole multiple of loan_interval and no "
    "larger than max_loan_amount (get_my_finances and get_my_company report both), and a reduction "
    "the bank balance cannot cover is refused; a refused amount leaves the loan unchanged and "
    "reports the game's own error name. The result reports requested_amount, loan_amount, and "
    "max_loan_amount."
)
DEMOLISH_DESCRIPTION = (
    "Clear one tile of whatever stands on it: road, a station or depot tile, a house, trees. The "
    "tile is left bare and the game's clearing cost is charged. remove_road, the narrower tool, "
    "takes road pieces off a straight span of tiles and leaves anything else on those tiles "
    "standing. What may be cleared is the game's decision: a refusal reports the game's own error "
    "name and leaves the tile untouched."
)
LIST_ENGINES_DESCRIPTION = (
    "List the road engines this game has: engine_id, name, buildable, price, running_cost, "
    "capacity, cargo_type/cargo_label, is_passenger, max_speed, reliability, design_date, "
    "max_age_days. reliability is the engine's current reliability percent on a 0-100 scale, which "
    "varies over the engine's model life; design_date is the date the engine's design was introduced, as a "
    "{raw, year, month, day} object; max_age_days is the engine's maximum age in calendar days. "
    "The result also carries the current in-game date. Engines not yet "
    "introduced at the current date are absent. The only_buildable and passenger_only filters "
    "restrict which engines are listed."
)
CHECK_ROUTE_CONNECTIVITY_DESCRIPTION = (
    "Answer whether a road vehicle could actually drive between the stop tiles and the depot you "
    "name: per stop, whether the depot reaches it and it reaches the depot; whether every stop "
    "reaches every other stop; and whether depot_tile and depot_front_tile join in both "
    "directions. stop_access_connected reports per stop tile whether a bay stop joins its front tile "
    "in both directions or a drive-through stop joins either end in both directions, and "
    "stop_to_stop_distances reports one row for each direction of every pair of different "
    f"stop_tiles list positions, for 1..{ROUTE_CONNECTIVITY_STOP_MAX} positions. "
    "Repeated positions produce repeated directed rows with 0 road_steps when both endpoints are "
    "the same road tile. A position that is not a road stop tile has its stop_access_connected row "
    "false; a position carrying no road at all is additionally unreachable in every direction. "
    "Bay stops use their station tile as the endpoint, like "
    "drive-through stops. Each row's from_tile/to_tile are tile-centre endpoints; road_steps "
    "is the shortest traced count of edge-adjacent directed road-graph transitions in that direction "
    "(0 when two positions name the same road tile), with reachable=false and road_steps=null when "
    "that direction has no traced path. "
    "all_on_one_component is true only when every connection and reachability check above holds; "
    "distance magnitudes do not affect it. Reachability is "
    "traced over edge-adjacent connected road tiles, road stations and road depots only: the "
    "trace does not enter bridge or tunnel tiles, so a path that crosses one is reported as not "
    "reachable. Nothing is built and no game time passes. "
    "component_size is the size of the connected road component containing the first stop tile "
    "(counted up to 512 tiles, whatever towns it spans); town_road_component_size counts only that "
    "component's tiles within town_id's influence, seeded at the same stop, and is 0 when the seed "
    "lies outside it (see seed_within_town_influence)."
)
QUERY_STATION_DESCRIPTION = (
    "Read factual cargo state for one station you own, identified by station_id or by tile (exactly "
    "one; station IDs resolve only for your own company's stations). Per cargo: waiting amount, "
    "whether the game tracks a rating (has_rating, with rating -1 otherwise), whether the station "
    "currently accepts the cargo, and the tile cargo acceptance measured around the station's "
    "location tile (the tile its sign stands on — not guaranteed to be one of its own station tiles) "
    "at the bus-stop coverage radius (for a station with more "
    "than one tile, this is not summed across all station tiles; the game lists a cargo as accepted "
    "when the acceptance summed over the station's catchment is at least 8 units, recomputed "
    "periodically as the surrounding tiles change, and a bus-stop-only station never accepts "
    "non-passenger cargo regardless of catchment). Also reports the "
    "station's bus-stop tiles and truck-stop tiles (a joined station can hold several road-stop "
    "bays) and the vehicles with orders to it. construction_date is the game date the station "
    "records as its construction date — the date the most recent part of it was added, so a station "
    "later extended with another bay carries that later date; the result's own date field carries "
    "the current date to measure it against. vehicles_loading is counted by scanning your vehicles: "
    "of the vehicles with orders to this station, the number currently in the loading/unloading "
    "state on one of this station's stop tiles; a vehicle loading at another station its orders "
    "also visit is not counted. If an unexpected bridge/API failure prevents a reported fact from "
    "being read confidently, the call fails with error sensor_api_error and a sensor_error object "
    "naming a bounded semantic code and the affected field; it does not replace that fact with "
    "false, zero, an empty list, or -1."
)
QUERY_ROUTE_ECONOMICS_DESCRIPTION = (
    "Report profit per route: your road vehicles grouped by the stops they serve, with each vehicle's "
    "profit this year and last year and the group's profit totals. "
    "A group's route_signature is the distinct valid road-station stop tiles taken from that vehicle's "
    "order list, in order; a two-stop signature is sorted so the same pair of stops is one route "
    "whichever stop comes first, and vehicles with no valid road-station order (a new vehicle with no "
    "orders yet, or one whose stops no longer exist) share the empty signature. "
    "Each group reports route_signature, vehicle_count, vehicle_ids, profit_this_year_total, and "
    "profit_last_year_total; each vehicle row adds station_tiles, order_count, engine_id/engine_name, "
    "its current tile/x/y, state, stopped_in_depot, cargo_type and cargo_load, current_value, "
    "running_cost, age_days/max_age_days/age_left_days, and reliability. "
    "profit_this_year and profit_last_year are OpenTTD's own per-vehicle figures: delivery income minus "
    "the running cost charged while the vehicle moves, with the vehicle's purchase price never "
    "subtracted. profit_this_year covers the current year only and resets at the year boundary, where "
    "its value becomes profit_last_year. "
    "The result also reports road_vehicle_count and the current in-game date. Groups are ordered by "
    "their signature tile ids, not by profit."
)
GET_CARGO_PAYMENT_RATE_DESCRIPTION = (
    "Return OpenTTD's current payment for one unit of cargo_id transported distance Manhattan "
    "tiles in days_in_transit economy-days. This is the game's own payment calculation for exactly "
    "those three inputs and nothing else; route revenue and a vehicle's "
    "recorded last delivered payment are separate facts unavailable through this call. cargo_id is "
    "the integer cargo type reported as cargo_type by list_engines and list_industries/get_industry. "
    "Valid cargo slots are 0..63, "
    "but unavailable slots in the current game return invalid_cargo_id; distance is 0..4294967295 "
    "and days_in_transit is 0..163838. The result echoes the inputs, cargo name/label, current date, "
    "and income; active game settings and NewGRFs can affect that current-game calculation."
)
LIST_INDUSTRIES_DESCRIPTION = (
    "List every industry currently on the map with its industry_id, name, "
    "type, location tile/x/y, stations_around, and its produced_cargo and accepted_cargo entries with cargo_type and cargo_label. "
    "stations_around counts the stations whose catchment covers that industry, the caller's and competitors' alike, whatever cargo each station handles. "
    "Each produced_cargo row also carries that cargo's last_month_production, last_month_transported, "
    "last_month_transported_percentage (0-100; transported counts output moved into the waiting queues "
    "of stations in range last month, not cargo loaded or delivered) and stockpiled counts, and each "
    "accepted_cargo row its accept_state and stockpiled count. stockpiled is the accepted-cargo input "
    "pile, so it reads -1 on a produced cargo the industry does not also accept; un-collected output is "
    "not observable. A figure the game cannot read at all comes back as -1 with a metric_error on that row."
    + BOUNDED_FACT_ERRORS_DESCRIPTION
)
LIST_SUBSIDIES_DESCRIPTION = (
    "List every subsidy the game currently holds, open offers and awarded subsidies alike. Each row reports "
    "subsidy_id, cargo type/label/name, factual offered-or-awarded status, the awarded company id "
    "and whether it is awarded to your resolved company when awarded, expire_date as "
    "the last valid economy-date, and source/destination "
    "participant facts (kind town/industry, the raw participant-type number, id, validity, name, and location). "
    "The expiry is the offer expiry while offered and changes to the awarded subsidy expiry when "
    "awarded; expired subsidies are absent, because only subsidies that currently exist in the game are listed."
)
QUERY_INDUSTRY_DESCRIPTION = (
    "Read one industry's record by industry_id: the same per-industry facts list_industries reports, "
    "for a single id. A closed or stale industry_id comes back as invalid_industry_id instead of "
    "merely being absent from a list. Returns location/type plus stations_around, which counts the "
    "stations whose catchment covers that industry, the caller's and competitors' alike, whatever cargo "
    "each station handles. Returns "
    "produced_cargo and accepted_cargo with cargo labels, each produced cargo's last_month_production, "
    "last_month_transported and last_month_transported_percentage (0-100; transported counts output "
    "moved into the waiting queues of stations in range during the last closed month, not cargo loaded "
    "onto vehicles or delivered), and stockpiled counts. stockpiled is the accepted-cargo input pile "
    "waiting to be processed, so it reads -1 on a produced cargo the industry does not also accept; an "
    "industry's un-collected output pile is not observable through any sensor."
    + WHOLE_SENSOR_ERROR_DESCRIPTION
)
QUERY_TRUCK_CATCHMENT_DESCRIPTION = (
    "Report the factual catchment consequences of a hypothetical new standalone station "
    "containing one truck-stop tile: raw town-cargo acceptance units and the industries "
    "a stop there would attach to. "
    "truck_coverage_radius is the coverage radius "
    "the game uses for truck stops. The catchment is the same for a bay and a drive-through stop: the game's "
    "coverage radius depends on the station type, not the stop layout. town_cargo_acceptance[] enumerates "
    "every cargo in this game that both has a town effect and is not passenger-class, because this standalone "
    "truck-only station has no facility that accepts passenger-class cargo. A truck stop joined to a station "
    "that also has a bus, train, airport, or dock facility can accept passenger-class cargo; this hypothetical "
    "does not model such a join. Each row reports cargo_type, cargo_label, name, towneffect_id, "
    "towneffect_name, and the raw acceptance unit count for a 1x1 footprint at truck_coverage_radius. "
    "The count sums every accepting tile in that radius, including houses, industries, and headquarters; "
    "town cargo means cargo with a town effect, not cargo accepted only by town buildings. "
    "A station accepts that cargo when its periodically recomputed catchment count is at least 8 units; the "
    "row remains the integer count. get_tile/get_region can report "
    "the same exact-radius acceptance for one caller-selected cargo_id; their *_radius4 field is a fixed-radius "
    "comparison, not the truck-stop catchment used here. get_tile separately reports whether the "
    "hypothetical station's tile is legal to build on. "
    "producers[] lists each industry whose "
    "produced cargo would attach to a stop at this tile — i.e. the tile lies inside that industry's "
    "producing catchment at truck_coverage_radius — with industry_id, name, "
    "industry_type/industry_type_name, location tile/x/y, produced_cargo rows (cargo_type, cargo_label, "
    "last-month production/transported, stockpiled), and stations_around, which counts only the stations "
    "that already attach to that industry, the caller's and competitors' alike. acceptors[] is the "
    "delivery-side twin, the same membership test against the industry's accepting catchment, with "
    "accepted_cargo rows. Each row's cargo_type is the integer cargo type list_engines and "
    "list_industries/get_industry also report under that name, and get_tile/get_region/"
    "refit_vehicle take as cargo_id (same integer, different field name). "
    "Membership in those catchments is the game's own attach test: a stop outside "
    "them does not attach even when that "
    "tile's cargo_production_radius4 is positive (the raw radius4 field does not predict attachment). An "
    "invalid tile returns invalid_tile. If an unexpected bridge/API failure prevents a reported fact "
    "from being read confidently, the call fails with error sensor_api_error and a sensor_error object "
    "naming a bounded semantic code and the affected field; it does not treat the industry as absent "
    "from a catchment or substitute false, zero, an empty list, or -1. This includes cargo facts "
    "inside a producer or acceptor row."
)
REPORT_FEEDBACK_DESCRIPTION = (
    "Whenever you wish you could see or do something the tools don't offer, file it at that moment; "
    "it costs nothing, advances no game time, and grants no capability during this episode. Kinds: "
    "missing_observation = facts you wanted to inspect; "
    "missing_action = command you wanted to execute; "
    "ambiguous_tool_result = result was unclear or underspecified; "
    "strategic_note = lesson that may improve future play."
)
VIEW_MAP_DESCRIPTION = (
    "See any part of the map as an image: returns a square crop centered on `tile`, freshly rendered "
    "by default, showing the world exactly as the game draws it for a human player - terrain and water "
    "barriers, roads and bridges as actually built, town and industry surroundings, and your own "
    "stations, depots, and vehicles at their current positions. A single crop shows things no text "
    "query reports: whether a route physically connects end to end, how a station sits relative to its "
    "town or industry, the lay of land and water between two points, and where vehicles stand - "
    "including many vehicles bunched at one station or depot. `refresh=false` re-crops the most recent "
    "render instead of drawing a new one, letting several regions be viewed from one consistent moment; "
    "the returned `age_seconds` and `mutations_since_capture` metadata report that frame's currency. "
    "`window` is the crop side length in pixels, accepted from 64 through 2048 inclusive (default 384; "
    "larger shows more area with less detail). "
    "Coordinate convention: tile index = y * map_width + x. `frame_corner_tiles` reports the tile-space "
    "x/y coordinate and `in_map` status at each visible corner; out-of-map corners are not clamped. "
    "`rendered_center_tile` reports the tile-space coordinate actually at the crop center after pixel-edge "
    "clamping; when in-map, its `tile` is the containing tile id from int(x), int(y), and is otherwise "
    "omitted. `tile_extent` reports the frame's extent along the map x/y axes. "
    "`visible_diamond_tile_bbox` is the axis-aligned tile-space bounding box of the visible diamond: "
    "because the map is isometric, it is not a claim that every tile in that box is shown. The image is "
    "the game's own unannotated rendering; exact tile identities come from the JSON metadata and the "
    "query tools, not from reading the image. The image contains no tile ids."
)


def _validate_end_episode_mode(mode: str) -> str:
    if mode not in END_EPISODE_MODES:
        raise ValueError(
            f"end_episode_mode must be one of {END_EPISODE_MODES}, got {mode!r}"
        )
    return mode


def _first_sentence(text: str) -> str:
    text = text.strip()
    for index, character in enumerate(text):
        # A period ending an ellipsis is not a sentence boundary: several tool
        # descriptions write path semantics as "from_tile -> waypoints... ->
        # to_tile", and splitting there would drop the destination from the
        # always-visible index line.
        if (
            character == "."
            and (index == 0 or text[index - 1] != ".")
            and (index + 1 == len(text) or text[index + 1].isspace())
        ):
            return text[: index + 1]
    return text


def tool_specs(
    enable_skill: bool = False,
    end_episode_mode: str = "standard",
    enable_vision: bool = False,
) -> list[ToolSpec]:
    end_episode_mode = _validate_end_episode_mode(end_episode_mode)
    int_schema = {"type": "integer"}
    non_negative_int_schema = {**int_schema, "minimum": 0}
    string_schema = {"type": "string"}
    flag_name_schema = {"type": "string", "enum": list(ORDER_FLAG_BITS)}
    flags_schema = {"type": "array", "items": flag_name_schema, "default": []}
    # anyOf, not oneOf: an empty [] validly matches both the flat and nested
    # branches (both mean "no flags"), which would make an exactly-one oneOf
    # reject it. No outer default either — [] is not a member of either branch's
    # value space in a way strict validators accept as a schema-level default.
    append_orders_flags_schema = {
        "anyOf": [
            {"type": "array", "items": flag_name_schema},
            {"type": "array", "items": {"type": "array", "items": flag_name_schema}},
        ],
    }
    end_episode_description = (
        DISTRESS_END_EPISODE_DESCRIPTION
        if end_episode_mode == "distress"
        else STANDARD_END_EPISODE_DESCRIPTION
    )
    subject_screenshot_schema = (
        {"screenshot": {"type": "boolean", "default": False}} if enable_vision else {}
    )

    def subject_description(description: str) -> str:
        if not enable_vision:
            return description
        return description + SUBJECT_SCREENSHOT_DESCRIPTION

    # Ordering rule: this list order is the order of the generated tool
    # index the model reads, and it mirrors the episode lifecycle - observe ->
    # plan -> act -> clock/diagnostics -> run control. Flag-gated tools are
    # inserted into their lifecycle block, never appended after the list, and
    # end_episode is always the final entry.
    specs = [
        ToolSpec(
            "get_tile",
            subject_description(QUERY_TILE_DESCRIPTION),
            object_schema(
                {
                    "tile": int_schema,
                    "cargo_id": {
                        **int_schema,
                        "minimum": CARGO_ID_MIN,
                        "maximum": CARGO_ID_MAX,
                    },
                    **subject_screenshot_schema,
                },
                ["tile"],
            ),
        ),
        ToolSpec(
            "get_region",
            QUERY_REGION_DESCRIPTION,
            object_schema(
                {
                    "center_tile": int_schema,
                    "radius": {
                        "type": "integer",
                        "minimum": QUERY_REGION_RADIUS_MIN,
                        "maximum": QUERY_REGION_RADIUS_MAX,
                        "description": (
                            f"Maximum depends on detail: {QUERY_REGION_RADIUS_MAX_TILES} with detail='tiles', "
                            f"{QUERY_REGION_RADIUS_MAX_GRID} with detail='grid'. A radius above the mode's "
                            "maximum is rejected, never reduced."
                        ),
                    },
                    "cargo_id": {
                        **int_schema,
                        "minimum": CARGO_ID_MIN,
                        "maximum": CARGO_ID_MAX,
                        "description": (
                            "detail='tiles' only. Cargo type integer (the cargo_type field from list_engines and "
                            "list_industries); adds the cargo_* fields for that cargo to every tile."
                        ),
                    },
                    "detail": {
                        "type": "string",
                        "enum": list(DETAIL_VALUES),
                        "default": "tiles",
                        "description": (
                            "'tiles' returns full per-tile facts (about 120KB at radius 6, its maximum); 'grid' "
                            "returns a one-character-per-tile category map of the same region (about 1.5KB at "
                            f"radius 6, about 2.7KB at radius {QUERY_REGION_RADIUS_MAX_GRID}, its maximum)."
                        ),
                    },
                    "fields": {
                        "type": "array",
                        "items": {"type": "string"},
                        "default": [],
                        "description": (
                            "detail='tiles' only. Tile field names to keep; tile/x/y are always kept. Accepts any "
                            "name in the 'Valid fields' list in this tool's description."
                        ),
                    },
                    "where": {
                        "type": "array",
                        "items": {"type": "string"},
                        "default": [],
                        "description": (
                            "detail='tiles' only. Keeps just the tiles where every named field is true, so it "
                            "accepts only these boolean tile categories: "
                            + ", ".join(sorted(WHERE_TILE_FIELDS))
                            + ". Measurement and identifier fields such as cargo_acceptance, passenger_production, "
                            "or industry_id are valid in fields but are rejected here."
                        ),
                    },
                },
                ["center_tile", "radius"],
            ),
        ),
    ]
    if enable_vision:
        # The other area-observation sensor: it sits directly after
        # get_region, not at the end of the catalog.
        specs.append(
            ToolSpec(
                VIEW_MAP_TOOL_NAME,
                VIEW_MAP_DESCRIPTION,
                object_schema(
                    {
                        "tile": {"type": "integer"},
                        "window": {
                            "type": "integer",
                            "minimum": WINDOW_MIN,
                            "maximum": WINDOW_MAX,
                            "default": DEFAULT_WINDOW,
                        },
                        "refresh": {"type": "boolean", "default": DEFAULT_REFRESH},
                    },
                    ["tile"],
                ),
            )
        )
    specs += [
        ToolSpec(
            "list_towns",
            LIST_TOWNS_DESCRIPTION,
            object_schema({}),
        ),
        ToolSpec(
            "get_town",
            QUERY_TOWN_DESCRIPTION,
            object_schema({"town_id": int_schema}, ["town_id"]),
        ),
        ToolSpec("list_my_vehicles", LIST_MY_VEHICLES_DESCRIPTION, object_schema({})),
        ToolSpec(
            "get_vehicle",
            subject_description(GET_VEHICLE_DESCRIPTION),
            object_schema(
                {"vehicle_id": int_schema, **subject_screenshot_schema},
                ["vehicle_id"],
            ),
        ),
        ToolSpec("list_my_stations", LIST_MY_STATIONS_DESCRIPTION, object_schema({})),
        ToolSpec("list_my_depots", LIST_MY_DEPOTS_DESCRIPTION, object_schema({})),
        ToolSpec("get_my_finances", GET_FINANCES_DESCRIPTION, object_schema({})),
        ToolSpec(
            "list_engines",
            LIST_ENGINES_DESCRIPTION,
            object_schema(
                {
                    "only_buildable": {"type": "boolean", "default": True},
                    "passenger_only": {"type": "boolean", "default": False},
                }
            ),
        ),
        ToolSpec("list_industries", LIST_INDUSTRIES_DESCRIPTION, object_schema({})),
        ToolSpec("list_subsidies", LIST_SUBSIDIES_DESCRIPTION, object_schema({})),
        ToolSpec(
            "get_industry",
            QUERY_INDUSTRY_DESCRIPTION,
            object_schema({"industry_id": int_schema}, ["industry_id"]),
        ),
        ToolSpec(
            "list_bridge_types",
            LIST_BRIDGE_TYPES_DESCRIPTION,
            object_schema(
                {"length": {"type": "integer", "minimum": BRIDGE_LENGTH_MIN}},
                ["length"],
            ),
        ),
        ToolSpec("get_my_company", GET_COMPANY_REPORT_DESCRIPTION, object_schema({})),
        ToolSpec(
            "get_my_station",
            subject_description(QUERY_STATION_DESCRIPTION),
            object_schema(
                {
                    "station_id": int_schema,
                    "tile": int_schema,
                    **subject_screenshot_schema,
                }
            ),
        ),
        ToolSpec(
            "check_truck_stop_catchment",
            QUERY_TRUCK_CATCHMENT_DESCRIPTION,
            object_schema({"tile": int_schema}, ["tile"]),
        ),
        ToolSpec(
            "list_my_route_economics",
            QUERY_ROUTE_ECONOMICS_DESCRIPTION,
            object_schema({}),
        ),
        ToolSpec(
            "get_cargo_payment_rate",
            GET_CARGO_PAYMENT_RATE_DESCRIPTION,
            object_schema(
                {
                    "cargo_id": {
                        "type": "integer",
                        "minimum": CARGO_ID_MIN,
                        "maximum": CARGO_ID_MAX,
                    },
                    "distance": {
                        "type": "integer",
                        "minimum": CARGO_DISTANCE_MIN,
                        "maximum": CARGO_DISTANCE_MAX,
                    },
                    "days_in_transit": {
                        "type": "integer",
                        "minimum": CARGO_TRANSIT_DAYS_MIN,
                        "maximum": CARGO_TRANSIT_DAYS_MAX,
                    },
                },
                ["cargo_id", "distance", "days_in_transit"],
            ),
        ),
        ToolSpec(
            "check_build_road",
            ESTIMATE_ROAD_BUILD_DESCRIPTION,
            object_schema(
                {
                    "from_tile": int_schema,
                    "to_tile": int_schema,
                    "waypoints": {"type": "array", "items": int_schema, "default": []},
                },
                ["from_tile", "to_tile"],
            ),
        ),
        ToolSpec(
            "check_build_road_depot",
            ESTIMATE_DEPOT_BUILD_DESCRIPTION,
            object_schema(
                {"tile": int_schema, "front_tile": int_schema}, ["tile", "front_tile"]
            ),
        ),
    ]
    if enable_skill:
        # Context-only retrieval, not construction: it belongs with the
        # planning aids, not among the build_* actions.
        specs.append(
            ToolSpec(
                SKILL_TOOL_NAME,
                "Context-only skill retrieval. Returns the stored skill body and guidance; runs no construction.",
                object_schema(
                    {"town_id": int_schema, "engine_id": {"type": "integer"}},
                    ["town_id"],
                ),
            )
        )
    specs += [
        ToolSpec(
            "build_road",
            BUILD_ROAD_DESCRIPTION,
            object_schema(
                {
                    "from_tile": int_schema,
                    "to_tile": int_schema,
                    "waypoints": {"type": "array", "items": int_schema, "default": []},
                },
                ["from_tile", "to_tile"],
            ),
            mutates_game=True,
        ),
        ToolSpec(
            "build_one_way_road",
            BUILD_ONE_WAY_ROAD_DESCRIPTION,
            object_schema(
                {
                    "from_tile": non_negative_int_schema,
                    "to_tile": non_negative_int_schema,
                },
                ["from_tile", "to_tile"],
            ),
            mutates_game=True,
        ),
        ToolSpec(
            "build_bridge",
            BUILD_BRIDGE_DESCRIPTION,
            object_schema(
                {
                    "start_tile": int_schema,
                    "end_tile": int_schema,
                    "bridge_id": int_schema,
                },
                ["start_tile", "end_tile", "bridge_id"],
            ),
            mutates_game=True,
        ),
        ToolSpec(
            "build_tunnel",
            BUILD_TUNNEL_DESCRIPTION,
            object_schema({"start_tile": int_schema}, ["start_tile"]),
            mutates_game=True,
        ),
        ToolSpec(
            "terraform_land",
            TERRAFORM_LAND_DESCRIPTION,
            object_schema(
                {
                    "tile": int_schema,
                    "mode": {"type": "string", "enum": list(TERRAFORM_MODES)},
                    "corners": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(TERRAFORM_CORNERS)},
                        "default": [],
                    },
                    "to_tile": int_schema,
                },
                ["tile", "mode"],
            ),
            mutates_game=True,
        ),
        ToolSpec(
            "build_bus_stop_drive_through",
            BUILD_BUS_STATION_DESCRIPTION,
            _station_build_schema(),
            True,
        ),
        ToolSpec(
            "build_truck_stop_drive_through",
            BUILD_TRUCK_STATION_DESCRIPTION,
            _station_build_schema(),
            True,
        ),
        ToolSpec(
            "build_bus_stop_bay",
            BUILD_BUS_BAY_STATION_DESCRIPTION,
            _station_build_schema(),
            True,
        ),
        ToolSpec(
            "build_truck_stop_bay",
            BUILD_TRUCK_BAY_STATION_DESCRIPTION,
            _station_build_schema(),
            True,
        ),
        ToolSpec(
            "build_road_depot",
            BUILD_ROAD_DEPOT_DESCRIPTION,
            object_schema(
                {"tile": int_schema, "front_tile": int_schema}, ["tile", "front_tile"]
            ),
            True,
        ),
        ToolSpec(
            "buy_vehicle",
            BUY_VEHICLE_DESCRIPTION,
            object_schema(
                {"depot_tile": int_schema, "engine_id": int_schema},
                ["depot_tile", "engine_id"],
            ),
            True,
        ),
        ToolSpec(
            "clone_vehicle",
            CLONE_VEHICLE_DESCRIPTION,
            object_schema(
                {
                    "depot_tile": int_schema,
                    "vehicle_id": int_schema,
                    "share_orders": {"type": "boolean"},
                },
                ["depot_tile", "vehicle_id", "share_orders"],
            ),
            True,
        ),
        ToolSpec(
            "refit_vehicle",
            REFIT_VEHICLE_DESCRIPTION,
            object_schema(
                {
                    "vehicle_id": int_schema,
                    "cargo_id": {
                        **int_schema,
                        "minimum": CARGO_ID_MIN,
                        "maximum": CARGO_ID_MAX,
                    },
                },
                ["vehicle_id", "cargo_id"],
            ),
            True,
        ),
        ToolSpec(
            "create_group",
            CREATE_GROUP_DESCRIPTION,
            object_schema({"name": string_schema}, ["name"]),
            True,
        ),
        ToolSpec(
            "move_vehicle_to_group",
            ASSIGN_TO_GROUP_DESCRIPTION,
            object_schema(
                {"vehicle_id": int_schema, "group_id": int_schema},
                ["vehicle_id", "group_id"],
            ),
            True,
        ),
        ToolSpec(
            "set_group_autoreplace",
            SET_AUTOREPLACE_DESCRIPTION,
            object_schema(
                {
                    "group_id": int_schema,
                    "engine_id_old": int_schema,
                    "engine_id_new": int_schema,
                },
                ["group_id", "engine_id_old", "engine_id_new"],
            ),
            True,
        ),
        ToolSpec(
            "insert_order",
            INSERT_ORDER_DESCRIPTION,
            object_schema(
                {
                    "vehicle_id": int_schema,
                    "position": int_schema,
                    "stop_tile": int_schema,
                    "flags": flags_schema,
                },
                ["vehicle_id", "position", "stop_tile"],
            ),
            True,
        ),
        ToolSpec(
            "delete_order",
            DELETE_ORDER_DESCRIPTION,
            object_schema(
                {"vehicle_id": int_schema, "position": int_schema},
                ["vehicle_id", "position"],
            ),
            True,
        ),
        ToolSpec(
            "set_order_flags",
            SET_ORDER_FLAGS_DESCRIPTION,
            object_schema(
                {
                    "vehicle_id": int_schema,
                    "position": int_schema,
                    "flags": flags_schema,
                },
                ["vehicle_id", "position", "flags"],
            ),
            True,
        ),
        ToolSpec(
            "append_orders",
            APPEND_ORDERS_DESCRIPTION,
            object_schema(
                {
                    "vehicle_id": int_schema,
                    "stop_tiles": {"type": "array", "items": int_schema, "minItems": 1},
                    "flags": append_orders_flags_schema,
                },
                ["vehicle_id", "stop_tiles"],
            ),
            True,
        ),
        ToolSpec(
            "insert_conditional_order",
            INSERT_CONDITIONAL_ORDER_DESCRIPTION,
            object_schema(
                {
                    "vehicle_id": non_negative_int_schema,
                    "position": non_negative_int_schema,
                    "condition": {
                        "type": "string",
                        "enum": list(CONDITIONAL_ORDER_CONDITIONS),
                    },
                    "compare": {
                        "type": "string",
                        "enum": list(CONDITIONAL_ORDER_COMPARES),
                    },
                    "value": {
                        **int_schema,
                        "minimum": 0,
                        "maximum": CONDITIONAL_VALUE_MAX,
                    },
                    "jump_to": non_negative_int_schema,
                },
                ["vehicle_id", "position", "condition", "compare", "value", "jump_to"],
            ),
            True,
        ),
        ToolSpec(
            "skip_to_order",
            SKIP_TO_ORDER_DESCRIPTION,
            object_schema(
                {
                    "vehicle_id": non_negative_int_schema,
                    "position": non_negative_int_schema,
                },
                ["vehicle_id", "position"],
            ),
            True,
        ),
        ToolSpec(
            "copy_orders",
            COPY_ORDERS_DESCRIPTION,
            object_schema(
                {
                    "vehicle_id": non_negative_int_schema,
                    "source_vehicle_id": non_negative_int_schema,
                },
                ["vehicle_id", "source_vehicle_id"],
            ),
            True,
        ),
        ToolSpec(
            "share_orders",
            SHARE_ORDERS_DESCRIPTION,
            object_schema(
                {
                    "vehicle_id": non_negative_int_schema,
                    "source_vehicle_id": non_negative_int_schema,
                },
                ["vehicle_id", "source_vehicle_id"],
            ),
            True,
        ),
        ToolSpec(
            "unshare_orders",
            UNSHARE_ORDERS_DESCRIPTION,
            object_schema({"vehicle_id": non_negative_int_schema}, ["vehicle_id"]),
            True,
        ),
        ToolSpec(
            "start_vehicle",
            "Start a stopped vehicle.",
            object_schema({"vehicle_id": int_schema}, ["vehicle_id"]),
            True,
        ),
        ToolSpec(
            "stop_vehicle",
            "Stop a running vehicle.",
            object_schema({"vehicle_id": int_schema}, ["vehicle_id"]),
            True,
        ),
        ToolSpec(
            "reverse_vehicle",
            REVERSE_VEHICLE_DESCRIPTION,
            object_schema({"vehicle_id": non_negative_int_schema}, ["vehicle_id"]),
            True,
        ),
        ToolSpec(
            "send_vehicle_to_depot",
            SEND_TO_DEPOT_DESCRIPTION,
            object_schema({"vehicle_id": int_schema}, ["vehicle_id"]),
            True,
        ),
        ToolSpec(
            "sell_vehicle",
            SELL_VEHICLE_DESCRIPTION,
            object_schema({"vehicle_id": int_schema}, ["vehicle_id"]),
            True,
        ),
        ToolSpec(
            "set_loan",
            SET_LOAN_DESCRIPTION,
            object_schema({"amount": int_schema}, ["amount"]),
            True,
        ),
        ToolSpec(
            "demolish_tile",
            DEMOLISH_DESCRIPTION,
            object_schema({"tile": int_schema}, ["tile"]),
            True,
        ),
        ToolSpec(
            "remove_road",
            "Remove the road span between from_tile and to_tile on one axis (same row or column).",
            object_schema(
                {"from_tile": int_schema, "to_tile": int_schema},
                ["from_tile", "to_tile"],
            ),
            mutates_game=True,
        ),
        ToolSpec(
            "wait",
            f"Advance in-game time by 1..{WAIT_DAYS_MAX} days. On success returns status='complete' with the "
            "advanced date and bank_balance, plus dated records of vehicles newly lost, newly "
            "waiting in a depot, and newly flagged unprofitable during the window. If the advance "
            "is not confirmed before this call stops waiting it returns status='timeout' (ok=false) "
            "with a note: the game may still have advanced and the company kept operating, so call "
            "get_my_company or get_my_finances (both report the current date) to read the true "
            "current date before retrying.",
            object_schema(
                {
                    "duration_days": {
                        "type": "integer",
                        "minimum": WAIT_DAYS_MIN,
                        "maximum": WAIT_DAYS_MAX,
                    }
                },
                ["duration_days"],
            ),
            True,
        ),
        ToolSpec(
            "check_route_connectivity",
            CHECK_ROUTE_CONNECTIVITY_DESCRIPTION,
            object_schema(
                {
                    "town_id": int_schema,
                    "depot_tile": int_schema,
                    "depot_front_tile": int_schema,
                    "stop_tiles": {
                        "type": "array",
                        "items": int_schema,
                        "minItems": 1,
                        "maxItems": ROUTE_CONNECTIVITY_STOP_MAX,
                    },
                },
                ["town_id", "depot_tile", "depot_front_tile", "stop_tiles"],
            ),
        ),
        ToolSpec(
            "wait_and_observe_vehicle",
            "Advance in-game time by advance_days while sampling the vehicle every game tick, then "
            "report window facts and a verdict. verdict: operating (the vehicle had at least one "
            "sampled station arrival in the window, counted as a transition into the game's "
            "loading-at-station state), moving_without_station_visit (it moved or progressed orders "
            "but had no sampled station arrival — a short window can end mid-leg), "
            "running_pending_progress (running with orders, no movement or progress), stuck, lost, "
            "or crashed. The result carries station_visits and stations_visited for the window, "
            "plus dated waiting_in_depot_events and unprofitable_events records for this vehicle "
            "with *_event_in_window flags; the game announces waiting_in_depot once when a vehicle "
            "sent to a depot arrives and halts, and unprofitable once per year on January 1 for a "
            "vehicle at least 2 years old whose closed year's profit was negative. If bridge "
            "confirmation times out, the failure reports game_mutated=true and "
            "time_may_have_advanced=true because game time may already have advanced; read the "
            "current date before deciding whether to retry.",
            object_schema(
                {
                    "vehicle_id": int_schema,
                    "advance_days": {
                        "type": "integer",
                        "minimum": WAIT_DAYS_MIN,
                        "maximum": WAIT_DAYS_MAX,
                        "default": WAIT_VEHICLE_DEFAULT_DAYS,
                    },
                },
                ["vehicle_id"],
            ),
            mutates_game=True,
        ),
        ToolSpec(
            "wait_and_observe_station",
            "Advance in-game time by advance_days while sampling one current-company road station "
            "every 5 script ticks, then return at most one row per observed calendar date. The "
            "station-wide near counts scan current-company primary road vehicles and count each "
            "vehicle once when its Manhattan distance from the union of all bus and truck stop "
            "tiles is at most 4; zero-speed means current speed equals zero, and both near counts "
            "include vehicles inside depots within that radius. Each stop tile also "
            "reports sampled maxima for exact-location vehicles and for exact-location vehicles "
            "whose game state is loading or unloading. All traffic maxima are independent sampled "
            "maxima and need not have occurred simultaneously. Waiting cargo is the last sampled "
            "snapshot for that date, includes only positive entries, and is not a maximum. Each "
            "date row is bounded: it lists at most max_stop_rows_per_date stop tiles, named in "
            "observed_stop_tiles, and at most max_waiting_cargo_rows_per_date waiting-cargo "
            "entries, with observed_stop_tiles_truncated and per-row "
            "last_sample_waiting_cargo_truncated reporting when something was dropped. The "
            "station-wide near counts still scan every road-stop tile, and each row's waiting-cargo "
            "total and type count still cover every positive cargo. The "
            "selected station must resolve to at least one current-company road-stop tile; an "
            "existing unowned station can pass the Script API validity check but is rejected when "
            "it has no such tile. Both checks happen before time advances. Unexpected sensor failures return "
            "sensor_api_error; a failure or timeout after the observation could have started "
            "explicitly reports that game time may have advanced.",
            object_schema(
                {
                    "station_id": non_negative_int_schema,
                    "advance_days": {
                        "type": "integer",
                        "minimum": WAIT_DAYS_MIN,
                        "maximum": WAIT_DAYS_MAX,
                        "default": WAIT_STATION_DEFAULT_DAYS,
                    },
                },
                ["station_id"],
            ),
            mutates_game=True,
        ),
        ToolSpec(
            FEEDBACK_TOOL_NAME,
            REPORT_FEEDBACK_DESCRIPTION,
            object_schema(
                {
                    "kind": {
                        "type": "string",
                        "enum": [
                            "missing_observation",
                            "missing_action",
                            "ambiguous_tool_result",
                            "strategic_note",
                        ],
                    },
                    "description": {"type": "string"},
                    "why_it_matters": {"type": "string"},
                    "current_blocker": {"type": "string"},
                },
                ["kind", "description", "why_it_matters", "current_blocker"],
            ),
        ),
        ToolSpec(
            END_EPISODE_TOOL_NAME,
            end_episode_description,
            object_schema({"reason": {"type": "string"}}, ["reason"]),
        ),
    ]
    return specs


# Header marker of the generated index. An external runner can import it to
# reject hand-written copies in episode prompts (the index is machine-generated
# and appended by the runner; a config prompt containing this marker would put
# a second, hand-editable copy in front of the model).
TOOL_INDEX_MARKER = "Full tool list (name: summary)"


def generate_server_instructions(
    *,
    enable_skill: bool = False,
    end_episode_mode: str = "standard",
    enable_vision: bool = False,
) -> str:
    """The model-visible tool index.

    The header is era-neutral: no experiment label reaches the model. The
    run's experiment is recorded in ``registry_snapshot()['experiment']`` for
    the evidence lane instead, and TOOL_INDEX_MARKER doubles as a runner's
    duplicate-copy guard.
    """
    lines = [
        (
            f"OpenTTD gameplay tools. {TOOL_INDEX_MARKER} - "
            "load any tool's schema by searching its exact name:"
        )
    ]
    lines.extend(
        f"{spec.name}: {_first_sentence(spec.description)}"
        for spec in tool_specs(
            enable_skill=enable_skill,
            end_episode_mode=end_episode_mode,
            enable_vision=enable_vision,
        )
    )
    return "\n".join(lines)


MUTATING_TOOL_NAMES = {
    spec.name
    for spec in tool_specs(enable_skill=True, enable_vision=True)
    if spec.mutates_game
}


def registry_snapshot(
    enable_skill: bool = False,
    experiment: str = DEFAULT_EXPERIMENT,
    end_episode_mode: str = "standard",
    enable_vision: bool = False,
    tool_index_channel: str | None = None,
) -> dict[str, Any]:
    end_episode_mode = _validate_end_episode_mode(end_episode_mode)
    if tool_index_channel not in (None, "prompt"):
        raise ValueError(f"unsupported tool index channel: {tool_index_channel!r}")
    snapshot = {
        "experiment": experiment,
        "end_episode_mode": end_episode_mode,
        # Older snapshots recorded this text under an "instructions" key
        # because it was served in initialize.instructions; the server now
        # serves no instructions (codex code mode prepended them to every
        # ALL_TOOLS description) and the identical index reaches the model
        # appended to the episode prompt instead. Key presence distinguishes
        # the two formats; the caller separately stamps the delivery channel
        # it actually used.
        "tool_index": generate_server_instructions(
            enable_skill=enable_skill,
            end_episode_mode=end_episode_mode,
            enable_vision=enable_vision,
        ),
        "transport": "mcp-stdio",
        "tools": [
            spec.as_registry_entry()
            for spec in tool_specs(
                enable_skill=enable_skill,
                end_episode_mode=end_episode_mode,
                enable_vision=enable_vision,
            )
        ],
    }
    # Delivery is a caller fact, not a property of the MCP server. Standalone
    # and deterministic scripted callers generate the same registry but do not
    # necessarily put the index in any model prompt, so they must not claim a
    # channel they did not deliver through.
    if tool_index_channel is not None:
        snapshot["tool_index_channel"] = tool_index_channel
    return snapshot


def _require_int(args: dict[str, Any], key: str) -> int:
    if key not in args:
        raise ValueError(f"missing required argument: {key}")
    value = args[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"argument {key} must be an integer")
    return value


def _require_int_at_least(args: dict[str, Any], key: str, minimum: int) -> int:
    value = _require_int(args, key)
    if value < minimum:
        raise ValueError(f"argument {key} must be >= {minimum}")
    return value


def _require_int_between(
    args: dict[str, Any], key: str, minimum: int, maximum: int
) -> int:
    value = _require_int(args, key)
    if value < minimum or value > maximum:
        raise ValueError(f"argument {key} must be between {minimum} and {maximum}")
    return value


def _optional_non_negative_int(args: dict[str, Any], key: str) -> int | None:
    if key not in args or args[key] is None:
        return None
    value = _require_int(args, key)
    if value < 0:
        raise ValueError(f"argument {key} must be a non-negative integer")
    return value


def _optional_int_between(
    args: dict[str, Any], key: str, minimum: int, maximum: int
) -> int | None:
    if key not in args or args[key] is None:
        return None
    return _require_int_between(args, key, minimum, maximum)


def _optional_bool(args: dict[str, Any], key: str, default: bool) -> bool:
    value = args.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"argument {key} must be a boolean")
    return value


def _require_bool(args: dict[str, Any], key: str) -> bool:
    if key not in args:
        raise ValueError(f"missing required argument: {key}")
    value = args[key]
    if not isinstance(value, bool):
        raise ValueError(f"argument {key} must be a boolean")
    return value


def _require_str_enum(args: dict[str, Any], key: str, allowed: set[str]) -> str:
    if key not in args:
        raise ValueError(f"missing required argument: {key}")
    value = args[key]
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"argument {key} must be one of {sorted(allowed)}")
    return value


def _optional_str_enum(
    args: dict[str, Any], key: str, allowed: set[str], default: str
) -> str:
    if key not in args or args[key] is None:
        return default
    value = args[key]
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"argument {key} must be one of {sorted(allowed)}")
    return value


def _corner_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("argument corners must be a list")
    out: list[str] = []
    for corner in value:
        if not isinstance(corner, str) or corner not in TERRAFORM_CORNERS:
            raise ValueError("argument corners must contain only w/s/e/n")
        if corner in out:
            raise ValueError("argument corners must not contain duplicates")
        out.append(corner)
    return out


def _require_str(args: dict[str, Any], key: str) -> str:
    if key not in args:
        raise ValueError(f"missing required argument: {key}")
    value = args[key]
    if not isinstance(value, str) or value == "":
        raise ValueError(f"argument {key} must be a non-empty string")
    return value


def _int_list(value: Any, key: str) -> list[int]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"argument {key} must be a non-empty list")
    out: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            raise ValueError(f"argument {key} must contain only integers")
        out.append(item)
    return out


def _order_flag_mask(value: Any, key: str) -> int:
    if value is None:
        return 0
    if not isinstance(value, list):
        raise ValueError(f"argument {key} must be a list of order flag names")
    mask = 0
    seen: set[str] = set()
    load_flags_seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"argument {key} must contain only order flag names")
        if item not in ORDER_FLAG_BITS:
            raise ValueError(
                f"unknown order flag {item!r}; expected one of {tuple(ORDER_FLAG_BITS)}"
            )
        if item in seen:
            raise ValueError(f"duplicate order flag {item!r}")
        seen.add(item)
        if item in MUTUALLY_EXCLUSIVE_LOAD_FLAGS:
            load_flags_seen.add(item)
        mask |= ORDER_FLAG_BITS[item]
    if len(load_flags_seen) > 1:
        raise ValueError("FULL_LOAD, FULL_LOAD_ANY, and NO_LOAD are mutually exclusive")
    return mask


def _append_orders_flag_masks(
    args: dict[str, Any], stop_tiles: list[int]
) -> list[int] | None:
    if "flags" not in args or args.get("flags") is None:
        return None
    flags = args["flags"]
    if not isinstance(flags, list):
        raise ValueError(
            "argument flags must be a list of order flag names or one list per stop"
        )
    if not flags:
        return [0 for _ in stop_tiles]
    if all(isinstance(item, str) for item in flags):
        return [_order_flag_mask(flags, "flags") for _ in stop_tiles]
    if all(isinstance(item, list) for item in flags):
        if len(flags) != len(stop_tiles):
            raise ValueError(
                "argument flags must have one entry per stop tile when nested"
            )
        return [_order_flag_mask(item, "flags") for item in flags]
    raise ValueError(
        "argument flags must contain either only strings or only lists of strings"
    )


def _conditional_order_values(args: dict[str, Any]) -> tuple[int, int, int]:
    condition = _require_str_enum(args, "condition", set(CONDITIONAL_ORDER_CONDITIONS))
    compare = _require_str_enum(args, "compare", set(CONDITIONAL_ORDER_COMPARES))
    value = _require_int_between(args, "value", 0, CONDITIONAL_VALUE_MAX)

    if condition == CONDITIONAL_BOOLEAN_CONDITION:
        if compare not in CONDITIONAL_BOOLEAN_COMPARES:
            raise ValueError("REQUIRES_SERVICE compare must be IS_TRUE or IS_FALSE")
        if value != 0:
            raise ValueError("REQUIRES_SERVICE value must be 0")
    elif condition == CONDITIONAL_UNCONDITIONAL_CONDITION:
        if compare != "EQUALS" or value != 0:
            raise ValueError("UNCONDITIONALLY requires compare EQUALS and value 0")
    else:
        if compare not in CONDITIONAL_NUMERIC_COMPARES:
            raise ValueError(
                f"{condition} compare must be one of {sorted(CONDITIONAL_NUMERIC_COMPARES)}"
            )
        if condition in CONDITIONAL_PERCENT_CONDITIONS and value > 100:
            raise ValueError(f"{condition} value must be between 0 and 100")

    return (
        CONDITIONAL_ORDER_CONDITIONS[condition],
        CONDITIONAL_ORDER_COMPARES[compare],
        value,
    )


def command_for_tool(name: str, arguments: dict[str, Any]) -> list[Any]:
    if name == "get_tile":
        command = [name, _require_int(arguments, "tile")]
        cargo_id = _optional_int_between(
            arguments, "cargo_id", CARGO_ID_MIN, CARGO_ID_MAX
        )
        if cargo_id is not None:
            command.append(cargo_id)
        return command
    if name == "get_region":
        detail = arguments.get("detail", "tiles")
        if (
            not isinstance(detail, str)
            or detail not in QUERY_REGION_RADIUS_MAX_BY_DETAIL
        ):
            raise ValueError(DETAIL_ERROR)
        maximum = QUERY_REGION_RADIUS_MAX_BY_DETAIL[detail]
        center_tile = _require_int(arguments, "center_tile")
        radius = _require_int(arguments, "radius")
        if radius < QUERY_REGION_RADIUS_MIN or radius > maximum:
            raise ValueError(
                f"argument radius must be between {QUERY_REGION_RADIUS_MIN} and {maximum} "
                f"with detail='{detail}'"
            )
        command = [name, center_tile, radius]
        cargo_id = _optional_int_between(
            arguments, "cargo_id", CARGO_ID_MIN, CARGO_ID_MAX
        )
        if cargo_id is not None:
            command.append(cargo_id)
        return command
    if name in {
        "list_subsidies",
        "list_towns",
        "list_my_vehicles",
        "list_my_stations",
        "list_my_depots",
        "get_my_finances",
        "get_my_company",
        "list_my_route_economics",
        "list_industries",
    }:
        if arguments:
            raise ValueError(f"{name} takes no arguments")
        return [name]
    if name == "get_vehicle":
        return [name, _require_int(arguments, "vehicle_id")]
    if name == "get_cargo_payment_rate":
        return [
            name,
            _require_int_between(arguments, "cargo_id", CARGO_ID_MIN, CARGO_ID_MAX),
            _require_int_between(
                arguments, "distance", CARGO_DISTANCE_MIN, CARGO_DISTANCE_MAX
            ),
            _require_int_between(
                arguments,
                "days_in_transit",
                CARGO_TRANSIT_DAYS_MIN,
                CARGO_TRANSIT_DAYS_MAX,
            ),
        ]
    if name == "get_industry":
        industry_id = _require_int(arguments, "industry_id")
        if industry_id < 0:
            raise ValueError("argument industry_id must be a non-negative integer")
        return [name, industry_id]
    if name == "get_town":
        town_id = _require_int(arguments, "town_id")
        if town_id < 0:
            raise ValueError("argument town_id must be a non-negative integer")
        return [name, town_id]
    if name == "list_engines":
        # Flat bridge command style: booleans travel as 0/1 ints.
        return [
            name,
            1 if _optional_bool(arguments, "only_buildable", True) else 0,
            1 if _optional_bool(arguments, "passenger_only", False) else 0,
        ]
    if name == "list_bridge_types":
        return [name, _require_int_at_least(arguments, "length", BRIDGE_LENGTH_MIN)]
    if name == "get_my_station":
        # Exactly one identifier; the absent slot travels as -1 in the flat
        # command, so a negative identifier must be rejected here.
        has_station_id = arguments.get("station_id") is not None
        has_tile = arguments.get("tile") is not None
        if has_station_id == has_tile:
            raise ValueError(
                "get_my_station requires exactly one of station_id or tile"
            )
        key = "station_id" if has_station_id else "tile"
        value = _require_int(arguments, key)
        if value < 0:
            raise ValueError(f"argument {key} must be a non-negative integer")
        return [name, value, -1] if has_station_id else [name, -1, value]
    if name == "check_truck_stop_catchment":
        tile = _require_int(arguments, "tile")
        if tile < 0:
            raise ValueError("argument tile must be a non-negative integer")
        return [name, tile]
    if name in {"build_road", "check_build_road"}:
        # Path order is from -> waypoints... -> to. The bridge builds the
        # flat point list pairwise in command order, so waypoints must sit
        # BETWEEN the endpoints — appended after to_tile they made the
        # bridge attempt the direct (possibly blocked) span first and visit
        # the waypoints afterwards.
        command = [name, _require_int(arguments, "from_tile")]
        waypoints = arguments.get("waypoints") or []
        if not isinstance(waypoints, list):
            raise ValueError("argument waypoints must be a list")
        for waypoint in waypoints:
            if isinstance(waypoint, bool) or not isinstance(waypoint, int):
                raise ValueError("argument waypoints must contain only integers")
            command.append(waypoint)
        command.append(_require_int(arguments, "to_tile"))
        return command
    if name == "build_one_way_road":
        return [
            name,
            _require_int_at_least(arguments, "from_tile", 0),
            _require_int_at_least(arguments, "to_tile", 0),
        ]
    if name == "build_bridge":
        return [
            name,
            _require_int(arguments, "start_tile"),
            _require_int(arguments, "end_tile"),
            _require_int(arguments, "bridge_id"),
        ]
    if name == "build_tunnel":
        return [name, _require_int(arguments, "start_tile")]
    if name == "terraform_land":
        tile = _require_int(arguments, "tile")
        mode = _require_str_enum(arguments, "mode", set(TERRAFORM_MODES))
        corners = _corner_list(arguments.get("corners", []))
        has_to_tile = arguments.get("to_tile") is not None
        # Reject the cross-mode argument instead of silently dropping it, so the
        # result can never echo a substituted sentinel (corners:[], to_tile:-1)
        # that contradicts what the caller actually passed.
        if mode == "level_to":
            if corners:
                raise ValueError("terraform_land level_to takes to_tile, not corners")
            return [name, tile, mode, _require_int(arguments, "to_tile")]
        if has_to_tile:
            raise ValueError("terraform_land raise/lower takes corners, not to_tile")
        if not corners:
            raise ValueError("terraform_land raise/lower requires at least one corner")
        return [name, tile, mode, -1, *corners]
    if name in {
        "build_bus_stop_drive_through",
        "build_truck_stop_drive_through",
        "build_bus_stop_bay",
        "build_truck_stop_bay",
    }:
        tile = _require_int(arguments, "tile")
        front_tile = _require_int(arguments, "front_tile")
        join = _optional_str_enum(arguments, "join", set(STATION_JOIN_MODES), "new")
        if join != "station_id" and arguments.get("station_id") is not None:
            raise ValueError("station_id requires join 'station_id'")
        if join == "new":
            return [name, tile, front_tile]
        if join == "adjacent":
            return [name, tile, front_tile, "adjacent"]
        station_id = _optional_non_negative_int(arguments, "station_id")
        if station_id is None:
            raise ValueError("join 'station_id' requires station_id")
        return [name, tile, front_tile, station_id]
    if name in {"build_road_depot", "check_build_road_depot"}:
        return [
            name,
            _require_int(arguments, "tile"),
            _require_int(arguments, "front_tile"),
        ]
    if name == "buy_vehicle":
        return [
            name,
            _require_int(arguments, "depot_tile"),
            _require_int(arguments, "engine_id"),
        ]
    if name == "clone_vehicle":
        return [
            name,
            _require_int(arguments, "depot_tile"),
            _require_int(arguments, "vehicle_id"),
            1 if _require_bool(arguments, "share_orders") else 0,
        ]
    if name == "create_group":
        return [name, _require_str(arguments, "name")]
    if name == "move_vehicle_to_group":
        return [
            name,
            _require_int(arguments, "vehicle_id"),
            _require_int(arguments, "group_id"),
        ]
    if name == "set_group_autoreplace":
        return [
            name,
            _require_int(arguments, "group_id"),
            _require_int(arguments, "engine_id_old"),
            _require_int(arguments, "engine_id_new"),
        ]
    if name == "insert_order":
        command = [
            name,
            _require_int(arguments, "vehicle_id"),
            _require_int(arguments, "position"),
            _require_int(arguments, "stop_tile"),
        ]
        if "flags" in arguments and arguments.get("flags") is not None:
            command.append(_order_flag_mask(arguments.get("flags"), "flags"))
        return command
    if name == "delete_order":
        return [
            name,
            _require_int(arguments, "vehicle_id"),
            _require_int(arguments, "position"),
        ]
    if name == "set_order_flags":
        # flags is required and its presence carries intent: reject an explicit
        # null the same as absence. An empty list is the only legitimate way to
        # clear every flag; null must not silently mask that to OF_NONE.
        if arguments.get("flags") is None:
            raise ValueError("missing required argument: flags")
        return [
            name,
            _require_int(arguments, "vehicle_id"),
            _require_int(arguments, "position"),
            _order_flag_mask(arguments.get("flags"), "flags"),
        ]
    if name == "append_orders":
        # command[2] is an explicit mode discriminator so the bridge never has
        # to guess between the two payload shapes: 0 = flagless (bare stop tiles
        # follow), 1 = flagged (stop, flag_mask pairs follow). A stop tile can
        # take any value, so length arithmetic alone (the old scheme) aliased a
        # flagless list onto the flagged form whenever the first tile happened
        # to equal the pair count.
        stop_tiles = _int_list(arguments.get("stop_tiles"), "stop_tiles")
        flag_masks = _append_orders_flag_masks(arguments, stop_tiles)
        if flag_masks is None:
            return [name, _require_int(arguments, "vehicle_id"), 0, *stop_tiles]
        command = [name, _require_int(arguments, "vehicle_id"), 1]
        for stop_tile, flag_mask in zip(stop_tiles, flag_masks, strict=True):
            command.extend([stop_tile, flag_mask])
        return command
    if name == "insert_conditional_order":
        condition, compare, value = _conditional_order_values(arguments)
        return [
            name,
            _require_int_at_least(arguments, "vehicle_id", 0),
            _require_int_at_least(arguments, "position", 0),
            condition,
            compare,
            value,
            _require_int_at_least(arguments, "jump_to", 0),
        ]
    if name == "skip_to_order":
        return [
            name,
            _require_int_at_least(arguments, "vehicle_id", 0),
            _require_int_at_least(arguments, "position", 0),
        ]
    if name in {"copy_orders", "share_orders"}:
        return [
            name,
            _require_int_at_least(arguments, "vehicle_id", 0),
            _require_int_at_least(arguments, "source_vehicle_id", 0),
        ]
    if name == "unshare_orders":
        return [name, _require_int_at_least(arguments, "vehicle_id", 0)]
    if name == "refit_vehicle":
        vehicle_id = _require_int(arguments, "vehicle_id")
        cargo_id = _require_int_between(
            arguments, "cargo_id", CARGO_ID_MIN, CARGO_ID_MAX
        )
        if vehicle_id < 0:
            raise ValueError("argument vehicle_id must be a non-negative integer")
        return [name, vehicle_id, cargo_id]
    if name == "reverse_vehicle":
        return [name, _require_int_at_least(arguments, "vehicle_id", 0)]
    if name in {
        "start_vehicle",
        "stop_vehicle",
        "send_vehicle_to_depot",
        "sell_vehicle",
    }:
        return [name, _require_int(arguments, "vehicle_id")]
    if name == "set_loan":
        return [name, _require_int(arguments, "amount")]
    if name == "demolish_tile":
        return [name, _require_int(arguments, "tile")]
    if name == "remove_road":
        return [
            name,
            _require_int(arguments, "from_tile"),
            _require_int(arguments, "to_tile"),
        ]
    if name == "wait":
        return [
            name,
            _require_int_between(
                arguments, "duration_days", WAIT_DAYS_MIN, WAIT_DAYS_MAX
            ),
        ]
    if name == "check_route_connectivity":
        stop_tiles = _int_list(arguments.get("stop_tiles"), "stop_tiles")
        if len(stop_tiles) > ROUTE_CONNECTIVITY_STOP_MAX:
            raise ValueError(
                "argument stop_tiles must contain at most "
                f"{ROUTE_CONNECTIVITY_STOP_MAX} items"
            )
        return [
            name,
            _require_int(arguments, "town_id"),
            _require_int(arguments, "depot_tile"),
            _require_int(arguments, "depot_front_tile"),
            *stop_tiles,
        ]
    if name == "wait_and_observe_vehicle":
        if "advance_days" in arguments:
            advance_days = _require_int_between(
                arguments, "advance_days", WAIT_DAYS_MIN, WAIT_DAYS_MAX
            )
        else:
            advance_days = WAIT_VEHICLE_DEFAULT_DAYS
        return [name, _require_int(arguments, "vehicle_id"), advance_days]
    if name == "wait_and_observe_station":
        if "advance_days" in arguments:
            advance_days = _require_int_between(
                arguments, "advance_days", WAIT_DAYS_MIN, WAIT_DAYS_MAX
            )
        else:
            advance_days = WAIT_STATION_DEFAULT_DAYS
        return [
            name,
            _require_int_at_least(arguments, "station_id", 0),
            advance_days,
        ]
    raise ValueError(f"unknown tool: {name}")


class _SubjectScreenshotResult(dict[str, Any]):
    """Dictionary result carrying image bytes outside its JSON representation."""

    image_bytes: bytes | None = None


class GameToolServer:
    """Pure handler for OpenTTD gameplay tools."""

    def __init__(
        self,
        dispatcher: Any | None,
        *,
        tool_log: Path,
        feedback_log: Path | None = None,
        enable_skill_path: Path | None = None,
        registry_path: Path | None = None,
        decision_budget: int | None = None,
        budget_echo: bool = True,
        budget_override_path: Path | None = None,
        experiment: str = DEFAULT_EXPERIMENT,
        end_episode_mode: str = "standard",
        tool_index_channel: str | None = None,
        vision_enabled: bool = False,
        admin_config: Any | None = None,
        screenshot_dir: Path | None = None,
        crops_dir: Path | None = None,
        map_log2: int = 7,
    ) -> None:
        self.dispatcher = dispatcher
        self.tool_log = tool_log
        self.experiment = experiment
        self.feedback_log = (
            feedback_log
            if feedback_log is not None
            else tool_log.parent / "feedback.jsonl"
        )
        self.enable_skill_path = enable_skill_path
        self.call_counter = 0
        self.feedback_counter = 0
        self.decision_budget = decision_budget
        self.budget_echo = budget_echo
        self.end_episode_mode = _validate_end_episode_mode(end_episode_mode)
        self.vision_enabled = vision_enabled
        self.admin_config = admin_config
        self.screenshot_dir = screenshot_dir
        self.crops_dir = crops_dir
        self.map_log2 = map_log2
        self.screenshot_source = None
        self.decisions_used = 0
        # A budget cut (end_episode / monitor override) sets remaining budget to
        # zero without touching decision_budget, so used/total stay factual.
        self.budget_cut: dict[str, Any] | None = None
        self.budget_override_path = (
            budget_override_path
            if budget_override_path is not None
            else tool_log.parent / BUDGET_OVERRIDE_FILENAME
        )
        self._lock = threading.Lock()
        self.skill: dict[str, Any] | None = None
        if enable_skill_path is not None:
            self.skill = migrate_retired_tool_names(
                json.loads(enable_skill_path.read_text())
            )
        snapshot = registry_snapshot(
            enable_skill=self.skill is not None,
            experiment=self.experiment,
            end_episode_mode=self.end_episode_mode,
            enable_vision=self.vision_enabled,
            tool_index_channel=tool_index_channel,
        )
        if registry_path is not None:
            write_json(registry_path, snapshot)

    @property
    def skill_enabled(self) -> bool:
        return self.skill is not None

    def _next_call_id(self, name: str) -> str:
        self.call_counter += 1
        return f"tool-{self.call_counter:04d}-{name}"

    def _timestamp(self, call_id: str) -> str:
        # The prefix stamps every bridge command/evidence timestamp with the
        # run's experiment identity (sanitized like the call id).
        prefix = "".join(
            ch if ch.isalnum() or ch in "-_" else "-" for ch in self.experiment
        )
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in call_id)
        return f"{prefix}-{int(time.time() * 1000)}-{safe}"

    def _tool_call_record_header(
        self,
        *,
        call_id: str,
        name: str,
        arguments: dict[str, Any],
        started_at_unix: float,
    ) -> dict[str, Any]:
        """Build the fields every ``tool_calls.jsonl`` record starts with."""
        return {
            "id": call_id,
            "name": name,
            "arguments": arguments,
            "started_at_unix": started_at_unix,
            "started_at_monotonic_ns": time.perf_counter_ns(),
            # Every evidence record is stamped, not only bridge-dispatched
            # ones (the final end_episode record once carried no timestamp).
            "timestamp": self._timestamp(call_id),
        }

    def _finalize_tool_call_record(
        self, record: dict[str, Any], *, finished_at_unix: float | None = None
    ) -> None:
        """Complete and append one ``tool_calls.jsonl`` record."""
        finished_at_monotonic_ns = time.perf_counter_ns()
        record["finished_at_unix"] = (
            time.time() if finished_at_unix is None else finished_at_unix
        )
        record["finished_at_monotonic_ns"] = finished_at_monotonic_ns
        started_at_monotonic_ns = record["started_at_monotonic_ns"]
        record["duration_ns"] = finished_at_monotonic_ns - started_at_monotonic_ns
        result = record.get("result")
        if record.get("timing_status") == "timeout" or (
            isinstance(result, dict) and result.get("status") == "timeout"
        ):
            timing_status = "timeout"
        elif record.get("name") == "budget_override_applied" or (
            isinstance(result, dict) and result.get("error") == EPISODE_STOP_SIGNAL
        ):
            timing_status = "skipped"
        elif "result" not in record:
            timing_status = "unmatched_interruption"
        elif record.get("transport_ok") is False or record.get("ok") is False:
            timing_status = "failure"
        else:
            timing_status = "success"
        record["timing_status"] = timing_status
        result_bytes = payload_bytes(record.get("result"))
        record["result_bytes"] = result_bytes
        record["result_token_estimate"] = result_bytes // TOKEN_BYTES_HEURISTIC
        # Best-effort per-turn context usage: codex exec surfaces only an
        # end-of-run usage aggregate, never per-call usage, to this
        # subprocess — so these stay null (a documented limitation).
        record["prompt_tokens"] = None
        record["total_context_tokens"] = None
        record["compaction_fired"] = None
        append_jsonl(self.tool_log, record)

    def budget_remaining(self) -> int | None:
        """Remaining decisions: 0 after a cut, None when no budget is enforced."""
        if self.budget_cut is not None:
            return 0
        if self.decision_budget is None:
            return None
        return max(0, self.decision_budget - self.decisions_used)

    def _apply_budget_override(self) -> None:
        """Pick up an out-of-band budget cut (monitor abort sentinel) and log it.

        The sentinel is checked once per tool call; an applied override is
        itself appended to ``tool_calls.jsonl`` so the evidence records when
        and why the budget was cut.
        """
        if self.budget_cut is not None or not self.budget_override_path.exists():
            return
        try:
            payload = json.loads(self.budget_override_path.read_text())
            if not isinstance(payload, dict):
                payload = {"reason": str(payload)}
        except (json.JSONDecodeError, OSError) as exc:
            payload = {"reason": f"unparseable budget_override.json: {exc}"}
        now = time.time()
        self.budget_cut = {
            "source": "budget_override",
            "reason": payload.get("reason"),
            "payload": payload,
            "applied_at_unix": now,
        }
        if self.decision_budget is not None:
            self.budget_cut["decisions_used"] = self.decisions_used
        record = self._tool_call_record_header(
            call_id=f"budget-override-{int(now * 1000)}",
            name="budget_override_applied",
            arguments=payload,
            started_at_unix=now,
        )
        record["command"] = None
        record["transport_ok"] = True
        record["ok"] = True
        record["result"] = dict(self.budget_cut)
        # The old override record was instantaneous: retain its matching
        # start/finish values while sharing normal record finalization.
        self._finalize_tool_call_record(record, finished_at_unix=now)

    def _stop_result(self, name: str) -> dict[str, Any]:
        result: dict[str, Any] = {
            "ok": False,
            "action": name,
            "error": EPISODE_STOP_SIGNAL,
            "note": (
                "This episode has ended. report_feedback remains available; "
                "close with your POSTMORTEM message."
            ),
        }
        if self.budget_echo:
            result["budget_cut"] = self.budget_cut
            if self.decision_budget is not None:
                result.update(
                    {
                        "decisions_used": self.decisions_used,
                        "decision_budget": self.decision_budget,
                    }
                )
        return result

    def _stamp_budget_fields(self, result: dict[str, Any]) -> dict[str, Any]:
        """Attach factual post-call budget counters to a tool result.

        When ``budget_echo`` is disabled, the result is returned unstamped.
        The model-visible MCP result and the ``tool_calls.jsonl`` record's
        ``result`` field deliberately share this dict, so the evidence records
        exactly what the model saw; external counting must use records rather
        than these optional echoed fields.

        With echo enabled, fields appear only while a budget is in force (an
        enforced ``--decision-budget`` or an applied cut); unlimited episodes
        stay unchanged. Counters reflect the state AFTER the current call was
        counted, so ``decisions_remaining`` can never contradict a post-cut
        ``stop`` refusal.
        """
        if not self.budget_echo:
            return result
        if self.decision_budget is None:
            return result
        if (
            self.end_episode_mode == "distress"
            and result.get("action") == END_EPISODE_TOOL_NAME
        ):
            return result
        remaining = self.budget_remaining()
        if remaining is None:
            return result
        result["decisions_used"] = self.decisions_used
        result["decisions_remaining"] = remaining
        return result

    @staticmethod
    def _merge_exception_tool_result_fields(
        result: dict[str, Any], exc: Exception
    ) -> None:
        structured_fields = getattr(exc, "tool_result_fields", None)
        if not isinstance(structured_fields, dict):
            return
        for key, value in structured_fields.items():
            if key not in _CANONICAL_ERROR_RESULT_FIELDS:
                result[key] = value

    def _dispatch_wait(
        self, duration_days: int, record: dict[str, Any]
    ) -> dict[str, Any]:
        """Dispatch a single `wait` and return an honest timeout result.

        The root fix (`main.nut`: the `wait` command handler now emits the lean
        `EmitGeneralObservation` like every other exposed primitive) removed the
        vestigial candidate-finding observation (~106 s at 128x128) that used to
        run after every `wait`. That scan stalled the single-threaded bridge and
        pushed the *next* command's dispatch past the 240 s ceiling and caused
        spurious timeouts. With it gone, a 90-day wait on
        a populated 128x128 game confirms in ~180 s (measured), under the ceiling.

        If a busier company's advance still fails to confirm in time, the
        dispatcher's `TimeoutError` becomes an honest `status:"timeout"` result
        instead of a bare error string: the game may have advanced and the company
        kept operating (the single-threaded bridge can still be draining the
        wait), so the model is told to re-observe for the true date rather than
        being unable to tell still-running from failed from safe-to-retry. On
        success the bridge token is returned unchanged plus `status:"complete"`.
        """
        command = command_for_tool("wait", {"duration_days": duration_days})
        record["command"] = command
        assert self.dispatcher is not None
        try:
            result = self.dispatcher.dispatch(command, record["timestamp"])
        except TimeoutError as exc:
            return {
                "ok": False,
                "action": "wait",
                "status": "timeout",
                "requested_duration_days": int(duration_days),
                "error": str(exc),
                "note": (
                    "This wait was not confirmed in time. The game may have advanced anyway -- "
                    "none, some, or all of the requested days may already have passed with the "
                    "company still operating. Call get_my_company or get_my_finances (both report the "
                    "current date) to read the true current date BEFORE acting. Do not blindly "
                    "re-issue the full wait; re-observe first, then wait only the days still "
                    "needed."
                ),
            }
        if isinstance(result, dict) and result.get("ok", True):
            # Only a successful advance is `status:"complete"`. Guard on ok so a
            # bridge-side ok:false wait envelope is never labeled complete (that
            # would be the self-contradictory result this exists to remove); the
            # real bridge only prints ok:true for a well-formed wait today, so
            # this is defensive.
            result.setdefault("status", "complete")
        return result

    def _dispatch_time_advancing_observation(
        self, name: str, arguments: dict[str, Any], record: dict[str, Any]
    ) -> dict[str, Any]:
        """Dispatch one observation whose timeout cannot prove elapsed game time."""
        command = command_for_tool(name, arguments)
        record["command"] = command
        assert self.dispatcher is not None
        try:
            result = self.dispatcher.dispatch(command, record["timestamp"])
        except TimeoutError as exc:
            return {
                "ok": False,
                "action": name,
                "status": "timeout",
                "error": "bridge_timeout",
                "timeout_detail": str(exc),
                "requested_advance_days": command[2],
                "game_mutated": True,
                "time_may_have_advanced": True,
                "note": (
                    "The observation was not confirmed before this call stopped waiting. Game time "
                    "may have advanced by none, some, or all of the requested days. Read the current "
                    "date with get_my_company or get_my_finances before deciding whether to advance "
                    "time again."
                ),
            }
        if isinstance(result, dict) and result.get("ok", True):
            result.setdefault("status", "complete")
        return result

    def handle_tool_call(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        with self._lock:
            return self._handle_tool_call_unlocked(name, arguments)

    def _handle_tool_call_unlocked(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        arguments = dict(arguments or {})
        self._apply_budget_override()
        call_id = self._next_call_id(name)
        started_at = time.time()
        record = self._tool_call_record_header(
            call_id=call_id,
            name=name,
            arguments=arguments,
            started_at_unix=started_at,
        )
        try:
            exhausted = self.budget_remaining() == 0
            if exhausted and name != FEEDBACK_TOOL_NAME:
                # Factual refusal, stated in the episode rules — never hidden
                # state. report_feedback stays callable so the run's most
                # valuable output is never starved.
                record["command"] = None
                result = self._stamp_budget_fields(self._stop_result(name))
                record["transport_ok"] = True
                record["ok"] = False
                record["result"] = result
                return result
            if not exhausted and name != FEEDBACK_TOOL_NAME:
                self.decisions_used += 1
            if "screenshot" in arguments and name not in SUBJECT_SCREENSHOT_TOOLS:
                raise ValueError(f"{name} does not accept a screenshot argument")
            if name == SKILL_TOOL_NAME:
                result = self._handle_skill(arguments)
                record["command"] = None
            elif name == FEEDBACK_TOOL_NAME:
                result = self._handle_report_feedback(arguments, call_id)
                record["command"] = None
            elif name == END_EPISODE_TOOL_NAME:
                result = self._handle_end_episode(arguments, call_id)
                record["command"] = None
            elif name == VIEW_MAP_TOOL_NAME:
                result = self._handle_view_map(arguments, call_id)
                record["command"] = None
            elif name == "wait":
                # Single dispatch, but a dispatcher TimeoutError is turned
                # into an honest status='timeout' result (not a bare error).
                # Intercepted before the generic
                # primitive path below; `wait` stays in PRIMITIVE_TOOLS for
                # registry/spec purposes.
                if self.dispatcher is None:
                    raise BridgeError(f"tool {name} requires a bridge dispatcher")
                result = self._dispatch_wait(
                    _require_int_between(
                        arguments, "duration_days", WAIT_DAYS_MIN, WAIT_DAYS_MAX
                    ),
                    record,
                )
            elif name in {"wait_and_observe_vehicle", "wait_and_observe_station"}:
                if self.dispatcher is None:
                    raise BridgeError(f"tool {name} requires a bridge dispatcher")
                result = self._dispatch_time_advancing_observation(
                    name, arguments, record
                )
            elif name in PRIMITIVE_TOOLS or name in QUERY_TOOLS:
                if self.dispatcher is None:
                    raise BridgeError(f"tool {name} requires a bridge dispatcher")
                screenshot_requested = (
                    _optional_bool(arguments, "screenshot", False)
                    if name in SUBJECT_SCREENSHOT_TOOLS
                    else False
                )
                query_region_options = (
                    validate_query_region_args(arguments)
                    if name == "get_region"
                    else None
                )
                # `screenshot` controls Python-side presentation only. The
                # Squirrel command remains a factual subject query, so the
                # bridge never receives a transport concern.
                command_arguments = dict(arguments)
                if name in SUBJECT_SCREENSHOT_TOOLS:
                    command_arguments.pop("screenshot", None)
                command = command_for_tool(name, command_arguments)
                record["command"] = command
                result = self.dispatcher.dispatch(command, record["timestamp"])
                if (
                    name == "get_region"
                    and isinstance(result, dict)
                    and query_region_options is not None
                ):
                    result = process_query_region_result(result, query_region_options)
                if (
                    screenshot_requested
                    and isinstance(result, dict)
                    and result.get("ok", True)
                ):
                    result = _SubjectScreenshotResult(result)
                    self._attach_subject_screenshot(name, result, call_id)
            else:
                raise ValueError(f"unknown tool: {name}")
            if isinstance(result, dict):
                # Stamped after the handler ran, so an end_episode cut is
                # already reflected. The MCP result returned to the model and
                # the tool_calls.jsonl record share this dict.
                self._stamp_budget_fields(result)
            record["transport_ok"] = True
            record["ok"] = (
                bool(result.get("ok", True)) if isinstance(result, dict) else True
            )
            if (
                self.screenshot_source is not None
                and name in MUTATING_TOOL_NAMES
                and (
                    record.get("ok")
                    or (isinstance(result, dict) and result.get("game_mutated") is True)
                )
            ):
                # Real-time sim: a successful build does not refresh the render
                # automatically; it just marks the cached frame as drifted so
                # the model sees mutations_since_capture climb.
                self.screenshot_source.note_mutation()
            record["result"] = result
            return result
        except EpisodeStopRequested:
            self._apply_budget_override()
            stopped = self._stamp_budget_fields(self._stop_result(name))
            record["transport_ok"] = True
            record["ok"] = False
            record["result"] = stopped
            return stopped
        except Exception as exc:  # noqa: BLE001 - tool errors must be transcript-visible
            error_result = self._stamp_budget_fields(
                {"ok": False, "action": name, "error": str(exc)}
            )
            self._merge_exception_tool_result_fields(error_result, exc)
            record["transport_ok"] = False
            record["ok"] = False
            record["result"] = error_result
            if isinstance(exc, TimeoutError):
                record["timing_status"] = "timeout"
            return error_result
        finally:
            self._finalize_tool_call_record(record)

    @staticmethod
    def _subject_tile(name: str, result: dict[str, Any]) -> int:
        """Resolve the current tile already returned by one subject read."""
        if name == "get_tile":
            subject = result.get("tile_info")
            tile = subject.get("tile") if isinstance(subject, dict) else None
        elif name == "get_vehicle":
            subject = result.get("vehicle")
            tile = subject.get("tile") if isinstance(subject, dict) else None
        elif name == "get_my_station":
            tile = result.get("tile")
        else:  # pragma: no cover - caller is gated by SUBJECT_SCREENSHOT_TOOLS
            raise ValueError(f"{name} does not support a subject screenshot")
        if isinstance(tile, bool) or not isinstance(tile, int):
            raise ValueError(f"{name} returned no integer subject tile")
        return tile

    def _attach_subject_screenshot(
        self, name: str, result: _SubjectScreenshotResult, call_id: str
    ) -> None:
        """Attach one fresh crop without changing the successful factual read."""
        try:
            tile = self._subject_tile(name, result)
        except Exception as exc:  # noqa: BLE001 - bounded model error, exact evidence
            result["screenshot"] = {
                "ok": False,
                "action": VIEW_MAP_TOOL_NAME,
                "error": "screenshot_unavailable",
                "reason": "subject_tile_unavailable",
                "error_detail": str(exc),
            }
            return

        try:
            screenshot = self._ensure_screenshot_source().crop_for_tile(
                tile,
                DEFAULT_WINDOW,
                refresh=True,
                call_id=call_id,
            )
            if not screenshot.get("ok", True):
                result["screenshot"] = screenshot
                return
            path = screenshot.get("path")
            if not isinstance(path, str) or not path:
                raise ValueError("successful screenshot capture returned no path")
            image_bytes = Path(path).read_bytes()
            if not image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("captured screenshot is not a PNG image")
            result.image_bytes = image_bytes
            result["screenshot"] = screenshot
        except Exception as exc:  # noqa: BLE001 - preserve facts and log exact failure
            result["screenshot"] = {
                "ok": False,
                "action": VIEW_MAP_TOOL_NAME,
                "tile": tile,
                "window": DEFAULT_WINDOW,
                "error": "screenshot_unavailable",
                "reason": (
                    "vision_disabled" if not self.vision_enabled else "capture_failed"
                ),
                # Evidence-only diagnostic. MCP wrappers remove this field so
                # an exception containing a local path never reaches the model.
                "error_detail": str(exc),
            }

    def _ensure_screenshot_source(self) -> ScreenshotSource:
        if not self.vision_enabled:
            raise ValueError("vision is not enabled on this server")
        if self.screenshot_source is None:
            if self.admin_config is None or self.screenshot_dir is None:
                raise RuntimeError(
                    "vision requires --admin-port, --screenshot-dir, and the OPENTTD_ADMIN_PASSWORD env var"
                )
            crops = (
                self.crops_dir
                if self.crops_dir is not None
                else self.tool_log.parent / "screenshots"
            )
            self.screenshot_source = ScreenshotSource(
                self.admin_config,
                self.screenshot_dir,
                crops,
                map_log2=self.map_log2,
            )
        return self.screenshot_source

    def _handle_view_map(
        self, arguments: dict[str, Any], call_id: str
    ) -> dict[str, Any]:
        tile = _require_int(arguments, "tile")
        window = arguments.get("window", DEFAULT_WINDOW)
        if isinstance(window, bool) or not isinstance(window, int):
            raise ValueError("window must be an integer")
        refresh = _optional_bool(arguments, "refresh", DEFAULT_REFRESH)
        # The advertised schema bounds are not enforced by FastMCP's generated
        # schema, so the server rejects values outside them.
        if window < WINDOW_MIN or window > WINDOW_MAX:
            raise ValueError(
                f"argument window must be between {WINDOW_MIN} and {WINDOW_MAX}"
            )
        return self._ensure_screenshot_source().crop_for_tile(
            tile, window, refresh=refresh, call_id=call_id
        )

    def _handle_report_feedback(
        self, arguments: dict[str, Any], call_id: str
    ) -> dict[str, Any]:
        entry = validate_report_feedback(arguments)
        self.feedback_counter += 1
        entry.update(
            {
                "tool_call_id": call_id,
                "turn_index": self.call_counter,
                "entry_index": self.feedback_counter,
                "recorded_at_unix": time.time(),
            }
        )
        append_feedback(self.feedback_log, entry)
        return {
            "ok": True,
            "action": FEEDBACK_TOOL_NAME,
            "recorded": True,
            "kind": entry["kind"],
            "entry_index": self.feedback_counter,
            "note": (
                "Feedback recorded as run evidence only; it advances no game time and grants no new "
                "capability during this episode."
            ),
        }

    def _handle_end_episode(
        self, arguments: dict[str, Any], call_id: str
    ) -> dict[str, Any]:
        reason = arguments.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("end_episode requires a non-empty reason string")
        self.budget_cut = {
            "source": END_EPISODE_TOOL_NAME,
            "reason": reason.strip(),
            "tool_call_id": call_id,
            "decisions_used": self.decisions_used,
            "applied_at_unix": time.time(),
        }
        if self.end_episode_mode == "distress":
            return {
                "ok": True,
                "action": END_EPISODE_TOOL_NAME,
                "episode_ended": True,
                "reason": reason.strip(),
                "note": (
                    "Episode stopped because you cannot make meaningful progress. Gameplay tools now return "
                    f"{EPISODE_STOP_SIGNAL}; report_feedback remains available and your closing POSTMORTEM "
                    "message is still expected."
                ),
            }
        # Counters here would leak the budget's existence/size; only echo them
        # when budget_echo is on (they are stamped factually by
        # _stamp_budget_fields), never in the neutral-signal configuration.
        return {
            "ok": True,
            "action": END_EPISODE_TOOL_NAME,
            "episode_ended": True,
            "reason": reason.strip(),
            "note": (
                "This episode has ended. Gameplay tools now return "
                f"{EPISODE_STOP_SIGNAL}; report_feedback remains available and your "
                "closing POSTMORTEM message is still expected."
            ),
        }

    def _handle_skill(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.skill is None:
            raise ValueError(
                "get_bus_route_skill skill tool is not enabled in this arm"
            )
        skill_body = migrate_retired_tool_names(self.skill)
        town_id = _require_int(arguments, "town_id")
        bound_params: dict[str, Any] = {"town_id": town_id}
        if "engine_id" in arguments and arguments["engine_id"] is not None:
            bound_params["engine_id"] = _require_int(arguments, "engine_id")
        return {
            "ok": True,
            "action": SKILL_TOOL_NAME,
            "mode": "context_injection",
            "skill_name": skill_body.get("name", SKILL_TOOL_NAME),
            "skill_version": skill_body.get("version"),
            "skill_body": skill_body,
            "bound_params": bound_params,
            "guidance": (
                "You have loaded get_bus_route_skill. Continue by calling the PRIMITIVE tools yourself; "
                "bind every tile/front/engine from current observations. Check connectivity before "
                "buy/orders/start and observe operation after start; build a connector/repair if the "
                "facts show a non-functional route."
            ),
        }


def load_script(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if not isinstance(item, dict):
            raise ValueError(f"script line is not an object: {line!r}")
        records.append(item)
    return records


def run_scripted(
    server: GameToolServer, script_path: Path, no_llm_marker: Path | None = None
) -> int:
    if no_llm_marker is not None:
        no_llm_marker.parent.mkdir(parents=True, exist_ok=True)
        no_llm_marker.write_text("true\n")
    for item in load_script(script_path):
        name = item.get("name") or item.get("tool")
        if not isinstance(name, str):
            raise ValueError(f"script item missing tool name: {item!r}")
        result = server.handle_tool_call(name, item.get("arguments") or {})
        if not bool(result.get("ok", False)) and item.get("require_ok", True):
            print(json.dumps({"failed_tool": name, "result": result}, sort_keys=True))
            return 2
    return 0


def _without_host_details(value: Any) -> Any:
    """Copy a model result without evidence-only local transport details."""
    if isinstance(value, dict):
        return {
            key: _without_host_details(item)
            for key, item in value.items()
            if key not in {"path", "error_detail"}
        }
    if isinstance(value, list):
        return [_without_host_details(item) for item in value]
    return value


def build_fastmcp(server: GameToolServer):
    try:
        from mcp.server.fastmcp import FastMCP
    except Exception as exc:
        raise SystemExit(
            "The Python 'mcp' package is required for stdio serving. Run with: "
            "uv run --with mcp python3 -m openttd_mcp.server ..."
        ) from exc

    # Deliberately no `instructions`: codex code mode prepends a non-empty
    # instructions string to EVERY nested tool description in its ALL_TOOLS
    # registry (verified against codex 0.144.5 source and by repro), and
    # the string reaches the model through no other channel. The generated tool
    # index instead travels appended to the episode prompt by the caller —
    # exactly once, guaranteed.
    mcp = FastMCP(f"openttd-{server.experiment}")

    # tool_specs() is the single source of truth for model-visible descriptions.
    # Every decorator below pulls its text from here so the served MCP tool
    # descriptions cannot drift from what registry_snapshot() records.
    desc = {
        spec.name: spec.description
        for spec in tool_specs(
            enable_skill=server.skill_enabled,
            end_episode_mode=server.end_episode_mode,
            enable_vision=server.vision_enabled,
        )
    }
    if server.vision_enabled:
        # FastMCP otherwise lets Pydantic coerce strings and integers to bool
        # before the handler can enforce the registry's boolean contract.
        from pydantic import StrictBool

    def get_tile_result(
        tile: int, cargo_id: int | None, screenshot: bool | None = None
    ) -> dict[str, Any]:
        args: dict[str, Any] = {"tile": tile}
        if cargo_id is not None:
            args["cargo_id"] = cargo_id
        if screenshot is not None:
            args["screenshot"] = screenshot
        return server.handle_tool_call("get_tile", args)

    if server.vision_enabled:

        def get_tile(
            tile: int,
            cargo_id: CargoId | None = None,
            screenshot: bool = False,
        ) -> dict[str, Any]:
            return _model_subject_content(get_tile_result(tile, cargo_id, screenshot))

        get_tile.__annotations__["screenshot"] = StrictBool
        mcp.tool(name="get_tile", description=desc["get_tile"])(get_tile)

    else:

        @mcp.tool(name="get_tile", description=desc["get_tile"])
        def get_tile(tile: int, cargo_id: CargoId | None = None) -> dict[str, Any]:
            return get_tile_result(tile, cargo_id)

    @mcp.tool(name="get_region", description=desc["get_region"])
    def get_region(
        center_tile: int,
        radius: QueryRegionRadius,
        cargo_id: CargoId | None = None,
        detail: QueryRegionDetail = "tiles",
        fields: list[str] | None = None,
        where: list[str] | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {
            "center_tile": center_tile,
            "radius": radius,
            "detail": detail,
            "fields": fields or [],
            "where": where or [],
        }
        if cargo_id is not None:
            args["cargo_id"] = cargo_id
        return server.handle_tool_call("get_region", args)

    @mcp.tool(name="list_towns", description=desc["list_towns"])
    def list_towns() -> dict[str, Any]:
        return server.handle_tool_call("list_towns", {})

    @mcp.tool(name="get_town", description=desc["get_town"])
    def get_town(town_id: int) -> dict[str, Any]:
        return server.handle_tool_call("get_town", {"town_id": town_id})

    @mcp.tool(name="list_my_vehicles", description=desc["list_my_vehicles"])
    def list_my_vehicles() -> dict[str, Any]:
        return server.handle_tool_call("list_my_vehicles", {})

    if server.vision_enabled:

        def get_vehicle(vehicle_id: int, screenshot: bool = False) -> dict[str, Any]:
            return _model_subject_content(
                server.handle_tool_call(
                    "get_vehicle",
                    {"vehicle_id": vehicle_id, "screenshot": screenshot},
                )
            )

        get_vehicle.__annotations__["screenshot"] = StrictBool
        mcp.tool(name="get_vehicle", description=desc["get_vehicle"])(get_vehicle)

    else:

        @mcp.tool(name="get_vehicle", description=desc["get_vehicle"])
        def get_vehicle(vehicle_id: int) -> dict[str, Any]:
            return server.handle_tool_call("get_vehicle", {"vehicle_id": vehicle_id})

    @mcp.tool(name="list_my_stations", description=desc["list_my_stations"])
    def list_my_stations() -> dict[str, Any]:
        return server.handle_tool_call("list_my_stations", {})

    @mcp.tool(name="list_my_depots", description=desc["list_my_depots"])
    def list_my_depots() -> dict[str, Any]:
        return server.handle_tool_call("list_my_depots", {})

    @mcp.tool(name="get_my_finances", description=desc["get_my_finances"])
    def get_my_finances() -> dict[str, Any]:
        return server.handle_tool_call("get_my_finances", {})

    @mcp.tool(name="get_my_company", description=desc["get_my_company"])
    def get_my_company() -> dict[str, Any]:
        return server.handle_tool_call("get_my_company", {})

    @mcp.tool(name="list_engines", description=desc["list_engines"])
    def list_engines(
        only_buildable: bool = True, passenger_only: bool = False
    ) -> dict[str, Any]:
        return server.handle_tool_call(
            "list_engines",
            {"only_buildable": only_buildable, "passenger_only": passenger_only},
        )

    @mcp.tool(name="list_bridge_types", description=desc["list_bridge_types"])
    def list_bridge_types(length: BridgeLength) -> dict[str, Any]:
        return server.handle_tool_call("list_bridge_types", {"length": length})

    @mcp.tool(name="list_industries", description=desc["list_industries"])
    def list_industries() -> dict[str, Any]:
        return server.handle_tool_call("list_industries", {})

    @mcp.tool(name="list_subsidies", description=desc["list_subsidies"])
    def list_subsidies() -> dict[str, Any]:
        return server.handle_tool_call("list_subsidies", {})

    @mcp.tool(name="get_industry", description=desc["get_industry"])
    def get_industry(industry_id: int) -> dict[str, Any]:
        return server.handle_tool_call("get_industry", {"industry_id": industry_id})

    def get_my_station_result(
        station_id: int | None,
        tile: int | None,
        screenshot: bool | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {}
        if station_id is not None:
            args["station_id"] = station_id
        if tile is not None:
            args["tile"] = tile
        if screenshot is not None:
            args["screenshot"] = screenshot
        return server.handle_tool_call("get_my_station", args)

    if server.vision_enabled:

        def get_my_station(
            station_id: int | None = None,
            tile: int | None = None,
            screenshot: bool = False,
        ) -> dict[str, Any]:
            return _model_subject_content(
                get_my_station_result(station_id, tile, screenshot)
            )

        get_my_station.__annotations__["screenshot"] = StrictBool
        mcp.tool(name="get_my_station", description=desc["get_my_station"])(
            get_my_station
        )

    else:

        @mcp.tool(name="get_my_station", description=desc["get_my_station"])
        def get_my_station(
            station_id: int | None = None, tile: int | None = None
        ) -> dict[str, Any]:
            return get_my_station_result(station_id, tile)

    @mcp.tool(
        name="check_truck_stop_catchment",
        description=desc["check_truck_stop_catchment"],
    )
    def check_truck_stop_catchment(tile: int) -> dict[str, Any]:
        return server.handle_tool_call("check_truck_stop_catchment", {"tile": tile})

    @mcp.tool(
        name="list_my_route_economics", description=desc["list_my_route_economics"]
    )
    def list_my_route_economics() -> dict[str, Any]:
        return server.handle_tool_call("list_my_route_economics", {})

    @mcp.tool(name="get_cargo_payment_rate", description=desc["get_cargo_payment_rate"])
    def get_cargo_payment_rate(
        cargo_id: CargoId, distance: CargoDistance, days_in_transit: CargoTransitDays
    ) -> dict[str, Any]:
        return server.handle_tool_call(
            "get_cargo_payment_rate",
            {
                "cargo_id": cargo_id,
                "distance": distance,
                "days_in_transit": days_in_transit,
            },
        )

    @mcp.tool(name="build_road", description=desc["build_road"])
    def build_road(
        from_tile: int, to_tile: int, waypoints: list[int] | None = None
    ) -> dict[str, Any]:
        return server.handle_tool_call(
            "build_road",
            {"from_tile": from_tile, "to_tile": to_tile, "waypoints": waypoints or []},
        )

    @mcp.tool(name="build_one_way_road", description=desc["build_one_way_road"])
    def build_one_way_road(
        from_tile: NonNegativeInt, to_tile: NonNegativeInt
    ) -> dict[str, Any]:
        return server.handle_tool_call(
            "build_one_way_road", {"from_tile": from_tile, "to_tile": to_tile}
        )

    @mcp.tool(name="build_bridge", description=desc["build_bridge"])
    def build_bridge(start_tile: int, end_tile: int, bridge_id: int) -> dict[str, Any]:
        return server.handle_tool_call(
            "build_bridge",
            {"start_tile": start_tile, "end_tile": end_tile, "bridge_id": bridge_id},
        )

    @mcp.tool(name="build_tunnel", description=desc["build_tunnel"])
    def build_tunnel(start_tile: int) -> dict[str, Any]:
        return server.handle_tool_call("build_tunnel", {"start_tile": start_tile})

    @mcp.tool(name="terraform_land", description=desc["terraform_land"])
    def terraform_land(
        tile: int,
        mode: TerraformMode,
        corners: list[TerraformCorner] | None = None,
        to_tile: int | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {"tile": tile, "mode": mode, "corners": corners or []}
        if to_tile is not None:
            args["to_tile"] = to_tile
        return server.handle_tool_call("terraform_land", args)

    @mcp.tool(name="check_build_road", description=desc["check_build_road"])
    def check_build_road(
        from_tile: int, to_tile: int, waypoints: list[int] | None = None
    ) -> dict[str, Any]:
        return server.handle_tool_call(
            "check_build_road",
            {"from_tile": from_tile, "to_tile": to_tile, "waypoints": waypoints or []},
        )

    @mcp.tool(name="check_build_road_depot", description=desc["check_build_road_depot"])
    def check_build_road_depot(tile: int, front_tile: int) -> dict[str, Any]:
        return server.handle_tool_call(
            "check_build_road_depot", {"tile": tile, "front_tile": front_tile}
        )

    def _make_station_builder(tool_name: str):
        def _build(
            tile: int,
            front_tile: int,
            join: StationJoinMode = "new",
            station_id: StationJoinId | None = None,
        ) -> dict[str, Any]:
            args: dict[str, Any] = {
                "tile": tile,
                "front_tile": front_tile,
                "join": join,
            }
            if station_id is not None:
                args["station_id"] = station_id
            return server.handle_tool_call(tool_name, args)

        return _build

    for _station_tool in (
        "build_bus_stop_drive_through",
        "build_truck_stop_drive_through",
        "build_bus_stop_bay",
        "build_truck_stop_bay",
    ):
        mcp.tool(name=_station_tool, description=desc[_station_tool])(
            _make_station_builder(_station_tool)
        )

    @mcp.tool(name="build_road_depot", description=desc["build_road_depot"])
    def build_road_depot(tile: int, front_tile: int) -> dict[str, Any]:
        return server.handle_tool_call(
            "build_road_depot", {"tile": tile, "front_tile": front_tile}
        )

    @mcp.tool(name="buy_vehicle", description=desc["buy_vehicle"])
    def buy_vehicle(depot_tile: int, engine_id: int) -> dict[str, Any]:
        return server.handle_tool_call(
            "buy_vehicle", {"depot_tile": depot_tile, "engine_id": engine_id}
        )

    @mcp.tool(name="clone_vehicle", description=desc["clone_vehicle"])
    def clone_vehicle(
        depot_tile: int, vehicle_id: int, share_orders: bool
    ) -> dict[str, Any]:
        return server.handle_tool_call(
            "clone_vehicle",
            {
                "depot_tile": depot_tile,
                "vehicle_id": vehicle_id,
                "share_orders": share_orders,
            },
        )

    @mcp.tool(name="create_group", description=desc["create_group"])
    def create_group(name: str) -> dict[str, Any]:
        return server.handle_tool_call("create_group", {"name": name})

    @mcp.tool(name="move_vehicle_to_group", description=desc["move_vehicle_to_group"])
    def move_vehicle_to_group(vehicle_id: int, group_id: int) -> dict[str, Any]:
        return server.handle_tool_call(
            "move_vehicle_to_group", {"vehicle_id": vehicle_id, "group_id": group_id}
        )

    @mcp.tool(name="set_group_autoreplace", description=desc["set_group_autoreplace"])
    def set_group_autoreplace(
        group_id: int, engine_id_old: int, engine_id_new: int
    ) -> dict[str, Any]:
        return server.handle_tool_call(
            "set_group_autoreplace",
            {
                "group_id": group_id,
                "engine_id_old": engine_id_old,
                "engine_id_new": engine_id_new,
            },
        )

    @mcp.tool(name="insert_order", description=desc["insert_order"])
    def insert_order(
        vehicle_id: int,
        position: int,
        stop_tile: int,
        flags: list[OrderFlagName] | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {
            "vehicle_id": vehicle_id,
            "position": position,
            "stop_tile": stop_tile,
        }
        if flags is not None:
            args["flags"] = flags
        return server.handle_tool_call("insert_order", args)

    @mcp.tool(name="delete_order", description=desc["delete_order"])
    def delete_order(vehicle_id: int, position: int) -> dict[str, Any]:
        return server.handle_tool_call(
            "delete_order", {"vehicle_id": vehicle_id, "position": position}
        )

    @mcp.tool(name="set_order_flags", description=desc["set_order_flags"])
    def set_order_flags(
        vehicle_id: int, position: int, flags: list[OrderFlagName]
    ) -> dict[str, Any]:
        return server.handle_tool_call(
            "set_order_flags",
            {"vehicle_id": vehicle_id, "position": position, "flags": flags},
        )

    @mcp.tool(name="append_orders", description=desc["append_orders"])
    def append_orders(
        vehicle_id: int,
        stop_tiles: NonEmptyIntList,
        flags: list[OrderFlagName] | list[list[OrderFlagName]] | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {"vehicle_id": vehicle_id, "stop_tiles": stop_tiles}
        if flags is not None:
            args["flags"] = flags
        return server.handle_tool_call("append_orders", args)

    @mcp.tool(
        name="insert_conditional_order",
        description=desc["insert_conditional_order"],
    )
    def insert_conditional_order(
        vehicle_id: NonNegativeInt,
        position: NonNegativeInt,
        condition: ConditionalOrderCondition,
        compare: ConditionalOrderCompare,
        value: ConditionalOrderValue,
        jump_to: NonNegativeInt,
    ) -> dict[str, Any]:
        return server.handle_tool_call(
            "insert_conditional_order",
            {
                "vehicle_id": vehicle_id,
                "position": position,
                "condition": condition,
                "compare": compare,
                "value": value,
                "jump_to": jump_to,
            },
        )

    @mcp.tool(name="skip_to_order", description=desc["skip_to_order"])
    def skip_to_order(
        vehicle_id: NonNegativeInt, position: NonNegativeInt
    ) -> dict[str, Any]:
        return server.handle_tool_call(
            "skip_to_order", {"vehicle_id": vehicle_id, "position": position}
        )

    @mcp.tool(name="copy_orders", description=desc["copy_orders"])
    def copy_orders(
        vehicle_id: NonNegativeInt, source_vehicle_id: NonNegativeInt
    ) -> dict[str, Any]:
        return server.handle_tool_call(
            "copy_orders",
            {"vehicle_id": vehicle_id, "source_vehicle_id": source_vehicle_id},
        )

    @mcp.tool(name="share_orders", description=desc["share_orders"])
    def share_orders(
        vehicle_id: NonNegativeInt, source_vehicle_id: NonNegativeInt
    ) -> dict[str, Any]:
        return server.handle_tool_call(
            "share_orders",
            {"vehicle_id": vehicle_id, "source_vehicle_id": source_vehicle_id},
        )

    @mcp.tool(name="unshare_orders", description=desc["unshare_orders"])
    def unshare_orders(vehicle_id: NonNegativeInt) -> dict[str, Any]:
        return server.handle_tool_call("unshare_orders", {"vehicle_id": vehicle_id})

    @mcp.tool(name="refit_vehicle", description=desc["refit_vehicle"])
    def refit_vehicle(vehicle_id: int, cargo_id: CargoId) -> dict[str, Any]:
        return server.handle_tool_call(
            "refit_vehicle", {"vehicle_id": vehicle_id, "cargo_id": cargo_id}
        )

    @mcp.tool(name="start_vehicle", description=desc["start_vehicle"])
    def start_vehicle(vehicle_id: int) -> dict[str, Any]:
        return server.handle_tool_call("start_vehicle", {"vehicle_id": vehicle_id})

    @mcp.tool(name="stop_vehicle", description=desc["stop_vehicle"])
    def stop_vehicle(vehicle_id: int) -> dict[str, Any]:
        return server.handle_tool_call("stop_vehicle", {"vehicle_id": vehicle_id})

    @mcp.tool(name="reverse_vehicle", description=desc["reverse_vehicle"])
    def reverse_vehicle(vehicle_id: NonNegativeInt) -> dict[str, Any]:
        return server.handle_tool_call("reverse_vehicle", {"vehicle_id": vehicle_id})

    @mcp.tool(name="send_vehicle_to_depot", description=desc["send_vehicle_to_depot"])
    def send_vehicle_to_depot(vehicle_id: int) -> dict[str, Any]:
        return server.handle_tool_call(
            "send_vehicle_to_depot", {"vehicle_id": vehicle_id}
        )

    @mcp.tool(name="sell_vehicle", description=desc["sell_vehicle"])
    def sell_vehicle(vehicle_id: int) -> dict[str, Any]:
        return server.handle_tool_call("sell_vehicle", {"vehicle_id": vehicle_id})

    @mcp.tool(name="set_loan", description=desc["set_loan"])
    def set_loan(amount: int) -> dict[str, Any]:
        return server.handle_tool_call("set_loan", {"amount": amount})

    @mcp.tool(name="demolish_tile", description=desc["demolish_tile"])
    def demolish_tile(tile: int) -> dict[str, Any]:
        return server.handle_tool_call("demolish_tile", {"tile": tile})

    @mcp.tool(name="remove_road", description=desc["remove_road"])
    def remove_road(from_tile: int, to_tile: int) -> dict[str, Any]:
        return server.handle_tool_call(
            "remove_road", {"from_tile": from_tile, "to_tile": to_tile}
        )

    @mcp.tool(name="wait", description=desc["wait"])
    def wait(duration_days: WaitDays) -> dict[str, Any]:
        return server.handle_tool_call("wait", {"duration_days": duration_days})

    @mcp.tool(
        name="check_route_connectivity", description=desc["check_route_connectivity"]
    )
    def check_route_connectivity(
        town_id: int,
        depot_tile: int,
        depot_front_tile: int,
        stop_tiles: RouteConnectivityStopList,
    ) -> dict[str, Any]:
        return server.handle_tool_call(
            "check_route_connectivity",
            {
                "town_id": town_id,
                "depot_tile": depot_tile,
                "depot_front_tile": depot_front_tile,
                "stop_tiles": stop_tiles,
            },
        )

    @mcp.tool(
        name="wait_and_observe_vehicle", description=desc["wait_and_observe_vehicle"]
    )
    def wait_and_observe_vehicle(
        vehicle_id: int, advance_days: WaitDays = WAIT_VEHICLE_DEFAULT_DAYS
    ) -> dict[str, Any]:
        return server.handle_tool_call(
            "wait_and_observe_vehicle",
            {"vehicle_id": vehicle_id, "advance_days": advance_days},
        )

    @mcp.tool(
        name="wait_and_observe_station",
        description=desc["wait_and_observe_station"],
    )
    def wait_and_observe_station(
        station_id: NonNegativeInt,
        advance_days: WaitDays = WAIT_STATION_DEFAULT_DAYS,
    ) -> dict[str, Any]:
        return server.handle_tool_call(
            "wait_and_observe_station",
            {"station_id": station_id, "advance_days": advance_days},
        )

    @mcp.tool(name=FEEDBACK_TOOL_NAME, description=desc[FEEDBACK_TOOL_NAME])
    def report_feedback(
        kind: FeedbackKind,
        description: str,
        why_it_matters: str,
        current_blocker: str,
    ) -> dict[str, Any]:
        return server.handle_tool_call(
            FEEDBACK_TOOL_NAME,
            {
                "kind": kind,
                "description": description,
                "why_it_matters": why_it_matters,
                "current_blocker": current_blocker,
            },
        )

    @mcp.tool(name=END_EPISODE_TOOL_NAME, description=desc[END_EPISODE_TOOL_NAME])
    def end_episode(reason: str) -> dict[str, Any]:
        return server.handle_tool_call(END_EPISODE_TOOL_NAME, {"reason": reason})

    if server.skill_enabled:

        @mcp.tool(name=SKILL_TOOL_NAME, description=desc[SKILL_TOOL_NAME])
        def get_bus_route_skill(
            town_id: int, engine_id: int | None = None
        ) -> dict[str, Any]:
            args: dict[str, Any] = {"town_id": town_id}
            if engine_id is not None:
                args["engine_id"] = engine_id
            return server.handle_tool_call(SKILL_TOOL_NAME, args)

    if server.vision_enabled:
        from mcp.server.fastmcp import Image
        from mcp.types import CallToolResult, TextContent

        def _model_subject_content(result: dict[str, Any]):
            """Return facts as structured JSON and optional image content.

            The wrappers retain a ``dict[str, Any]`` return annotation so
            FastMCP advertises and validates the same structured output schema
            as the vision-disabled wrappers. FastMCP explicitly accepts a
            CallToolResult return under such an annotation and validates its
            structuredContent against that schema.
            """
            model_result = _without_host_details(result)
            content = []
            image_bytes = getattr(result, "image_bytes", None)
            if image_bytes is not None:
                content.append(Image(data=image_bytes, format="png").to_image_content())
            content.append(
                TextContent(
                    type="text",
                    text=json.dumps(model_result, indent=2, sort_keys=True),
                )
            )
            return CallToolResult(
                content=content,
                structuredContent=model_result,
            )

        def _model_view_content(result: Any):
            path = getattr(result, "image_path", None)
            if path is None and isinstance(result, dict):
                path = result.get("path")
            if path:
                return [Image(path=str(path)), _without_host_details(result)]
            return _without_host_details(result)

        @mcp.tool(name=VIEW_MAP_TOOL_NAME, description=desc[VIEW_MAP_TOOL_NAME])
        def view_map(
            tile: int,
            window: ViewMapWindow = DEFAULT_WINDOW,
            refresh: bool = DEFAULT_REFRESH,
        ):
            result = server.handle_tool_call(
                VIEW_MAP_TOOL_NAME, {"tile": tile, "window": window, "refresh": refresh}
            )
            return _model_view_content(result)

    return mcp


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="OpenTTD MCP gameplay tool server",
        epilog="To launch and own a new game, run: openttd-mcp play --help",
    )
    parser.add_argument(
        "--bridge-root",
        required=True,
        help="Path to the per-run McpBridge AI directory",
    )
    parser.add_argument(
        "--openttd-log", help="OpenTTD stdout log to tail for MCP_RESULT markers"
    )
    parser.add_argument(
        "--tool-log",
        required=True,
        help="Authoritative JSONL log of tool calls/results",
    )
    parser.add_argument(
        "--feedback-log",
        help="JSONL log for report_feedback entries (default: feedback.jsonl beside the tool log)",
    )
    parser.add_argument(
        "--registry-snapshot", help="Path to write visible tool registry JSON"
    )
    parser.add_argument(
        "--enable-skill",
        help="Path to promoted get_bus_route_skill skill JSON; omit in control arms",
    )
    parser.add_argument(
        "--experiment",
        default=DEFAULT_EXPERIMENT,
        help="Experiment label stamped into the registry snapshot and evidence timestamps "
        "(default: %(default)s)",
    )
    parser.add_argument(
        "--decision-budget",
        type=int,
        help="Server-enforced episode decision budget (one decision = one tool call); omit for unlimited",
    )
    parser.add_argument(
        "--no-budget-echo",
        dest="budget_echo",
        action="store_false",
        default=True,
        help="Hide factual decision counters from model-visible tool results while keeping server enforcement/logging",
    )
    parser.add_argument(
        "--end-episode-mode",
        choices=END_EPISODE_MODES,
        default="standard",
        help="Model-visible end_episode semantics: standard budget-cut tool or distress-only exit",
    )
    parser.add_argument(
        "--tool-index-channel",
        choices=("prompt",),
        help="Evidence metadata for an external runner that delivers the generated index",
    )
    parser.add_argument(
        "--enable-vision",
        action="store_true",
        help="Enable the view_map rendered-pixel sensor",
    )
    parser.add_argument(
        "--admin-port", type=int, help="OpenTTD admin port used for screenshot commands"
    )
    # The admin password itself is never a flag (argv is recorded verbatim in
    # codex_command.json). It arrives via --admin-password-file (a path is not a
    # secret) or the OPENTTD_ADMIN_PASSWORD env var. The file is preferred because
    # codex does not forward the parent env to MCP server subprocesses.
    parser.add_argument(
        "--admin-password-file",
        help="Path to a file holding the OpenTTD admin password",
    )
    parser.add_argument(
        "--screenshot-dir", help="Directory where OpenTTD writes full-map screenshots"
    )
    parser.add_argument(
        "--crops-dir", help="Directory where view_map writes crop PNG files"
    )
    parser.add_argument(
        "--map-log2",
        type=int,
        default=7,
        help="Square map side as log2 tiles for tile-to-pixel mapping",
    )
    parser.add_argument("--result-timeout", type=float, default=240.0)
    parser.add_argument(
        "--scripted",
        help="Execute a JSONL tool sequence directly instead of serving MCP",
    )
    parser.add_argument(
        "--no-gameplay-llm-marker", help="Marker file to write in --scripted mode"
    )
    parser.add_argument(
        "--dump-registry", action="store_true", help="Print registry JSON and exit"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    effective_argv = sys.argv[1:] if argv is None else argv
    if effective_argv and effective_argv[0] == "play":
        from openttd_mcp.play import main as play_main

        return play_main(effective_argv[1:])
    args = parse_args(argv)
    bridge_root = Path(args.bridge_root)
    tool_log = Path(args.tool_log)
    skill_path = Path(args.enable_skill) if args.enable_skill else None
    registry_path = Path(args.registry_snapshot) if args.registry_snapshot else None

    if args.dump_registry:
        print(
            json.dumps(
                registry_snapshot(
                    enable_skill=skill_path is not None,
                    experiment=args.experiment,
                    end_episode_mode=args.end_episode_mode,
                    enable_vision=args.enable_vision,
                    tool_index_channel=args.tool_index_channel,
                ),
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    dispatcher = None
    if args.openttd_log:
        dispatcher = FileLogBridgeDispatcher(
            bridge_root,
            Path(args.openttd_log),
            timeout=args.result_timeout,
            stop_path=tool_log.parent / BUDGET_OVERRIDE_FILENAME,
        )
    admin_config = None
    if args.enable_vision:
        from openttd_mcp.admin import AdminLifecycleConfig

        admin_password = None
        if args.admin_password_file:
            admin_password = Path(args.admin_password_file).read_text().strip()
        if not admin_password:
            admin_password = os.environ.get(ADMIN_PASSWORD_ENV)
        if args.admin_port is None or not admin_password or not args.screenshot_dir:
            raise SystemExit(
                "--enable-vision requires --admin-port, --screenshot-dir, and the admin password via "
                f"--admin-password-file or the {ADMIN_PASSWORD_ENV} env var"
            )
        admin_config = AdminLifecycleConfig(
            port=args.admin_port, password=admin_password
        )
    server = GameToolServer(
        dispatcher,
        tool_log=tool_log,
        feedback_log=Path(args.feedback_log) if args.feedback_log else None,
        enable_skill_path=skill_path,
        registry_path=registry_path,
        decision_budget=args.decision_budget,
        budget_echo=args.budget_echo,
        experiment=args.experiment,
        end_episode_mode=args.end_episode_mode,
        tool_index_channel=args.tool_index_channel,
        vision_enabled=args.enable_vision,
        admin_config=admin_config,
        screenshot_dir=Path(args.screenshot_dir) if args.screenshot_dir else None,
        crops_dir=Path(args.crops_dir) if args.crops_dir else None,
        map_log2=args.map_log2,
    )

    if args.scripted:
        if dispatcher is None:
            raise SystemExit(
                "--scripted requires --openttd-log so commands can round-trip through OpenTTD"
            )
        return run_scripted(
            server,
            Path(args.scripted),
            Path(args.no_gameplay_llm_marker) if args.no_gameplay_llm_marker else None,
        )

    if dispatcher is None:
        raise SystemExit("MCP serve mode requires --openttd-log")
    mcp = build_fastmcp(server)
    mcp.run("stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
