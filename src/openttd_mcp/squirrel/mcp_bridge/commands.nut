/* Runtime command file rewritten by Python controllers/MCP tools.
 * Format: Commands.timestamp is a unique id; Commands.commands is a list of
 * flat command arrays, e.g. ["build_road", from_tile, to_tile] or
 * ["check_route_connectivity", town_id, depot_tile, depot_front_tile, stop...].
 */
class Commands {
    timestamp = "initial";
    emit_map_snapshot = false;
    commands = [];
}
