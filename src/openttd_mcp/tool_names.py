"""Current tool-name migration for historical skill artifacts and evidence."""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

# One-step mappings only: every value is the current surface name, including
# names that were retired and then renamed again.
RETIRED_TOOL_NAME_MAP: Mapping[str, str] = MappingProxyType(
    {
        "assign_to_group": "move_vehicle_to_group",
        "build_bus_bay_station": "build_bus_stop_bay",
        "build_bus_route": "get_bus_route_skill",
        "build_bus_station": "build_bus_stop_drive_through",
        "build_road_station": "build_bus_stop_drive_through",
        "build_truck_bay_station": "build_truck_stop_bay",
        "build_truck_station": "build_truck_stop_drive_through",
        "demolish": "demolish_tile",
        "estimate_depot_build": "check_build_road_depot",
        "estimate_road_build": "check_build_road",
        "get_company_report": "get_my_company",
        "get_finances": "get_my_finances",
        "observe_route_operation": "wait_and_observe_vehicle",
        "query_catchment": "check_truck_stop_catchment",
        "query_industry": "get_industry",
        "query_region": "get_region",
        "query_route_economics": "list_my_route_economics",
        "query_station": "get_my_station",
        "query_tile": "get_tile",
        "query_town": "get_town",
        "query_truck_catchment": "check_truck_stop_catchment",
        "send_to_depot": "send_vehicle_to_depot",
        "set_autoreplace": "set_group_autoreplace",
        "set_orders": "append_orders",
    }
)


def _alternation(names: list[str]) -> str:
    return "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True))


# A retired name shaped like an identifier (it carries an underscore) is only ever
# a tool reference, so it can be substituted anywhere in a string.  A retired name
# that is also an ordinary English word — "demolish" — cannot: prose such as "you
# may need to demolish the blocking house" is not a tool reference and must survive
# migration unchanged.
_IDENTIFIER_NAMES = [name for name in RETIRED_TOOL_NAME_MAP if "_" in name]
_WORD_NAMES = [name for name in RETIRED_TOOL_NAME_MAP if "_" not in name]

_RETIRED_TOOL_NAME_PATTERN = (
    re.compile(
        r"(?<![A-Za-z0-9_])(" + _alternation(_IDENTIFIER_NAMES) + r")(?![A-Za-z0-9_])"
    )
    if _IDENTIFIER_NAMES
    else None
)
# Word-shaped names migrate only where the string is delimited as code: `demolish`,
# "demolish", or 'demolish'.  Whole-string matches are handled before this pattern.
_DELIMITED_WORD_NAME_PATTERN = (
    re.compile(r"(?<=[`\"'])(" + _alternation(_WORD_NAMES) + r")(?=[`\"'])")
    if _WORD_NAMES
    else None
)


def _migrate_string(value: str) -> str:
    exact = RETIRED_TOOL_NAME_MAP.get(value)
    if exact is not None:
        return exact
    migrated = value
    for pattern in (_RETIRED_TOOL_NAME_PATTERN, _DELIMITED_WORD_NAME_PATTERN):
        if pattern is not None:
            migrated = pattern.sub(
                lambda match: RETIRED_TOOL_NAME_MAP[match.group(1)], migrated
            )
    return migrated


def _migrate_copied_value(value: Any) -> Any:
    if isinstance(value, str):
        return _migrate_string(value)
    if isinstance(value, dict):
        return {
            _migrate_copied_value(key): _migrate_copied_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_migrate_copied_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_migrate_copied_value(item) for item in value)
    if isinstance(value, set):
        return {_migrate_copied_value(item) for item in value}
    return value


def migrate_retired_tool_names(value: Any) -> Any:
    """Return a deep copy with every retired tool-name token made current."""
    return _migrate_copied_value(copy.deepcopy(value))
