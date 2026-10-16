"""Execution tests for production Squirrel code.

The supplied archive is generic Squirrel rather than OpenTTD's modified 2.2.5
runtime, so these tests cover language behavior and pure algorithms while live
OpenTTD reload verification remains authoritative for game bindings. Small
OpenTTD API stand-ins supply graph facts; assertions are against independently
chosen graph outcomes, never against production source spelling.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from openttd_mcp.verify import bus_network, bus_route, freight_network
from openttd_mcp.verify._squirrel import (
    DIRECT_CONNECTED,
    IS_ROAD_LIKE,
    REACHABLE_ANY,
    compose_town_road_component_size,
)
from tests.squirrel_runtime import (
    SQUIRREL_ARCHIVE_ENV,
    SQUIRREL_EXECUTABLE_ENV,
    VENDORED_SQUIRREL_ARCHIVE,
    _common_checkout_root,
    _install_build,
    _validate_archive,
    run_squirrel,
    squirrel_executable,
)
from tests.support import MCP_BRIDGE_DIR, REPO_ROOT


def _squirrel_string(path: Path) -> str:
    return json.dumps(str(path))


ROAD_STATION_API_STUB = """class AIMap {
    static function IsValidTile(tile) { return tile >= 0; }
    static function GetTileX(tile) { return tile; }
    static function GetTileY(tile) { return 0; }
}
class AIRoad {
    static function IsRoadTile(tile) { return AIMap.IsValidTile(tile); }
    static function IsRoadStationTile(tile) { return tile == 10 || tile == 20 || tile == 30; }
    static function IsRoadDepotTile(tile) { return false; }
    static function IsDriveThroughRoadStationTile(tile) { return tile == 20 || tile == 30; }
    static function GetRoadStationFrontTile(tile) {
        if (tile == 10) return 11;
        if (tile == 20) return 21;
        return 31;
    }
    static function GetDriveThroughBackTile(tile) { return tile - 1; }
    static function AreRoadTilesConnected(tile_from, tile_to) {
        return (tile_from == 11 && tile_to == 10)
            || (tile_from == 10 && tile_to == 11)
            || (tile_from == 19 && tile_to == 20)
            || (tile_from == 20 && tile_to == 19);
    }
}
"""


DEPOT_AUTOCONNECT_API_STUB = """
depot_tile <- 100;
physical_front <- 101;
depot_should_succeed <- true;
connector_should_succeed <- true;
connector_establishes_edges <- true;
front_is_road <- false;
depot_exists <- false;
front_accessor_throws <- false;
front_accessor_returns_invalid <- false;
ledger_cost <- 0;
depot_cost <- 40;
connector_cost <- 8;
depot_calls <- 0;
connector_calls <- 0;
depot_received_selector <- -1;
connector_received_from <- -1;
connector_received_to <- -1;
last_error_code <- 0;
last_error_name <- "ERR_NONE";
edges <- {};

function EdgeKey(tile_from, tile_to) {
    return tile_from + ":" + tile_to;
}

function AIDepotList(transport_type) {
    local result = {};
    if (::depot_exists) result[::depot_tile] <- 1;
    return result;
}

class AIController {}
class AIExecMode {}
class AIAccounting {
    start_cost = 0;
    constructor() { this.start_cost = ::ledger_cost; }
    function GetCosts() { return ::ledger_cost - this.start_cost; }
}
class AITile { static TRANSPORT_ROAD = 1; }
class AIDate {
    static function GetCurrentDate() { return 2000; }
    static function GetYear(date) { return 2000; }
    static function GetMonth(date) { return 1; }
}
class AICompany {
    static COMPANY_SELF = 0;
    static function GetBankBalance(company) { return 1000 - ::ledger_cost; }
}
class AIError {
    static function GetLastError() { return ::last_error_code; }
    static function GetLastErrorString() { return ::last_error_name; }
}
class AIMap {
    static function IsValidTile(tile) { return tile >= 0; }
    static function GetTileX(tile) { return tile; }
    static function GetTileY(tile) { return 0; }
}
class AIRoad {
    static function IsRoadTile(tile) {
        return tile == ::physical_front && ::front_is_road;
    }
    static function IsRoadStationTile(tile) { return false; }
    static function IsRoadDepotTile(tile) {
        return tile == ::depot_tile && ::depot_exists;
    }
    static function GetRoadDepotFrontTile(tile) {
        if (!AIRoad.IsRoadDepotTile(tile)) return -1;
        if (::front_accessor_throws) throw "front accessor unavailable";
        return ::front_accessor_returns_invalid ? -1 : ::physical_front;
    }
    static function AreRoadTilesConnected(tile_from, tile_to) {
        local key = EdgeKey(tile_from, tile_to);
        return ::edges.rawin(key) && ::edges[key];
    }
    static function BuildRoadDepot(tile, selector) {
        ::depot_calls++;
        ::depot_received_selector = selector;
        if (!::depot_should_succeed) {
            ::last_error_code = 260;
            ::last_error_name = "ERR_AREA_NOT_CLEAR";
            return false;
        }
        ::depot_exists = true;
        ::ledger_cost += ::depot_cost;
        ::last_error_code = 0;
        ::last_error_name = "ERR_NONE";
        return true;
    }
    static function BuildRoad(tile_from, tile_to) {
        ::connector_calls++;
        ::connector_received_from = tile_from;
        ::connector_received_to = tile_to;
        if (!::connector_should_succeed) {
            ::last_error_code = 260;
            ::last_error_name = "ERR_AREA_NOT_CLEAR";
            return false;
        }
        ::ledger_cost += ::connector_cost;
        ::front_is_road = true;
        if (::connector_establishes_edges) {
            ::edges[EdgeKey(tile_from, tile_to)] <- true;
            ::edges[EdgeKey(tile_to, tile_from)] <- true;
        }
        ::last_error_code = 0;
        ::last_error_name = "ERR_NONE";
        return true;
    }
}
"""


def _run_depot_autoconnect_scenario(tmp_path: Path, setup: str) -> tuple[dict, dict]:
    source = (
        DEPOT_AUTOCONNECT_API_STUB
        + f"\ndofile({_squirrel_string(MCP_BRIDGE_DIR / 'main.nut')});\n"
        + setup
        + "\nlocal bridge = McpBridge();\n"
        + "bridge.last_seen_economy_year = 2000;\n"
        + "bridge.pending_year_end_reports = [];\n"
        + 'bridge.PrimBuildRoadDepot(::depot_tile, ::input_selector, "fixture");\n'
        + 'print("\\nSTATE|{\\"depot_exists\\":" + (depot_exists ? "true" : "false")\n'
        + '    + ",\\"depot_calls\\":" + depot_calls\n'
        + '    + ",\\"connector_calls\\":" + connector_calls\n'
        + '    + ",\\"depot_received_selector\\":" + depot_received_selector\n'
        + '    + ",\\"connector_received_from\\":" + connector_received_from\n'
        + '    + ",\\"connector_received_to\\":" + connector_received_to\n'
        + '    + ",\\"ledger_cost\\":" + ledger_cost\n'
        + '    + ",\\"forward\\":" + (AIRoad.AreRoadTilesConnected(depot_tile, physical_front) ? "true" : "false")\n'
        + '    + ",\\"reverse\\":" + (AIRoad.AreRoadTilesConnected(physical_front, depot_tile) ? "true" : "false")\n'
        + '    + "}\\n");\n'
        + 'print("depot-autoconnect-ok\\n");\n'
    )
    stdout = run_squirrel(
        tmp_path, source, "depot-autoconnect-ok", name="depot-autoconnect.nut"
    )
    result_line = next(
        line for line in stdout.splitlines() if line.startswith("MCP_RESULT|fixture|")
    )
    state_line = next(line for line in stdout.splitlines() if line.startswith("STATE|"))
    return (
        json.loads(result_line.split("|", 2)[2]),
        json.loads(state_line.removeprefix("STATE|")),
    )


def test_vendored_squirrel_archive_keeps_its_pinned_identity() -> None:
    """A corrupt or substituted interpreter source archive must fail the gate."""
    _validate_archive(VENDORED_SQUIRREL_ARCHIVE)


def _fake_build(root: Path, marker: str) -> Path:
    """Create a build tree whose ``bin/sq`` content identifies which build it is."""
    (root / "bin").mkdir(parents=True)
    executable = root / "bin" / "sq"
    executable.write_text(f"#!/bin/sh\necho {marker}\n")
    executable.chmod(0o755)
    return root


def test_build_install_replaces_a_stale_interpreter_directory(tmp_path: Path) -> None:
    """A leftover build directory must not make every later build fail to install."""
    target = tmp_path / "squirrel-2.2"
    (target / "bin").mkdir(parents=True)
    (target / "bin" / "unfinished").write_text("stale")
    source_root = _fake_build(tmp_path / "SQUIRREL2", "fresh")

    _install_build(source_root, target)

    assert (target / "bin" / "sq").read_text() == "#!/bin/sh\necho fresh\n"
    assert not (target / "bin" / "unfinished").exists()
    assert not source_root.exists()


def test_build_install_keeps_the_interpreter_a_concurrent_build_installed(
    tmp_path: Path,
) -> None:
    """Losing the install race must not delete the interpreter other runs are using."""
    target = _fake_build(tmp_path / "squirrel-2.2", "winner")
    source_root = _fake_build(tmp_path / "SQUIRREL2", "loser")

    _install_build(source_root, target)

    assert (target / "bin" / "sq").read_text() == "#!/bin/sh\necho winner\n"


def test_configured_archive_outranks_an_already_built_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A prebuilt directory must not shadow the archive the caller configured."""
    missing_archive = tmp_path / "absent-squirrel.tar.gz"
    monkeypatch.delenv(SQUIRREL_EXECUTABLE_ENV, raising=False)
    monkeypatch.setenv(SQUIRREL_ARCHIVE_ENV, str(missing_archive))
    squirrel_executable.cache_clear()
    try:
        with pytest.raises(
            pytest.fail.Exception, match=re.escape(str(missing_archive))
        ):
            squirrel_executable()
    finally:
        squirrel_executable.cache_clear()


def test_checkout_root_falls_back_when_git_cannot_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A machine without git must resolve a build root instead of raising."""
    monkeypatch.setenv("PATH", str(tmp_path))

    assert _common_checkout_root() == REPO_ROOT


@pytest.mark.parametrize(
    ("source", "marker"),
    [
        (
            (
                f"class AIController {{}}\ndofile({_squirrel_string(MCP_BRIDGE_DIR / 'main.nut')});\n"
                'assert(typeof McpBridge == "class");\n'
                'print("main-loaded\\n");\n'
            ),
            "main-loaded",
        ),
        (
            (
                f"dofile({_squirrel_string(MCP_BRIDGE_DIR / 'commands.nut')});\n"
                "local commands = Commands();\n"
                'assert(commands.timestamp == "initial");\n'
                "assert(commands.commands.len() == 0);\n"
                'print("commands-loaded\\n");\n'
            ),
            "commands-loaded",
        ),
        (
            (
                "class AIInfo {}\nregistered_ais <- [];\n"
                "function RegisterAI(info) { ::registered_ais.append(info); }\n"
                f"dofile({_squirrel_string(MCP_BRIDGE_DIR / 'info.nut')});\n"
                "assert(::registered_ais.len() == 2);\n"
                'assert(::registered_ais[0].GetName() == "McpBridge");\n'
                'assert(::registered_ais[0].GetShortName() == "MCPB");\n'
                'assert(::registered_ais[0].CreateInstance() == "McpBridge");\n'
                'assert(::registered_ais[1].GetName() == "BusRouteBridge");\n'
                'assert(::registered_ais[1].GetShortName() == "BRTE");\n'
                'assert(::registered_ais[1].CreateInstance() == "McpBridge");\n'
                'print("info-loaded\\n");\n'
            ),
            "info-loaded",
        ),
    ],
    ids=["main", "commands", "info"],
)
def test_bridge_files_execute_in_standalone_squirrel(
    tmp_path: Path, source: str, marker: str
) -> None:
    """A syntax error or invalid top-level definition must fail before OpenTTD."""
    run_squirrel(tmp_path, source, marker, name=f"{marker}.nut")


def test_runner_rejects_an_error_callstack_that_contains_the_success_marker(
    tmp_path: Path,
) -> None:
    """The interpreter's zero error exit code must not create a false pass."""
    with pytest.raises(AssertionError, match="execution error"):
        run_squirrel(
            tmp_path,
            "assert(false);\n",
            "false-positive",
            name="false-positive.nut",
        )


def test_bridge_json_escape_round_trips_control_characters(tmp_path: Path) -> None:
    """Bridge output must preserve JSON data without emitting extra lines."""
    original = 'quote" slash\\ newline\nreturn\rtab\tend'
    source = (
        "class AIController {}\n"
        f"dofile({_squirrel_string(MCP_BRIDGE_DIR / 'main.nut')});\n"
        f"local original = {json.dumps(original)};\n"
        "local bridge = McpBridge();\n"
        'print("json-escape-result|" + bridge.JsonEscape(original) + "\\n");\n'
        'print("json-escape-ok\\n");\n'
    )

    stdout = run_squirrel(tmp_path, source, "json-escape-ok")

    encoded = next(
        line.removeprefix("json-escape-result|")
        for line in stdout.splitlines()
        if line.startswith("json-escape-result|")
    )
    assert json.loads(f'"{encoded}"') == original


def test_bridge_save_load_restores_event_state_but_reemits_observation(
    tmp_path: Path,
) -> None:
    """Reload must preserve evidence while leaving the startup observation due."""
    source = (
        "class AIController {}\n"
        f"dofile({_squirrel_string(MCP_BRIDGE_DIR / 'main.nut')});\n"
        "local original = McpBridge();\n"
        "original.EnsureEventState();\n"
        'original.last_command_timestamp = "turn-7";\n'
        "original.observation_emitted = true;\n"
        "original.observation_counter = 9;\n"
        "original.vehicle_lost_events = 3;\n"
        "original.lost_events.append({ vehicle_id = 41 });\n"
        "local saved = original.Save();\n"
        "local restored = McpBridge();\n"
        "restored.Load(1, saved);\n"
        'assert(restored.last_command_timestamp == "turn-7");\n'
        "assert(restored.observation_counter == 9);\n"
        "assert(restored.vehicle_lost_events == 3);\n"
        "assert(restored.lost_events.len() == 1);\n"
        "assert(restored.lost_events[0].vehicle_id == 41);\n"
        "assert(!restored.observation_emitted);\n"
        'print("save-load-ok\\n");\n'
    )
    run_squirrel(tmp_path, source, "save-load-ok")


@pytest.mark.parametrize(
    ("setup", "selector", "connector_calls", "combined_cost"),
    [
        ("input_selector <- 120;", 120, 1, 48),
        (
            "input_selector <- 101; front_is_road = true; edges[EdgeKey(100, 101)] <- true; edges[EdgeKey(101, 100)] <- true;",
            101,
            0,
            40,
        ),
        (
            "input_selector <- 101; front_is_road = true; edges[EdgeKey(101, 100)] <- true;",
            101,
            1,
            48,
        ),
    ],
    ids=["distant-selector", "already-connected", "one-direction-missing"],
)
def test_depot_autoconnect_uses_the_physical_front_at_most_once(
    tmp_path: Path,
    setup: str,
    selector: int,
    connector_calls: int,
    combined_cost: int,
) -> None:
    """A direction selector must not become the connector endpoint or cause repeat builds."""
    result, state = _run_depot_autoconnect_scenario(tmp_path, setup)

    assert result["ok"] is True
    assert result["front_tile"] == selector
    assert result["actual_front_tile"] == 101
    assert result["is_road_depot"] is True
    assert result["front_road_connected"] is True
    assert result["connector_attempted"] is (connector_calls == 1)
    assert result["connector_succeeded"] is (connector_calls == 1)
    assert result["game_mutated"] is True
    assert result["cost"] == combined_cost
    assert state == {
        "depot_exists": True,
        "depot_calls": 1,
        "connector_calls": connector_calls,
        "depot_received_selector": selector,
        "connector_received_from": 100 if connector_calls else -1,
        "connector_received_to": 101 if connector_calls else -1,
        "ledger_cost": combined_cost,
        "forward": True,
        "reverse": True,
    }


def test_depot_failure_cannot_issue_a_connector_or_claim_mutation(
    tmp_path: Path,
) -> None:
    """A refused depot command must stop before the conditional road command."""
    result, state = _run_depot_autoconnect_scenario(
        tmp_path, "input_selector <- 101; depot_should_succeed = false;"
    )

    assert result["ok"] is False
    assert result["actual_front_tile"] == -1
    assert result["is_road_depot"] is False
    assert result["connector_attempted"] is False
    assert result["connector_succeeded"] is False
    assert result["game_mutated"] is False
    assert result["cost"] == 0
    assert state["depot_exists"] is False
    assert state["connector_calls"] == 0


@pytest.mark.parametrize(
    "setup",
    [
        "input_selector <- 101; front_accessor_throws = true;",
        "input_selector <- 101; front_accessor_returns_invalid = true;",
    ],
    ids=["accessor-throws", "accessor-returns-invalid"],
)
def test_paid_depot_with_unavailable_entrance_emits_a_bounded_result(
    tmp_path: Path, setup: str
) -> None:
    """A paid depot must emit one result rather than strand the bridge on an entrance read."""
    result, state = _run_depot_autoconnect_scenario(tmp_path, setup)

    assert result["ok"] is False
    assert result["error"]
    assert result["actual_front_tile"] == -1
    assert result["is_road_depot"] is True
    assert result["front_road_connected"] is False
    assert result["connector_attempted"] is False
    assert result["connector_succeeded"] is False
    assert result["game_mutated"] is True
    assert result["cost"] == 40
    assert len(result["fact_errors"]) == 1
    assert result["fact_errors"][0]["field"] == "actual_front_tile"
    assert "unavailable" in result["fact_errors"][0]["code"]
    assert state == {
        "depot_exists": True,
        "depot_calls": 1,
        "connector_calls": 0,
        "depot_received_selector": 101,
        "connector_received_from": -1,
        "connector_received_to": -1,
        "ledger_cost": 40,
        "forward": False,
        "reverse": False,
    }


def test_refused_depot_build_reports_the_connected_depot_already_present(
    tmp_path: Path,
) -> None:
    """A refused rebuild must report the existing entrance without claiming this call mutated it."""
    result, state = _run_depot_autoconnect_scenario(
        tmp_path,
        "input_selector <- 101; depot_exists = true; depot_should_succeed = false; "
        "front_is_road = true; edges[EdgeKey(100, 101)] <- true; "
        "edges[EdgeKey(101, 100)] <- true;",
    )

    assert result["ok"] is False
    assert result["actual_front_tile"] == 101
    assert result["is_road_depot"] is True
    assert result["front_is_road"] is True
    assert result["front_road_connected"] is True
    assert result["connector_attempted"] is False
    assert result["connector_succeeded"] is False
    assert result["game_mutated"] is False
    assert result["cost"] == 0
    assert state == {
        "depot_exists": True,
        "depot_calls": 1,
        "connector_calls": 0,
        "depot_received_selector": 101,
        "connector_received_from": -1,
        "connector_received_to": -1,
        "ledger_cost": 0,
        "forward": True,
        "reverse": True,
    }


def test_connector_failure_reports_the_paid_depot_as_a_partial_mutation(
    tmp_path: Path,
) -> None:
    """A failed connector must not hide or roll back the depot already paid for."""
    result, state = _run_depot_autoconnect_scenario(
        tmp_path, "input_selector <- 101; connector_should_succeed = false;"
    )

    assert result["ok"] is False
    assert result["error"]
    assert result["cost"] == 40
    assert result["is_road_depot"] is True
    assert result["front_road_connected"] is False
    assert result["connector_attempted"] is True
    assert result["connector_succeeded"] is False
    assert result["game_mutated"] is True
    assert state["depot_exists"] is True
    assert state["connector_calls"] == 1
    assert state["ledger_cost"] == 40
    assert state["forward"] is False
    assert state["reverse"] is False


def test_connector_return_cannot_replace_the_observed_bidirectional_postcondition(
    tmp_path: Path,
) -> None:
    """A successful native return without both graph edges must still fail the action."""
    result, state = _run_depot_autoconnect_scenario(
        tmp_path, "input_selector <- 101; connector_establishes_edges = false;"
    )

    assert result["ok"] is False
    assert result["connector_attempted"] is True
    assert result["connector_succeeded"] is True
    assert result["front_road_connected"] is False
    assert result["game_mutated"] is True
    assert result["cost"] == 48
    assert state["connector_calls"] == 1
    assert state["forward"] is False
    assert state["reverse"] is False


@pytest.mark.parametrize(
    "main_nut",
    [
        bus_route.MAIN_NUT,
        bus_network.MAIN_NUT,
        freight_network.MAIN_NUT,
    ],
    ids=["bus-route", "bus-network", "freight-network"],
)
def test_composed_verifier_programs_execute_shared_behaviors(
    tmp_path: Path, main_nut: str
) -> None:
    """Fragment loss or an invalid composition must fail as executable Squirrel."""
    source = (
        "class AIController {}\n"
        + ROAD_STATION_API_STUB
        + main_nut
        + "\nlocal bridge = McpBridge();\n"
        + 'assert(bridge.BoolStr(true) == "true");\n'
        + 'assert(bridge.BoolStr(false) == "false");\n'
        + "local values = [2, 3];\n"
        + "bridge.AppendUnique(values, 3);\n"
        + "bridge.AppendUnique(values, 5);\n"
        + "assert(values.len() == 3 && values[2] == 5);\n"
        + "assert(bridge.StationTileConnected(10));\n"
        + "assert(bridge.StationTileConnected(20));\n"
        + "assert(!bridge.StationTileConnected(30));\n"
        + "assert(!bridge.StationTileConnected(40));\n"
        + '\nprint("verifier-composition-ok\\n");\n'
    )
    run_squirrel(tmp_path, source, "verifier-composition-ok")


@pytest.mark.parametrize(
    ("info_nut", "expected_short_name"),
    [
        (bus_route.INFO_NUT, "MCPB"),
        (bus_network.INFO_NUT, "MCPB"),
        (freight_network.INFO_NUT, "BRTF"),
    ],
    ids=["bus-route", "bus-network", "freight-network"],
)
def test_composed_verifier_info_registers_its_runtime_identity(
    tmp_path: Path, info_nut: str, expected_short_name: str
) -> None:
    """A malformed info composition must not pass by containing the right text."""
    source = (
        "class AIInfo {}\n"
        "registered_ais <- [];\n"
        "function RegisterAI(info) { ::registered_ais.append(info); }\n"
        + info_nut
        + "\nassert(::registered_ais.len() == 2);\n"
        + 'assert(::registered_ais[0].GetName() == "McpBridge");\n'
        + f'assert(::registered_ais[0].GetShortName() == "{expected_short_name}");\n'
        + 'assert(::registered_ais[0].CreateInstance() == "McpBridge");\n'
        + 'assert(::registered_ais[1].GetName() == "BusRouteBridge");\n'
        + 'assert(::registered_ais[1].CreateInstance() == "McpBridge");\n'
        + 'print("verifier-info-ok\\n");\n'
    )
    run_squirrel(tmp_path, source, "verifier-info-ok")


def test_reachable_any_obeys_direction_and_uses_every_start_tile(
    tmp_path: Path,
) -> None:
    """The multi-source search must find 0→2 without inventing a 2→0 path."""
    source = (
        """class AIMap {
    static function IsValidTile(tile) { return tile >= 0 && tile < 12; }
    static function GetMapSizeX() { return 4; }
    static function GetMapSizeY() { return 3; }
    static function GetTileIndex(x, y) { return y * 4 + x; }
    static function GetTileX(tile) { return tile % 4; }
    static function GetTileY(tile) { return tile / 4; }
}
class AIRoad {
    static function IsRoadTile(tile) { return AIMap.IsValidTile(tile); }
    static function IsRoadStationTile(tile) { return false; }
    static function IsRoadDepotTile(tile) { return false; }
    static function AreRoadTilesConnected(tile_from, tile_to) {
        return (tile_from == 0 && tile_to == 1)
            || (tile_from == 1 && tile_to == 2)
            || (tile_from == 8 && tile_to == 9);
    }
}
class McpBridge {
    function IsRoadLike(tile);
    function DirectConnected(tile_from, tile_to);
    function ReachableAny(tiles_from, tiles_to);
}
"""
        + IS_ROAD_LIKE
        + DIRECT_CONNECTED
        + REACHABLE_ANY
        + """local bridge = McpBridge();
assert(bridge.ReachableAny([8, 0], [2]));
assert(!bridge.ReachableAny([2], [0]));
assert(!bridge.ReachableAny([8], [2]));
assert(!bridge.ReachableAny([0], []));
print("reachable-any-ok\\n");
"""
    )
    run_squirrel(tmp_path, source, "reachable-any-ok")


@pytest.mark.parametrize(
    ("guard_invalid_town", "invalid_town_size", "invalid_town_calls"),
    [(False, 4, "> 0"), (True, 0, "== 0")],
    ids=["bus-route", "bus-network"],
)
def test_town_component_variants_execute_their_invalid_town_policy(
    tmp_path: Path,
    guard_invalid_town: bool,
    invalid_town_size: int,
    invalid_town_calls: str,
) -> None:
    """Both variants traverse valid towns; only the guarded one refuses town -1.

    The unguarded variant must still flood the whole road component (tiles
    0, 1, 2 and 4) for an invalid town, so swapping the two compositions cannot
    pass: each parametrized row asserts a different invalid-town outcome.
    """
    component = compose_town_road_component_size(guard_invalid_town=guard_invalid_town)
    source = (
        """class AIMap {
    static function IsValidTile(tile) { return tile >= 0 && tile < 6; }
    static function GetMapSizeX() { return 3; }
    static function GetMapSizeY() { return 2; }
    static function GetTileIndex(x, y) { return y * 3 + x; }
    static function GetTileX(tile) { return tile % 3; }
    static function GetTileY(tile) { return tile / 3; }
}
class AIRoad {
    static function IsRoadTile(tile) { return tile >= 0 && tile < 5; }
    static function IsRoadStationTile(tile) { return false; }
    static function IsRoadDepotTile(tile) { return false; }
    static function AreRoadTilesConnected(tile_from, tile_to) {
        return (tile_from == 0 && tile_to == 1)
            || (tile_from == 1 && tile_to == 0)
            || (tile_from == 1 && tile_to == 2)
            || (tile_from == 2 && tile_to == 1)
            || (tile_from == 1 && tile_to == 4)
            || (tile_from == 4 && tile_to == 1);
    }
}
town_calls <- 0;
class AITown {
    static function IsWithinTownInfluence(town_id, tile) {
        ::town_calls++;
        if (town_id < 0) return true;
        return town_id == 7 && tile != 2;
    }
}
class McpBridge {
    function IsRoadLike(tile);
    function DirectConnected(tile_from, tile_to);
    function TownRoadComponentSize(tile, town_id, max_visits);
}
"""
        + IS_ROAD_LIKE
        + DIRECT_CONNECTED
        + component
        + """local bridge = McpBridge();
assert(bridge.TownRoadComponentSize(0, 7, 20) == 3);
assert(bridge.TownRoadComponentSize(0, 7, 2) == 2);
::town_calls = 0;
"""
        + f"assert(bridge.TownRoadComponentSize(0, -1, 20) == {invalid_town_size});\n"
        + f"assert(::town_calls {invalid_town_calls});\n"
        + """
print("town-component-ok\\n");
"""
    )
    run_squirrel(tmp_path, source, "town-component-ok")


# A 4x2 world for the bus-route verifier: bus-stop tiles 0 and 3 (stations 10 and
# 20), a road depot on tile 1, and a road corridor 4-5-6-7 joining all three. The
# one road vehicle (id 7) is parked stopped inside the depot for the whole run.
# ``print`` is wrapped so standalone output is line-oriented the way OpenTTD's AI
# log is, which lets the verifier's own trace parser read it back.
BUS_ROUTE_WORLD_STUB = """native_print <- ::print;
::print = function (value) { ::native_print(value + "\\n"); };
sleep_budget <- 2000;
event_cursor <- 0;
pending_events <- [];

function StubIdTable(ids)
{
    local table = {};
    foreach (id in ids) table[id] <- 1;
    return table;
}

class AIController {
    sleep_calls = 0;
    function Sleep(ticks)
    {
        this.sleep_calls++;
        if (this.sleep_calls > ::sleep_budget) throw "sleep-budget-exhausted";
    }
}

class AIMap {
    static function IsValidTile(tile) { return tile >= 0 && tile < 8; }
    static function GetMapSizeX() { return 4; }
    static function GetMapSizeY() { return 2; }
    static function GetTileIndex(x, y) { return y * 4 + x; }
    static function GetTileX(tile) { return tile % 4; }
    static function GetTileY(tile) { return tile / 4; }
}

class AITile {
    static TRANSPORT_ROAD = 0;
    static function GetClosestTown(tile) { return 3; }
}

class AIRoad {
    static ROADTYPE_ROAD = 0;
    static function SetCurrentRoadType(road_type) {}
    static function IsRoadTile(tile) { return tile >= 4 && tile < 8; }
    static function IsRoadStationTile(tile) { return tile == 0 || tile == 3; }
    static function IsRoadDepotTile(tile) { return tile == 1; }
    static function IsDriveThroughRoadStationTile(tile) { return false; }
    static function GetDriveThroughBackTile(tile) { return -1; }
    static function GetRoadStationFrontTile(tile)
    {
        if (tile == 0) return 4;
        if (tile == 3) return 7;
        return -1;
    }
    static function GetRoadDepotFrontTile(tile) { return tile == 1 ? 5 : -1; }
    static function AreRoadTilesConnected(tile_from, tile_to)
    {
        local edges = [[4, 5], [5, 6], [6, 7], [0, 4], [1, 5], [3, 7]];
        foreach (edge in edges) {
            if (edge[0] == tile_from && edge[1] == tile_to) return true;
            if (edge[1] == tile_from && edge[0] == tile_to) return true;
        }
        return false;
    }
}

class AITown {
    static function GetPopulation(town_id) { return 500; }
    static function GetName(town_id) { return "Testville"; }
    static function IsWithinTownInfluence(town_id, tile) { return true; }
}
function AITownList() { return StubIdTable([3]); }

class AIStation {
    static STATION_BUS_STOP = 1;
    static function IsValidStation(station_id) { return station_id == 10 || station_id == 20; }
    static function HasStationType(station_id, station_type) { return AIStation.IsValidStation(station_id); }
    static function IsWithinTownInfluence(station_id, town_id) { return true; }
    static function GetStationID(tile)
    {
        if (tile == 0) return 10;
        if (tile == 3) return 20;
        return -1;
    }
}
function AIStationList(station_type) { return StubIdTable([10, 20]); }
function AITileList_StationType(station_id, station_type)
{
    if (station_id == 10) return StubIdTable([0]);
    if (station_id == 20) return StubIdTable([3]);
    return {};
}
function AIDepotList(transport_type) { return StubIdTable([1]); }
function AIVehicleList() { return StubIdTable([7]); }

class AIVehicle {
    static VT_ROAD = 1;
    static function GetVehicleType(vehicle_id) { return AIVehicle.VT_ROAD; }
    static function GetLocation(vehicle_id) { return 1; }
    static function GetCurrentSpeed(vehicle_id) { return 0; }
    static function GetState(vehicle_id) { return 5; }
    static function IsInDepot(vehicle_id) { return true; }
    static function IsStoppedInDepot(vehicle_id) { return true; }
}

class AIOrder {
    static ORDER_CURRENT = 255;
    static function GetOrderCount(vehicle_id) { return 2; }
    static function IsGotoStationOrder(vehicle_id, order_position) { return true; }
    static function GetOrderDestination(vehicle_id, order_position) { return order_position == 1 ? 3 : 0; }
    static function ResolveOrderPosition(vehicle_id, order_position) { return 0; }
}

class AIEvent {
    static ET_STATION_FIRST_VEHICLE = 1;
    static ET_VEHICLE_LOST = 2;
    static ET_VEHICLE_CRASHED = 3;
    static ET_VEHICLE_WAITING_IN_DEPOT = 4;
}
class StubEvent {
    event_type = 0;
    vehicle_id = -1;
    station_id = -1;
    constructor(type_id, vid, sid)
    {
        this.event_type = type_id;
        this.vehicle_id = vid;
        this.station_id = sid;
    }
    function GetEventType() { return this.event_type; }
    function GetVehicleID() { return this.vehicle_id; }
    function GetStationID() { return this.station_id; }
}
class AIEventController {
    static function IsEventWaiting() { return ::event_cursor < ::pending_events.len(); }
    static function GetNextEvent()
    {
        local event = ::pending_events[::event_cursor];
        ::event_cursor++;
        return event;
    }
}
class AIEventStationFirstVehicle { static function Convert(event) { return event; } }
class AIEventVehicleLost { static function Convert(event) { return event; } }
class AIEventVehicleCrashed { static function Convert(event) { return event; } }
class AIEventVehicleWaitingInDepot { static function Convert(event) { return event; } }

"""

# Position of ``samples`` in the ``BUS_ROUTE_VERIFIER|`` payload that
# ``bus_route.main`` parses as ``dynamic_sample_count``.
DYNAMIC_SAMPLE_COUNT_INDEX = 40
VERIFIER_PAYLOAD_FIELDS = 43


def test_bus_route_event_and_trace_emission_round_trips_through_its_parser(
    tmp_path: Path,
) -> None:
    """An emitter/parser mismatch or a dropped event branch must fail here."""
    source = (
        BUS_ROUTE_WORLD_STUB
        + bus_route.MAIN_NUT
        + """::pending_events = [
    StubEvent(AIEvent.ET_STATION_FIRST_VEHICLE, 7, 10),
    StubEvent(AIEvent.ET_VEHICLE_LOST, 7, -1),
    StubEvent(AIEvent.ET_STATION_FIRST_VEHICLE, 8, 20),
];
local bridge = McpBridge();
local event_state = {
    station_a_first_vehicle = false,
    station_b_first_vehicle = false,
    vehicle_lost = false,
    vehicle_crashed = false,
};
bridge.ProcessEvents(7, 10, 20, event_state);
assert(event_state.station_a_first_vehicle);
assert(!event_state.station_b_first_vehicle);
assert(event_state.vehicle_lost);
assert(!event_state.vehicle_crashed);
bridge.EmitTrace(3, 7);
print("bus-route-trace-ok");
"""
    )

    stdout = run_squirrel(tmp_path, source, "bus-route-trace-ok")

    entries = [
        entry
        for entry in (bus_route.parse_trace_line(line) for line in stdout.splitlines())
        if entry is not None
    ]
    assert entries == [
        {
            "type": "event",
            "event": "station_first_vehicle",
            "station_id": 10,
            "vehicle_id": 7,
        },
        {"type": "event", "event": "vehicle_lost", "vehicle_id": 7},
        {
            "type": "event",
            "event": "station_first_vehicle",
            "station_id": 20,
            "vehicle_id": 8,
        },
        {
            "type": "sample",
            "sample": 3,
            "vehicle_tile": 1,
            "speed": 0,
            "state": 5,
            "in_depot": True,
            "stopped_in_depot": True,
            "current_order_index": 0,
            "current_order_destination": 0,
            "current_order_station": 10,
            "location_station": -1,
        },
    ]


def test_bus_route_sampling_stops_early_on_a_vehicle_stopped_in_its_depot(
    tmp_path: Path,
) -> None:
    """Without the terminal-depot break the loop burns all 900 samples per verify."""
    source = (
        BUS_ROUTE_WORLD_STUB
        + bus_route.MAIN_NUT
        + """local bridge = McpBridge();
try {
    bridge.Start();
} catch (error) {
    // Start() parks in an endless Sleep once it has printed its payload.
    if (error != "sleep-budget-exhausted") throw error;
}
print("bus-route-start-ok");
"""
    )

    stdout = run_squirrel(tmp_path, source, "bus-route-start-ok")

    payloads = [
        line.split("BUS_ROUTE_VERIFIER|", 1)[1].split("|")
        for line in stdout.splitlines()
        if "BUS_ROUTE_VERIFIER|" in line
    ]
    assert len(payloads) == 1
    assert len(payloads[0]) == VERIFIER_PAYLOAD_FIELDS
    assert payloads[0][DYNAMIC_SAMPLE_COUNT_INDEX] == "5"


def test_bus_network_station_own_town_reports_each_tile_closest_town(
    tmp_path: Path,
) -> None:
    """The network verifier must keep its own per-station town lookup."""
    source = (
        """class AIController {}
class AIMap {
    static function IsValidTile(tile) { return tile >= 0 && tile < 8; }
}
class AITile {
    static function GetClosestTown(tile) { return tile == 4 ? 9 : 2; }
}
"""
        + bus_network.MAIN_NUT
        + """local bridge = McpBridge();
assert(bridge.StationOwnTown(4) == 9);
assert(bridge.StationOwnTown(6) == 2);
assert(bridge.StationOwnTown(-1) == -1);
print("station-own-town-ok\\n");
"""
    )
    run_squirrel(tmp_path, source, "station-own-town-ok")


def test_freight_primary_cargo_picks_the_largest_capacity_cargo(
    tmp_path: Path,
) -> None:
    """The freight verifier must keep the cargo lookup that rejects passenger routes."""
    source = (
        """class AIController {}
function AICargoList()
{
    local cargoes = {};
    foreach (cargo_id in [0, 2, 5]) cargoes[cargo_id] <- 1;
    return cargoes;
}
class AICargo {
    static CC_PASSENGERS = 1;
    static function GetCargoLabel(cargo_id) { return cargo_id == 2 ? "COAL" : "PASS"; }
    static function HasCargoClass(cargo_id, cargo_class) { return cargo_id == 0; }
}
class AIVehicle {
    static function GetCapacity(vehicle_id, cargo_id)
    {
        if (vehicle_id != 11) return 0;
        if (cargo_id == 0) return 30;
        if (cargo_id == 2) return 40;
        return 0;
    }
}
"""
        + freight_network.MAIN_NUT
        + """local bridge = McpBridge();
local freight = bridge.PrimaryCargo(11);
assert(freight[0] == 2);
assert(freight[1] == "COAL");
assert(freight[2] == 40);
assert(!freight[3]);
local empty = bridge.PrimaryCargo(12);
assert(empty[0] == -1);
assert(empty[1] == "");
assert(empty[2] == 0);
assert(!empty[3]);
print("primary-cargo-ok\\n");
"""
    )
    run_squirrel(tmp_path, source, "primary-cargo-ok")


STATION_OBSERVER_API_STUB = r"""
current_date <- 100;
sample_index <- 0;
sleep_calls <- 0;
sleep_ticks_total <- 0;
daily_date_steps <- false;
date_never_advances <- false;
fail_speed_after_first_sleep <- false;
wide_station <- false;

function StubStationObserverList(ids)
{
    local result = {};
    foreach (id in ids) result[id] <- 1;
    return result;
}

class AIController {
    function Sleep(ticks)
    {
        ::sleep_calls++;
        ::sleep_ticks_total += ticks;
        ::sample_index++;
        if (::date_never_advances) {
            return;
        } else if (::daily_date_steps) {
            ::current_date++;
        } else if (::sample_index % 2 == 0) {
            ::current_date++;
        }
    }
}

class AIDate {
    static function GetCurrentDate() { return ::current_date; }
    static function GetYear(date) { return 2000; }
    static function GetMonth(date) { return 1; }
    static function GetDayOfMonth(date) { return date - 99; }
}

class AICompany {
    static COMPANY_SELF = 0;
    static function GetBankBalance(company) { return 100000; }
}

class AIEventController {
    static function IsEventWaiting() { return false; }
}

class AIMap {
    static function GetTileX(tile) { return tile % 10; }
    static function GetTileY(tile) { return tile / 10; }
}

class AIStation {
    static STATION_BUS_STOP = 1;
    static STATION_TRUCK_STOP = 2;
    static function IsValidStation(station_id) { return station_id == 10 || station_id == 20; }
    static function GetName(station_id) { return station_id == 10 ? "Union Road" : "Rail Only"; }
    static function GetLocation(station_id) { return station_id == 10 ? 11 : 50; }
    static function GetCargoWaiting(station_id, cargo_id)
    {
        if (station_id != 10) return 0;
        if (::wide_station) return cargo_id + 1;
        if (::sample_index == 0) return cargo_id == 0 ? 9 : 0;
        if (::sample_index == 1) {
            if (cargo_id == 0) return 3;
            if (cargo_id == 1) return 4;
            return 0;
        }
        if (::sample_index == 2) {
            if (cargo_id == 0) return 9;
            if (cargo_id == 1) return 4;
            return 0;
        }
        if (::sample_index == 3) return cargo_id == 0 ? 2 : 0;
        return 0;
    }
}

function AITileList_StationType(station_id, station_type)
{
    if (station_id != 10) return StubStationObserverList([]);
    if (::wide_station) {
        /* Nine adjacent bus tiles on row one plus one far truck tile, so the
         * tenth tile is dropped by the per-date stop-row cap while staying
         * inside the station-wide near radius of its own vehicle. */
        if (station_type == AIStation.STATION_BUS_STOP) {
            return StubStationObserverList([11, 12, 13, 14, 15, 16, 17, 18, 19]);
        }
        if (station_type == AIStation.STATION_TRUCK_STOP) return StubStationObserverList([91]);
        return StubStationObserverList([]);
    }
    if (station_type == AIStation.STATION_BUS_STOP) return StubStationObserverList([11]);
    if (station_type == AIStation.STATION_TRUCK_STOP) return StubStationObserverList([13]);
    return StubStationObserverList([]);
}

function AIVehicleList() { return StubStationObserverList([1, 2, 3, 4]); }

class AIVehicle {
    static VT_ROAD = 1;
    static VT_RAIL = 2;
    static VS_RUNNING = 1;
    static VS_AT_STATION = 3;
    static function IsValidVehicle(vehicle_id) { return vehicle_id >= 1 && vehicle_id <= 3; }
    static function IsInDepot(vehicle_id) { return false; }
    static function IsStoppedInDepot(vehicle_id) { return false; }
    static function GetVehicleType(vehicle_id) { return vehicle_id == 4 ? AIVehicle.VT_RAIL : AIVehicle.VT_ROAD; }
    static function GetLocation(vehicle_id)
    {
        if (::wide_station) {
            if (vehicle_id == 1) return 91;
            if (vehicle_id == 2) return 11;
            return 77;
        }
        if (::sample_index == 0) {
            if (vehicle_id == 1) return 12;
            if (vehicle_id == 2) return 55;
            if (vehicle_id == 3) return 44;
            return 90;
        }
        if (::sample_index == 1) return vehicle_id == 2 ? 11 : 90;
        if (::sample_index == 2) return vehicle_id == 2 || vehicle_id == 3 ? 11 : 90;
        if (::sample_index == 3) {
            if (vehicle_id == 2) return 11;
            if (vehicle_id == 3) return 13;
        }
        return 90;
    }
    static function GetCurrentSpeed(vehicle_id)
    {
        if (::fail_speed_after_first_sleep && ::sample_index >= 1) throw "speed API failed";
        if (::sample_index == 0 && vehicle_id == 1) return 0;
        if (::sample_index == 0 && vehicle_id == 3) return 5;
        if (::sample_index == 1 && vehicle_id == 2) return 5;
        if (::sample_index == 2) return 5;
        return 0;
    }
    static function GetState(vehicle_id)
    {
        if (::sample_index == 1 && vehicle_id == 2) return AIVehicle.VS_AT_STATION;
        if (::sample_index == 3 && (vehicle_id == 2 || vehicle_id == 3)) return AIVehicle.VS_AT_STATION;
        return AIVehicle.VS_RUNNING;
    }
}

class AIOrder {
    static ORDER_CURRENT = 0;
    static function ResolveOrderPosition(vehicle_id, position) { return 0; }
    static function GetOrderCount(vehicle_id) { return 1; }
}

function AICargoList()
{
    if (::wide_station) return StubStationObserverList([0, 1, 2, 3, 4, 5, 6, 7, 8, 9]);
    return StubStationObserverList([0, 1, 2]);
}

class AICargo {
    static function GetCargoLabel(cargo_id)
    {
        if (cargo_id == 0) return "PASS";
        if (cargo_id == 1) return "MAIL";
        if (cargo_id == 2) return "COAL";
        return "C" + cargo_id;
    }
}
"""


def _run_station_observer(
    tmp_path: Path,
    *,
    station_id: int = 10,
    advance_days: int = 2,
    setup: str = "",
    timestamp: str = "station-fixture",
) -> tuple[dict, dict]:
    source = (
        STATION_OBSERVER_API_STUB
        + f"\ndofile({_squirrel_string(MCP_BRIDGE_DIR / 'main.nut')});\n"
        + setup
        + "\nlocal bridge = McpBridge();\n"
        + "bridge.last_seen_economy_year = 2000;\n"
        + "bridge.pending_year_end_reports = [];\n"
        + f'bridge.DispatchCommand(["wait_and_observe_station", {station_id}, {advance_days}], "{timestamp}");\n'
        + 'print("\\nSTATE|{\\"current_date\\":" + current_date\n'
        + '    + ",\\"sleep_calls\\":" + sleep_calls\n'
        + '    + ",\\"sleep_ticks_total\\":" + sleep_ticks_total + "}\\n");\n'
        + 'print("station-observer-ok\\n");\n'
    )
    stdout = run_squirrel(
        tmp_path, source, "station-observer-ok", name=f"{timestamp}.nut"
    )
    result_line = next(
        line
        for line in stdout.splitlines()
        if line.startswith(f"MCP_RESULT|{timestamp}|")
    )
    state_line = next(line for line in stdout.splitlines() if line.startswith("STATE|"))
    return (
        json.loads(result_line.split("|", 2)[2]),
        json.loads(state_line.removeprefix("STATE|")),
    )


def _run_vehicle_observer(
    tmp_path: Path,
    *,
    vehicle_id: int = 1,
    advance_days: int = 1,
    setup: str = "",
    timestamp: str = "vehicle-fixture",
) -> tuple[dict, dict]:
    source = (
        STATION_OBSERVER_API_STUB
        + f"\ndofile({_squirrel_string(MCP_BRIDGE_DIR / 'main.nut')});\n"
        + setup
        + "\nlocal bridge = McpBridge();\n"
        + "bridge.last_seen_economy_year = 2000;\n"
        + "bridge.pending_year_end_reports = [];\n"
        + f'bridge.DispatchCommand(["wait_and_observe_vehicle", {vehicle_id}, {advance_days}], "{timestamp}");\n'
        + 'print("\\nSTATE|{\\"current_date\\":" + current_date\n'
        + '    + ",\\"sleep_calls\\":" + sleep_calls\n'
        + '    + ",\\"sleep_ticks_total\\":" + sleep_ticks_total + "}\\n");\n'
        + 'print("vehicle-observer-ok\\n");\n'
    )
    stdout = run_squirrel(
        tmp_path, source, "vehicle-observer-ok", name=f"{timestamp}.nut"
    )
    result_line = next(
        line
        for line in stdout.splitlines()
        if line.startswith(f"MCP_RESULT|{timestamp}|")
    )
    state_line = next(line for line in stdout.splitlines() if line.startswith("STATE|"))
    return (
        json.loads(result_line.split("|", 2)[2]),
        json.loads(state_line.removeprefix("STATE|")),
    )


def test_station_observer_uses_manhattan_union_geometry_without_double_counting(
    tmp_path: Path,
) -> None:
    """The union must include radius four, exclude diagonal six, and count once."""
    result, state = _run_station_observer(
        tmp_path,
        advance_days=1,
        setup="::daily_date_steps = true;",
        timestamp="station-union",
    )

    assert result["ok"] is True
    assert result["daily"] == [
        {
            "date": 100,
            "sample_count": 1,
            "sampled_max_vehicles_near": 2,
            "sampled_max_zero_speed_vehicles_near": 1,
            "stops": [
                {
                    "tile": 11,
                    "sampled_max_vehicles_at_stop": 0,
                    "sampled_max_loading_or_unloading_vehicles_at_stop": 0,
                },
                {
                    "tile": 13,
                    "sampled_max_vehicles_at_stop": 0,
                    "sampled_max_loading_or_unloading_vehicles_at_stop": 0,
                },
            ],
            "last_sample_waiting_cargo": [
                {"cargo_id": 0, "cargo_label": "PASS", "waiting": 9}
            ],
            "last_sample_waiting_cargo_total": 9,
            "last_sample_waiting_cargo_types": 1,
            "last_sample_waiting_cargo_truncated": False,
        }
    ]
    assert state == {"current_date": 101, "sleep_calls": 1, "sleep_ticks_total": 5}


def test_station_observer_aggregates_independent_daily_maxima_and_last_cargo_sample(
    tmp_path: Path,
) -> None:
    """Per-date maxima and cargo must retain their distinct sample semantics."""
    result, state = _run_station_observer(tmp_path, timestamp="station-series")

    assert result["ok"] is True
    assert result["station_id"] == 10
    assert result["station_name"] == "Union Road"
    assert result["station_tile"] == 11
    assert result["bus_stop_tiles"] == [11]
    assert result["truck_stop_tiles"] == [13]
    assert result["road_stop_tiles"] == [11, 13]
    assert result["vehicle_scope"] == "current_company_primary_road_vehicles"
    assert result["near_counts_include_vehicles_in_depots"] is True
    assert result["near_manhattan_radius"] == 4
    assert result["sampling_interval_script_ticks"] == 5
    assert result["sampled_maxima_are_independent"] is True
    assert result["requested_advance_days"] == 2
    assert result["advanced_days"] == 2
    assert result["start_date"] == 100
    assert result["end_date"] == 102
    assert result["bounded_window_completed"] is True
    assert result["safety_cap_exhausted"] is False
    assert result["game_mutated"] is True
    assert result["time_may_have_advanced"] is False
    assert state == {"current_date": 102, "sleep_calls": 4, "sleep_ticks_total": 20}

    first, second = result["daily"]
    assert first["sample_count"] == 2
    assert first["sampled_max_vehicles_near"] == 2
    assert first["sampled_max_zero_speed_vehicles_near"] == 1
    assert first["stops"][0] == {
        "tile": 11,
        "sampled_max_vehicles_at_stop": 1,
        "sampled_max_loading_or_unloading_vehicles_at_stop": 1,
    }
    assert first["last_sample_waiting_cargo"] == [
        {"cargo_id": 0, "cargo_label": "PASS", "waiting": 3},
        {"cargo_id": 1, "cargo_label": "MAIL", "waiting": 4},
    ]
    assert first["last_sample_waiting_cargo_total"] == 7

    assert second["sample_count"] == 2
    assert second["sampled_max_vehicles_near"] == 2
    assert second["sampled_max_zero_speed_vehicles_near"] == 2
    assert second["stops"] == [
        {
            "tile": 11,
            "sampled_max_vehicles_at_stop": 2,
            "sampled_max_loading_or_unloading_vehicles_at_stop": 1,
        },
        {
            "tile": 13,
            "sampled_max_vehicles_at_stop": 1,
            "sampled_max_loading_or_unloading_vehicles_at_stop": 1,
        },
    ]
    assert second["last_sample_waiting_cargo"] == [
        {"cargo_id": 0, "cargo_label": "PASS", "waiting": 2}
    ]
    assert second["last_sample_waiting_cargo_total"] == 2


def test_station_observer_caps_direct_bridge_calls_at_ninety_daily_rows(
    tmp_path: Path,
) -> None:
    """A bypassed caller must not make the bridge emit an unbounded date series."""
    result, state = _run_station_observer(
        tmp_path,
        advance_days=200,
        setup="::daily_date_steps = true;",
        timestamp="station-bound",
    )

    assert result["ok"] is True
    assert result["requested_advance_days"] == 200
    assert result["advanced_days"] == 90
    assert len(result["daily"]) == 90
    assert [row["date"] for row in result["daily"]] == list(range(100, 190))
    assert state == {"current_date": 190, "sleep_calls": 90, "sleep_ticks_total": 450}


def test_station_observer_caps_the_per_date_stop_and_cargo_dimensions(
    tmp_path: Path,
) -> None:
    """A many-stop, many-cargo station must not multiply each date row unbounded."""
    result, _ = _run_station_observer(
        tmp_path,
        advance_days=1,
        setup="::daily_date_steps = true; ::wide_station = true;",
        timestamp="station-wide",
    )

    assert result["ok"] is True
    # Script API list order is unspecified, so the contract is which tiles the
    # result names as observed, not which of the nine bus tiles they are.
    assert sorted(result["road_stop_tiles"]) == [11, 12, 13, 14, 15, 16, 17, 18, 19, 91]
    assert result["observed_stop_tiles"] == result["road_stop_tiles"][:8]
    assert 91 not in result["observed_stop_tiles"]
    assert result["observed_stop_tiles_truncated"] is True
    assert result["max_stop_rows_per_date"] == 8
    assert result["max_waiting_cargo_rows_per_date"] == 8

    (row,) = result["daily"]
    assert [stop["tile"] for stop in row["stops"]] == result["observed_stop_tiles"]
    # The vehicle parked on the dropped truck tile still counts station-wide.
    assert row["sampled_max_vehicles_near"] == 2
    assert len(row["last_sample_waiting_cargo"]) == 8
    assert all(
        cargo["waiting"] == cargo["cargo_id"] + 1
        for cargo in row["last_sample_waiting_cargo"]
    )
    assert row["last_sample_waiting_cargo_truncated"] is True
    assert row["last_sample_waiting_cargo_types"] == 10
    assert row["last_sample_waiting_cargo_total"] == 55


def test_station_observer_rejects_a_station_without_road_stops_before_sleep(
    tmp_path: Path,
) -> None:
    """A rail-only station must fail before any script interval can advance time."""
    result, state = _run_station_observer(
        tmp_path, station_id=20, timestamp="station-no-road"
    )

    assert result["ok"] is False
    assert result["error"] == "station_has_no_road_stop_tiles"
    assert result["requested_advance_days"] == 2
    assert result["road_stop_tiles"] == []
    assert result["game_mutated"] is False
    assert result["time_may_have_advanced"] is False
    assert state == {"current_date": 100, "sleep_calls": 0, "sleep_ticks_total": 0}


def test_station_observer_rejects_an_invalid_station_before_sleep(
    tmp_path: Path,
) -> None:
    """An invalid station id must fail without entering the time-advance loop."""
    result, state = _run_station_observer(
        tmp_path, station_id=99, advance_days=7, timestamp="station-invalid"
    )

    assert result["ok"] is False
    assert result["error"] == "invalid_station_id"
    assert result["requested_advance_days"] == 7
    assert result["game_mutated"] is False
    assert result["time_may_have_advanced"] is False
    assert state == {"current_date": 100, "sleep_calls": 0, "sleep_ticks_total": 0}


def test_station_observer_reports_a_known_partial_advance_when_the_cap_binds(
    tmp_path: Path,
) -> None:
    """A stalled date must not turn the defensive loop cap into false success."""
    result, state = _run_station_observer(
        tmp_path,
        advance_days=1,
        setup="::date_never_advances = true;",
        timestamp="station-cap",
    )

    assert result["ok"] is False
    assert result["error"] == "advance_incomplete"
    assert result["status"] == "advance_incomplete"
    assert result["requested_advance_days"] == 1
    assert result["advanced_days"] == 0
    assert result["bounded_window_completed"] is False
    assert result["safety_cap_exhausted"] is True
    assert result["game_mutated"] is True
    assert result["time_may_have_advanced"] is False
    assert state == {
        "current_date": 100,
        "sleep_calls": 250,
        "sleep_ticks_total": 1250,
    }


def test_vehicle_observer_reports_a_known_partial_advance_when_the_cap_binds(
    tmp_path: Path,
) -> None:
    """The existing vehicle observer must share the fail-closed cap envelope."""
    result, state = _run_vehicle_observer(
        tmp_path,
        advance_days=1,
        setup="::date_never_advances = true;",
        timestamp="vehicle-cap",
    )

    assert result["ok"] is False
    assert result["error"] == "advance_incomplete"
    assert result["status"] == "advance_incomplete"
    assert result["requested_advance_days"] == 1
    assert result["advanced_days"] == 0
    assert result["bounded_window_completed"] is False
    assert result["safety_cap_exhausted"] is True
    assert result["game_mutated"] is True
    assert result["time_may_have_advanced"] is False
    assert state == {
        "current_date": 100,
        "sleep_calls": 250,
        "sleep_ticks_total": 250,
    }


def test_station_observer_contains_sensor_failure_after_time_may_have_advanced(
    tmp_path: Path,
) -> None:
    """A later sample failure must emit sensor_api_error without claiming read-only state."""
    result, state = _run_station_observer(
        tmp_path,
        setup="::daily_date_steps = true; ::fail_speed_after_first_sleep = true;",
        timestamp="station-sensor-failure",
    )

    assert result["ok"] is False
    assert result["error"] == "sensor_api_error"
    assert result["sensor_error"] == {
        "code": "station_vehicle_sample_unavailable",
        "field": "daily",
    }
    assert result["start_date"] == 100
    assert result["last_known_date"] == 101
    assert result["game_mutated"] is True
    assert result["time_may_have_advanced"] is True
    assert state == {"current_date": 101, "sleep_calls": 1, "sleep_ticks_total": 5}
