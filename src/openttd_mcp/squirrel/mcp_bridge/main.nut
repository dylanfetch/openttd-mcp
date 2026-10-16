/* Derived needs_replacement threshold (verified against the OpenTTD 15.1
 * source). The vehicle/depot GUIs apply their old-age cue
 * when age_left_days is at most 365 (vehicle_gui.cpp/depot_gui.cpp). The
 * separate news event fires later and is not exposed through the AI API. This
 * factual cue never recommends or ranks a replacement. */
const NEEDS_REPLACEMENT_AGE_LEFT_DAYS = 365;
/* Widest get_region radius any detail mode accepts; the per-mode maximum is
 * enforced in server.py (QUERY_REGION_RADIUS_MAX_BY_DETAIL) before dispatch. */
const QUERY_REGION_RADIUS_MAX = 12;
const LOST_EVENT_LOG_CAP = 50;
const WARNINGS_EVENT_LOG_CAP = 12;
/* wait_and_observe_station repeats its per-stop-tile and per-cargo objects once
 * per observed calendar date, so both dimensions are capped as well as the 90
 * date rows; without these caps a many-stop station multiplies the response by
 * its stop count. Truncation stays visible in the emitted result. */
const STATION_STOP_ROW_CAP = 8;
const STATION_CARGO_ROW_CAP = 8;
const YEAR_END_NOT_IN_REPORT = "the OpenTTD 15.1 script API does not expose the finance window's category ledger or the one-time construction, vehicle purchase, and vehicle sale amounts; only the recurring quarterly figures above are available";

class McpBridge extends AIController {
    last_command_timestamp = null;
    observation_emitted = false;
    observation_counter = 0;
    vehicle_lost_events = 0;
    vehicle_crashed_events = 0;
    lost_events = null;
    /* The two other per-vehicle events the game announces
     * (arrived-and-halted in depot; flagged unprofitable at the economy-year
     * roll) share the lost-event pattern: a scalar uncapped counter plus a
     * capped, save-persisted attributed log appended at drain time, so an
     * event drained by any handler still reaches a later wait window. */
    vehicle_depot_events = 0;
    depot_events = null;
    vehicle_unprofitable_events = 0;
    unprofitable_events = null;
    last_seen_economy_year = null;
    pending_year_end_reports = null;
    year_end_delivery_timestamp = null;
    year_end_delivered_count = 0;
    legacy_clear_year_end_on_emit = true;

    function Start();
    function Save();
    function Load(version, data);
    function EmitBridgeOutput(line);
    function EnsureEventState();
    function JsonEscape(s);
    function BoolJson(value);
    function DateJson(date);
    function QuarterFinanceTable(offset);
    function QuarterFinanceJson(facts);
    function MaybeSnapshotYearEnd();
    function YearEndReportJson(report);
    function VehicleStateName(state);
    function Contains(items, value);
    function AppendUnique(items, value);
    function CompareIntLists(left, right);
    function IntArrayJson(items);
    function FactErrorsJson(errors);
    function StationTypeTilesList(sid, station_type);
    function ResolveStationJoinArg(join_arg);
    function RoadStopErrUnknownExtra(tile, join, include_drive_through, fact_errors);
    function AttributedEventRecord(event_kind, event_index, vehicle_id);
    function AttributedEventRecordJson(record);
    function AttributedEventsJson(records, cap);
    function RecentEventsSince(records, start_count);
    function RecordLostEvent(vehicle_id);
    function RecordDepotEvent(vehicle_id);
    function RecordUnprofitableEvent(vehicle_id);
    function DrainGameEvents(watch);
    function VehicleEventsFor(records, vehicle_id, cap);
    function PollEvents();
    function FindLargestTown();
    function FindPassengerEngines(max_count);
    function IsRoadLike(tile);
    function DirectConnected(tile_from, tile_to);
    function RoadEdgeRemovable(tile, nbr, field, fact_errors);
    function RoadBitJson(tile, nbr, field, fact_errors);
    function RoadBitsJson(tile, tile_x, tile_y, field_prefix, fact_errors);
    function RoadDistancesFrom(tile_from);
    function RoadDistanceFrom(distances, tile_to);
    function RoadPathLength(tile_from, tile_to);
    function Reachable(tile_from, tile_to);
    function RoadComponentSize(tile, max_visits);
    function TownRoadComponentSize(tile, town_id, max_visits);
    function StationTileConnected(station_tile);
    function QueryRouteConnectivity(town_id, depot_tile, depot_front_tile, stop_tiles, timestamp);
    function WaitAndObserveVehicle(vehicle_id, advance_days, timestamp);
    function WaitAndObserveStation(station_id, advance_days, timestamp);
    function CountRoadVehicles();
    function CompanyFinanceCoreJson();
    function CompanyQuarterReportsJson();
    function VehiclesJson();
    function VehicleJson(vehicle_id);
    function GroupName(group_id);
    function RoadEngineCandidates();
    function AutoreplaceRulesJson(group_id, candidates);
    function GroupsJson();
    function RoutesJson();
    function RouteStationTiles(vehicle_id);
    function OrderDestinationsJson(vehicle_id);
    function OrderStationsJson(vehicle_id);
    function OrderConditionName(condition);
    function OrderCompareName(compare);
    function OrderListJson(vehicle_id);
    function OrderFlagsJson(vehicle_id);
    function CargoCapacitiesJson(vehicle_id);
    function CanonicalRouteSignature(station_tiles);
    function RouteEconomicsVehicleJson(vehicle_id, station_tiles);
    function RouteEconomicsJson();
    function StationsJson(passenger_cargo);
    function TownsJson();
    function CargoesJson();
    function TownCargoAcceptanceJson(tile, truck_radius);
    function CargoListJson(cargo_list, industry_id, include_production_metrics, fact_errors);
    function IndustriesJson();
    function SubsidyParticipantJson(participant_type, participant_id);
    function SubsidiesJson(company_id);
    function DepotsJson();
    function ClosestTownId(tile);
    function IndustryAtTile(tile);
    function TileJson(tile, tile_x, tile_y, passenger_cargo, largest_town_id, cargo_id);
    function TimelapseTileJson(tile, tile_x, tile_y);
    function ShouldEmitMapSnapshot();
    function EmitMapSnapshot();
    function EmitTimelapseMapSnapshot();
    function ProcessCommand(command, timestamp, ack_result_timestamp);
    function DispatchCommand(command, timestamp);
    function ExecuteWait(duration_days, timestamp);
    /* General action surface: legality-only primitives.
       These deliberately do NOT pre-reject legal moves or consult any candidate
       list -- the only rejections allowed are real AIError results from the game. */
    function EmitGeneralObservation();
    function EmitPrimResult(timestamp, action, ok, err, cost, extra);
    function EmitSensorApiError(timestamp, action, code, field, extra);
    function PrimGetTile(tile, cargo_id, timestamp);
    function PrimGetRegion(center_tile, radius, cargo_id, timestamp);
    function PrimListTowns(timestamp);
    function PrimListMyVehicles(timestamp);
    function PrimGetVehicle(vehicle_id, timestamp);
    function PrimListMyStations(timestamp);
    function PrimGetMyFinances(timestamp);
    function PrimGetMyCompany(timestamp);
    function PrimListEngines(only_buildable, passenger_only, timestamp);
    function PrimListBridgeTypes(length, timestamp);
    function PrimListIndustries(timestamp);
    function PrimListSubsidies(timestamp);
    function PrimGetIndustry(industry_id, timestamp);
    function PrimGetMyStation(station_id, tile, timestamp);
    function PrimGetMyStationFacts(station_id, tile, timestamp);
    function PrimCheckTruckStopCatchment(tile, timestamp);
    function PrimCheckTruckStopCatchmentFacts(tile, timestamp);
    function IndustryCatchmentRowJson(industry_id, produced_side);
    function PrimListMyRouteEconomics(timestamp);
    function PrimGetCargoPaymentRate(cargo_id, distance, days_in_transit, timestamp);
    function BuildRoadSegment(from_tile, to_tile);
    function EstimateRoadSpan(span_from, span_to);
    function EstimateRoadSegment(from_tile, to_tile);
    function RoadSpanFirstBlocker(span_from, span_to);
    function RoadSpanFirstBlockedTile(span_from, span_to);
    function PrimCheckBuildRoad(points, timestamp);
    function PrimCheckBuildRoadDepot(tile, front_tile, timestamp);
    function PrimBuildRoad(points, timestamp);
    function PrimBuildOneWayRoad(from_tile, to_tile, timestamp);
    function PrimBuildBridge(start_tile, end_tile, bridge_id, timestamp);
    function PrimBuildTunnel(start_tile, timestamp);
    function PrimTerraformLand(tile, mode, to_tile, corners, timestamp);
    function PrimBuildBusStopDriveThrough(tile, front_tile, join_arg, timestamp);
    function PrimBuildTruckStopDriveThrough(tile, front_tile, join_arg, timestamp);
    function PrimBuildBusStopBay(tile, front_tile, join_arg, timestamp);
    function PrimBuildTruckStopBay(tile, front_tile, join_arg, timestamp);
    function PrimBuildRoadDepot(tile, front_tile, timestamp);
    function PrimBuyVehicle(depot_tile, engine_id, timestamp);
    function PrimCloneVehicle(depot_tile, vehicle_id, share_orders, timestamp);
    function PrimRefitVehicle(vehicle_id, cargo_id, timestamp);
    function PrimCreateGroup(name, timestamp);
    function PrimMoveVehicleToGroup(vehicle_id, group_id, timestamp);
    function PrimSetGroupAutoReplace(group_id, engine_id_old, engine_id_new, timestamp);
    function PrimInsertOrder(vehicle_id, position, stop_tile, order_flags, timestamp);
    function PrimDeleteOrder(vehicle_id, position, timestamp);
    function PrimSetOrderFlags(vehicle_id, position, order_flags, timestamp);
    function PrimAppendOrders(vehicle_id, stop_tiles, order_flags, timestamp);
    function PrimInsertConditionalOrder(vehicle_id, position, condition, compare, value, jump_to, timestamp);
    function PrimSkipToOrder(vehicle_id, position, timestamp);
    function PrimCopyShareOrders(action, vehicle_id, source_vehicle_id, share, timestamp);
    function PrimUnshareOrders(vehicle_id, timestamp);
    function PrimStartStopVehicle(vehicle_id, want_running, timestamp);
    function PrimReverseVehicle(vehicle_id, timestamp);
    function PrimSendVehicleToDepot(vehicle_id, timestamp);
    function PrimSellVehicle(vehicle_id, timestamp);
    function PrimSetLoan(amount, timestamp);
    function PrimDemolishTile(tile, timestamp);
    function PrimRemoveRoad(from_tile, to_tile, timestamp);
};

function McpBridge::Start()
{
    this.EnsureEventState();
    AIController.SetCommandDelay(1);
    AIRoad.SetCurrentRoadType(AIRoad.ROADTYPE_ROAD);
    AICompany.SetName("McpBridge");
    if (this.last_seen_economy_year == null || this.pending_year_end_reports == null) {
        this.last_seen_economy_year = AIDate.GetYear(AIDate.GetCurrentDate());
        this.pending_year_end_reports = [];
    }
    this.EmitBridgeOutput("MCP_BRIDGE_READY");

    while (true) {
        this.MaybeSnapshotYearEnd();
        this.PollEvents();
        require("commands.nut");
        local command_file = Commands();
        if (!this.observation_emitted) {
            local suppress_initial_observation = command_file.rawin("suppress_initial_observation")
                && command_file.suppress_initial_observation;
            if (!suppress_initial_observation) this.EmitGeneralObservation();
            this.observation_emitted = true;
        }

        if (command_file.timestamp != this.last_command_timestamp) {
            this.last_command_timestamp = command_file.timestamp;
            local ack_result_timestamp = command_file.rawin("ack_result_timestamp") ? command_file.ack_result_timestamp : null;
            foreach (command in command_file.commands) {
                this.ProcessCommand(command, command_file.timestamp, ack_result_timestamp);
            }
            /* Offline evidence extraction uses a fresh command-file timestamp
             * but no gameplay command.  The dedicated flag is never exposed
             * through the player-visible MCP registry. */
            if (command_file.rawin("emit_timelapse_snapshot") && command_file.emit_timelapse_snapshot) {
                this.EmitTimelapseMapSnapshot();
            }
        }
        this.Sleep(5);
    }
}

function McpBridge::Save()
{
    this.EnsureEventState();
    return {
        last_command_timestamp = this.last_command_timestamp,
        observation_emitted = this.observation_emitted,
        observation_counter = this.observation_counter,
        vehicle_lost_events = this.vehicle_lost_events,
        vehicle_crashed_events = this.vehicle_crashed_events,
        lost_events = this.lost_events,
        vehicle_depot_events = this.vehicle_depot_events,
        depot_events = this.depot_events,
        vehicle_unprofitable_events = this.vehicle_unprofitable_events,
        unprofitable_events = this.unprofitable_events,
        last_seen_economy_year = this.last_seen_economy_year,
        pending_year_end_reports = this.pending_year_end_reports,
    };
}

function McpBridge::Load(version, data)
{
    this.EnsureEventState();
    if (data == null) return;
    if (data.rawin("last_command_timestamp")) this.last_command_timestamp = data.last_command_timestamp;
    /* Deliberately do NOT restore observation_emitted: a resumed controller
       expects the AI to re-emit its observation on the next tick, so leave it at
       the default false instead of suppressing the startup observation. */
    if (data.rawin("observation_counter")) this.observation_counter = data.observation_counter;
    if (data.rawin("vehicle_lost_events")) this.vehicle_lost_events = data.vehicle_lost_events;
    if (data.rawin("vehicle_crashed_events")) this.vehicle_crashed_events = data.vehicle_crashed_events;
    if (data.rawin("lost_events")) this.lost_events = data.lost_events;
    if (data.rawin("vehicle_depot_events")) this.vehicle_depot_events = data.vehicle_depot_events;
    if (data.rawin("depot_events")) this.depot_events = data.depot_events;
    if (data.rawin("vehicle_unprofitable_events")) this.vehicle_unprofitable_events = data.vehicle_unprofitable_events;
    if (data.rawin("unprofitable_events")) this.unprofitable_events = data.unprofitable_events;
    if (data.rawin("last_seen_economy_year") && data.rawin("pending_year_end_reports")) {
        this.last_seen_economy_year = data.last_seen_economy_year;
        this.pending_year_end_reports = data.pending_year_end_reports;
    }
}

function McpBridge::EnsureEventState()
{
    if (this.lost_events == null) this.lost_events = [];
    if (this.depot_events == null) this.depot_events = [];
    if (this.unprofitable_events == null) this.unprofitable_events = [];
}

function McpBridge::JsonEscape(s)
{
    local out = "";
    for (local i = 0; i < s.len(); i++) {
        local ch = s.slice(i, i + 1);
        if (ch == "\\") out += "\\\\";
        else if (ch == "\"") out += "\\\"";
        /* Control chars would split the single-line result record and break the
           line-based Python parser, so escape them instead of passing them raw. */
        else if (ch == "\n") out += "\\n";
        else if (ch == "\r") out += "\\r";
        else if (ch == "\t") out += "\\t";
        else out += ch;
    }
    return out;
}

function McpBridge::BoolJson(value)
{
    return value ? "true" : "false";
}

function McpBridge::DateJson(date)
{
    return "{\"raw\":" + date + ",\"year\":" + AIDate.GetYear(date) + ",\"month\":" + AIDate.GetMonth(date) + ",\"day\":" + AIDate.GetDayOfMonth(date) + "}";
}

/* The five quarterly company facts, fetched once here so the year-end
 * snapshot and CompanyQuarterReportsJson cannot drift field-for-field --
 * the same single-source rule CompanyFinanceCoreJson applies to the
 * balance/loan facts. */
function McpBridge::QuarterFinanceTable(offset)
{
    return {
        income = AICompany.GetQuarterlyIncome(AICompany.COMPANY_SELF, offset),
        expenses = AICompany.GetQuarterlyExpenses(AICompany.COMPANY_SELF, offset),
        cargo_delivered = AICompany.GetQuarterlyCargoDelivered(AICompany.COMPANY_SELF, offset),
        performance_rating = AICompany.GetQuarterlyPerformanceRating(AICompany.COMPANY_SELF, offset),
        company_value = AICompany.GetQuarterlyCompanyValue(AICompany.COMPANY_SELF, offset),
    };
}

function McpBridge::QuarterFinanceJson(facts)
{
    return ",\"income\":" + facts.income
        + ",\"expenses\":" + facts.expenses
        + ",\"cargo_delivered\":" + facts.cargo_delivered
        + ",\"performance_rating\":" + facts.performance_rating
        + ",\"company_value\":" + facts.company_value;
}

function McpBridge::MaybeSnapshotYearEnd()
{
    local current_date = AIDate.GetCurrentDate();
    local current_year = AIDate.GetYear(current_date);
    local current_month = AIDate.GetMonth(current_date);
    while (this.last_seen_economy_year < current_year) {
        local closed_year = this.last_seen_economy_year;
        local q4_offset = 4 * (current_year - closed_year - 1) + (current_month - 1) / 3 + 1;
        local quarters = [];
        for (local quarter_of_year = 1; quarter_of_year <= 4; quarter_of_year++) {
            local facts = this.QuarterFinanceTable(q4_offset + 4 - quarter_of_year);
            facts.quarter_of_year <- quarter_of_year;
            quarters.append(facts);
        }
        this.pending_year_end_reports.append({
            economy_year = closed_year,
            date = current_date,
            quarters = quarters,
            bank_balance = AICompany.GetBankBalance(AICompany.COMPANY_SELF),
            loan_amount = AICompany.GetLoanAmount(),
        });
        this.last_seen_economy_year++;
    }
}

function McpBridge::YearEndReportJson(report)
{
    local quarters = "[";
    for (local i = 0; i < report.quarters.len(); i++) {
        if (i != 0) quarters += ",";
        local quarter = report.quarters[i];
        quarters += "{\"quarter_of_year\":" + quarter.quarter_of_year
            + this.QuarterFinanceJson(quarter)
            + "}";
    }
    quarters += "]";
    return "{\"economy_year\":" + report.economy_year
        + ",\"date\":" + this.DateJson(report.date)
        + ",\"quarters\":" + quarters
        + ",\"bank_balance\":" + report.bank_balance
        + ",\"loan_amount\":" + report.loan_amount
        + ",\"not_in_report\":\"" + this.JsonEscape(YEAR_END_NOT_IN_REPORT) + "\"}";
}

function McpBridge::VehicleStateName(state)
{
    if (state == AIVehicle.VS_RUNNING) return "running";
    if (state == AIVehicle.VS_STOPPED) return "stopped";
    if (state == AIVehicle.VS_IN_DEPOT) return "in_depot";
    if (state == AIVehicle.VS_AT_STATION) return "at_station";
    if (state == AIVehicle.VS_BROKEN) return "broken";
    if (state == AIVehicle.VS_CRASHED) return "crashed";
    if (state == AIVehicle.VS_INVALID) return "invalid";
    return "unknown";
}

function McpBridge::Contains(items, value)
{
    foreach (item in items) {
        if (item == value) return true;
    }
    return false;
}

function McpBridge::AppendUnique(items, value)
{
    if (!this.Contains(items, value)) items.append(value);
}

function McpBridge::CompareIntLists(left, right)
{
    local min_len = left.len() < right.len() ? left.len() : right.len();
    for (local i = 0; i < min_len; i++) {
        if (left[i] < right[i]) return -1;
        if (left[i] > right[i]) return 1;
    }
    if (left.len() < right.len()) return -1;
    if (left.len() > right.len()) return 1;
    return 0;
}

function McpBridge::IntArrayJson(items)
{
    local s = "[";
    for (local i = 0; i < items.len(); i++) {
        if (i != 0) s += ",";
        s += items[i];
    }
    s += "]";
    return s;
}

/* A bounded model-visible record for a field whose Script API call raised
 * unexpectedly. Values keep their stable JSON types for collection/snapshot
 * callers, but no sentinel beside a matching fact_errors entry is a game
 * fact. Raw API symbols and exception text never cross the bridge. */
function McpBridge::FactErrorsJson(errors)
{
    if (errors.len() == 0) return "";
    local s = ",\"fact_errors\":[";
    for (local i = 0; i < errors.len(); i++) {
        if (i != 0) s += ",";
        s += "{\"code\":\"" + this.JsonEscape(errors[i][0])
            + "\",\"field\":\"" + this.JsonEscape(errors[i][1]) + "\"}";
    }
    return s + "]";
}

/* The station's tiles of one facility type, returned as { ok, tiles } so a
 * caller can both emit them and test tile membership without enumerating
 * twice, and can tell an empty station apart from a failed enumeration. */
function McpBridge::StationTypeTilesList(sid, station_type)
{
    local tiles = [];
    try {
        local tile_list = AITileList_StationType(sid, station_type);
        foreach (t, _ in tile_list) tiles.append(t);
    } catch (e) {
        return { ok = false, tiles = [] };
    }
    return { ok = true, tiles = tiles };
}

/* Join-capable build: the bridge command's optional 4th element selects the
 * station-join mode. Only the literal "adjacent" marker or a non-negative
 * integer station id are meaningful; anything else is rejected as a malformed
 * command instead of reaching the script API with a wrong-typed argument
 * (which would raise an uncaught parameter error and kill the bridge). */
function McpBridge::ResolveStationJoinArg(join_arg)
{
    if (join_arg == null) return { valid = true, join = "new", station_arg = AIStation.STATION_NEW };
    if (join_arg == "adjacent") return { valid = true, join = "adjacent", station_arg = AIStation.STATION_JOIN_ADJACENT };
    if (typeof join_arg == "integer" && join_arg >= 0) return { valid = true, join = "station_id", station_arg = join_arg };
    return { valid = false, join = "invalid", station_arg = -1 };
}

/* Shared ERR_UNKNOWN fact reporting for road-station builders. OpenTTD 15.1
 * leaves two rejection strings unmapped in the AI error table, so both surface
 * as the literal ERR_UNKNOWN: the drive-through corner/junction rejection
 * (road bits off the station axis -- only possible on a road tile) and the
 * adjacent-join adjoins-more-than-one-own-station rejection (possible on any
 * buildable tile, road or clear). Attach the factual note for whichever
 * sources apply; bay builders pass include_drive_through=false because the
 * corner/junction rejection is not part of their topology. Facts only; the
 * note states the game's error mapping and never placement advice. */
function McpBridge::RoadStopErrUnknownExtra(tile, join, include_drive_through, fact_errors)
{
    local extra = "";
    local note = "";
    if (include_drive_through && AIRoad.IsRoadTile(tile)) {
        extra += ",\"tile_road_bits\":" + this.RoadBitsJson(
            tile, AIMap.GetTileX(tile), AIMap.GetTileY(tile), "tile_road_bits", fact_errors);
        note += "OpenTTD 15.1 reports drive-through corner/junction rejections"
            + " (road bits off the station axis on the station tile) as ERR_UNKNOWN;"
            + " tile_road_bits holds the station tile's factual per-direction road bits.";
    }
    if (join == "adjacent") {
        if (note != "") note += " ";
        note += "OpenTTD 15.1 also reports adjacent-join rejections where the stop adjoins more than one existing own station as ERR_UNKNOWN.";
    }
    if (note != "") extra += ",\"error_note\":\"" + note + "\"";
    return extra;
}

function McpBridge::AttributedEventRecord(event_kind, event_index, vehicle_id)
{
    local date = AIDate.GetCurrentDate();
    local record = {
        event_kind = event_kind,
        event_index = event_index,
        vehicle_id = vehicle_id,
        date = date,
        valid = false,
    };

    if (!AIVehicle.IsValidVehicle(vehicle_id)) return record;

    local tile = AIVehicle.GetLocation(vehicle_id);
    local x = -1;
    local y = -1;
    if (AIMap.IsValidTile(tile)) { x = AIMap.GetTileX(tile); y = AIMap.GetTileY(tile); }
    local state = AIVehicle.GetState(vehicle_id);
    record.valid = true;
    record.tile <- tile;
    record.x <- x;
    record.y <- y;
    record.state_name <- this.VehicleStateName(state);

    local order_count = AIOrder.GetOrderCount(vehicle_id);
    if (order_count > 0) {
        local current_order_index = AIOrder.ResolveOrderPosition(vehicle_id, AIOrder.ORDER_CURRENT);
        local current_order_destination = AIOrder.GetOrderDestination(vehicle_id, AIOrder.ORDER_CURRENT);
        record.current_order_index <- current_order_index;
        record.current_order_destination <- current_order_destination;
        if (AIMap.IsValidTile(current_order_destination) && AIRoad.IsRoadStationTile(current_order_destination)) {
            record.current_order_station <- AIStation.GetStationID(current_order_destination);
        }
    }
    record.route_station_tiles <- this.RouteStationTiles(vehicle_id);
    return record;
}

function McpBridge::AttributedEventRecordJson(record)
{
    local s = "{\"event_kind\":\"" + this.JsonEscape(record.event_kind) + "\""
        + ",\"event_index\":" + record.event_index
        + ",\"vehicle_id\":" + record.vehicle_id
        + ",\"date\":" + this.DateJson(record.date)
        + ",\"valid\":" + this.BoolJson(record.valid);
    if (record.rawin("tile")) s += ",\"tile\":" + record.tile;
    if (record.rawin("x")) s += ",\"x\":" + record.x;
    if (record.rawin("y")) s += ",\"y\":" + record.y;
    if (record.rawin("state_name")) s += ",\"state_name\":\"" + this.JsonEscape(record.state_name) + "\"";
    if (record.rawin("current_order_index")) s += ",\"current_order_index\":" + record.current_order_index;
    if (record.rawin("current_order_destination")) s += ",\"current_order_destination\":" + record.current_order_destination;
    if (record.rawin("current_order_station")) s += ",\"current_order_station\":" + record.current_order_station;
    if (record.rawin("route_station_tiles")) s += ",\"route_station_tiles\":" + this.IntArrayJson(record.route_station_tiles);
    s += "}";
    return s;
}

function McpBridge::AttributedEventsJson(records, cap)
{
    local start = records.len() - cap;
    if (start < 0) start = 0;
    local s = "[";
    local first = true;
    for (local i = start; i < records.len(); i++) {
        if (!first) s += ",";
        first = false;
        s += this.AttributedEventRecordJson(records[i]);
    }
    s += "]";
    return s;
}

function McpBridge::RecentEventsSince(records, start_count)
{
    local out = [];
    foreach (record in records) {
        if (record.event_index <= start_count) continue;
        out.append(record);
    }
    return out;
}

function McpBridge::RecordLostEvent(vehicle_id)
{
    this.EnsureEventState();
    this.vehicle_lost_events++;
    local event_index = this.vehicle_lost_events;
    this.lost_events.append(this.AttributedEventRecord("lost", event_index, vehicle_id));
    while (this.lost_events.len() > LOST_EVENT_LOG_CAP) this.lost_events.remove(0);
}

function McpBridge::RecordDepotEvent(vehicle_id)
{
    this.EnsureEventState();
    this.vehicle_depot_events++;
    local event_index = this.vehicle_depot_events;
    this.depot_events.append(this.AttributedEventRecord("waiting_in_depot", event_index, vehicle_id));
    while (this.depot_events.len() > LOST_EVENT_LOG_CAP) this.depot_events.remove(0);
}

function McpBridge::RecordUnprofitableEvent(vehicle_id)
{
    this.EnsureEventState();
    this.vehicle_unprofitable_events++;
    local event_index = this.vehicle_unprofitable_events;
    this.unprofitable_events.append(this.AttributedEventRecord("unprofitable", event_index, vehicle_id));
    while (this.unprofitable_events.len() > LOST_EVENT_LOG_CAP) this.unprofitable_events.remove(0);
}

/* The vehicle_id-filtered tail of one attributed event log, newest-last.
 * Used by wait_and_observe_vehicle to hand a wait every recorded event for
 * its vehicle, including events drained before the wait began. */
function McpBridge::VehicleEventsFor(records, vehicle_id, cap)
{
    local mine = [];
    foreach (record in records) {
        if (record.vehicle_id == vehicle_id) mine.append(record);
    }
    local start = mine.len() - cap;
    if (start < 0) start = 0;
    return mine.slice(start);
}

function McpBridge::EmitBridgeOutput(line)
{
    print(line);
}

/* The game's event queue is one destructively consumed
 * stream (AIEventController.GetNextEvent removes the event), and it is
 * drained from the main loop, every observation, and every wait window. All
 * of those paths now share this single drain, which captures every
 * recognized per-vehicle event into a save-persisted attributed log at drain
 * time — so which code path happened to drain an event no longer decides
 * whether a later wait can report it. `watch` is null for anonymous drains;
 * a wait window passes its vehicle id and event flags so every matching event
 * is marked at drain time, independently of the capped history logs. */
function McpBridge::DrainGameEvents(watch)
{
    while (AIEventController.IsEventWaiting()) {
        local event = AIEventController.GetNextEvent();
        local event_type = event.GetEventType();
        if (event_type == AIEvent.ET_VEHICLE_LOST) {
            local lost_event = AIEventVehicleLost.Convert(event);
            local lost_vehicle = lost_event.GetVehicleID();
            this.RecordLostEvent(lost_vehicle);
            this.EmitBridgeOutput("MCP_EVENT|vehicle_lost|" + lost_vehicle + "|" + this.vehicle_lost_events);
            if (watch != null && lost_vehicle == watch.vehicle_id) watch.vehicle_lost = true;
        } else if (event_type == AIEvent.ET_VEHICLE_CRASHED) {
            local crash_event = AIEventVehicleCrashed.Convert(event);
            local crashed_vehicle = crash_event.GetVehicleID();
            this.vehicle_crashed_events++;
            this.EmitBridgeOutput("MCP_EVENT|vehicle_crashed|" + crashed_vehicle + "|" + this.vehicle_crashed_events);
            if (watch != null && crashed_vehicle == watch.vehicle_id) watch.vehicle_crashed = true;
        } else if (event_type == AIEvent.ET_VEHICLE_WAITING_IN_DEPOT) {
            local depot_event = AIEventVehicleWaitingInDepot.Convert(event);
            local depot_vehicle = depot_event.GetVehicleID();
            this.RecordDepotEvent(depot_vehicle);
            this.EmitBridgeOutput("MCP_EVENT|vehicle_waiting_in_depot|" + depot_vehicle + "|" + this.vehicle_depot_events);
            if (watch != null && depot_vehicle == watch.vehicle_id) watch.waiting_in_depot = true;
        } else if (event_type == AIEvent.ET_VEHICLE_UNPROFITABLE) {
            local unprofitable_event = AIEventVehicleUnprofitable.Convert(event);
            local unprofitable_vehicle = unprofitable_event.GetVehicleID();
            this.RecordUnprofitableEvent(unprofitable_vehicle);
            this.EmitBridgeOutput("MCP_EVENT|vehicle_unprofitable|" + unprofitable_vehicle + "|" + this.vehicle_unprofitable_events);
            if (watch != null && unprofitable_vehicle == watch.vehicle_id) watch.unprofitable = true;
        } else if (event_type == AIEvent.ET_STATION_FIRST_VEHICLE) {
            /* Fires once per station lifetime per vehicle category
             * (had_vehicle_of_type guard, roadveh_cmd.cpp) — a trace fact,
             * never a per-visit signal. Visits are counted by state
             * sampling in WaitAndObserveVehicle. */
            local station_event = AIEventStationFirstVehicle.Convert(event);
            this.EmitBridgeOutput("MCP_TRACE|event|station_first_vehicle|" + station_event.GetStationID() + "|" + station_event.GetVehicleID());
        }
    }
}

function McpBridge::PollEvents()
{
    this.DrainGameEvents(null);
}

function McpBridge::FindLargestTown()
{
    local towns = AITownList();
    local best = -1;
    local best_pop = -1;
    foreach (town_id, value in towns) {
        local pop = AITown.GetPopulation(town_id);
        if (pop > best_pop) {
            best = town_id;
            best_pop = pop;
        }
    }
    return best;
}

function McpBridge::FindPassengerEngines(max_count)
{
    local out = [];
    local engines = AIEngineList(AIVehicle.VT_ROAD);
    foreach (engine_id, value in engines) {
        if (out.len() >= max_count) break;
        if (!AIEngine.IsBuildable(engine_id)) continue;
        local cargo = AIEngine.GetCargoType(engine_id);
        if (AIRoad.GetRoadVehicleTypeForCargo(cargo) != AIRoad.ROADVEHTYPE_BUS) continue;
        if (AIEngine.GetCapacity(engine_id) <= 0) continue;
        out.append([engine_id, cargo, AIEngine.GetCapacity(engine_id), AIEngine.GetPrice(engine_id), AIEngine.GetMaxSpeed(engine_id), AIEngine.GetName(engine_id)]);
    }
    return out;
}

function McpBridge::IsRoadLike(tile)
{
    if (!AIMap.IsValidTile(tile)) return false;
    if (AIRoad.IsRoadTile(tile)) return true;
    if (AIRoad.IsRoadStationTile(tile)) return true;
    if (AIRoad.IsRoadDepotTile(tile)) return true;
    return false;
}

function McpBridge::DirectConnected(tile_from, tile_to)
{
    if (!AIMap.IsValidTile(tile_from) || !AIMap.IsValidTile(tile_to)) return false;
    local dx = AIMap.GetTileX(tile_from) - AIMap.GetTileX(tile_to);
    if (dx < 0) dx = -dx;
    local dy = AIMap.GetTileY(tile_from) - AIMap.GetTileY(tile_to);
    if (dy < 0) dy = -dy;
    if (dx + dy != 1) return false;
    if (!this.IsRoadLike(tile_from) || !this.IsRoadLike(tile_to)) return false;
    return AIRoad.AreRoadTilesConnected(tile_from, tile_to);
}

/* Directed path lengths from one origin over the connectivity checker's road graph.
 * One step is one edge-adjacent tile-centre transition accepted by
 * AIRoad.AreRoadTilesConnected(from, to). Breadth-first search makes each
 * first visit shortest because every edge has the same one-step cost. */
function McpBridge::RoadDistancesFrom(tile_from)
{
    local distances = {};
    if (!this.IsRoadLike(tile_from)) return distances;
    local queue = [tile_from];
    distances["" + tile_from] <- 0;
    local max_x = AIMap.GetMapSizeX();
    local max_y = AIMap.GetMapSizeY();
    for (local i = 0; i < queue.len(); i++) {
        local tile = queue[i];
        local distance = distances["" + tile];
        local x = AIMap.GetTileX(tile);
        local y = AIMap.GetTileY(tile);
        local neighbours = [];
        if (x > 0) neighbours.append(AIMap.GetTileIndex(x - 1, y));
        if (x + 1 < max_x) neighbours.append(AIMap.GetTileIndex(x + 1, y));
        if (y > 0) neighbours.append(AIMap.GetTileIndex(x, y - 1));
        if (y + 1 < max_y) neighbours.append(AIMap.GetTileIndex(x, y + 1));
        foreach (next_tile in neighbours) {
            local key = "" + next_tile;
            if (distances.rawin(key)) continue;
            if (!this.DirectConnected(tile, next_tile)) continue;
            distances[key] <- distance + 1;
            queue.append(next_tile);
        }
    }
    return distances;
}

/* -1 is the bridge-internal sentinel for an unreachable direction; the
 * model-visible result emits JSON null instead. */
function McpBridge::RoadDistanceFrom(distances, tile_to)
{
    if (!this.IsRoadLike(tile_to)) return -1;
    local key = "" + tile_to;
    if (!distances.rawin(key)) return -1;
    return distances[key];
}

function McpBridge::RoadPathLength(tile_from, tile_to)
{
    if (!this.IsRoadLike(tile_from) || !this.IsRoadLike(tile_to)) return -1;
    local queue = [tile_from];
    local distances = {};
    distances["" + tile_from] <- 0;
    local max_x = AIMap.GetMapSizeX();
    local max_y = AIMap.GetMapSizeY();
    for (local i = 0; i < queue.len(); i++) {
        local tile = queue[i];
        local distance = distances["" + tile];
        if (tile == tile_to) return distance;
        local x = AIMap.GetTileX(tile);
        local y = AIMap.GetTileY(tile);
        local neighbours = [];
        if (x > 0) neighbours.append(AIMap.GetTileIndex(x - 1, y));
        if (x + 1 < max_x) neighbours.append(AIMap.GetTileIndex(x + 1, y));
        if (y > 0) neighbours.append(AIMap.GetTileIndex(x, y - 1));
        if (y + 1 < max_y) neighbours.append(AIMap.GetTileIndex(x, y + 1));
        foreach (next_tile in neighbours) {
            local key = "" + next_tile;
            if (distances.rawin(key)) continue;
            if (!this.DirectConnected(tile, next_tile)) continue;
            distances[key] <- distance + 1;
            queue.append(next_tile);
        }
    }
    return -1;
}

function McpBridge::Reachable(tile_from, tile_to)
{
    return this.RoadPathLength(tile_from, tile_to) >= 0;
}

/* Size of the connected road component containing `tile` (BFS over
 * DirectConnected edges), counted up to max_visits — no town-influence gate.
 * check_route_connectivity's component_size used to be town-influence-gated, so a
 * seed stop outside the passed town's influence returned 0 while every
 * reachability fact said all_on_one_component true. Returns 0 only when the
 * tile carries no road at all — and then StationTileConnected is false too,
 * so 0 can no longer coexist with all_on_one_component. */
function McpBridge::RoadComponentSize(tile, max_visits)
{
    if (!this.IsRoadLike(tile)) return 0;
    local queue = [tile];
    local visited = {};
    visited["" + tile] <- true;
    local count = 0;
    local max_x = AIMap.GetMapSizeX();
    local max_y = AIMap.GetMapSizeY();
    for (local i = 0; i < queue.len() && count < max_visits; i++) {
        local current = queue[i];
        count++;
        local x = AIMap.GetTileX(current);
        local y = AIMap.GetTileY(current);
        local neighbours = [];
        if (x > 0) neighbours.append(AIMap.GetTileIndex(x - 1, y));
        if (x + 1 < max_x) neighbours.append(AIMap.GetTileIndex(x + 1, y));
        if (y > 0) neighbours.append(AIMap.GetTileIndex(x, y - 1));
        if (y + 1 < max_y) neighbours.append(AIMap.GetTileIndex(x, y + 1));
        foreach (next_tile in neighbours) {
            local key = "" + next_tile;
            if (visited.rawin(key)) continue;
            if (!this.DirectConnected(current, next_tile)) continue;
            visited[key] <- true;
            queue.append(next_tile);
        }
    }
    return count;
}

function McpBridge::TownRoadComponentSize(tile, town_id, max_visits)
{
    if (!this.IsRoadLike(tile) || !AITown.IsWithinTownInfluence(town_id, tile)) return 0;
    local queue = [tile];
    local visited = {};
    visited["" + tile] <- true;
    local count = 0;
    local max_x = AIMap.GetMapSizeX();
    local max_y = AIMap.GetMapSizeY();
    for (local i = 0; i < queue.len() && count < max_visits; i++) {
        local current = queue[i];
        count++;
        local x = AIMap.GetTileX(current);
        local y = AIMap.GetTileY(current);
        local neighbours = [];
        if (x > 0) neighbours.append(AIMap.GetTileIndex(x - 1, y));
        if (x + 1 < max_x) neighbours.append(AIMap.GetTileIndex(x + 1, y));
        if (y > 0) neighbours.append(AIMap.GetTileIndex(x, y - 1));
        if (y + 1 < max_y) neighbours.append(AIMap.GetTileIndex(x, y + 1));
        foreach (next_tile in neighbours) {
            local key = "" + next_tile;
            if (visited.rawin(key)) continue;
            if (!AITown.IsWithinTownInfluence(town_id, next_tile)) continue;
            if (!this.DirectConnected(current, next_tile)) continue;
            visited[key] <- true;
            queue.append(next_tile);
        }
    }
    return count;
}

function McpBridge::StationTileConnected(station_tile)
{
    if (!AIRoad.IsRoadStationTile(station_tile)) return false;
    local front = AIRoad.GetRoadStationFrontTile(station_tile);
    local front_ok = AIMap.IsValidTile(front) && this.IsRoadLike(front)
        && this.DirectConnected(front, station_tile)
        && this.DirectConnected(station_tile, front);
    if (!AIRoad.IsDriveThroughRoadStationTile(station_tile)) return front_ok;
    if (front_ok) return true;
    local back = AIRoad.GetDriveThroughBackTile(station_tile);
    return AIMap.IsValidTile(back) && this.IsRoadLike(back)
        && this.DirectConnected(back, station_tile)
        && this.DirectConnected(station_tile, back);
}

function McpBridge::QueryRouteConnectivity(town_id, depot_tile, depot_front_tile, stop_tiles, timestamp)
{
    /* Cache one full breadth-first trace per unique caller-supplied origin.
     * This keeps the pairwise result shape without retracing the road graph
     * once for every origin/destination pair. */
    local distance_maps_by_origin = {};
    local depot_origin_key = "" + depot_tile;
    local depot_distances = this.RoadDistancesFrom(depot_tile);
    distance_maps_by_origin[depot_origin_key] <- depot_distances;
    local stop_distance_maps = [];
    foreach (stop in stop_tiles) {
        local origin_key = "" + stop;
        local distances = null;
        if (distance_maps_by_origin.rawin(origin_key)) {
            distances = distance_maps_by_origin[origin_key];
        } else {
            distances = this.RoadDistancesFrom(stop);
            distance_maps_by_origin[origin_key] <- distances;
        }
        stop_distance_maps.append(distances);
    }
    local depot_front_direct_connected = this.DirectConnected(depot_tile, depot_front_tile) && this.DirectConnected(depot_front_tile, depot_tile);
    local stop_access_connected_json = "[";
    local each_stop_reachable_json = "[";
    local depot_reachable_json = "[";
    local stop_tiles_json = "[";
    local stop_to_stop_distances_json = "[";
    local distance_row_count = 0;
    local stops_mutually_reachable = true;
    local all_on_one_component = depot_front_direct_connected;
    for (local i = 0; i < stop_tiles.len(); i++) {
        local stop = stop_tiles[i];
        local stop_distances = stop_distance_maps[i];
        local stop_connected = this.StationTileConnected(stop);
        local depot_to_stop = this.RoadDistanceFrom(depot_distances, stop) >= 0;
        local stop_to_depot = this.RoadDistanceFrom(stop_distances, depot_tile) >= 0;
        if (i != 0) {
            stop_access_connected_json += ",";
            each_stop_reachable_json += ",";
            depot_reachable_json += ",";
            stop_tiles_json += ",";
        }
        stop_tiles_json += stop;
        stop_access_connected_json += "{\"tile\":" + stop + ",\"connected\":" + this.BoolJson(stop_connected) + "}";
        each_stop_reachable_json += "{\"tile\":" + stop + ",\"reachable\":" + this.BoolJson(depot_to_stop) + "}";
        depot_reachable_json += "{\"tile\":" + stop + ",\"reachable\":" + this.BoolJson(stop_to_depot) + "}";
        if (!stop_connected || !depot_to_stop || !stop_to_depot) all_on_one_component = false;
        for (local j = i + 1; j < stop_tiles.len(); j++) {
            local other_stop = stop_tiles[j];
            local forward_steps = this.RoadDistanceFrom(stop_distances, other_stop);
            local reverse_steps = this.RoadDistanceFrom(stop_distance_maps[j], stop);
            if (distance_row_count != 0) stop_to_stop_distances_json += ",";
            stop_to_stop_distances_json += "{\"from_tile\":" + stop
                + ",\"to_tile\":" + other_stop
                + ",\"reachable\":" + this.BoolJson(forward_steps >= 0)
                + ",\"road_steps\":" + (forward_steps >= 0 ? forward_steps : "null")
                + "},{\"from_tile\":" + other_stop
                + ",\"to_tile\":" + stop
                + ",\"reachable\":" + this.BoolJson(reverse_steps >= 0)
                + ",\"road_steps\":" + (reverse_steps >= 0 ? reverse_steps : "null")
                + "}";
            distance_row_count += 2;
            if (forward_steps < 0 || reverse_steps < 0) stops_mutually_reachable = false;
        }
    }
    stop_access_connected_json += "]";
    each_stop_reachable_json += "]";
    depot_reachable_json += "]";
    stop_tiles_json += "]";
    stop_to_stop_distances_json += "]";
    if (!stops_mutually_reachable) all_on_one_component = false;
    /* component_size used to use the town-influence-gated BFS, so a seed stop
     * outside town_id's influence reported 0 alongside
     * all_on_one_component:true — internally contradictory facts.
     * component_size is now the real road
     * component of the seed stop (no town gate); the town-gated count stays
     * available under its own explicit name, with the seed tile and its
     * town-influence membership stated so a 0 explains itself. */
    local component_seed = stop_tiles.len() > 0 ? stop_tiles[0] : depot_front_tile;
    local component_size = this.RoadComponentSize(component_seed, 512);
    local seed_within_town_influence = AITown.IsWithinTownInfluence(town_id, component_seed);
    local town_road_component_size = this.TownRoadComponentSize(component_seed, town_id, 512);
    this.EmitPrimResult(timestamp, "check_route_connectivity", true, "", 0,
        ",\"town_id\":" + town_id
        + ",\"depot_tile\":" + depot_tile
        + ",\"depot_front_tile\":" + depot_front_tile
        + ",\"stop_tiles\":" + stop_tiles_json
        + ",\"stop_access_connected\":" + stop_access_connected_json
        + ",\"depot_front_direct_connected\":" + this.BoolJson(depot_front_direct_connected)
        + ",\"each_stop_reachable_from_depot\":" + each_stop_reachable_json
        + ",\"depot_reachable_from_each_stop\":" + depot_reachable_json
        + ",\"stop_to_stop_distances\":" + stop_to_stop_distances_json
        + ",\"stops_mutually_reachable\":" + this.BoolJson(stops_mutually_reachable)
        + ",\"all_on_one_component\":" + this.BoolJson(all_on_one_component)
        + ",\"component_seed_tile\":" + component_seed
        + ",\"component_size\":" + component_size
        + ",\"seed_within_town_influence\":" + this.BoolJson(seed_within_town_influence)
        + ",\"town_road_component_size\":" + town_road_component_size);
}

function McpBridge::WaitAndObserveVehicle(vehicle_id, advance_days, timestamp)
{
    if (!AIVehicle.IsValidVehicle(vehicle_id)) {
        this.EmitPrimResult(timestamp, "wait_and_observe_vehicle", false, "invalid_vehicle_id", 0, ",\"vehicle_id\":" + vehicle_id);
        return;
    }
    local days = advance_days;
    if (days < 1) days = 1;
    if (days > 90) days = 90;
    this.EnsureEventState();
    local start_date = AIDate.GetCurrentDate();
    local target_date = start_date + days;
    local initial_tile = AIVehicle.GetLocation(vehicle_id);
    local initial_order_index = AIOrder.ResolveOrderPosition(vehicle_id, AIOrder.ORDER_CURRENT);
    local current_order_index = initial_order_index;
    local visited_tiles = {};
    visited_tiles["" + initial_tile] <- true;
    local distinct_tiles_seen = 1;
    local moved = false;
    local order_index_progressed = false;
    local event_state = {
        vehicle_id = vehicle_id,
        vehicle_lost = false,
        vehicle_crashed = false,
        waiting_in_depot = false,
        unprofitable = false,
    };
    /* Per-visit station counting (live-probed): a visit is
     * a sampled transition into AIVehicle.VS_AT_STATION — the game's own
     * "stopped at a station and currently loading or unloading" state
     * (script_vehicle.cpp maps it from current_order.IsType(OT_LOADING)).
     * The loop samples every tick (Sleep(1)); the shortest loading stop the
     * probe observed lasted 5 ticks, so this cadence caught every observed
     * stop and reduces the risk of missing shorter stops. A vehicle already
     * loading when the wait starts is one ongoing
     * visit, not a new one: at_station_at_start reports that case. */
    local at_station_at_start = AIVehicle.GetState(vehicle_id) == AIVehicle.VS_AT_STATION;
    local prev_at_station = at_station_at_start;
    local station_visits = 0;
    local stations_visited = [];
    local loops = 0;
    while (AIDate.GetCurrentDate() < target_date && loops < days * 250) {
        this.MaybeSnapshotYearEnd();
        this.DrainGameEvents(event_state);
        if (!AIVehicle.IsValidVehicle(vehicle_id)) break;
        local tile = AIVehicle.GetLocation(vehicle_id);
        local key = "" + tile;
        if (!visited_tiles.rawin(key)) {
            visited_tiles[key] <- true;
            distinct_tiles_seen++;
        }
        if (tile != initial_tile) moved = true;
        current_order_index = AIOrder.ResolveOrderPosition(vehicle_id, AIOrder.ORDER_CURRENT);
        if (current_order_index != initial_order_index) order_index_progressed = true;
        local at_station = AIVehicle.GetState(vehicle_id) == AIVehicle.VS_AT_STATION;
        if (at_station && !prev_at_station) {
            station_visits++;
            local visit_station = AIStation.GetStationID(AIVehicle.GetLocation(vehicle_id));
            if (AIStation.IsValidStation(visit_station)) this.AppendUnique(stations_visited, visit_station);
        }
        prev_at_station = at_station;
        this.Sleep(1);
        loops++;
    }
    this.DrainGameEvents(event_state);
    local end_date = AIDate.GetCurrentDate();
    local safety_cap_exhausted = end_date < target_date && loops >= days * 250;
    local final_tile = AIVehicle.IsValidVehicle(vehicle_id) ? AIVehicle.GetLocation(vehicle_id) : -1;
    local in_depot = AIVehicle.IsValidVehicle(vehicle_id) ? AIVehicle.IsInDepot(vehicle_id) : false;
    local stopped_in_depot = AIVehicle.IsValidVehicle(vehicle_id) ? AIVehicle.IsStoppedInDepot(vehicle_id) : false;
    local order_count = AIVehicle.IsValidVehicle(vehicle_id) ? AIOrder.GetOrderCount(vehicle_id) : 0;
    local crashed = event_state.vehicle_crashed;
    local lost = event_state.vehicle_lost;
    local moving_or_progressing = moved || distinct_tiles_seen > 1 || order_index_progressed || station_visits > 0;
    // Honest flag: an earlier expression omitted
    // !moving_or_progressing, so every out-of-depot vehicle with orders was
    // reported "running_without_progress":true even while moving and
    // progressing orders. The flag must mean exactly what it says.
    local running_without_progress = !in_depot && !stopped_in_depot && order_count > 0 && !moving_or_progressing;
    /* Verdict: "operating" requires a sampled transition into loading at a
     * station in the window. Movement or order progress without that arrival
     * is reported as exactly that, never as operation. */
    local verdict = "stuck";
    if (crashed) verdict = "crashed";
    else if (lost) verdict = "lost";
    else if (station_visits > 0) verdict = "operating";
    else if (moving_or_progressing) verdict = "moving_without_station_visit";
    else if (running_without_progress) verdict = "running_pending_progress";
    /* Dated event records for this vehicle from the shared drain's logs.
     * They include events drained before this wait began (the queue is
     * consumed-once, so a pre-wait drain would otherwise swallow them); the
     * *_in_window flags say whether any landed inside this wait. */
    local depot_records = this.VehicleEventsFor(this.depot_events, vehicle_id, WARNINGS_EVENT_LOG_CAP);
    local unprofitable_records = this.VehicleEventsFor(this.unprofitable_events, vehicle_id, WARNINGS_EVENT_LOG_CAP);
    this.EmitPrimResult(timestamp, "wait_and_observe_vehicle", !safety_cap_exhausted,
        safety_cap_exhausted ? "advance_incomplete" : "", 0,
        ",\"vehicle_id\":" + vehicle_id
        + (safety_cap_exhausted ? ",\"status\":\"advance_incomplete\"" : "")
        + ",\"requested_advance_days\":" + advance_days
        + ",\"advanced_days\":" + (end_date - start_date)
        + ",\"start_date\":" + start_date
        + ",\"end_date\":" + end_date
        + ",\"bounded_window_completed\":" + this.BoolJson(end_date >= target_date)
        + ",\"safety_cap_exhausted\":" + this.BoolJson(safety_cap_exhausted)
        + ",\"game_mutated\":" + this.BoolJson(loops > 0)
        + ",\"time_may_have_advanced\":false"
        + ",\"initial_tile\":" + initial_tile
        + ",\"final_tile\":" + final_tile
        + ",\"moved\":" + this.BoolJson(moved)
        + ",\"distinct_tiles_seen\":" + distinct_tiles_seen
        + ",\"station_visits\":" + station_visits
        + ",\"stations_visited\":" + this.IntArrayJson(stations_visited)
        + ",\"at_station_at_start\":" + this.BoolJson(at_station_at_start)
        + ",\"initial_order_index\":" + initial_order_index
        + ",\"current_order_index\":" + current_order_index
        + ",\"order_count\":" + order_count
        + ",\"order_index_progressed\":" + this.BoolJson(order_index_progressed)
        + ",\"moving_or_progressing\":" + this.BoolJson(moving_or_progressing)
        + ",\"running_without_progress\":" + this.BoolJson(running_without_progress)
        + ",\"in_depot\":" + this.BoolJson(in_depot)
        + ",\"stopped_in_depot\":" + this.BoolJson(stopped_in_depot)
        + ",\"vehicle_lost\":" + this.BoolJson(lost)
        + ",\"vehicle_crashed\":" + this.BoolJson(crashed)
        + ",\"waiting_in_depot_events\":" + this.AttributedEventsJson(depot_records, depot_records.len())
        + ",\"waiting_in_depot_event_in_window\":" + this.BoolJson(event_state.waiting_in_depot)
        + ",\"unprofitable_events\":" + this.AttributedEventsJson(unprofitable_records, unprofitable_records.len())
        + ",\"unprofitable_event_in_window\":" + this.BoolJson(event_state.unprofitable)
        + ",\"verdict\":\"" + verdict + "\"");
}

/* Bounded station-traffic observation.
 * Every five script ticks this scans AIVehicleList, which contains this
 * company's primary vehicles, and keeps only current road vehicles. The
 * station-wide near count tests a vehicle once against the union of its bus
 * and truck stop tiles. Per-date rows retain independent maxima plus the last
 * waiting-cargo sample, so the output size is bounded by calendar days rather
 * than script ticks. */
function McpBridge::WaitAndObserveStation(station_id, advance_days, timestamp)
{
    local action = "wait_and_observe_station";
    local days = advance_days;
    if (days < 1) days = 1;
    if (days > 90) days = 90;
    local start_date = -1;
    local last_known_date = -1;
    local game_mutated = false;
    local time_may_have_advanced = false;
    local sensor_code = "station_validation_unavailable";
    local sensor_field = "station_id";

    try {
        if (!AIStation.IsValidStation(station_id)) {
            this.EmitPrimResult(timestamp, action, false, "invalid_station_id", 0,
                ",\"station_id\":" + station_id
                + ",\"requested_advance_days\":" + advance_days
                + ",\"game_mutated\":false,\"time_may_have_advanced\":false");
            return;
        }

        sensor_code = "road_stop_tiles_unavailable";
        sensor_field = "road_stop_tiles";
        local bus_result = this.StationTypeTilesList(station_id, AIStation.STATION_BUS_STOP);
        local truck_result = this.StationTypeTilesList(station_id, AIStation.STATION_TRUCK_STOP);
        if (!bus_result.ok || !truck_result.ok) {
            this.EmitSensorApiError(timestamp, action, sensor_code, sensor_field,
                ",\"station_id\":" + station_id
                + ",\"requested_advance_days\":" + advance_days
                + ",\"game_mutated\":false,\"time_may_have_advanced\":false");
            return;
        }
        local road_stop_tiles = [];
        foreach (bus_tile in bus_result.tiles) this.AppendUnique(road_stop_tiles, bus_tile);
        foreach (truck_tile in truck_result.tiles) this.AppendUnique(road_stop_tiles, truck_tile);
        if (road_stop_tiles.len() == 0) {
            this.EmitPrimResult(timestamp, action, false, "station_has_no_road_stop_tiles", 0,
                ",\"station_id\":" + station_id
                + ",\"bus_stop_tiles\":" + this.IntArrayJson(bus_result.tiles)
                + ",\"truck_stop_tiles\":" + this.IntArrayJson(truck_result.tiles)
                + ",\"road_stop_tiles\":[]"
                + ",\"requested_advance_days\":" + advance_days
                + ",\"game_mutated\":false,\"time_may_have_advanced\":false");
            return;
        }

        /* Station-wide near counts keep scanning the whole union, so the cap
         * narrows only the per-stop rows the response repeats per date. */
        local observed_stop_tiles = [];
        foreach (stop_tile in road_stop_tiles) {
            if (observed_stop_tiles.len() >= STATION_STOP_ROW_CAP) break;
            observed_stop_tiles.append(stop_tile);
        }
        local observed_stop_tiles_truncated = observed_stop_tiles.len() < road_stop_tiles.len();

        sensor_code = "station_identity_unavailable";
        sensor_field = "station_identity";
        local station_name = "" + AIStation.GetName(station_id);
        local station_tile = AIStation.GetLocation(station_id);
        start_date = AIDate.GetCurrentDate();
        last_known_date = start_date;
        local target_date = start_date + days;
        local daily = [];
        local loops = 0;

        while (AIDate.GetCurrentDate() < target_date && loops < days * 250) {
            local sample_date = AIDate.GetCurrentDate();
            last_known_date = sample_date;
            local row = null;
            foreach (candidate in daily) {
                if (candidate.date == sample_date) { row = candidate; break; }
            }
            if (row == null && daily.len() < 90) {
                local stop_rows = [];
                foreach (stop_tile in observed_stop_tiles) {
                    stop_rows.append({
                        tile = stop_tile,
                        sampled_max_vehicles_at_stop = 0,
                        sampled_max_loading_or_unloading_vehicles_at_stop = 0,
                    });
                }
                row = {
                    date = sample_date,
                    sample_count = 0,
                    sampled_max_vehicles_near = 0,
                    sampled_max_zero_speed_vehicles_near = 0,
                    stops = stop_rows,
                    last_sample_waiting_cargo = [],
                    last_sample_waiting_cargo_total = 0,
                    last_sample_waiting_cargo_types = 0,
                    last_sample_waiting_cargo_truncated = false,
                };
                daily.append(row);
            }

            sensor_code = "station_vehicle_sample_unavailable";
            sensor_field = "daily";
            local vehicles_near = 0;
            local zero_speed_vehicles_near = 0;
            local vehicles_at_stop = [];
            local loading_or_unloading_at_stop = [];
            foreach (unused_stop_tile in observed_stop_tiles) {
                vehicles_at_stop.append(0);
                loading_or_unloading_at_stop.append(0);
            }
            local vehicles = AIVehicleList();
            foreach (vehicle_id, _ in vehicles) {
                if (AIVehicle.GetVehicleType(vehicle_id) != AIVehicle.VT_ROAD) continue;
                local vehicle_tile = AIVehicle.GetLocation(vehicle_id);
                local near = false;
                foreach (stop_tile in road_stop_tiles) {
                    local dx = AIMap.GetTileX(vehicle_tile) - AIMap.GetTileX(stop_tile);
                    if (dx < 0) dx = -dx;
                    local dy = AIMap.GetTileY(vehicle_tile) - AIMap.GetTileY(stop_tile);
                    if (dy < 0) dy = -dy;
                    if (dx + dy <= 4) { near = true; break; }
                }
                if (near) {
                    vehicles_near++;
                    if (AIVehicle.GetCurrentSpeed(vehicle_id) == 0) zero_speed_vehicles_near++;
                }
                for (local stop_index = 0; stop_index < observed_stop_tiles.len(); stop_index++) {
                    if (vehicle_tile != observed_stop_tiles[stop_index]) continue;
                    vehicles_at_stop[stop_index]++;
                    if (AIVehicle.GetState(vehicle_id) == AIVehicle.VS_AT_STATION) {
                        loading_or_unloading_at_stop[stop_index]++;
                    }
                }
            }

            sensor_code = "station_cargo_sample_unavailable";
            sensor_field = "last_sample_waiting_cargo";
            local waiting_cargo = [];
            local waiting_cargo_total = 0;
            local waiting_cargo_types = 0;
            local cargoes = AICargoList();
            foreach (cargo_id, _ in cargoes) {
                local waiting = AIStation.GetCargoWaiting(station_id, cargo_id);
                if (waiting <= 0) continue;
                waiting_cargo_total += waiting;
                waiting_cargo_types++;
                /* The total and the type count above cover every positive
                 * cargo; only the listed entries are capped. */
                if (waiting_cargo.len() >= STATION_CARGO_ROW_CAP) continue;
                waiting_cargo.append({
                    cargo_id = cargo_id,
                    cargo_label = "" + AICargo.GetCargoLabel(cargo_id),
                    waiting = waiting,
                });
            }

            sensor_code = "station_row_aggregation_unavailable";
            sensor_field = "daily";
            if (row != null) {
                row.sample_count++;
                if (vehicles_near > row.sampled_max_vehicles_near) {
                    row.sampled_max_vehicles_near = vehicles_near;
                }
                if (zero_speed_vehicles_near > row.sampled_max_zero_speed_vehicles_near) {
                    row.sampled_max_zero_speed_vehicles_near = zero_speed_vehicles_near;
                }
                for (local stop_index = 0; stop_index < row.stops.len(); stop_index++) {
                    if (vehicles_at_stop[stop_index] > row.stops[stop_index].sampled_max_vehicles_at_stop) {
                        row.stops[stop_index].sampled_max_vehicles_at_stop = vehicles_at_stop[stop_index];
                    }
                    if (loading_or_unloading_at_stop[stop_index] > row.stops[stop_index].sampled_max_loading_or_unloading_vehicles_at_stop) {
                        row.stops[stop_index].sampled_max_loading_or_unloading_vehicles_at_stop = loading_or_unloading_at_stop[stop_index];
                    }
                }
                row.last_sample_waiting_cargo = waiting_cargo;
                row.last_sample_waiting_cargo_total = waiting_cargo_total;
                row.last_sample_waiting_cargo_types = waiting_cargo_types;
                row.last_sample_waiting_cargo_truncated = waiting_cargo.len() < waiting_cargo_types;
            }

            sensor_code = "station_observation_time_advance_unavailable";
            sensor_field = "advanced_days";
            this.MaybeSnapshotYearEnd();
            this.PollEvents();
            /* Sleep can throw after the game has already run script ticks, so
             * mutation truth changes before entering the API call. */
            game_mutated = true;
            time_may_have_advanced = true;
            this.Sleep(5);
            loops++;
        }

        sensor_code = "station_observation_end_date_unavailable";
        sensor_field = "end_date";
        local end_date = AIDate.GetCurrentDate();
        last_known_date = end_date;
        local safety_cap_exhausted = end_date < target_date && loops >= days * 250;
        local daily_json = "[";
        for (local row_index = 0; row_index < daily.len(); row_index++) {
            if (row_index != 0) daily_json += ",";
            local row = daily[row_index];
            daily_json += "{\"date\":" + row.date
                + ",\"sample_count\":" + row.sample_count
                + ",\"sampled_max_vehicles_near\":" + row.sampled_max_vehicles_near
                + ",\"sampled_max_zero_speed_vehicles_near\":" + row.sampled_max_zero_speed_vehicles_near
                + ",\"stops\":[";
            for (local stop_index = 0; stop_index < row.stops.len(); stop_index++) {
                if (stop_index != 0) daily_json += ",";
                local stop_row = row.stops[stop_index];
                daily_json += "{\"tile\":" + stop_row.tile
                    + ",\"sampled_max_vehicles_at_stop\":" + stop_row.sampled_max_vehicles_at_stop
                    + ",\"sampled_max_loading_or_unloading_vehicles_at_stop\":" + stop_row.sampled_max_loading_or_unloading_vehicles_at_stop
                    + "}";
            }
            daily_json += "],\"last_sample_waiting_cargo\":[";
            for (local cargo_index = 0; cargo_index < row.last_sample_waiting_cargo.len(); cargo_index++) {
                if (cargo_index != 0) daily_json += ",";
                local cargo_row = row.last_sample_waiting_cargo[cargo_index];
                daily_json += "{\"cargo_id\":" + cargo_row.cargo_id
                    + ",\"cargo_label\":\"" + this.JsonEscape(cargo_row.cargo_label) + "\""
                    + ",\"waiting\":" + cargo_row.waiting + "}";
            }
            daily_json += "],\"last_sample_waiting_cargo_total\":" + row.last_sample_waiting_cargo_total
                + ",\"last_sample_waiting_cargo_types\":" + row.last_sample_waiting_cargo_types
                + ",\"last_sample_waiting_cargo_truncated\":" + this.BoolJson(row.last_sample_waiting_cargo_truncated)
                + "}";
        }
        daily_json += "]";

        this.EmitPrimResult(timestamp, action, !safety_cap_exhausted,
            safety_cap_exhausted ? "advance_incomplete" : "", 0,
            ",\"station_id\":" + station_id
            + (safety_cap_exhausted ? ",\"status\":\"advance_incomplete\"" : "")
            + ",\"station_name\":\"" + this.JsonEscape(station_name) + "\""
            + ",\"station_tile\":" + station_tile
            + ",\"bus_stop_tiles\":" + this.IntArrayJson(bus_result.tiles)
            + ",\"truck_stop_tiles\":" + this.IntArrayJson(truck_result.tiles)
            + ",\"road_stop_tiles\":" + this.IntArrayJson(road_stop_tiles)
            + ",\"observed_stop_tiles\":" + this.IntArrayJson(observed_stop_tiles)
            + ",\"observed_stop_tiles_truncated\":" + this.BoolJson(observed_stop_tiles_truncated)
            + ",\"max_stop_rows_per_date\":" + STATION_STOP_ROW_CAP
            + ",\"max_waiting_cargo_rows_per_date\":" + STATION_CARGO_ROW_CAP
            + ",\"response_bound_meaning\":\"Each date row repeats one object per observed stop tile and per listed waiting cargo, so both are capped; station-wide near counts still scan every road stop tile, and each row's waiting-cargo total and type count still cover every positive cargo.\""
            + ",\"vehicle_scope\":\"current_company_primary_road_vehicles\""
            + ",\"near_counts_include_vehicles_in_depots\":true"
            + ",\"near_manhattan_radius\":4"
            + ",\"sampling_interval_script_ticks\":5"
            + ",\"sampled_maxima_are_independent\":true"
            + ",\"sampled_maxima_meaning\":\"Each sampled maximum is computed independently across samples and need not have occurred at the same time.\""
            + ",\"cargo_snapshot_meaning\":\"Waiting cargo is the last sampled snapshot for each date, not a maximum.\""
            + ",\"loading_or_unloading_meaning\":\"Vehicle location equals the road-stop tile and vehicle state is VS_AT_STATION; this is not physical bay occupancy.\""
            + ",\"requested_advance_days\":" + advance_days
            + ",\"advanced_days\":" + (end_date - start_date)
            + ",\"start_date\":" + start_date
            + ",\"end_date\":" + end_date
            + ",\"bounded_window_completed\":" + this.BoolJson(end_date >= target_date)
            + ",\"safety_cap_exhausted\":" + this.BoolJson(safety_cap_exhausted)
            + ",\"game_mutated\":" + this.BoolJson(game_mutated)
            /* A completed response, or a safety-cap response with an exact
             * end date, has no elapsed-time uncertainty. Failures thrown
             * after Sleep began use the catch path below instead. */
            + ",\"time_may_have_advanced\":false"
            + ",\"daily\":" + daily_json);
    } catch (e) {
        local extra = ",\"station_id\":" + station_id
            + ",\"requested_advance_days\":" + advance_days
            + ",\"game_mutated\":" + this.BoolJson(game_mutated)
            + ",\"time_may_have_advanced\":" + this.BoolJson(time_may_have_advanced);
        if (start_date >= 0) extra += ",\"start_date\":" + start_date;
        if (last_known_date >= 0) extra += ",\"last_known_date\":" + last_known_date;
        this.EmitSensorApiError(timestamp, action, sensor_code, sensor_field, extra);
    }
}

function McpBridge::CountRoadVehicles()
{
    local count = 0;
    local vehicles = AIVehicleList();
    foreach (vehicle_id, value in vehicles) {
        if (AIVehicle.GetVehicleType(vehicle_id) == AIVehicle.VT_ROAD) count++;
    }
    return count;
}

// Shared finance fact fragment (no leading comma; DateJson precedent) so
// get_my_finances and get_my_company read the same AICompany calls once instead
// of maintaining two hand-built copies that can drift field-for-field. The
// shared result envelope emits bank_balance.
function McpBridge::CompanyFinanceCoreJson()
{
    return "\"loan_amount\":" + AICompany.GetLoanAmount()
        + ",\"max_loan_amount\":" + AICompany.GetMaxLoanAmount()
        + ",\"loan_interval\":" + AICompany.GetLoanInterval();
}

function McpBridge::CompanyQuarterReportsJson()
{
    // CURRENT_QUARTER (0, the current quarter) .. EARLIEST_QUARTER (the oldest
    // queryable quarter) is a fixed ascending range on any build, so iterate it
    // directly. The old direction-inferring while(true)+guard>64 loop could
    // silently truncate the array if EARLIEST_QUARTER ever widened past the
    // guard, reporting a short list while still emitting the full earliest_quarter
    // fact -- an undetectable honest-sensor contract violation. A bounded for
    // over the documented range cannot desynchronize from that fact.
    local s = "[";
    local first = true;
    for (local q = AICompany.CURRENT_QUARTER; q <= AICompany.EARLIEST_QUARTER; q++) {
        if (!first) s += ",";
        first = false;
        s += "{\"quarter\":" + q
            + this.QuarterFinanceJson(this.QuarterFinanceTable(q))
            + "}";
    }
    s += "]";
    return s;
}

function McpBridge::GroupName(group_id)
{
    if (AIGroup.IsValidGroup(group_id)) {
        local name = AIGroup.GetName(group_id);
        return name == null ? "" : name;
    }
    if (group_id == AIGroup.GROUP_DEFAULT) return "default";
    if (group_id == AIGroup.GROUP_ALL) return "all";
    return "invalid";
}

/* Candidate old-engine ids to probe for autoreplace rules. AIEngineList(VT_ROAD)
 * only lists engines the company can currently buy, so a rule keyed on an old
 * engine that has since aged out would be silently dropped even though it is
 * still live. Union in the engine types the company still operates (those are
 * exactly the retired engines an autoreplace rule can still act on). Returned as
 * a sorted AIList so the emitted JSON order stays deterministic. */
function McpBridge::RoadEngineCandidates()
{
    local ids = AIList();
    local engines = AIEngineList(AIVehicle.VT_ROAD);
    foreach (engine_id, _ in engines) ids.AddItem(engine_id, engine_id);
    local vehicles = AIVehicleList();
    foreach (vehicle_id, _ in vehicles) {
        if (AIVehicle.GetVehicleType(vehicle_id) != AIVehicle.VT_ROAD) continue;
        local e = AIVehicle.GetEngineType(vehicle_id);
        if (AIEngine.IsValidEngine(e) && !ids.HasItem(e)) ids.AddItem(e, e);
    }
    ids.Sort(AIList.SORT_BY_ITEM, AIList.SORT_ASCENDING);
    return ids;
}

function McpBridge::AutoreplaceRulesJson(group_id, candidates)
{
    local s = "[";
    local first = true;
    foreach (engine_id, _ in candidates) {
        if (!AIEngine.IsValidEngine(engine_id)) continue;
        local replacement = AIGroup.GetEngineReplacement(group_id, engine_id);
        if (!AIEngine.IsValidEngine(replacement)) continue;
        if (!first) s += ",";
        first = false;
        s += "{\"engine_id_old\":" + engine_id
            + ",\"engine_id_new\":" + replacement
            + ",\"engine_old_name\":\"" + this.JsonEscape(AIEngine.GetName(engine_id)) + "\""
            + ",\"engine_new_name\":\"" + this.JsonEscape(AIEngine.GetName(replacement)) + "\"}";
    }
    s += "]";
    return s;
}

function McpBridge::GroupsJson()
{
    local s = "[";
    local first = true;
    local groups = AIGroupList();
    /* Build the candidate engine list once, not once per group. */
    local candidates = this.RoadEngineCandidates();
    foreach (group_id, _ in groups) {
        if (!AIGroup.IsValidGroup(group_id)) continue;
        if (AIGroup.GetVehicleType(group_id) != AIVehicle.VT_ROAD) continue;
        if (!first) s += ",";
        first = false;
        s += "{\"group_id\":" + group_id
            + ",\"name\":\"" + this.JsonEscape(this.GroupName(group_id)) + "\""
            + ",\"vehicle_type\":" + AIGroup.GetVehicleType(group_id)
            + ",\"vehicle_count\":" + AIGroup.GetNumVehicles(group_id, AIVehicle.VT_ROAD)
            + ",\"autoreplace_rules\":" + this.AutoreplaceRulesJson(group_id, candidates)
            + "}";
    }
    s += "]";
    return s;
}

function McpBridge::VehiclesJson()
{
    local s = "[";
    local first = true;
    local vehicles = AIVehicleList();
    foreach (vehicle_id, value in vehicles) {
        if (AIVehicle.GetVehicleType(vehicle_id) != AIVehicle.VT_ROAD) continue;
        if (!first) s += ",";
        first = false;
        s += this.VehicleJson(vehicle_id);
    }
    s += "]";
    return s;
}

function McpBridge::VehicleJson(vehicle_id)
{
    local tile = AIVehicle.GetLocation(vehicle_id);
    local x = -1;
    local y = -1;
    if (AIMap.IsValidTile(tile)) { x = AIMap.GetTileX(tile); y = AIMap.GetTileY(tile); }
    local engine_id = AIVehicle.GetEngineType(vehicle_id);
    local cargo = AIEngine.GetCargoType(engine_id);
    local age_left_days = AIVehicle.GetAgeLeft(vehicle_id);
    local order_count = AIOrder.GetOrderCount(vehicle_id);
    local state = AIVehicle.GetState(vehicle_id);
    local group_id = AIVehicle.GetGroupID(vehicle_id);
    local current_order_index = -1;
    if (order_count > 0) current_order_index = AIOrder.ResolveOrderPosition(vehicle_id, AIOrder.ORDER_CURRENT);
    local current_order_destination = AIOrder.GetOrderDestination(vehicle_id, AIOrder.ORDER_CURRENT);
    local current_order_station = -1;
    if (AIMap.IsValidTile(current_order_destination)) {
        local station_id = AIStation.GetStationID(current_order_destination);
        if (AIStation.IsValidStation(station_id)) current_order_station = station_id;
    }
    return "{\"vehicle_id\":" + vehicle_id
        + ",\"name\":\"" + this.JsonEscape(AIVehicle.GetName(vehicle_id)) + "\""
        + ",\"engine_id\":" + engine_id
        + ",\"engine_name\":\"" + this.JsonEscape(AIEngine.GetName(engine_id)) + "\""
        + ",\"tile\":" + tile
        + ",\"x\":" + x
        + ",\"y\":" + y
        + ",\"state\":" + state
        + ",\"state_name\":\"" + this.VehicleStateName(state) + "\""
        + ",\"group_id\":" + group_id
        + ",\"group_name\":\"" + this.JsonEscape(this.GroupName(group_id)) + "\""
        + ",\"speed\":" + AIVehicle.GetCurrentSpeed(vehicle_id)
        + ",\"in_depot\":" + this.BoolJson(AIVehicle.IsInDepot(vehicle_id))
        + ",\"stopped_in_depot\":" + this.BoolJson(AIVehicle.IsStoppedInDepot(vehicle_id))
        + ",\"order_count\":" + order_count
        + ",\"current_order_index\":" + current_order_index
        + ",\"current_order_destination\":" + current_order_destination
        + ",\"current_order_station\":" + current_order_station
        + ",\"cargo_type\":" + cargo
        + ",\"cargo_load\":" + AIVehicle.GetCargoLoad(vehicle_id, cargo)
        + ",\"profit_this_year\":" + AIVehicle.GetProfitThisYear(vehicle_id)
        + ",\"profit_last_year\":" + AIVehicle.GetProfitLastYear(vehicle_id)
        + ",\"current_value\":" + AIVehicle.GetCurrentValue(vehicle_id)
        + ",\"running_cost\":" + AIVehicle.GetRunningCost(vehicle_id)
        + ",\"has_shared_orders\":" + this.BoolJson(AIVehicle.HasSharedOrders(vehicle_id))
        + ",\"age_days\":" + AIVehicle.GetAge(vehicle_id)
        + ",\"max_age_days\":" + AIVehicle.GetMaxAge(vehicle_id)
        + ",\"age_left_days\":" + age_left_days
        + ",\"reliability\":" + AIVehicle.GetReliability(vehicle_id)
        + ",\"needs_replacement\":" + this.BoolJson(age_left_days <= NEEDS_REPLACEMENT_AGE_LEFT_DAYS)
        + "}";
}

function McpBridge::RoutesJson()
{
    local s = "[";
    local first_route = true;
    local vehicles = AIVehicleList();
    foreach (vehicle_id, value in vehicles) {
        if (AIVehicle.GetVehicleType(vehicle_id) != AIVehicle.VT_ROAD) continue;
        local station_ids = [];
        local station_tiles = [];
        local order_count = AIOrder.GetOrderCount(vehicle_id);
        for (local i = 0; i < order_count; i++) {
            if (!AIOrder.IsGotoStationOrder(vehicle_id, i)) continue;
            local dest = AIOrder.GetOrderDestination(vehicle_id, i);
            if (!AIMap.IsValidTile(dest) || !AIRoad.IsRoadStationTile(dest)) continue;
            local station_id = AIStation.GetStationID(dest);
            if (!AIStation.IsValidStation(station_id)) continue;
            this.AppendUnique(station_ids, station_id);
            this.AppendUnique(station_tiles, dest);
        }
        if (station_ids.len() < 2) continue;
        if (!first_route) s += ",";
        first_route = false;
        s += "{\"route_id\":\"route-" + vehicle_id + "\",\"vehicle_id\":" + vehicle_id + ",\"order_count\":" + order_count + ",\"station_ids\":[";
        for (local j = 0; j < station_ids.len(); j++) {
            if (j != 0) s += ",";
            s += station_ids[j];
        }
        s += "],\"station_tiles\":[";
        for (local k = 0; k < station_tiles.len(); k++) {
            if (k != 0) s += ",";
            s += station_tiles[k];
        }
        s += "],\"profit_this_year\":" + AIVehicle.GetProfitThisYear(vehicle_id)
            + ",\"profit_last_year\":" + AIVehicle.GetProfitLastYear(vehicle_id)
            + ",\"stopped_in_depot\":" + this.BoolJson(AIVehicle.IsStoppedInDepot(vehicle_id))
            + "}";
    }
    s += "]";
    return s;
}

function McpBridge::RouteStationTiles(vehicle_id)
{
    local station_tiles = [];
    local order_count = AIOrder.GetOrderCount(vehicle_id);
    for (local i = 0; i < order_count; i++) {
        if (!AIOrder.IsGotoStationOrder(vehicle_id, i)) continue;
        local dest = AIOrder.GetOrderDestination(vehicle_id, i);
        if (!AIMap.IsValidTile(dest) || !AIRoad.IsRoadStationTile(dest)) continue;
        local station_id = AIStation.GetStationID(dest);
        if (!AIStation.IsValidStation(station_id)) continue;
        this.AppendUnique(station_tiles, dest);
    }
    return station_tiles;
}

function McpBridge::OrderDestinationsJson(vehicle_id)
{
    local destinations = [];
    if (!AIVehicle.IsValidVehicle(vehicle_id)) return "[]";
    local order_count = AIOrder.GetOrderCount(vehicle_id);
    for (local i = 0; i < order_count; i++) {
        destinations.append(AIOrder.GetOrderDestination(vehicle_id, i));
    }
    return this.IntArrayJson(destinations);
}

/* The game stores a station order as the station, never the caller's tile
 * (live-probed). AIOrder.GetOrderDestination re-computes a representative tile of that
 * station on every read, so the per-position station id is the order's only
 * stable destination identity. -1 marks a position with no station
 * destination: conditional, void, depot, or otherwise unresolvable. */
function McpBridge::OrderStationsJson(vehicle_id)
{
    local stations = [];
    if (!AIVehicle.IsValidVehicle(vehicle_id)) return "[]";
    local order_count = AIOrder.GetOrderCount(vehicle_id);
    for (local i = 0; i < order_count; i++) {
        local station_id = -1;
        if (AIOrder.IsGotoStationOrder(vehicle_id, i)) {
            local destination = AIOrder.GetOrderDestination(vehicle_id, i);
            if (AIMap.IsValidTile(destination)) {
                local candidate = AIStation.GetStationID(destination);
                if (AIStation.IsValidStation(candidate)) station_id = candidate;
            }
        }
        stations.append(station_id);
    }
    return this.IntArrayJson(stations);
}

function McpBridge::OrderConditionName(condition)
{
    switch (condition) {
        case AIOrder.OC_LOAD_PERCENTAGE: return "LOAD_PERCENTAGE";
        case AIOrder.OC_RELIABILITY: return "RELIABILITY";
        case AIOrder.OC_MAX_SPEED: return "MAX_SPEED";
        case AIOrder.OC_AGE: return "AGE";
        case AIOrder.OC_REQUIRES_SERVICE: return "REQUIRES_SERVICE";
        case AIOrder.OC_UNCONDITIONALLY: return "UNCONDITIONALLY";
        case AIOrder.OC_REMAINING_LIFETIME: return "REMAINING_LIFETIME";
        case AIOrder.OC_MAX_RELIABILITY: return "MAX_RELIABILITY";
        default: return "INVALID";
    }
}

function McpBridge::OrderCompareName(compare)
{
    switch (compare) {
        case AIOrder.CF_EQUALS: return "EQUALS";
        case AIOrder.CF_NOT_EQUALS: return "NOT_EQUALS";
        case AIOrder.CF_LESS_THAN: return "LESS_THAN";
        case AIOrder.CF_LESS_EQUALS: return "LESS_EQUALS";
        case AIOrder.CF_MORE_THAN: return "MORE_THAN";
        case AIOrder.CF_MORE_EQUALS: return "MORE_EQUALS";
        case AIOrder.CF_IS_TRUE: return "IS_TRUE";
        case AIOrder.CF_IS_FALSE: return "IS_FALSE";
        default: return "INVALID";
    }
}

function McpBridge::OrderListJson(vehicle_id)
{
    if (!AIVehicle.IsValidVehicle(vehicle_id)) return "[]";
    local s = "[";
    local first = true;
    local order_count = AIOrder.GetOrderCount(vehicle_id);
    for (local i = 0; i < order_count; i++) {
        local is_conditional = AIOrder.IsConditionalOrder(vehicle_id, i);
        local is_void = AIOrder.IsVoidOrder(vehicle_id, i);
        if (!first) s += ",";
        first = false;
        s += "{\"position\":" + i
            + ",\"is_conditional\":" + this.BoolJson(is_conditional)
            + ",\"is_void\":" + this.BoolJson(is_void);
        if (is_conditional) {
            local condition = AIOrder.GetOrderCondition(vehicle_id, i);
            local compare = AIOrder.GetOrderCompareFunction(vehicle_id, i);
            s += ",\"condition\":\"" + this.OrderConditionName(condition) + "\""
                + ",\"condition_code\":" + condition
                + ",\"compare\":\"" + this.OrderCompareName(compare) + "\""
                + ",\"compare_code\":" + compare
                + ",\"compare_value\":" + AIOrder.GetOrderCompareValue(vehicle_id, i)
                + ",\"jump_to\":" + AIOrder.GetOrderJumpTo(vehicle_id, i);
        } else if (!is_void) {
            local destination = AIOrder.GetOrderDestination(vehicle_id, i);
            s += ",\"is_goto_station\":" + this.BoolJson(AIOrder.IsGotoStationOrder(vehicle_id, i))
                + ",\"is_goto_depot\":" + this.BoolJson(AIOrder.IsGotoDepotOrder(vehicle_id, i))
                + ",\"destination\":" + destination;
            if (AIMap.IsValidTile(destination)) {
                local station_id = AIStation.GetStationID(destination);
                if (AIStation.IsValidStation(station_id)) s += ",\"station_id\":" + station_id;
            }
            s += ",\"flags\":" + AIOrder.GetOrderFlags(vehicle_id, i);
        }
        s += "}";
    }
    s += "]";
    return s;
}

function McpBridge::OrderFlagsJson(vehicle_id)
{
    local flags = [];
    if (!AIVehicle.IsValidVehicle(vehicle_id)) return "[]";
    local order_count = AIOrder.GetOrderCount(vehicle_id);
    for (local i = 0; i < order_count; i++) {
        if (AIOrder.IsConditionalOrder(vehicle_id, i) || AIOrder.IsVoidOrder(vehicle_id, i)) {
            flags.append(AIOrder.OF_INVALID);
        } else {
            flags.append(AIOrder.GetOrderFlags(vehicle_id, i));
        }
    }
    return this.IntArrayJson(flags);
}

function McpBridge::CanonicalRouteSignature(station_tiles)
{
    local signature = [];
    for (local i = 0; i < station_tiles.len(); i++) signature.append(station_tiles[i]);
    if (signature.len() == 2 && signature[1] < signature[0]) {
        local tmp = signature[0];
        signature[0] = signature[1];
        signature[1] = tmp;
    }
    return signature;
}

function McpBridge::RouteEconomicsVehicleJson(vehicle_id, station_tiles)
{
    local tile = AIVehicle.GetLocation(vehicle_id);
    local x = -1;
    local y = -1;
    if (AIMap.IsValidTile(tile)) { x = AIMap.GetTileX(tile); y = AIMap.GetTileY(tile); }
    local engine_id = AIVehicle.GetEngineType(vehicle_id);
    local cargo = AIEngine.GetCargoType(engine_id);
    return "{\"vehicle_id\":" + vehicle_id
        + ",\"station_tiles\":" + this.IntArrayJson(station_tiles)
        + ",\"order_count\":" + AIOrder.GetOrderCount(vehicle_id)
        + ",\"engine_id\":" + engine_id
        + ",\"engine_name\":\"" + this.JsonEscape(AIEngine.GetName(engine_id)) + "\""
        + ",\"tile\":" + tile
        + ",\"x\":" + x
        + ",\"y\":" + y
        + ",\"state\":" + AIVehicle.GetState(vehicle_id)
        + ",\"stopped_in_depot\":" + this.BoolJson(AIVehicle.IsStoppedInDepot(vehicle_id))
        + ",\"cargo_type\":" + cargo
        + ",\"cargo_load\":" + AIVehicle.GetCargoLoad(vehicle_id, cargo)
        + ",\"profit_this_year\":" + AIVehicle.GetProfitThisYear(vehicle_id)
        + ",\"profit_last_year\":" + AIVehicle.GetProfitLastYear(vehicle_id)
        + ",\"current_value\":" + AIVehicle.GetCurrentValue(vehicle_id)
        + ",\"running_cost\":" + AIVehicle.GetRunningCost(vehicle_id)
        + ",\"age_days\":" + AIVehicle.GetAge(vehicle_id)
        + ",\"max_age_days\":" + AIVehicle.GetMaxAge(vehicle_id)
        + ",\"age_left_days\":" + AIVehicle.GetAgeLeft(vehicle_id)
        + ",\"reliability\":" + AIVehicle.GetReliability(vehicle_id)
        + "}";
}

function McpBridge::RouteEconomicsJson()
{
    local groups = [];
    local vehicles = AIVehicleList();
    foreach (vehicle_id, value in vehicles) {
        if (AIVehicle.GetVehicleType(vehicle_id) != AIVehicle.VT_ROAD) continue;
        local station_tiles = this.RouteStationTiles(vehicle_id);
        local signature = this.CanonicalRouteSignature(station_tiles);
        local group = null;
        local insert_at = groups.len();
        for (local i = 0; i < groups.len(); i++) {
            local cmp = this.CompareIntLists(signature, groups[i].signature);
            if (cmp == 0) {
                group = groups[i];
                break;
            }
            if (cmp < 0) {
                insert_at = i;
                break;
            }
        }
        if (group == null) {
            group = {
                signature = signature,
                vehicles = [],
                profit_this_year_total = 0,
                profit_last_year_total = 0,
            };
            groups.insert(insert_at, group);
        }

        local profit_this_year = AIVehicle.GetProfitThisYear(vehicle_id);
        local profit_last_year = AIVehicle.GetProfitLastYear(vehicle_id);
        group.profit_this_year_total += profit_this_year;
        group.profit_last_year_total += profit_last_year;

        local vehicle_json = this.RouteEconomicsVehicleJson(vehicle_id, station_tiles);
        local inserted_vehicle = false;
        for (local j = 0; j < group.vehicles.len(); j++) {
            if (vehicle_id < group.vehicles[j][0]) {
                group.vehicles.insert(j, [vehicle_id, vehicle_json]);
                inserted_vehicle = true;
                break;
            }
        }
        if (!inserted_vehicle) group.vehicles.append([vehicle_id, vehicle_json]);
    }

    local s = "[";
    for (local g = 0; g < groups.len(); g++) {
        if (g != 0) s += ",";
        local group = groups[g];
        s += "{\"route_signature\":" + this.IntArrayJson(group.signature)
            + ",\"signature_semantics\":\"unique_station_stop_tiles; two_stop_signatures_are_sorted\""
            + ",\"vehicle_count\":" + group.vehicles.len()
            + ",\"vehicle_ids\":[";
        for (local v = 0; v < group.vehicles.len(); v++) {
            if (v != 0) s += ",";
            s += group.vehicles[v][0];
        }
        s += "],\"profit_this_year_total\":" + group.profit_this_year_total
            + ",\"profit_last_year_total\":" + group.profit_last_year_total
            + ",\"vehicles\":[";
        for (local r = 0; r < group.vehicles.len(); r++) {
            if (r != 0) s += ",";
            s += group.vehicles[r][1];
        }
        s += "]}";
    }
    s += "]";
    return s;
}

function McpBridge::StationsJson(passenger_cargo)
{
    local s = "[";
    local first = true;
    /* Enumerate both bus and truck road stops so a station built by
     * build_truck_stop_drive_through appears in the observation/map-snapshot stations table:
     * an earlier bus-only AIStationList made truck stops
     * invisible, so the model read a successful truck build as a silent failure.
     * A station can carry both stop types under one id, so dedupe by station_id
     * and report is_bus_stop/is_truck_stop so the kind is unambiguous. */
    local seen = {};
    local road_stop_types = [AIStation.STATION_BUS_STOP, AIStation.STATION_TRUCK_STOP];
    foreach (station_type in road_stop_types) {
        local stations = AIStationList(station_type);
        foreach (station_id, value in stations) {
            if (!AIStation.IsValidStation(station_id)) continue;
            local key = "" + station_id;
            if (seen.rawin(key)) continue;
            seen[key] <- true;
            if (!first) s += ",";
            first = false;
            local tile = AIStation.GetLocation(station_id);
            local x = -1;
            local y = -1;
            if (AIMap.IsValidTile(tile)) { x = AIMap.GetTileX(tile); y = AIMap.GetTileY(tile); }
            local waiting = -1;
            local rating = -1;
            if (passenger_cargo != -1) {
                waiting = AIStation.GetCargoWaiting(station_id, passenger_cargo);
                if (AIStation.HasCargoRating(station_id, passenger_cargo)) rating = AIStation.GetCargoRating(station_id, passenger_cargo);
            }
            local is_bus_stop = false;
            local is_truck_stop = false;
            local fact_errors = [];
            try {
                is_bus_stop = AIStation.HasStationType(station_id, AIStation.STATION_BUS_STOP);
            } catch (e) {
                fact_errors.append(["station_type_unavailable", "is_bus_stop"]);
            }
            try {
                is_truck_stop = AIStation.HasStationType(station_id, AIStation.STATION_TRUCK_STOP);
            } catch (e2) {
                fact_errors.append(["station_type_unavailable", "is_truck_stop"]);
            }
            s += "{\"station_id\":" + station_id
                + ",\"name\":\"" + this.JsonEscape(AIStation.GetName(station_id)) + "\""
                + ",\"tile\":" + tile
                + ",\"x\":" + x
                + ",\"y\":" + y
                + ",\"is_bus_stop\":" + this.BoolJson(is_bus_stop)
                + ",\"is_truck_stop\":" + this.BoolJson(is_truck_stop)
                + ",\"waiting_passengers\":" + waiting
                + ",\"passenger_rating\":" + rating
                + this.FactErrorsJson(fact_errors)
                + "}";
        }
    }
    s += "]";
    return s;
}

function McpBridge::TownsJson()
{
    local s = "[";
    local first = true;
    local towns = AITownList();
    foreach (town_id, value in towns) {
        if (!first) s += ",";
        first = false;
        local tile = AITown.GetLocation(town_id);
        s += "{\"town_id\":" + town_id
            + ",\"name\":\"" + this.JsonEscape(AITown.GetName(town_id)) + "\""
            + ",\"population\":" + AITown.GetPopulation(town_id)
            + ",\"x\":" + AIMap.GetTileX(tile)
            + ",\"y\":" + AIMap.GetTileY(tile)
            + "}";
    }
    s += "]";
    return s;
}

/* Per-cargo town statistics. Every figure is the AITown return
 * value as the API reports it, unflattened: an invalid town or cargo yields -1,
 * while a valid town holding no supplied-cargo slot for that cargo yields 0
 * (script_town.cpp:89-122). GetLastMonthSupplied is a naming trap -- it reads
 * history[LAST_MONTH].transported, i.e. how much of last month's production
 * MoveGoodsToStation moved into station waiting queues (town_cmd.cpp:535-538),
 * not what the town produced and not cargo carried away by a vehicle -- so it
 * is emitted as last_month_to_stations. There is no AICargoList_TownSupplying,
 * so the scan is over AICargoList(); rows whose three figures are all zero are
 * dropped, which is the payload-lean half of the same filter stated in the tool
 * description. */
function McpBridge::TownCargoStatsJson(town_id)
{
    local s = "[";
    local first = true;
    local cargoes = AICargoList();
    foreach (cargo_id, value in cargoes) {
        local production = -1;
        local to_stations = -1;
        local transported_percentage = -1;
        local metric_error = "";
        local fact_errors = [];
        try {
            production = AITown.GetLastMonthProduction(town_id, cargo_id);
        } catch (e) {
            metric_error = "last_month_production_unavailable";
            fact_errors.append([metric_error, "last_month_production"]);
        }
        try {
            to_stations = AITown.GetLastMonthSupplied(town_id, cargo_id);
        } catch (e2) {
            if (metric_error == "") metric_error = "last_month_to_stations_unavailable";
            fact_errors.append(["last_month_to_stations_unavailable", "last_month_to_stations"]);
        }
        try {
            transported_percentage = AITown.GetLastMonthTransportedPercentage(town_id, cargo_id);
        } catch (e3) {
            if (metric_error == "") metric_error = "last_month_transported_percentage_unavailable";
            fact_errors.append(["last_month_transported_percentage_unavailable", "last_month_transported_percentage"]);
        }
        if (production == 0 && to_stations == 0 && transported_percentage == 0) continue;
        /* Invalid cargo returns a null label (probed); report "" rather than fake a name. */
        local label = AICargo.GetCargoLabel(cargo_id);
        if (label == null) label = "";
        if (!first) s += ",";
        first = false;
        s += "{\"cargo_type\":" + cargo_id
            + ",\"cargo_label\":\"" + this.JsonEscape("" + label) + "\""
            + ",\"last_month_production\":" + production
            + ",\"last_month_to_stations\":" + to_stations
            + ",\"last_month_transported_percentage\":" + transported_percentage;
        if (metric_error != "") s += ",\"metric_error\":\"" + metric_error + "\"";
        s += this.FactErrorsJson(fact_errors) + "}";
    }
    s += "]";
    return s;
}

/* Stable names for the ScriptCargo TownEffect enum (script_cargo.hpp:48-56).
 * The raw id travels next to the name so a game whose cargoes map to an effect
 * this bridge does not name still reports the number the API returned. */
function McpBridge::TownEffectName(towneffect_id)
{
    if (towneffect_id == AICargo.TE_PASSENGERS) return "passengers";
    if (towneffect_id == AICargo.TE_MAIL) return "mail";
    if (towneffect_id == AICargo.TE_GOODS) return "goods";
    if (towneffect_id == AICargo.TE_WATER) return "water";
    if (towneffect_id == AICargo.TE_FOOD) return "food";
    if (towneffect_id == AICargo.TE_NONE) return "none";
    return "unknown";
}

/* Town growth-cargo statistics. AITown.GetLastMonthReceived and
 * GetCargoGoal are keyed by TownEffect, not by cargo (script_town.cpp:124-163),
 * so the effect set is collected from AICargo.GetTownEffect over this game's
 * cargoes; TE_NONE means "this cargo has no effect on a town" and is skipped.
 * Effects are sorted by id so the row order does not depend on cargo
 * enumeration order. */
function McpBridge::TownGrowthCargoJson(town_id)
{
    local seen = {};
    local effects = [];
    local cargoes = AICargoList();
    foreach (cargo_id, value in cargoes) {
        local effect = AICargo.TE_NONE;
        try {
            effect = AICargo.GetTownEffect(cargo_id);
        } catch (e) {
            return {
                ok = false,
                code = "town_effect_unavailable",
                field = "growth_cargo",
                json = ""
            };
        }
        if (effect == AICargo.TE_NONE) continue;
        if (seen.rawin(effect)) continue;
        seen[effect] <- true;
        effects.append(effect);
    }
    effects.sort();
    local s = "[";
    foreach (index, effect in effects) {
        local received = -1;
        local cargo_goal = -1;
        local metric_error = "";
        local fact_errors = [];
        try {
            received = AITown.GetLastMonthReceived(town_id, effect);
        } catch (e2) {
            metric_error = "last_month_received_unavailable";
            fact_errors.append([metric_error, "last_month_received"]);
        }
        try {
            cargo_goal = AITown.GetCargoGoal(town_id, effect);
        } catch (e3) {
            if (metric_error == "") metric_error = "cargo_goal_unavailable";
            fact_errors.append(["cargo_goal_unavailable", "cargo_goal"]);
        }
        if (index != 0) s += ",";
        s += "{\"towneffect_id\":" + effect
            + ",\"towneffect_name\":\"" + this.TownEffectName(effect) + "\""
            + ",\"last_month_received\":" + received
            + ",\"cargo_goal\":" + cargo_goal;
        if (metric_error != "") s += ",\"metric_error\":\"" + metric_error + "\"";
        s += this.FactErrorsJson(fact_errors) + "}";
    }
    s += "]";
    return { ok = true, code = "", field = "", json = s };
}

function McpBridge::CargoesJson()
{
    local s = "[";
    local first = true;
    local cargoes = AICargoList();
    foreach (cargo_id, value in cargoes) {
        if (!first) s += ",";
        first = false;
        s += "{\"cargo_type\":" + cargo_id
            + ",\"label\":\"" + this.JsonEscape("" + AICargo.GetCargoLabel(cargo_id)) + "\""
            + ",\"name\":\"" + this.JsonEscape("" + AICargo.GetName(cargo_id)) + "\"}";
    }
    s += "]";
    return s;
}

/* Raw pre-build acceptance for a hypothetical new standalone station containing
 * one truck-stop tile. OpenTTD 15.1 defines CC_PASSENGERS as the bus-versus-truck
 * boundary: passenger-class cargo appears at bus stops and every other class
 * appears at truck stops (script_cargo.hpp:28); station acceptance independently
 * zeroes passenger-class cargo for a truck-only facility
 * (station_cmd.cpp:622-634). A truck tile joined to a station with a bus, train,
 * airport, or dock facility can accept passenger cargo, so that mixed-facility
 * case is outside this standalone hypothetical. TE_NONE is not town cargo. The
 * values are the direct
 * AITile.GetCargoAcceptance result at the current truck-stop coverage radius,
 * which sums every accepting tile in the area (including houses, industries,
 * and headquarters), not only town buildings (station_cmd.cpp:562-583), with
 * no threshold verdict or placement judgement. Any thrown API error
 * reaches PrimCheckTruckStopCatchment's whole-sensor catch. */
function McpBridge::TownCargoAcceptanceJson(tile, truck_radius)
{
    local s = "[";
    local first = true;
    local cargoes = AICargoList();
    local cargo_ids = [];
    local towneffects = {};
    foreach (cargo_id, value in cargoes) {
        local towneffect_id = AICargo.GetTownEffect(cargo_id);
        if (towneffect_id == AICargo.TE_NONE) continue;
        if (AICargo.HasCargoClass(cargo_id, AICargo.CC_PASSENGERS)) continue;
        cargo_ids.append(cargo_id);
        towneffects[cargo_id] <- towneffect_id;
    }
    cargo_ids.sort();
    foreach (cargo_id in cargo_ids) {
        local towneffect_id = towneffects[cargo_id];
        local acceptance = AITile.GetCargoAcceptance(tile, cargo_id, 1, 1, truck_radius);
        if (acceptance < 0) {
            return {
                ok = false,
                code = "town_cargo_acceptance_unavailable",
                field = "town_cargo_acceptance",
                json = ""
            };
        }
        local label = AICargo.GetCargoLabel(cargo_id);
        local name = AICargo.GetName(cargo_id);
        if (!first) s += ",";
        first = false;
        s += "{\"cargo_type\":" + cargo_id
            + ",\"cargo_label\":\"" + this.JsonEscape("" + label) + "\""
            + ",\"name\":\"" + this.JsonEscape("" + name) + "\""
            + ",\"towneffect_id\":" + towneffect_id
            + ",\"towneffect_name\":\"" + this.TownEffectName(towneffect_id) + "\""
            + ",\"acceptance\":" + acceptance
            + "}";
    }
    s += "]";
    return { ok = true, code = "", field = "", json = s };
}

function McpBridge::CargoCapacitiesJson(vehicle_id)
{
    if (!AIVehicle.IsValidVehicle(vehicle_id)) return "[]";
    local s = "[";
    local first = true;
    local cargoes = AICargoList();
    foreach (cargo, value in cargoes) {
        local capacity = AIVehicle.GetCapacity(vehicle_id, cargo);
        if (capacity <= 0) continue;
        local label = AICargo.GetCargoLabel(cargo);
        if (label == null) label = "";
        if (!first) s += ",";
        first = false;
        s += "{\"cargo_type\":" + cargo
            + ",\"cargo_label\":\"" + this.JsonEscape("" + label) + "\""
            + ",\"capacity\":" + capacity
            + ",\"load\":" + AIVehicle.GetCargoLoad(vehicle_id, cargo)
            + "}";
    }
    s += "]";
    return s;
}

function McpBridge::CargoListJson(cargo_list, industry_id, include_production_metrics, fact_errors)
{
    local s = "[";
    local first = true;
    foreach (cargo_id, value in cargo_list) {
        if (!first) s += ",";
        first = false;
        local production = -1;
        local transported = -1;
        local transported_percentage = -1;
        local stockpiled = -1;
        local accept_state = -1;
        local metric_error = "";
        local row_fact_errors = [];
        if (include_production_metrics) {
            try {
                production = AIIndustry.GetLastMonthProduction(industry_id, cargo_id);
            } catch (e) {
                metric_error = "last_month_production_unavailable";
                row_fact_errors.append([metric_error, "last_month_production"]);
            }
            try {
                transported = AIIndustry.GetLastMonthTransported(industry_id, cargo_id);
            } catch (e2) {
                if (metric_error == "") metric_error = "last_month_transported_unavailable";
                row_fact_errors.append(["last_month_transported_unavailable", "last_month_transported"]);
            }
            /* The game's own percentage for the same pair
             * (script_industry.cpp:122-133). -1 when this industry has no such
             * produced slot, matching its two siblings above. */
            try {
                transported_percentage = AIIndustry.GetLastMonthTransportedPercentage(industry_id, cargo_id);
            } catch (e5) {
                if (metric_error == "") metric_error = "last_month_transported_percentage_unavailable";
                row_fact_errors.append(["last_month_transported_percentage_unavailable", "last_month_transported_percentage"]);
            }
        } else {
            try {
                accept_state = AIIndustry.IsCargoAccepted(industry_id, cargo_id) ? 1 : 0;
            } catch (e3) {
                metric_error = "acceptance_unavailable";
                row_fact_errors.append([metric_error, "accept_state"]);
            }
        }
        try {
            stockpiled = AIIndustry.GetStockpiledCargo(industry_id, cargo_id);
        } catch (e4) {
            if (metric_error == "") metric_error = "stockpile_unavailable";
            row_fact_errors.append(["stockpile_unavailable", "stockpiled"]);
        }
        local label = AICargo.GetCargoLabel(cargo_id);
        if (label == null) label = "";
        s += "{\"cargo_type\":" + cargo_id
            + ",\"cargo_label\":\"" + this.JsonEscape("" + label) + "\""
            + ",\"label\":\"" + this.JsonEscape("" + label) + "\""
            + ",\"name\":\"" + this.JsonEscape("" + AICargo.GetName(cargo_id)) + "\"";
        if (include_production_metrics) {
            s += ",\"last_month_production\":" + production
                + ",\"last_month_transported\":" + transported
                + ",\"last_month_transported_percentage\":" + transported_percentage
                + ",\"stockpiled\":" + stockpiled;
        } else {
            s += ",\"accept_state\":" + accept_state + ",\"stockpiled\":" + stockpiled;
        }
        if (metric_error != "") s += ",\"metric_error\":\"" + metric_error + "\"";
        s += this.FactErrorsJson(row_fact_errors);
        foreach (fact_error in row_fact_errors) fact_errors.append(fact_error);
        s += "}";
    }
    s += "]";
    return s;
}

function McpBridge::IndustriesJson()
{
    local s = "[";
    local first = true;
    local industries = AIIndustryList();
    foreach (industry_id, value in industries) {
        if (!AIIndustry.IsValidIndustry(industry_id)) continue;
        if (!first) s += ",";
        first = false;
        local tile = AIIndustry.GetLocation(industry_id);
        local x = -1;
        local y = -1;
        if (AIMap.IsValidTile(tile)) { x = AIMap.GetTileX(tile); y = AIMap.GetTileY(tile); }
        local industry_type = AIIndustry.GetIndustryType(industry_id);
        local industry_type_name = "";
        local fact_errors = [];
        local cargo_fact_errors = [];
        try {
            industry_type_name = "" + AIIndustryType.GetName(industry_type);
        } catch (e) {
            industry_type_name = "unknown";
            fact_errors.append(["industry_type_name_unavailable", "industry_type_name"]);
        }
        local built_on_water = false;
        try {
            built_on_water = AIIndustry.IsBuiltOnWater(industry_id);
        } catch (e2) {
            built_on_water = false;
            fact_errors.append(["built_on_water_unavailable", "built_on_water"]);
        }
        local stations_around = -1;
        try {
            stations_around = AIIndustry.GetAmountOfStationsAround(industry_id);
        } catch (e3) {
            stations_around = -1;
            fact_errors.append(["stations_around_unavailable", "stations_around"]);
        }
        s += "{\"industry_id\":" + industry_id
            + ",\"name\":\"" + this.JsonEscape("" + AIIndustry.GetName(industry_id)) + "\""
            + ",\"industry_type\":" + industry_type
            + ",\"industry_type_name\":\"" + this.JsonEscape(industry_type_name) + "\""
            + ",\"tile\":" + tile
            + ",\"x\":" + x
            + ",\"y\":" + y
            + ",\"produced_cargo\":" + this.CargoListJson(
                AIIndustryType.GetProducedCargo(industry_type), industry_id, true, cargo_fact_errors)
            + ",\"accepted_cargo\":" + this.CargoListJson(
                AIIndustryType.GetAcceptedCargo(industry_type), industry_id, false, cargo_fact_errors)
            + ",\"built_on_water\":" + this.BoolJson(built_on_water)
            + ",\"stations_around\":" + stations_around
            + this.FactErrorsJson(fact_errors)
            + "}";
    }
    s += "]";
    return s;
}

/* Factual subsidy sensor. AISubsidyList enumerates all
 * current subsidies, both unawarded offers and awarded subsidies. Participant
 * getters return an id whose domain is determined by the companion SPT enum;
 * keep the raw enum and the named kind together so no town/industry id can be
 * misread. Validity gates every name/location lookup, making stale participant
 * references safe even though a valid subsidy should normally retain valid
 * endpoints. */
function McpBridge::SubsidyParticipantJson(participant_type, participant_id)
{
    local kind = "unknown";
    local valid = false;
    local name = "";
    local tile = -1;
    if (participant_type == AISubsidy.SPT_INDUSTRY) {
        kind = "industry";
        valid = AIIndustry.IsValidIndustry(participant_id);
        if (valid) {
            name = "" + AIIndustry.GetName(participant_id);
            tile = AIIndustry.GetLocation(participant_id);
        }
    } else if (participant_type == AISubsidy.SPT_TOWN) {
        kind = "town";
        valid = AITown.IsValidTown(participant_id);
        if (valid) {
            name = "" + AITown.GetName(participant_id);
            tile = AITown.GetLocation(participant_id);
        }
    }
    local x = -1;
    local y = -1;
    if (AIMap.IsValidTile(tile)) {
        x = AIMap.GetTileX(tile);
        y = AIMap.GetTileY(tile);
    }
    return "{\"kind\":\"" + this.JsonEscape(kind) + "\""
        + ",\"type_raw\":" + participant_type
        + ",\"id\":" + participant_id
        + ",\"valid\":" + this.BoolJson(valid)
        + ",\"name\":\"" + this.JsonEscape(name) + "\""
        + ",\"tile\":" + tile
        + ",\"x\":" + x
        + ",\"y\":" + y
        + "}";
}

function McpBridge::SubsidiesJson(company_id)
{
    local json = "[";
    local listed = 0;
    local subsidies = AISubsidyList();
    foreach (subsidy_id, _ in subsidies) {
        // The list is the source of valid IDs, but re-check before the getters
        // so an invalid/stale ID can never trip an API precondition.
        if (!AISubsidy.IsValidSubsidy(subsidy_id)) continue;
        local cargo_type = AISubsidy.GetCargoType(subsidy_id);
        local cargo_label = AICargo.GetCargoLabel(cargo_type);
        if (cargo_label == null) cargo_label = "";
        local cargo_name = AICargo.GetName(cargo_type);
        if (cargo_name == null) cargo_name = "";
        local awarded = AISubsidy.IsAwarded(subsidy_id);
        local status = awarded ? "awarded" : "offered";
        local source_type = AISubsidy.GetSourceType(subsidy_id);
        local source_id = AISubsidy.GetSourceIndex(subsidy_id);
        local destination_type = AISubsidy.GetDestinationType(subsidy_id);
        local destination_id = AISubsidy.GetDestinationIndex(subsidy_id);
        if (listed > 0) json += ",";
        json += "{\"subsidy_id\":" + subsidy_id
            + ",\"cargo_type\":" + cargo_type
            + ",\"cargo_label\":\"" + this.JsonEscape("" + cargo_label) + "\""
            + ",\"cargo_name\":\"" + this.JsonEscape(cargo_name) + "\""
            + ",\"status\":\"" + status + "\""
            + ",\"awarded\":" + this.BoolJson(awarded);
        // GetAwardedTo has IsAwarded as a precondition; omit the field for an
        // open offer instead of inventing a null/sentinel company id.
        if (awarded) {
            local awarded_to_company_id = AISubsidy.GetAwardedTo(subsidy_id);
            json += ",\"awarded_to_company_id\":" + awarded_to_company_id
                + ",\"awarded_to_self\":" + this.BoolJson(awarded_to_company_id == company_id);
        }
        json += ",\"expire_date\":" + this.DateJson(AISubsidy.GetExpireDate(subsidy_id))
            + ",\"source\":" + this.SubsidyParticipantJson(source_type, source_id)
            + ",\"destination\":" + this.SubsidyParticipantJson(destination_type, destination_id)
            + "}";
        listed++;
    }
    return { json = json + "]", count = listed };
}

function McpBridge::DepotsJson()
{
    local s = "[";
    local first = true;
    /* One pass over the company's vehicles into a tile -> count table, so the
     * per-depot lookup below is a hash hit rather than a rescan of every
     * in-depot vehicle for every depot. */
    local vehicles_in_depot_by_tile = {};
    local vehicles = AIVehicleList();
    foreach (vehicle_id, value in vehicles) {
        if (AIVehicle.IsInDepot(vehicle_id)) {
            local vehicle_tile = AIVehicle.GetLocation(vehicle_id);
            if (vehicles_in_depot_by_tile.rawin(vehicle_tile)) {
                vehicles_in_depot_by_tile[vehicle_tile] = vehicles_in_depot_by_tile[vehicle_tile] + 1;
            } else {
                vehicles_in_depot_by_tile[vehicle_tile] <- 1;
            }
        }
    }
    // AIDepotList takes script_tile.hpp:101-106 values, not script_vehicle.hpp:76-80 values.
    local depots = AIDepotList(AITile.TRANSPORT_ROAD);
    foreach (depot_tile, value in depots) {
        if (!AIMap.IsValidTile(depot_tile) || !AIRoad.IsRoadDepotTile(depot_tile)) continue;
        if (!first) s += ",";
        first = false;
        local vehicles_inside = vehicles_in_depot_by_tile.rawin(depot_tile)
            ? vehicles_in_depot_by_tile[depot_tile]
            : 0;
        local front_tile = -1;
        local fact_errors = [];
        try {
            front_tile = AIRoad.GetRoadDepotFrontTile(depot_tile);
        } catch (e) {
            front_tile = -1;
            fact_errors.append(["depot_front_tile_unavailable", "front_tile"]);
        }
        s += "{\"tile\":" + depot_tile
            + ",\"x\":" + AIMap.GetTileX(depot_tile)
            + ",\"y\":" + AIMap.GetTileY(depot_tile)
            + ",\"front_tile\":" + front_tile;
        if (AIMap.IsValidTile(front_tile)) {
            s += ",\"front_x\":" + AIMap.GetTileX(front_tile) + ",\"front_y\":" + AIMap.GetTileY(front_tile);
        } else {
            s += ",\"front_x\":-1,\"front_y\":-1";
        }
        s += ",\"vehicles_inside\":" + vehicles_inside
            + this.FactErrorsJson(fact_errors) + "}";
    }
    s += "]";
    return s;
}

function McpBridge::ClosestTownId(tile)
{
    local towns = AITownList();
    local best = -1;
    local best_distance = 1000000;
    local x = AIMap.GetTileX(tile);
    local y = AIMap.GetTileY(tile);
    foreach (town_id, value in towns) {
        local town_tile = AITown.GetLocation(town_id);
        local dx = x - AIMap.GetTileX(town_tile);
        if (dx < 0) dx = -dx;
        local dy = y - AIMap.GetTileY(town_tile);
        if (dy < 0) dy = -dy;
        local distance = dx + dy;
        if (distance < best_distance) {
            best_distance = distance;
            best = town_id;
        }
    }
    return best;
}

function McpBridge::IndustryAtTile(tile)
{
    local id = AIIndustry.GetIndustryID(tile);
    return AIIndustry.IsValidIndustry(id) ? id : -1;
}

/* Test-mode probe: is there removable road material on the edge between two
 * adjacent tiles? AIRoad.RemoveRoad on an adjacent pair succeeds iff this
 * tile's half-road bit toward the neighbour OR the neighbour's half-bit toward
 * this tile exists (CmdRemoveLongRoad; live-probed on OpenTTD 15.1). The
 * AITestMode object is function-scoped, so the previous mode is restored on
 * return and nothing is ever removed. */
function McpBridge::RoadEdgeRemovable(tile, nbr, field, fact_errors)
{
    if (!AIMap.IsValidTile(nbr)) return false;
    try {
        local test = AITestMode();
        return AIRoad.RemoveRoad(tile, nbr);
    } catch (e) {
        fact_errors.append(["road_edge_probe_unavailable", field]);
        return false;
    }
}

/* One direction of road_bits. JSON literal:
 *   true      -- this tile carries road material toward that neighbour: either
 *                a two-way connection (AIRoad.AreRoadTilesConnected) or a
 *                one-sided stub whose neighbour carries no road, so the
 *                material can only sit on this tile.
 *   "unknown" -- removable road material exists on the shared edge, but both
 *                tiles carry road and the 15.1 API cannot attribute which side
 *                holds the half-bit (documented limitation).
 *   false     -- no removable road material toward that neighbour. */
function McpBridge::RoadBitJson(tile, nbr, field, fact_errors)
{
    if (nbr == -1) return "false";
    local connected = false;
    try {
        connected = AIRoad.AreRoadTilesConnected(tile, nbr);
    } catch (e) {
        fact_errors.append(["road_connection_unavailable", field]);
    }
    if (connected) return "true";
    if (!this.RoadEdgeRemovable(tile, nbr, field, fact_errors)) return "false";
    if (!this.IsRoadLike(nbr)) return "true";
    return "\"unknown\"";
}

/* Factual per-direction road bits for a plain road tile. Distinct from
 * road_connections (which needs road on BOTH tiles): a town-generated
 * one-sided half-road stub toward a roadless neighbour shows here as true
 * while road_connections stays false. Off-axis road bits are what
 * CmdBuildRoadStop rejects as drive-through corner/junction. */
function McpBridge::RoadBitsJson(tile, tile_x, tile_y, field_prefix, fact_errors)
{
    local max_x = AIMap.GetMapSizeX();
    local max_y = AIMap.GetMapSizeY();
    local n = tile_y > 0 ? AIMap.GetTileIndex(tile_x, tile_y - 1) : -1;
    local e = tile_x + 1 < max_x ? AIMap.GetTileIndex(tile_x + 1, tile_y) : -1;
    local s = tile_y + 1 < max_y ? AIMap.GetTileIndex(tile_x, tile_y + 1) : -1;
    local w = tile_x > 0 ? AIMap.GetTileIndex(tile_x - 1, tile_y) : -1;
    return "{\"n\":" + this.RoadBitJson(tile, n, field_prefix + ".n", fact_errors)
        + ",\"e\":" + this.RoadBitJson(tile, e, field_prefix + ".e", fact_errors)
        + ",\"s\":" + this.RoadBitJson(tile, s, field_prefix + ".s", fact_errors)
        + ",\"w\":" + this.RoadBitJson(tile, w, field_prefix + ".w", fact_errors) + "}";
}

function McpBridge::TileJson(tile, tile_x, tile_y, passenger_cargo, largest_town_id, cargo_id)
{
    local x = tile_x;
    local y = tile_y;
    local fact_errors = [];
    local water = false;
    local sea = false;
    local river = false;
    local coast = false;
    local rough = false;
    local rock = false;
    local farm = false;
    local tree = false;
    local station_tile = false;
    try { water = AITile.IsWaterTile(tile); } catch (e) { fact_errors.append(["water_unavailable", "water"]); }
    try { sea = AITile.IsSeaTile(tile); } catch (e2) { fact_errors.append(["sea_unavailable", "sea"]); }
    try { river = AITile.IsRiverTile(tile); } catch (e3) { fact_errors.append(["river_unavailable", "river"]); }
    try { coast = AITile.IsCoastTile(tile); } catch (e4) { fact_errors.append(["coast_unavailable", "coast"]); }
    try { rough = AITile.IsRoughTile(tile); } catch (e5) { fact_errors.append(["rough_unavailable", "rough"]); }
    try { rock = AITile.IsRockTile(tile); } catch (e6) { fact_errors.append(["rock_unavailable", "rock"]); }
    try { farm = AITile.IsFarmTile(tile); } catch (e7) { fact_errors.append(["farm_unavailable", "farm"]); }
    try { tree = AITile.HasTreeOnTile(tile); } catch (e8) { fact_errors.append(["tree_unavailable", "tree"]); }
    try { station_tile = AITile.IsStationTile(tile); } catch (e9) { fact_errors.append(["station_tile_unavailable", "station_tile"]); }

    local road_tile = AIRoad.IsRoadTile(tile);
    local road_station_tile = AIRoad.IsRoadStationTile(tile);
    local road_depot_tile = AIRoad.IsRoadDepotTile(tile);
    local drive_through_road_station = false;
    if (road_station_tile) {
        try {
            drive_through_road_station = AIRoad.IsDriveThroughRoadStationTile(tile);
        } catch (e10) {
            fact_errors.append(["drive_through_status_unavailable", "drive_through_road_station"]);
        }
    }
    local road_station_front_tile = -1;
    local drive_through_back_tile = -1;
    local road_depot_front_tile = -1;
    if (road_station_tile) {
        try {
            road_station_front_tile = AIRoad.GetRoadStationFrontTile(tile);
        } catch (e11) {
            fact_errors.append(["road_station_front_unavailable", "road_station_front_tile"]);
        }
    }
    if (drive_through_road_station) {
        try {
            drive_through_back_tile = AIRoad.GetDriveThroughBackTile(tile);
        } catch (e12) {
            fact_errors.append(["drive_through_back_unavailable", "drive_through_back_tile"]);
        }
    }
    if (road_depot_tile) {
        try {
            road_depot_front_tile = AIRoad.GetRoadDepotFrontTile(tile);
        } catch (e13) {
            fact_errors.append(["road_depot_front_unavailable", "road_depot_front_tile"]);
        }
    }

    local n = false;
    local e = false;
    local s = false;
    local w = false;
    local max_x = AIMap.GetMapSizeX();
    local max_y = AIMap.GetMapSizeY();
    if (y > 0) n = this.DirectConnected(tile, AIMap.GetTileIndex(x, y - 1));
    if (x + 1 < max_x) e = this.DirectConnected(tile, AIMap.GetTileIndex(x + 1, y));
    if (y + 1 < max_y) s = this.DirectConnected(tile, AIMap.GetTileIndex(x, y + 1));
    if (x > 0) w = this.DirectConnected(tile, AIMap.GetTileIndex(x - 1, y));

    local passenger_acceptance = 0;
    local passenger_production = 0;
    local passenger_acceptance_radius4 = 0;
    local passenger_production_radius4 = 0;
    if (passenger_cargo != -1) {
        local bus_stop_coverage_radius = AIStation.GetCoverageRadius(AIStation.STATION_BUS_STOP);
        try {
            passenger_acceptance = AITile.GetCargoAcceptance(tile, passenger_cargo, 1, 1, bus_stop_coverage_radius);
        } catch (e14) {
            fact_errors.append(["passenger_acceptance_unavailable", "passenger_acceptance"]);
        }
        try {
            passenger_production = AITile.GetCargoProduction(tile, passenger_cargo, 1, 1, bus_stop_coverage_radius);
        } catch (e15) {
            fact_errors.append(["passenger_production_unavailable", "passenger_production"]);
        }
        try {
            passenger_acceptance_radius4 = AITile.GetCargoAcceptance(tile, passenger_cargo, 1, 1, 4);
        } catch (e16) {
            fact_errors.append(["passenger_acceptance_radius4_unavailable", "passenger_acceptance_radius4"]);
        }
        try {
            passenger_production_radius4 = AITile.GetCargoProduction(tile, passenger_cargo, 1, 1, 4);
        } catch (e17) {
            fact_errors.append(["passenger_production_radius4_unavailable", "passenger_production_radius4"]);
        }
    }
    local cargo_json = "";
    if (cargo_id != -1) {
        local cargo_acceptance = -1;
        local cargo_production = -1;
        local cargo_acceptance_radius4 = -1;
        local cargo_production_radius4 = -1;
        local cargo_coverage_radius = AIStation.GetCoverageRadius(AIStation.STATION_TRUCK_STOP);
        try {
            cargo_acceptance = AITile.GetCargoAcceptance(tile, cargo_id, 1, 1, cargo_coverage_radius);
        } catch (cargo_e1) {
            fact_errors.append(["cargo_acceptance_unavailable", "cargo_acceptance"]);
        }
        try {
            cargo_production = AITile.GetCargoProduction(tile, cargo_id, 1, 1, cargo_coverage_radius);
        } catch (cargo_e2) {
            fact_errors.append(["cargo_production_unavailable", "cargo_production"]);
        }
        try {
            cargo_acceptance_radius4 = AITile.GetCargoAcceptance(tile, cargo_id, 1, 1, 4);
        } catch (cargo_e3) {
            fact_errors.append(["cargo_acceptance_radius4_unavailable", "cargo_acceptance_radius4"]);
        }
        try {
            cargo_production_radius4 = AITile.GetCargoProduction(tile, cargo_id, 1, 1, 4);
        } catch (cargo_e4) {
            fact_errors.append(["cargo_production_radius4_unavailable", "cargo_production_radius4"]);
        }
        local cargo_label = "";
        try {
            cargo_label = AICargo.GetCargoLabel(cargo_id);
        } catch (cargo_e5) {
            fact_errors.append(["cargo_label_unavailable", "cargo_label"]);
        }
        if (cargo_label == null) cargo_label = "";
        cargo_json = ",\"cargo_id\":" + cargo_id
            + ",\"cargo_label\":\"" + this.JsonEscape(cargo_label) + "\""
            + ",\"cargo_acceptance_radius4\":" + cargo_acceptance_radius4
            + ",\"cargo_production_radius4\":" + cargo_production_radius4
            + ",\"cargo_acceptance\":" + cargo_acceptance
            + ",\"cargo_production\":" + cargo_production;
    }
    local in_largest_town_influence = false;
    if (AITown.IsValidTown(largest_town_id)) in_largest_town_influence = AITown.IsWithinTownInfluence(largest_town_id, tile);
    /* Honest house fact: AITile.IsHouseTile is a 15.0 API addition,
     * live-probed on the installed 15.1 runtime. It replaces an earlier
     * radius-4 production heuristic, which marked buildable grass tiles as
     * houses, so the model demolished grass believing it was clearing houses.
     * IsHouseTile never reports true together with IsBuildable (probed
     * map-wide). */
    local house = false;
    try { house = AITile.IsHouseTile(tile); } catch (e18) { fact_errors.append(["house_unavailable", "house"]); }
    /* Per-direction road bits: emitted only for plain road
     * tiles, where the test-mode RemoveRoad edge probe is well-defined.
     * Station/depot road topology is already factual via the front/back tile
     * fields and road_connections. */
    local road_bits_json = "";
    if (road_tile) {
        road_bits_json = ",\"road_bits\":" + this.RoadBitsJson(
            tile, x, y, "road_bits", fact_errors);
    }

    return "{\"tile\":" + tile
        + ",\"x\":" + x
        + ",\"y\":" + y
        + ",\"terrain_type\":" + AITile.GetTerrainType(tile)
        + ",\"slope\":" + AITile.GetSlope(tile)
        + ",\"min_height\":" + AITile.GetMinHeight(tile)
        + ",\"max_height\":" + AITile.GetMaxHeight(tile)
        + ",\"owner\":" + AITile.GetOwner(tile)
        + ",\"buildable\":" + this.BoolJson(AITile.IsBuildable(tile))
        + ",\"water\":" + this.BoolJson(water)
        + ",\"sea\":" + this.BoolJson(sea)
        + ",\"river\":" + this.BoolJson(river)
        + ",\"coast\":" + this.BoolJson(coast)
        + ",\"rough\":" + this.BoolJson(rough)
        + ",\"rock\":" + this.BoolJson(rock)
        + ",\"farm\":" + this.BoolJson(farm)
        + ",\"tree\":" + this.BoolJson(tree)
        + ",\"house\":" + this.BoolJson(house)
        + ",\"station_tile\":" + this.BoolJson(station_tile || road_station_tile)
        + ",\"road_tile\":" + this.BoolJson(road_tile)
        + ",\"road_station_tile\":" + this.BoolJson(road_station_tile)
        + ",\"road_depot_tile\":" + this.BoolJson(road_depot_tile)
        + ",\"drive_through_road_station\":" + this.BoolJson(drive_through_road_station)
        + ",\"road_station_front_tile\":" + road_station_front_tile
        + ",\"drive_through_back_tile\":" + drive_through_back_tile
        + ",\"road_depot_front_tile\":" + road_depot_front_tile
        + ",\"road_connections\":{\"n\":" + this.BoolJson(n) + ",\"e\":" + this.BoolJson(e) + ",\"s\":" + this.BoolJson(s) + ",\"w\":" + this.BoolJson(w) + "}"
        + road_bits_json
        + ",\"closest_town_id\":" + this.ClosestTownId(tile)
        + ",\"in_largest_town_influence\":" + this.BoolJson(in_largest_town_influence)
        + ",\"passenger_acceptance_radius4\":" + passenger_acceptance_radius4
        + ",\"passenger_production_radius4\":" + passenger_production_radius4
        + ",\"passenger_acceptance\":" + passenger_acceptance
        + ",\"passenger_production\":" + passenger_production
        + cargo_json
        + ",\"industry_id\":" + this.IndustryAtTile(tile)
        + this.FactErrorsJson(fact_errors)
        + "}";
}

function McpBridge::ShouldEmitMapSnapshot()
{
    require("commands.nut");
    local command_file = Commands();
    return command_file.rawin("emit_map_snapshot") && command_file.emit_map_snapshot;
}

/* The network-timelapse renderer needs visual terrain, industry occupancy,
 * exact company ownership, and connected road geometry. It does not need the
 * full snapshot's cargo-radius sweeps, buildability catalogue, nearest-town
 * scan, or test-mode half-road probes. */
function McpBridge::TimelapseTileJson(tile, tile_x, tile_y)
{
    local road_tile = AIRoad.IsRoadTile(tile);
    local road_station_tile = AIRoad.IsRoadStationTile(tile);
    local road_depot_tile = AIRoad.IsRoadDepotTile(tile);
    local road_like = road_tile || road_station_tile || road_depot_tile;
    local n = false;
    local e = false;
    local s = false;
    local w = false;
    if (road_like) {
        local max_x = AIMap.GetMapSizeX();
        local max_y = AIMap.GetMapSizeY();
        if (tile_y > 0) n = this.DirectConnected(tile, AIMap.GetTileIndex(tile_x, tile_y - 1));
        if (tile_x + 1 < max_x) e = this.DirectConnected(tile, AIMap.GetTileIndex(tile_x + 1, tile_y));
        if (tile_y + 1 < max_y) s = this.DirectConnected(tile, AIMap.GetTileIndex(tile_x, tile_y + 1));
        if (tile_x > 0) w = this.DirectConnected(tile, AIMap.GetTileIndex(tile_x - 1, tile_y));
    }
    return "{\"tile\":" + tile
        + ",\"x\":" + tile_x
        + ",\"y\":" + tile_y
        + ",\"water\":" + this.BoolJson(AITile.IsWaterTile(tile))
        + ",\"house\":" + this.BoolJson(AITile.IsHouseTile(tile))
        + ",\"industry_id\":" + this.IndustryAtTile(tile)
        + ",\"road_tile\":" + this.BoolJson(road_tile)
        + ",\"road_station_tile\":" + this.BoolJson(road_station_tile)
        + ",\"road_depot_tile\":" + this.BoolJson(road_depot_tile)
        + ",\"owner\":" + AITile.GetOwner(tile)
        + ",\"road_connections\":{\"n\":" + this.BoolJson(n) + ",\"e\":" + this.BoolJson(e) + ",\"s\":" + this.BoolJson(s) + ",\"w\":" + this.BoolJson(w) + "}"
        + "}";
}

function McpBridge::EmitTimelapseMapSnapshot()
{
    local engines = this.FindPassengerEngines(1);
    local passenger_cargo = engines.len() > 0 ? engines[0][1] : -1;
    local current_date = AIDate.GetCurrentDate();
    local company_id = AICompany.ResolveCompanyID(AICompany.COMPANY_SELF);
    local size_x = AIMap.GetMapSizeX();
    local size_y = AIMap.GetMapSizeY();
    this.EmitBridgeOutput("MCP_MAP_SNAPSHOT_BEGIN|{\"type\":\"map_snapshot\",\"schema_version\":1,\"snapshot_profile\":\"network_timelapse\",\"observation_index\":" + this.observation_counter + ",\"company_id\":" + company_id + ",\"map_size\":[" + size_x + "," + size_y + "],\"date\":" + this.DateJson(current_date) + "}");
    local tile_count = 0;
    for (local y = 0; y < size_y; y++) {
        local row = "[";
        for (local x = 0; x < size_x; x++) {
            if (x != 0) row += ",";
            local tile = AIMap.GetTileIndex(x, y);
            row += this.TimelapseTileJson(tile, x, y);
            tile_count++;
        }
        row += "]";
        this.EmitBridgeOutput("MCP_MAP_SNAPSHOT_TILES|" + y + "|" + row);
    }
    this.EmitBridgeOutput("MCP_MAP_SNAPSHOT_END|{\"tile_count\":" + tile_count + ",\"row_count\":" + size_y + "}");
    this.EmitBridgeOutput("MCP_MAP_SNAPSHOT_OBJECTS|{\"towns\":" + this.TownsJson()
        + ",\"industries\":" + this.IndustriesJson()
        + ",\"stations\":" + this.StationsJson(passenger_cargo)
        + ",\"vehicles\":" + this.VehiclesJson()
        + ",\"depots\":" + this.DepotsJson()
        + ",\"cargoes\":" + this.CargoesJson()
        + ",\"routes\":" + this.RoutesJson()
        + "}");
}

function McpBridge::EmitMapSnapshot()
{
    this.PollEvents();
    local largest_town_id = this.FindLargestTown();
    /* lean_snapshot: skip the per-tile cargo-radius scans (the dominant
     * cost: an 81-tile sweep per tile). Passing passenger_cargo=-1 makes
     * TileJson skip GetCargoAcceptance/GetCargoProduction. A caller that
     * queries cargo on demand can set this to keep long sessions tractable. */
    require("commands.nut");
    local cf = Commands();
    local lean = cf.rawin("lean_snapshot") && cf.lean_snapshot;
    local engines = lean ? [] : this.FindPassengerEngines(1);
    local passenger_cargo = -1;
    if (engines.len() > 0) passenger_cargo = engines[0][1];
    local current_date = AIDate.GetCurrentDate();
    local size_x = AIMap.GetMapSizeX();
    local size_y = AIMap.GetMapSizeY();
    this.EmitBridgeOutput("MCP_MAP_SNAPSHOT_BEGIN|{\"type\":\"map_snapshot\",\"schema_version\":1,\"observation_index\":" + this.observation_counter + ",\"map_size\":[" + size_x + "," + size_y + "],\"date\":" + this.DateJson(current_date) + "}");
    local tile_count = 0;
    for (local y = 0; y < size_y; y++) {
        local row = "[";
        for (local x = 0; x < size_x; x++) {
            if (x != 0) row += ",";
            local tile = AIMap.GetTileIndex(x, y);
            row += this.TileJson(tile, x, y, passenger_cargo, largest_town_id, -1);
            tile_count++;
        }
        row += "]";
        this.EmitBridgeOutput("MCP_MAP_SNAPSHOT_TILES|" + y + "|" + row);
    }
    this.EmitBridgeOutput("MCP_MAP_SNAPSHOT_END|{\"tile_count\":" + tile_count + ",\"row_count\":" + size_y + "}");
    this.EmitBridgeOutput("MCP_MAP_SNAPSHOT_OBJECTS|{\"towns\":" + this.TownsJson()
        + ",\"industries\":" + this.IndustriesJson()
        + ",\"stations\":" + this.StationsJson(passenger_cargo)
        + ",\"vehicles\":" + this.VehiclesJson()
        + ",\"depots\":" + this.DepotsJson()
        + ",\"cargoes\":" + this.CargoesJson()
        + "}");
}

function McpBridge::ProcessCommand(command, timestamp, ack_result_timestamp)
{
    this.legacy_clear_year_end_on_emit = ack_result_timestamp == null;
    if (ack_result_timestamp != null && ack_result_timestamp == this.year_end_delivery_timestamp) {
        /* The ack proves the model saw the reports attached to that result --
         * and only those. A report snapshotted after that emission is still
         * undelivered, so drop exactly the acknowledged count from the front. */
        local undelivered = [];
        for (local i = this.year_end_delivered_count; i < this.pending_year_end_reports.len(); i++) {
            undelivered.append(this.pending_year_end_reports[i]);
        }
        this.pending_year_end_reports = undelivered;
        this.year_end_delivery_timestamp = null;
        this.year_end_delivered_count = 0;
    }
    if (command.len() < 1) return;
    this.DispatchCommand(command, timestamp);
    /* Dispatch and every primitive-local exception/accounting scope have
     * returned before this one post-result observation is emitted. */
    this.EmitGeneralObservation();
}

function McpBridge::DispatchCommand(command, timestamp)
{
    if (command[0] == "wait") {
        if (command.len() != 2) {
            this.EmitPrimResult(timestamp, "wait", false, "wait_requires_duration_days", 0, "");
            return;
        }
        this.ExecuteWait(command[1], timestamp);
        return;
    }
    if (command[0] == "check_route_connectivity") {
        if (command.len() < 5) { this.EmitPrimResult(timestamp, "check_route_connectivity", false, "check_route_connectivity_requires_town_depot_front_and_stops", 0, ""); return; }
        local stop_tiles = [];
        for (local i = 4; i < command.len(); i++) stop_tiles.append(command[i]);
        this.QueryRouteConnectivity(command[1], command[2], command[3], stop_tiles, timestamp);
        return;
    }
    if (command[0] == "wait_and_observe_vehicle") {
        if (command.len() < 2 || command.len() > 3) { this.EmitPrimResult(timestamp, "wait_and_observe_vehicle", false, "wait_and_observe_vehicle_requires_vehicle_id_and_optional_days", 0, ""); return; }
        local advance_days = command.len() == 3 ? command[2] : 24;
        this.WaitAndObserveVehicle(command[1], advance_days, timestamp);
        return;
    }
    if (command[0] == "wait_and_observe_station") {
        if (command.len() < 2 || command.len() > 3) { this.EmitPrimResult(timestamp, "wait_and_observe_station", false, "wait_and_observe_station_requires_station_id_and_optional_days", 0, ""); return; }
        local advance_days = command.len() == 3 ? command[2] : 14;
        this.WaitAndObserveStation(command[1], advance_days, timestamp);
        return;
    }
    /* --- General-action-surface primitives.
     * These use the lean EmitGeneralObservation (no candidate scans). --- */
    if (command[0] == "get_tile") {
        if (command.len() != 2 && command.len() != 3) { this.EmitPrimResult(timestamp, "get_tile", false, "get_tile_requires_tile_and_optional_cargo_id", 0, ""); return; }
        local cargo_id = command.len() == 3 ? command[2] : -1;
        this.PrimGetTile(command[1], cargo_id, timestamp);
        return;
    }
    if (command[0] == "get_region") {
        if (command.len() != 3 && command.len() != 4) { this.EmitPrimResult(timestamp, "get_region", false, "get_region_requires_center_radius_and_optional_cargo_id", 0, ""); return; }
        local cargo_id = command.len() == 4 ? command[3] : -1;
        this.PrimGetRegion(command[1], command[2], cargo_id, timestamp);
        return;
    }
    if (command[0] == "list_towns") {
        this.PrimListTowns(timestamp);
        return;
    }
    if (command[0] == "list_my_vehicles") {
        this.PrimListMyVehicles(timestamp);
        return;
    }
    if (command[0] == "get_vehicle") {
        if (command.len() != 2) { this.EmitPrimResult(timestamp, "get_vehicle", false, "get_vehicle_requires_vehicle_id", 0, ""); return; }
        this.PrimGetVehicle(command[1], timestamp);
        return;
    }
    if (command[0] == "list_my_stations") {
        if (command.len() != 1) { this.EmitPrimResult(timestamp, "list_my_stations", false, "list_my_stations_takes_no_arguments", 0, ""); return; }
        this.PrimListMyStations(timestamp);
        return;
    }
    if (command[0] == "list_my_depots") {
        this.PrimListMyDepots(timestamp);
        return;
    }
    if (command[0] == "get_my_finances") {
        this.PrimGetMyFinances(timestamp);
        return;
    }
    if (command[0] == "get_my_company") {
        this.PrimGetMyCompany(timestamp);
        return;
    }
    if (command[0] == "list_engines") {
        if (command.len() != 3) { this.EmitPrimResult(timestamp, "list_engines", false, "list_engines_requires_only_buildable_and_passenger_only", 0, ""); return; }
        this.PrimListEngines(command[1] != 0, command[2] != 0, timestamp);
        return;
    }
    if (command[0] == "list_bridge_types") {
        if (command.len() != 2) { this.EmitPrimResult(timestamp, "list_bridge_types", false, "list_bridge_types_requires_length", 0, ""); return; }
        this.PrimListBridgeTypes(command[1], timestamp);
        return;
    }
    if (command[0] == "list_industries") {
        this.PrimListIndustries(timestamp);
        return;
    }
    if (command[0] == "list_subsidies") {
        if (command.len() != 1) { this.EmitPrimResult(timestamp, "list_subsidies", false, "list_subsidies_takes_no_arguments", 0, ""); return; }
        this.PrimListSubsidies(timestamp);
        return;
    }
    if (command[0] == "get_industry") {
        if (command.len() != 2) { this.EmitPrimResult(timestamp, "get_industry", false, "get_industry_requires_industry_id", 0, ""); return; }
        this.PrimGetIndustry(command[1], timestamp);
        return;
    }
    if (command[0] == "get_town") {
        if (command.len() != 2) { this.EmitPrimResult(timestamp, "get_town", false, "get_town_requires_town_id", 0, ""); return; }
        this.PrimGetTown(command[1], timestamp);
        return;
    }
    if (command[0] == "get_my_station") {
        if (command.len() != 3) { this.EmitPrimResult(timestamp, "get_my_station", false, "get_my_station_requires_station_id_and_tile_slots", 0, ""); return; }
        this.PrimGetMyStation(command[1], command[2], timestamp);
        return;
    }
    if (command[0] == "check_truck_stop_catchment") {
        if (command.len() != 2) { this.EmitPrimResult(timestamp, "check_truck_stop_catchment", false, "check_truck_stop_catchment_requires_tile", 0, ""); return; }
        this.PrimCheckTruckStopCatchment(command[1], timestamp);
        return;
    }
    if (command[0] == "list_my_route_economics") {
        this.PrimListMyRouteEconomics(timestamp);
        return;
    }
    if (command[0] == "get_cargo_payment_rate") {
        if (command.len() != 4) { this.EmitPrimResult(timestamp, "get_cargo_payment_rate", false, "get_cargo_payment_rate_requires_cargo_id_distance_and_days_in_transit", 0, ""); return; }
        this.PrimGetCargoPaymentRate(command[1], command[2], command[3], timestamp);
        return;
    }
    if (command[0] == "check_build_road") {
        if (command.len() < 3) { this.EmitPrimResult(timestamp, "check_build_road", false, "check_build_road_requires_from_and_to", 0, ""); return; }
        local points = [];
        for (local i = 1; i < command.len(); i++) points.append(command[i]);
        this.PrimCheckBuildRoad(points, timestamp);
        return;
    }
    if (command[0] == "check_build_road_depot") {
        if (command.len() != 3) { this.EmitPrimResult(timestamp, "check_build_road_depot", false, "check_build_road_depot_requires_tile_and_front", 0, ""); return; }
        this.PrimCheckBuildRoadDepot(command[1], command[2], timestamp);
        return;
    }
    if (command[0] == "build_road") {
        if (command.len() < 3) { this.EmitPrimResult(timestamp, "build_road", false, "build_road_requires_from_and_to", 0, ""); return; }
        local points = [];
        for (local i = 1; i < command.len(); i++) points.append(command[i]);
        this.PrimBuildRoad(points, timestamp);
        return;
    }
    if (command[0] == "build_one_way_road") {
        if (command.len() != 3) { this.EmitPrimResult(timestamp, "build_one_way_road", false, "build_one_way_road_requires_from_and_to", 0, ""); return; }
        this.PrimBuildOneWayRoad(command[1], command[2], timestamp);
        return;
    }
    if (command[0] == "build_bridge") {
        if (command.len() != 4) { this.EmitPrimResult(timestamp, "build_bridge", false, "build_bridge_requires_start_end_and_bridge_id", 0, ""); return; }
        this.PrimBuildBridge(command[1], command[2], command[3], timestamp);
        return;
    }
    if (command[0] == "build_tunnel") {
        if (command.len() != 2) { this.EmitPrimResult(timestamp, "build_tunnel", false, "build_tunnel_requires_start_tile", 0, ""); return; }
        this.PrimBuildTunnel(command[1], timestamp);
        return;
    }
    if (command[0] == "terraform_land") {
        if (command.len() < 4) { this.EmitPrimResult(timestamp, "terraform_land", false, "terraform_land_requires_tile_mode_to_tile_and_optional_corners", 0, ""); return; }
        local corners = [];
        for (local i = 4; i < command.len(); i++) corners.append(command[i]);
        this.PrimTerraformLand(command[1], command[2], command[3], corners, timestamp);
        return;
    }
    if (command[0] == "build_bus_stop_drive_through") {
        if (command.len() != 3 && command.len() != 4) { this.EmitPrimResult(timestamp, "build_bus_stop_drive_through", false, "build_bus_stop_drive_through_requires_tile_front_and_optional_join", 0, ""); return; }
        local join_arg = command.len() == 4 ? command[3] : null;
        this.PrimBuildBusStopDriveThrough(command[1], command[2], join_arg, timestamp);
        return;
    }
    if (command[0] == "build_truck_stop_drive_through") {
        if (command.len() != 3 && command.len() != 4) { this.EmitPrimResult(timestamp, "build_truck_stop_drive_through", false, "build_truck_stop_drive_through_requires_tile_front_and_optional_join", 0, ""); return; }
        local join_arg = command.len() == 4 ? command[3] : null;
        this.PrimBuildTruckStopDriveThrough(command[1], command[2], join_arg, timestamp);
        return;
    }
    if (command[0] == "build_bus_stop_bay") {
        if (command.len() != 3 && command.len() != 4) { this.EmitPrimResult(timestamp, "build_bus_stop_bay", false, "build_bus_stop_bay_requires_tile_front_and_optional_join", 0, ""); return; }
        local join_arg = command.len() == 4 ? command[3] : null;
        this.PrimBuildBusStopBay(command[1], command[2], join_arg, timestamp);
        return;
    }
    if (command[0] == "build_truck_stop_bay") {
        if (command.len() != 3 && command.len() != 4) { this.EmitPrimResult(timestamp, "build_truck_stop_bay", false, "build_truck_stop_bay_requires_tile_front_and_optional_join", 0, ""); return; }
        local join_arg = command.len() == 4 ? command[3] : null;
        this.PrimBuildTruckStopBay(command[1], command[2], join_arg, timestamp);
        return;
    }
    if (command[0] == "build_road_depot") {
        if (command.len() != 3) { this.EmitPrimResult(timestamp, "build_road_depot", false, "build_road_depot_requires_tile_and_front", 0, ""); return; }
        this.PrimBuildRoadDepot(command[1], command[2], timestamp);
        return;
    }
    if (command[0] == "buy_vehicle") {
        if (command.len() != 3) { this.EmitPrimResult(timestamp, "buy_vehicle", false, "buy_vehicle_requires_depot_tile_and_engine_id", 0, ""); return; }
        this.PrimBuyVehicle(command[1], command[2], timestamp);
        return;
    }
    if (command[0] == "clone_vehicle") {
        if (command.len() != 4) { this.EmitPrimResult(timestamp, "clone_vehicle", false, "clone_vehicle_requires_depot_tile_vehicle_id_and_share_orders", 0, ""); return; }
        this.PrimCloneVehicle(command[1], command[2], command[3] != 0, timestamp);
        return;
    }
    if (command[0] == "create_group") {
        if (command.len() != 2) { this.EmitPrimResult(timestamp, "create_group", false, "create_group_requires_name", 0, ""); return; }
        this.PrimCreateGroup(command[1], timestamp);
        return;
    }
    if (command[0] == "move_vehicle_to_group") {
        if (command.len() != 3) { this.EmitPrimResult(timestamp, "move_vehicle_to_group", false, "move_vehicle_to_group_requires_vehicle_and_group", 0, ""); return; }
        this.PrimMoveVehicleToGroup(command[1], command[2], timestamp);
        return;
    }
    if (command[0] == "set_group_autoreplace") {
        if (command.len() != 4) { this.EmitPrimResult(timestamp, "set_group_autoreplace", false, "set_group_autoreplace_requires_group_old_engine_new_engine", 0, ""); return; }
        this.PrimSetGroupAutoReplace(command[1], command[2], command[3], timestamp);
        return;
    }
    if (command[0] == "insert_order") {
        if (command.len() != 4 && command.len() != 5) { this.EmitPrimResult(timestamp, "insert_order", false, "insert_order_requires_vehicle_position_stop_tile_and_optional_flags", 0, ""); return; }
        local order_flags = command.len() == 5 ? command[4] : AIOrder.OF_NONE;
        this.PrimInsertOrder(command[1], command[2], command[3], order_flags, timestamp);
        return;
    }
    if (command[0] == "delete_order") {
        if (command.len() != 3) { this.EmitPrimResult(timestamp, "delete_order", false, "delete_order_requires_vehicle_and_position", 0, ""); return; }
        this.PrimDeleteOrder(command[1], command[2], timestamp);
        return;
    }
    if (command[0] == "set_order_flags") {
        if (command.len() != 4) { this.EmitPrimResult(timestamp, "set_order_flags", false, "set_order_flags_requires_vehicle_position_and_flags", 0, ""); return; }
        this.PrimSetOrderFlags(command[1], command[2], command[3], timestamp);
        return;
    }
    if (command[0] == "append_orders") {
        /* Wire form: [append_orders, vehicle_id, mode, payload...]. command[2] is
         * an explicit mode discriminator — 0 = flagless (bare stop tiles),
         * 1 = flagged (stop, flag_mask pairs) — so a stop tile value can never
         * be mistaken for a pair count. */
        if (command.len() < 4) { this.EmitPrimResult(timestamp, "append_orders", false, "append_orders_requires_vehicle_and_at_least_one_stop", 0, ""); return; }
        local stops = [];
        local order_flags = [];
        if (command[2] == 1) {
            for (local i = 3; i + 1 < command.len(); i += 2) {
                stops.append(command[i]);
                order_flags.append(command[i + 1]);
            }
        } else {
            for (local i = 3; i < command.len(); i++) {
                stops.append(command[i]);
                order_flags.append(AIOrder.OF_NONE);
            }
        }
        this.PrimAppendOrders(command[1], stops, order_flags, timestamp);
        return;
    }
    if (command[0] == "insert_conditional_order") {
        if (command.len() != 7) { this.EmitPrimResult(timestamp, "insert_conditional_order", false, "insert_conditional_order_requires_vehicle_position_condition_compare_value_and_jump_to", 0, ""); return; }
        this.PrimInsertConditionalOrder(command[1], command[2], command[3], command[4], command[5], command[6], timestamp);
        return;
    }
    if (command[0] == "skip_to_order") {
        if (command.len() != 3) { this.EmitPrimResult(timestamp, "skip_to_order", false, "skip_to_order_requires_vehicle_and_position", 0, ""); return; }
        this.PrimSkipToOrder(command[1], command[2], timestamp);
        return;
    }
    if (command[0] == "copy_orders") {
        if (command.len() != 3) { this.EmitPrimResult(timestamp, "copy_orders", false, "copy_orders_requires_vehicle_and_source_vehicle", 0, ""); return; }
        this.PrimCopyShareOrders("copy_orders", command[1], command[2], false, timestamp);
        return;
    }
    if (command[0] == "share_orders") {
        if (command.len() != 3) { this.EmitPrimResult(timestamp, "share_orders", false, "share_orders_requires_vehicle_and_source_vehicle", 0, ""); return; }
        this.PrimCopyShareOrders("share_orders", command[1], command[2], true, timestamp);
        return;
    }
    if (command[0] == "unshare_orders") {
        if (command.len() != 2) { this.EmitPrimResult(timestamp, "unshare_orders", false, "unshare_orders_requires_vehicle", 0, ""); return; }
        this.PrimUnshareOrders(command[1], timestamp);
        return;
    }
    if (command[0] == "refit_vehicle") {
        if (command.len() != 3) { this.EmitPrimResult(timestamp, "refit_vehicle", false, "refit_vehicle_requires_vehicle_id_and_cargo_id", 0, ""); return; }
        this.PrimRefitVehicle(command[1], command[2], timestamp);
        return;
    }
    if (command[0] == "start_vehicle") {
        if (command.len() != 2) { this.EmitPrimResult(timestamp, "start_vehicle", false, "start_vehicle_requires_vehicle_id", 0, ""); return; }
        this.PrimStartStopVehicle(command[1], true, timestamp);
        return;
    }
    if (command[0] == "stop_vehicle") {
        if (command.len() != 2) { this.EmitPrimResult(timestamp, "stop_vehicle", false, "stop_vehicle_requires_vehicle_id", 0, ""); return; }
        this.PrimStartStopVehicle(command[1], false, timestamp);
        return;
    }
    if (command[0] == "reverse_vehicle") {
        if (command.len() != 2) { this.EmitPrimResult(timestamp, "reverse_vehicle", false, "reverse_vehicle_requires_vehicle_id", 0, ""); return; }
        this.PrimReverseVehicle(command[1], timestamp);
        return;
    }
    if (command[0] == "send_vehicle_to_depot") {
        if (command.len() != 2) { this.EmitPrimResult(timestamp, "send_vehicle_to_depot", false, "send_vehicle_to_depot_requires_vehicle_id", 0, ""); return; }
        this.PrimSendVehicleToDepot(command[1], timestamp);
        return;
    }
    if (command[0] == "sell_vehicle") {
        if (command.len() != 2) { this.EmitPrimResult(timestamp, "sell_vehicle", false, "sell_vehicle_requires_vehicle_id", 0, ""); return; }
        this.PrimSellVehicle(command[1], timestamp);
        return;
    }
    if (command[0] == "set_loan") {
        if (command.len() != 2) { this.EmitPrimResult(timestamp, "set_loan", false, "set_loan_requires_amount", 0, ""); return; }
        this.PrimSetLoan(command[1], timestamp);
        return;
    }
    if (command[0] == "demolish_tile") {
        if (command.len() != 2) { this.EmitPrimResult(timestamp, "demolish_tile", false, "demolish_tile_requires_tile", 0, ""); return; }
        this.PrimDemolishTile(command[1], timestamp);
        return;
    }
    if (command[0] == "remove_road") {
        if (command.len() != 3) { this.EmitPrimResult(timestamp, "remove_road", false, "remove_road_requires_from_and_to", 0, ""); return; }
        this.PrimRemoveRoad(command[1], command[2], timestamp);
        return;
    }

    this.EmitPrimResult(timestamp, this.JsonEscape(command[0]), false, "unknown_action", 0, "");
}

function McpBridge::ExecuteWait(duration_days, timestamp)
{
    this.EnsureEventState();
    local days = duration_days;
    if (days < 1) days = 1;
    if (days > 90) days = 90;
    local start_date = AIDate.GetCurrentDate();
    local start_lost_count = this.vehicle_lost_events;
    local start_depot_count = this.vehicle_depot_events;
    local start_unprofitable_count = this.vehicle_unprofitable_events;
    local target_date = start_date + days;
    local loops = 0;
    while (AIDate.GetCurrentDate() < target_date && loops < days * 250) {
        this.MaybeSnapshotYearEnd();
        this.PollEvents();
        this.Sleep(5);
        loops++;
    }
    this.PollEvents();
    local end_date = AIDate.GetCurrentDate();
    local newly_lost_records = this.RecentEventsSince(this.lost_events, start_lost_count);
    local newly_lost_vehicle_ids = [];
    foreach (record in newly_lost_records) {
        this.AppendUnique(newly_lost_vehicle_ids, record.vehicle_id);
    }
    local lost_missing_count = (this.vehicle_lost_events - start_lost_count) - newly_lost_records.len();
    if (lost_missing_count < 0) lost_missing_count = 0;
    /* The depot and unprofitable announcements ride the
     * same drain-time logs as lost events, windowed by the same
     * counter-versus-log-index rule (truncation stays visible as a count
     * gap, exactly like newly_lost). */
    local newly_depot_records = this.RecentEventsSince(this.depot_events, start_depot_count);
    local newly_depot_vehicle_ids = [];
    foreach (depot_record in newly_depot_records) {
        this.AppendUnique(newly_depot_vehicle_ids, depot_record.vehicle_id);
    }
    local depot_missing_count = (this.vehicle_depot_events - start_depot_count) - newly_depot_records.len();
    if (depot_missing_count < 0) depot_missing_count = 0;
    local newly_unprofitable_records = this.RecentEventsSince(this.unprofitable_events, start_unprofitable_count);
    local newly_unprofitable_vehicle_ids = [];
    foreach (unprofitable_record in newly_unprofitable_records) {
        this.AppendUnique(newly_unprofitable_vehicle_ids, unprofitable_record.vehicle_id);
    }
    local unprofitable_missing_count = (this.vehicle_unprofitable_events - start_unprofitable_count) - newly_unprofitable_records.len();
    if (unprofitable_missing_count < 0) unprofitable_missing_count = 0;
    this.EmitPrimResult(timestamp, "wait", true, "", 0,
        ",\"requested_duration_days\":" + duration_days + ",\"duration_days\":" + days + ",\"start_date\":" + start_date + ",\"end_date\":" + end_date + ",\"days_elapsed\":" + (end_date - start_date) + ",\"vehicle_lost_events\":" + this.vehicle_lost_events + ",\"vehicle_crashed_events\":" + this.vehicle_crashed_events
        + ",\"newly_lost_vehicles\":" + this.AttributedEventsJson(newly_lost_records, newly_lost_records.len())
        + ",\"newly_lost_vehicle_ids\":" + this.IntArrayJson(newly_lost_vehicle_ids)
        + ",\"newly_lost_truncated\":" + this.BoolJson(lost_missing_count > 0)
        + ",\"newly_lost_missing_count\":" + lost_missing_count
        + ",\"newly_waiting_in_depot_vehicles\":" + this.AttributedEventsJson(newly_depot_records, newly_depot_records.len())
        + ",\"newly_waiting_in_depot_vehicle_ids\":" + this.IntArrayJson(newly_depot_vehicle_ids)
        + ",\"newly_waiting_in_depot_truncated\":" + this.BoolJson(depot_missing_count > 0)
        + ",\"newly_waiting_in_depot_missing_count\":" + depot_missing_count
        + ",\"newly_unprofitable_vehicles\":" + this.AttributedEventsJson(newly_unprofitable_records, newly_unprofitable_records.len())
        + ",\"newly_unprofitable_vehicle_ids\":" + this.IntArrayJson(newly_unprofitable_vehicle_ids)
        + ",\"newly_unprofitable_truncated\":" + this.BoolJson(unprofitable_missing_count > 0)
        + ",\"newly_unprofitable_missing_count\":" + unprofitable_missing_count);
}

/* =====================================================================
 * General-action-surface primitives.
 *
 * Every build/manage primitive is legality-only: it executes the model's
 * chosen action and reports the truth (ok, the real AIError string, the real
 * command cost from an AIAccounting scope around the command, and any new
 * entity ids). The cost sign convention is positive = money spent, negative =
 * money received (e.g. sell_vehicle). A bank-balance delta is NOT a command
 * cost: the AI is suspended while the command executes, so concurrent money
 * flows (vehicle income, daily running costs) land inside the window -- a
 * ~150-cost station once reported cost -909 that way. It does NOT
 * consult candidate lists, compute quality scores, or reject a legal move that
 * is not on a pre-approved list. The only rejections are real AIError results
 * and malformed argument shapes.
 * ===================================================================== */

/* Candidate-free per-step observation. It emits only factual world state.
 * The map snapshot (if requested) still follows so the controller can render. */
function McpBridge::EmitGeneralObservation()
{
    this.PollEvents();
    local town_id = this.FindLargestTown();
    local engines = this.FindPassengerEngines(8);
    local passenger_cargo = -1;
    if (engines.len() > 0) passenger_cargo = engines[0][1];
    local town_tile = AITown.GetLocation(town_id);
    local current_date = AIDate.GetCurrentDate();
    local road_vehicle_count = this.CountRoadVehicles();

    local s = "{\"type\":\"observation\",\"surface\":\"general\""
        + ",\"observation_index\":" + this.observation_counter
        + ",\"objective\":\"build a single coherent, verifiable passenger bus route from the blank primitive surface\""
        + ",\"map_size\":[" + AIMap.GetMapSizeX() + "," + AIMap.GetMapSizeY() + "]"
        + ",\"date\":" + this.DateJson(current_date)
        + ",\"bank_balance\":" + AICompany.GetBankBalance(AICompany.COMPANY_SELF)
        + ",\"company\":{\"bank_balance\":" + AICompany.GetBankBalance(AICompany.COMPANY_SELF)
            + ",\"loan_amount\":" + AICompany.GetLoanAmount()
            + ",\"max_loan_amount\":" + AICompany.GetMaxLoanAmount()
            + ",\"quarterly_income_0\":" + AICompany.GetQuarterlyIncome(AICompany.COMPANY_SELF, 0)
            + ",\"quarterly_expenses_0\":" + AICompany.GetQuarterlyExpenses(AICompany.COMPANY_SELF, 0)
            + ",\"road_vehicle_count\":" + road_vehicle_count
        + "}"
        + ",\"largest_town\":{\"town_id\":" + town_id + ",\"name\":\"" + this.JsonEscape(AITown.GetName(town_id)) + "\",\"population\":" + AITown.GetPopulation(town_id) + ",\"x\":" + AIMap.GetTileX(town_tile) + ",\"y\":" + AIMap.GetTileY(town_tile) + "}"
        + ",\"towns\":" + this.TownsJson()
        + ",\"routes\":" + this.RoutesJson()
        + ",\"vehicles\":" + this.VehiclesJson()
        + ",\"stations\":" + this.StationsJson(passenger_cargo)
        + ",\"engine_candidates\":[";
    for (local j = 0; j < engines.len(); j++) {
        local e = engines[j];
        if (j != 0) s += ",";
        s += "{\"engine_id\":" + e[0] + ",\"cargo_type\":" + e[1] + ",\"capacity\":" + e[2] + ",\"price\":" + e[3] + ",\"max_speed\":" + e[4] + ",\"name\":\"" + this.JsonEscape(e[5]) + "\"}";
    }
    s += "]";
    s += ",\"warnings\":{\"vehicle_lost_events\":" + this.vehicle_lost_events + ",\"vehicle_crashed_events\":" + this.vehicle_crashed_events
        + ",\"lost_vehicles\":" + this.AttributedEventsJson(this.lost_events, WARNINGS_EVENT_LOG_CAP)
        + "}";
    s += "}";
    this.EmitBridgeOutput("MCP_OBSERVATION|" + s);
    if (this.ShouldEmitMapSnapshot()) this.EmitMapSnapshot();
    this.observation_counter++;
}

function McpBridge::EmitPrimResult(timestamp, action, ok, err, cost, extra)
{
    this.MaybeSnapshotYearEnd();
    local year_end_extra = "";
    /* A repeat emission under an already-recorded delivery timestamp is a
     * later command of the same batch, not a new delivery: Python reads only
     * the first matching result line, so re-attaching here would count
     * reports as delivered that no reader ever saw. */
    local has_year_end_reports = this.pending_year_end_reports.len() > 0
        && timestamp != this.year_end_delivery_timestamp;
    if (has_year_end_reports) {
        year_end_extra = ",\"year_end_reports\":[";
        for (local i = 0; i < this.pending_year_end_reports.len(); i++) {
            if (i != 0) year_end_extra += ",";
            year_end_extra += this.YearEndReportJson(this.pending_year_end_reports[i]);
        }
        year_end_extra += "]";
    }
    this.EmitBridgeOutput("MCP_RESULT|" + timestamp + "|{\"ok\":" + this.BoolJson(ok)
        + ",\"action\":\"" + action + "\""
        + ",\"error\":\"" + this.JsonEscape(err) + "\""
        + ",\"cost\":" + cost
        + ",\"bank_balance\":" + AICompany.GetBankBalance(AICompany.COMPANY_SELF)
        + extra + year_end_extra + "}");
    if (has_year_end_reports) {
        this.year_end_delivery_timestamp = timestamp;
        this.year_end_delivered_count = this.pending_year_end_reports.len();
        if (this.legacy_clear_year_end_on_emit) {
            this.pending_year_end_reports = [];
            this.year_end_delivery_timestamp = null;
            this.year_end_delivered_count = 0;
        }
    }
}

/* A validated sensor input reaching an unexpected bridge/API failure is not a
 * negative game fact. Fail the whole sensor with a bounded semantic code and
 * the exact affected field; raw Script API names and exception text stay out
 * of the model-visible result. */
function McpBridge::EmitSensorApiError(timestamp, action, code, field, extra)
{
    local sensor_error = ",\"sensor_error\":{\"code\":\"" + this.JsonEscape(code)
        + "\",\"field\":\"" + this.JsonEscape(field) + "\"}";
    this.EmitPrimResult(timestamp, action, false, "sensor_api_error", 0, extra + sensor_error);
}

function McpBridge::PrimGetTile(tile, cargo_id, timestamp)
{
    if (!AIMap.IsValidTile(tile)) {
        this.EmitPrimResult(timestamp, "get_tile", false, "invalid_tile", 0, ",\"tile\":" + tile);
        return;
    }
    if (cargo_id != -1 && !AICargo.IsValidCargo(cargo_id)) {
        this.EmitPrimResult(timestamp, "get_tile", false, "invalid_cargo_id", 0,
            ",\"tile\":" + tile + ",\"cargo_id\":" + cargo_id);
        return;
    }
    local engines = this.FindPassengerEngines(1);
    local passenger_cargo = engines.len() > 0 ? engines[0][1] : -1;
    local largest_town_id = this.FindLargestTown();
    local info = this.TileJson(tile, AIMap.GetTileX(tile), AIMap.GetTileY(tile), passenger_cargo, largest_town_id, cargo_id);
    this.EmitPrimResult(timestamp, "get_tile", true, "", 0, ",\"tile_info\":" + info);
}

function McpBridge::PrimGetRegion(center_tile, radius, cargo_id, timestamp)
{
    if (!AIMap.IsValidTile(center_tile)) {
        this.EmitPrimResult(timestamp, "get_region", false, "invalid_center_tile", 0, ",\"center_tile\":" + center_tile);
        return;
    }
    if (cargo_id != -1 && !AICargo.IsValidCargo(cargo_id)) {
        this.EmitPrimResult(timestamp, "get_region", false, "invalid_cargo_id", 0,
            ",\"center_tile\":" + center_tile + ",\"cargo_id\":" + cargo_id);
        return;
    }
    /* Server-side validation (server.py QUERY_REGION_RADIUS_MAX_BY_DETAIL) is
     * the real gate and is per detail mode, which this layer cannot see. The
     * check here is the widest bound, kept as a rejection: a clamp would survey
     * a different region than the caller asked for and never say so. */
    if (radius < 0 || radius > QUERY_REGION_RADIUS_MAX) {
        this.EmitPrimResult(timestamp, "get_region", false, "radius_must_be_between_0_and_" + QUERY_REGION_RADIUS_MAX, 0, ",\"radius\":" + radius);
        return;
    }
    local engines = this.FindPassengerEngines(1);
    local passenger_cargo = engines.len() > 0 ? engines[0][1] : -1;
    local largest_town_id = this.FindLargestTown();
    local cx = AIMap.GetTileX(center_tile);
    local cy = AIMap.GetTileY(center_tile);
    local max_x = AIMap.GetMapSizeX();
    local max_y = AIMap.GetMapSizeY();
    local road_tiles = 0;
    local buildable_tiles = 0;
    local water_tiles = 0;
    local station_tiles = 0;
    local tile_count = 0;
    local tiles_json = "[";
    local first = true;
    for (local y = cy - radius; y <= cy + radius; y++) {
        if (y < 0 || y >= max_y) continue;
        for (local x = cx - radius; x <= cx + radius; x++) {
            if (x < 0 || x >= max_x) continue;
            local tile = AIMap.GetTileIndex(x, y);
            if (AIRoad.IsRoadTile(tile) || AIRoad.IsRoadStationTile(tile) || AIRoad.IsRoadDepotTile(tile)) road_tiles++;
            if (AITile.IsBuildable(tile)) buildable_tiles++;
            if (AITile.IsWaterTile(tile)) water_tiles++;
            if (AITile.IsStationTile(tile)) station_tiles++;
            if (!first) tiles_json += ",";
            first = false;
            tiles_json += this.TileJson(tile, x, y, passenger_cargo, largest_town_id, cargo_id);
            tile_count++;
        }
    }
    tiles_json += "]";
    this.EmitPrimResult(timestamp, "get_region", true, "", 0,
        ",\"center_tile\":" + center_tile + ",\"center_x\":" + cx + ",\"center_y\":" + cy + ",\"radius\":" + radius
        + ",\"summary\":{\"tile_count\":" + tile_count + ",\"road_tiles\":" + road_tiles + ",\"buildable_tiles\":" + buildable_tiles + ",\"water_tiles\":" + water_tiles + ",\"station_tiles\":" + station_tiles + "}"
        + ",\"tiles\":" + tiles_json);
}

function McpBridge::PrimListTowns(timestamp)
{
    this.EmitPrimResult(timestamp, "list_towns", true, "", 0, ",\"towns\":" + this.TownsJson());
}

function McpBridge::PrimListMyVehicles(timestamp)
{
    this.EmitPrimResult(timestamp, "list_my_vehicles", true, "", 0,
        ",\"needs_replacement_age_left_threshold_days\":" + NEEDS_REPLACEMENT_AGE_LEFT_DAYS + ",\"vehicles\":" + this.VehiclesJson() + ",\"routes\":" + this.RoutesJson() + ",\"groups\":" + this.GroupsJson());
}

function McpBridge::PrimGetVehicle(vehicle_id, timestamp)
{
    if (!AIVehicle.IsValidVehicle(vehicle_id) || AIVehicle.GetVehicleType(vehicle_id) != AIVehicle.VT_ROAD) {
        this.EmitPrimResult(timestamp, "get_vehicle", false, "invalid_road_vehicle_id", 0, ",\"vehicle_id\":" + vehicle_id);
        return;
    }
    this.EmitPrimResult(timestamp, "get_vehicle", true, "", 0,
        ",\"needs_replacement_age_left_threshold_days\":" + NEEDS_REPLACEMENT_AGE_LEFT_DAYS + ",\"vehicle\":" + this.VehicleJson(vehicle_id) + ",\"orders\":" + this.OrderListJson(vehicle_id) + ",\"cargo_capacities\":" + this.CargoCapacitiesJson(vehicle_id));
}

/* Standalone station inventory. StationsJson is the same enumeration the
 * per-step observation carries (both road-stop types, deduped by station_id),
 * and the passenger cargo it needs for waiting/rating is resolved exactly as
 * EmitGeneralObservation resolves it -- from the first buildable bus engine,
 * or -1 when this game offers none. */
function McpBridge::PrimListMyStations(timestamp)
{
    local engines = this.FindPassengerEngines(1);
    local passenger_cargo = -1;
    if (engines.len() > 0) passenger_cargo = engines[0][1];
    this.EmitPrimResult(timestamp, "list_my_stations", true, "", 0, ",\"stations\":" + this.StationsJson(passenger_cargo));
}

function McpBridge::PrimListMyDepots(timestamp)
{
    this.EmitPrimResult(timestamp, "list_my_depots", true, "", 0, ",\"depots\":" + this.DepotsJson());
}

function McpBridge::PrimGetMyFinances(timestamp)
{
    // economy.cpp:817 charges the cache, economy.cpp:898 sets it once; script_gamesettings.cpp:19-31
    // reads config without a filter and already returns -1 for a setting it cannot resolve, so no
    // separate IsValid guard is needed to keep the -1 contract.
    local interest_rate_percent = AIGameSettings.GetValue("difficulty.initial_interest");
    this.EmitPrimResult(timestamp, "get_my_finances", true, "", 0,
        "," + this.CompanyFinanceCoreJson()
        + ",\"interest_rate_percent\":" + interest_rate_percent
        + ",\"date\":" + this.DateJson(AIDate.GetCurrentDate())
        + ",\"quarterly_income_0\":" + AICompany.GetQuarterlyIncome(AICompany.COMPANY_SELF, 0)
        + ",\"quarterly_expenses_0\":" + AICompany.GetQuarterlyExpenses(AICompany.COMPANY_SELF, 0)
        + ",\"quarterly_income_1\":" + AICompany.GetQuarterlyIncome(AICompany.COMPANY_SELF, 1)
        + ",\"quarterly_expenses_1\":" + AICompany.GetQuarterlyExpenses(AICompany.COMPANY_SELF, 1));
}

function McpBridge::PrimGetMyCompany(timestamp)
{
    local vehicle_count = 0;
    local vehicles = AIVehicleList();
    foreach (vehicle_id, value in vehicles) vehicle_count++;
    local road_vehicle_count = this.CountRoadVehicles();
    local company_id = AICompany.ResolveCompanyID(AICompany.COMPANY_SELF);
    local current_quarter = AICompany.CURRENT_QUARTER;
    local earliest_quarter = AICompany.EARLIEST_QUARTER;
    // Finance facts come from the shared fragment; the current-quarter company
    // value is not re-emitted at the top level because it is byte-identical to
    // quarters[0].company_value (current_quarter is CURRENT_QUARTER == 0, the
    // first quarter row) -- one emitter, no drift between the two.
    this.EmitPrimResult(timestamp, "get_my_company", true, "", 0,
        ",\"date\":" + this.DateJson(AIDate.GetCurrentDate())
        + ",\"company_id\":" + company_id
        + ",\"company_name\":\"" + this.JsonEscape(AICompany.GetName(AICompany.COMPANY_SELF)) + "\""
        + "," + this.CompanyFinanceCoreJson()
        + ",\"vehicle_count\":" + vehicle_count
        + ",\"road_vehicle_count\":" + road_vehicle_count
        + ",\"current_quarter\":" + current_quarter
        + ",\"earliest_quarter\":" + earliest_quarter
        + ",\"quarters\":" + this.CompanyQuarterReportsJson());
}

function McpBridge::PrimListMyRouteEconomics(timestamp)
{
    this.EmitPrimResult(timestamp, "list_my_route_economics", true, "", 0,
        ",\"date\":" + this.DateJson(AIDate.GetCurrentDate())
        + ",\"signature_semantics\":\"unique valid road-station order tiles; exactly two-stop signatures are sorted as an unordered pair; zero/one-stop vehicles are grouped under their exact signature\""
        + ",\"road_vehicle_count\":" + this.CountRoadVehicles()
        + ",\"route_groups\":" + this.RouteEconomicsJson());
}

/* Factual cargo payment-rate sensor. The OpenTTD 15.1 source's
 * script_cargo.hpp documents distance as Manhattan tiles and
 * transit time as economy-days. script_cargo.cpp calls
 * GetTransportedGoodsIncome for exactly one piece, clamps distance to uint32,
 * converts economy-days to transit periods, and returns -1 for invalid cargo.
 * The MCP server validates documented bounds before dispatch; this bridge guard
 * distinguishes an unavailable cargo slot from a possibly negative NewGRF
 * profit callback. Source-inspected and live/reload-verified on OpenTTD 15.1. */
function McpBridge::PrimGetCargoPaymentRate(cargo_id, distance, days_in_transit, timestamp)
{
    if (!AICargo.IsValidCargo(cargo_id)) {
        this.EmitPrimResult(timestamp, "get_cargo_payment_rate", false, "invalid_cargo_id", 0,
            ",\"cargo_id\":" + cargo_id + ",\"distance\":" + distance + ",\"days_in_transit\":" + days_in_transit);
        return;
    }
    if (distance < 0 || distance > 4294967295) {
        this.EmitPrimResult(timestamp, "get_cargo_payment_rate", false, "distance_out_of_range", 0,
            ",\"cargo_id\":" + cargo_id + ",\"distance\":" + distance + ",\"days_in_transit\":" + days_in_transit);
        return;
    }
    if (days_in_transit < 0 || days_in_transit > 163838) {
        this.EmitPrimResult(timestamp, "get_cargo_payment_rate", false, "days_in_transit_out_of_range", 0,
            ",\"cargo_id\":" + cargo_id + ",\"distance\":" + distance + ",\"days_in_transit\":" + days_in_transit);
        return;
    }

    local cargo_name = AICargo.GetName(cargo_id);
    if (cargo_name == null) cargo_name = "";
    local cargo_label = AICargo.GetCargoLabel(cargo_id);
    if (cargo_label == null) cargo_label = "";
    local income = AICargo.GetCargoIncome(cargo_id, distance, days_in_transit);
    this.EmitPrimResult(timestamp, "get_cargo_payment_rate", true, "", 0,
        ",\"date\":" + this.DateJson(AIDate.GetCurrentDate())
        + ",\"cargo_id\":" + cargo_id
        + ",\"cargo_name\":\"" + this.JsonEscape(cargo_name) + "\""
        + ",\"cargo_label\":\"" + this.JsonEscape(cargo_label) + "\""
        + ",\"cargo_amount\":1"
        + ",\"distance\":" + distance
        + ",\"days_in_transit\":" + days_in_transit
        + ",\"income\":" + income);
}

/* Honest engine catalog. Enumerates every AIEngineList(VT_ROAD)
 * entry -- no cap, no bus-only pre-filter, no sorting by desirability. The two
 * filters are factual predicates the model chooses. AIEngineList only contains
 * engines the game currently offers, so engines not yet introduced at the
 * current date are absent (probed). The date rides along so buildability is anchored in time. */
function McpBridge::PrimListEngines(only_buildable, passenger_only, timestamp)
{
    local engines = AIEngineList(AIVehicle.VT_ROAD);
    local listed = 0;
    local json = "[";
    foreach (engine_id, _ in engines) {
        local buildable = AIEngine.IsBuildable(engine_id);
        if (only_buildable && !buildable) continue;
        local cargo = AIEngine.GetCargoType(engine_id);
        local is_passenger = AIRoad.GetRoadVehicleTypeForCargo(cargo) == AIRoad.ROADVEHTYPE_BUS;
        if (passenger_only && !is_passenger) continue;
        /* Invalid cargo returns a null label (probed); report "" rather than fake a name. */
        local label = AICargo.GetCargoLabel(cargo);
        if (label == null) label = "";
        if (listed > 0) json += ",";
        json += "{\"engine_id\":" + engine_id
            + ",\"name\":\"" + this.JsonEscape(AIEngine.GetName(engine_id)) + "\""
            + ",\"buildable\":" + this.BoolJson(buildable)
            + ",\"price\":" + AIEngine.GetPrice(engine_id)
            + ",\"running_cost\":" + AIEngine.GetRunningCost(engine_id)
            + ",\"capacity\":" + AIEngine.GetCapacity(engine_id)
            + ",\"cargo_type\":" + cargo
            + ",\"cargo_label\":\"" + this.JsonEscape(label) + "\""
            + ",\"is_passenger\":" + this.BoolJson(is_passenger)
            + ",\"max_speed\":" + AIEngine.GetMaxSpeed(engine_id)
            + ",\"reliability\":" + AIEngine.GetReliability(engine_id)
            + ",\"design_date\":" + this.DateJson(AIEngine.GetDesignDate(engine_id))
            + ",\"max_age_days\":" + AIEngine.GetMaxAge(engine_id)
            + "}";
        listed++;
    }
    json += "]";
    this.EmitPrimResult(timestamp, "list_engines", true, "", 0,
        ",\"date\":" + this.DateJson(AIDate.GetCurrentDate())
        + ",\"vehicle_type\":\"road\""
        + ",\"only_buildable\":" + this.BoolJson(only_buildable)
        + ",\"passenger_only\":" + this.BoolJson(passenger_only)
        + ",\"engine_count\":" + listed
        + ",\"engines\":" + json);
}

/* Factual bridge catalog. The model supplies the bridge span
 * length, and the result enumerates the bridge IDs OpenTTD says can be built
 * for that length. It does not sort by quality or choose a type; build_bridge
 * requires the model to pass one bridge_id explicitly. */
function McpBridge::PrimListBridgeTypes(length, timestamp)
{
    if (length < 2) {
        this.EmitPrimResult(timestamp, "list_bridge_types", false, "length_must_be_at_least_2", 0, ",\"length\":" + length);
        return;
    }
    local bridges = AIBridgeList_Length(length);
    local listed = 0;
    local json = "[";
    foreach (bridge_id, _ in bridges) {
        if (listed > 0) json += ",";
        json += "{\"bridge_id\":" + bridge_id
            + ",\"max_speed\":" + AIBridge.GetMaxSpeed(bridge_id)
            + ",\"price\":" + AIBridge.GetPrice(bridge_id, length)
            + ",\"min_length\":" + AIBridge.GetMinLength(bridge_id)
            + ",\"max_length\":" + AIBridge.GetMaxLength(bridge_id)
            + "}";
        listed++;
    }
    json += "]";
    this.EmitPrimResult(timestamp, "list_bridge_types", true, "", 0,
        ",\"date\":" + this.DateJson(AIDate.GetCurrentDate())
        + ",\"vehicle_type\":\"road\""
        + ",\"length\":" + length
        + ",\"bridge_type_count\":" + listed
        + ",\"bridge_types\":" + json);
}

function McpBridge::PrimListIndustries(timestamp)
{
    this.EmitPrimResult(timestamp, "list_industries", true, "", 0,
        ",\"date\":" + this.DateJson(AIDate.GetCurrentDate())
        + ",\"industries\":" + this.IndustriesJson());
}

function McpBridge::PrimListSubsidies(timestamp)
{
    local company_id = AICompany.ResolveCompanyID(AICompany.COMPANY_SELF);
    local subsidies = this.SubsidiesJson(company_id);
    this.EmitPrimResult(timestamp, "list_subsidies", true, "", 0,
        ",\"company_id\":" + company_id
        + ",\"date\":" + this.DateJson(AIDate.GetCurrentDate())
        + ",\"subsidy_count\":" + subsidies.count
        + ",\"subsidies\":" + subsidies.json);
}

function McpBridge::PrimGetIndustry(industry_id, timestamp)
{
    if (!AIIndustry.IsValidIndustry(industry_id)) {
        this.EmitPrimResult(timestamp, "get_industry", false, "invalid_industry_id", 0, ",\"industry_id\":" + industry_id);
        return;
    }
    local tile = AIIndustry.GetLocation(industry_id);
    local x = -1;
    local y = -1;
    if (AIMap.IsValidTile(tile)) { x = AIMap.GetTileX(tile); y = AIMap.GetTileY(tile); }
    local industry_type = AIIndustry.GetIndustryType(industry_id);
    local industry_type_name = "";
    try {
        industry_type_name = "" + AIIndustryType.GetName(industry_type);
    } catch (e) {
        this.EmitSensorApiError(timestamp, "get_industry", "industry_type_name_unavailable",
            "industry_type_name", ",\"industry_id\":" + industry_id);
        return;
    }
    local built_on_water = false;
    try {
        built_on_water = AIIndustry.IsBuiltOnWater(industry_id);
    } catch (e2) {
        this.EmitSensorApiError(timestamp, "get_industry", "industry_built_on_water_unavailable",
            "built_on_water", ",\"industry_id\":" + industry_id);
        return;
    }
    local stations_around = -1;
    try {
        stations_around = AIIndustry.GetAmountOfStationsAround(industry_id);
    } catch (e3) {
        this.EmitSensorApiError(timestamp, "get_industry", "stations_around_unavailable",
            "stations_around", ",\"industry_id\":" + industry_id);
        return;
    }
    local fact_errors = [];
    local produced_cargo = this.CargoListJson(
        AIIndustryType.GetProducedCargo(industry_type), industry_id, true, fact_errors);
    local accepted_cargo = this.CargoListJson(
        AIIndustryType.GetAcceptedCargo(industry_type), industry_id, false, fact_errors);
    if (fact_errors.len() > 0) {
        this.EmitSensorApiError(timestamp, "get_industry", fact_errors[0][0],
            fact_errors[0][1], ",\"industry_id\":" + industry_id);
        return;
    }
    this.EmitPrimResult(timestamp, "get_industry", true, "", 0,
        ",\"date\":" + this.DateJson(AIDate.GetCurrentDate())
        + ",\"industry_id\":" + industry_id
        + ",\"name\":\"" + this.JsonEscape("" + AIIndustry.GetName(industry_id)) + "\""
        + ",\"industry_type\":" + industry_type
        + ",\"industry_type_name\":\"" + this.JsonEscape(industry_type_name) + "\""
        + ",\"tile\":" + tile
        + ",\"x\":" + x
        + ",\"y\":" + y
        + ",\"produced_cargo\":" + produced_cargo
        + ",\"accepted_cargo\":" + accepted_cargo
        + ",\"built_on_water\":" + this.BoolJson(built_on_water)
        + ",\"stations_around\":" + stations_around);
}

/* Factual town cargo-statistics sensor. list_towns reports a
 * town's identity, population, and location; nothing on the surface reported
 * what a town produces, how much of it reaches station queues, or what it
 * consumes for growth. The two arrays come from the helpers above:
 * cargo_stats (per cargo, AICargoList-scanned) and growth_cargo (per
 * TownEffect). Envelope fields repeat the TownsJson meanings exactly, so a
 * town_id from list_towns reads the same population and x/y here. Facts only:
 * no verdicts, thresholds, or advice. */
function McpBridge::PrimGetTown(town_id, timestamp)
{
    if (!AITown.IsValidTown(town_id)) {
        this.EmitPrimResult(timestamp, "get_town", false, "invalid_town_id", 0, ",\"town_id\":" + town_id);
        return;
    }
    local tile = AITown.GetLocation(town_id);
    local x = -1;
    local y = -1;
    if (AIMap.IsValidTile(tile)) { x = AIMap.GetTileX(tile); y = AIMap.GetTileY(tile); }
    local growth_cargo = this.TownGrowthCargoJson(town_id);
    if (!growth_cargo.ok) {
        this.EmitSensorApiError(timestamp, "get_town", growth_cargo.code,
            growth_cargo.field, ",\"town_id\":" + town_id);
        return;
    }
    this.EmitPrimResult(timestamp, "get_town", true, "", 0,
        ",\"date\":" + this.DateJson(AIDate.GetCurrentDate())
        + ",\"town_id\":" + town_id
        + ",\"name\":\"" + this.JsonEscape("" + AITown.GetName(town_id)) + "\""
        + ",\"population\":" + AITown.GetPopulation(town_id)
        + ",\"x\":" + x
        + ",\"y\":" + y
        + ",\"cargo_stats\":" + this.TownCargoStatsJson(town_id)
        + ",\"growth_cargo\":" + growth_cargo.json);
}

/* Factual station cargo-state sensor. Without it the model had no way to
 * observe that a stop accepted ~0 passengers and had to infer it from vehicle
 * cargo_load staying full. Every field below was live-probed on the installed
 * OpenTTD 15.1 runtime:
 *   - AIStation.GetCargoWaiting / HasCargoRating / GetCargoRating (rating -1
 *     when the game tracks none, matching the StationsJson convention);
 *   - AICargoList_StationAccepting = the station's own current acceptance
 *     (probed: a bus-stop-only station never accepts non-passenger cargo,
 *     so this can factually differ from raw tile acceptance);
 *   - AITile.GetCargoAcceptance(location, cargo, 1, 1, coverage_radius) = the
 *     catchment acceptance around the station's location tile (exact for a
 *     single-tile stop; bus_stop_tiles/truck_stop_tiles show the station's
 *     road-stop tiles per type — a joined station can hold several bays);
 *   - AITileList_StationType, AIVehicleList_Station, AIStation.GetNearestTown,
 *     AIStation.GetCoverageRadius, AIStation.HasStationType.
 * AIStation.IsValidStation resolves only stations owned by this company
 * (script_station.hpp) -- a documented scope limitation, stated in the tool
 * description. Facts only: no verdicts, thresholds, or advice.
 *
 * Further fields, cited against the OpenTTD 15.1 source:
 *   - AIStation.GetCargoPlanned (script_station.hpp:129) is NOT emitted: it
 *     reads the FlowStatMap (script_station.cpp:106-115), which only
 *     LinkGraphJob::FinaliseJob writes (linkgraphjob.cpp:124-125), and that
 *     never runs under DistributionType::Manual — the linkgraph distribution
 *     default this project's games never override. Live-probed both ways
 *     (always 0 under Manual beside waiting:28; 72 under a Symmetric
 *     control), so the field would read 0 in every episode and a
 *     perpetual 0 beside a real waiting count invites a false no-demand
 *     reading. Revisit only if the distribution settings change.
 *   - AIBaseStation.GetConstructionDate(sid) (script_basestation.hpp:81, impl
 *     script_basestation.cpp:69-74) returns BaseStation::build_date, which
 *     Station::AddFacility rewrites on every added part (station.cpp:241,
 *     called per road-stop tile at station_cmd.cpp:2183) -- so it is the last
 *     date a part was added, not necessarily the first-built date.
 *   - vehicles_loading is DERIVED, not a game-reported occupancy field: the
 *     order-scoped AIVehicleList_Station candidates (ScriptVehicleList_Station
 *     calls FindVehiclesWithOrder, script_vehiclelist.cpp:65-66) filtered by
 *     AIVehicle.GetState == VS_AT_STATION ("stopped at a station and is
 *     currently loading or unloading", script_vehicle.hpp:91) and by
 *     AIVehicle.GetLocation (script_vehicle.hpp:161) being one of this
 *     station's own stop tiles, so a vehicle loading at another station on the
 *     same orders is excluded. Bay occupancy itself (RoadStop::status,
 *     roadstop_base.h:37) has no script wrapper, and a queued-but-not-loading
 *     count is not derivable at all -- neither ships. */
function McpBridge::PrimGetMyStation(station_id, tile, timestamp)
{
    try {
        this.PrimGetMyStationFacts(station_id, tile, timestamp);
    } catch (sensor_error) {
        this.EmitSensorApiError(timestamp, "get_my_station", "station_sensor_unavailable", "sensor",
            ",\"station_id\":" + station_id + ",\"tile\":" + tile);
        return;
    }
}

function McpBridge::PrimGetMyStationFacts(station_id, tile, timestamp)
{
    local sid = station_id;
    if (tile != -1) {
        if (!AIMap.IsValidTile(tile)) {
            this.EmitPrimResult(timestamp, "get_my_station", false, "invalid_tile", 0, ",\"tile\":" + tile);
            return;
        }
        sid = AIStation.GetStationID(tile);
        if (!AIStation.IsValidStation(sid)) {
            this.EmitPrimResult(timestamp, "get_my_station", false, "no_station_at_tile", 0, ",\"tile\":" + tile);
            return;
        }
    }
    if (!AIStation.IsValidStation(sid)) {
        this.EmitPrimResult(timestamp, "get_my_station", false, "invalid_station_id", 0, ",\"station_id\":" + sid);
        return;
    }
    local location = AIStation.GetLocation(sid);
    local is_bus_stop = false;
    local is_truck_stop = false;
    try {
        is_bus_stop = AIStation.HasStationType(sid, AIStation.STATION_BUS_STOP);
    } catch (e) {
        this.EmitSensorApiError(timestamp, "get_my_station", "station_type_unavailable", "is_bus_stop",
            ",\"station_id\":" + sid);
        return;
    }
    try {
        is_truck_stop = AIStation.HasStationType(sid, AIStation.STATION_TRUCK_STOP);
    } catch (e2) {
        this.EmitSensorApiError(timestamp, "get_my_station", "station_type_unavailable", "is_truck_stop",
            ",\"station_id\":" + sid);
        return;
    }
    local nearest_town = -1;
    try {
        nearest_town = AIStation.GetNearestTown(sid);
    } catch (e3) {
        this.EmitSensorApiError(timestamp, "get_my_station", "nearest_town_unavailable", "nearest_town_id",
            ",\"station_id\":" + sid);
        return;
    }
    local coverage_radius = AIStation.GetCoverageRadius(AIStation.STATION_BUS_STOP);

    /* Join-capable build: stations can hold multiple road-stop bays,
     * and the bus-stop list never contains truck bays (probed: a truck-only
     * multi-bay station reads bus_stop_tiles []). Report both bay types
     * through the same probed tile-list API. */
    local bus_stop_tile_result = this.StationTypeTilesList(sid, AIStation.STATION_BUS_STOP);
    if (!bus_stop_tile_result.ok) {
        this.EmitSensorApiError(timestamp, "get_my_station", "station_tiles_unavailable", "bus_stop_tiles",
            ",\"station_id\":" + sid);
        return;
    }
    local truck_stop_tile_result = this.StationTypeTilesList(sid, AIStation.STATION_TRUCK_STOP);
    if (!truck_stop_tile_result.ok) {
        this.EmitSensorApiError(timestamp, "get_my_station", "station_tiles_unavailable", "truck_stop_tiles",
            ",\"station_id\":" + sid);
        return;
    }
    local bus_stop_tile_list = bus_stop_tile_result.tiles;
    local truck_stop_tile_list = truck_stop_tile_result.tiles;
    local bus_stop_tiles = this.IntArrayJson(bus_stop_tile_list);
    local truck_stop_tiles = this.IntArrayJson(truck_stop_tile_list);

    /* The station's own stop tiles, reused as the membership test below. */
    local stop_tiles = [];
    foreach (t in bus_stop_tile_list) this.AppendUnique(stop_tiles, t);
    foreach (t2 in truck_stop_tile_list) this.AppendUnique(stop_tiles, t2);

    local construction_date = -1;
    try {
        construction_date = AIBaseStation.GetConstructionDate(sid);
    } catch (e4) {
        this.EmitSensorApiError(timestamp, "get_my_station", "construction_date_unavailable", "construction_date",
            ",\"station_id\":" + sid);
        return;
    }

    local vehicle_ids = "[";
    local vehicle_count = 0;
    local vehicles_loading = 0;
    try {
        local vl = AIVehicleList_Station(sid);
        foreach (v, _ in vl) {
            if (vehicle_count > 0) vehicle_ids += ",";
            vehicle_ids += v;
            vehicle_count++;
            /* Derived: an order-scoped candidate counts only when the game says
             * it is loading/unloading AND it stands on one of this station's own
             * stop tiles. */
            try {
                if (AIVehicle.GetState(v) == AIVehicle.VS_AT_STATION
                    && this.Contains(stop_tiles, AIVehicle.GetLocation(v))) {
                    vehicles_loading++;
                }
            } catch (e8) {
                this.EmitSensorApiError(timestamp, "get_my_station", "vehicle_loading_unavailable",
                    "vehicles_loading", ",\"station_id\":" + sid + ",\"vehicle_id\":" + v);
                return;
            }
        }
    } catch (e5) {
        this.EmitSensorApiError(timestamp, "get_my_station", "vehicle_list_unavailable", "vehicle_ids",
            ",\"station_id\":" + sid);
        return;
    }
    vehicle_ids += "]";

    local accepting = {};
    try {
        local al = AICargoList_StationAccepting(sid);
        foreach (c, _ in al) accepting[c] <- true;
    } catch (e6) {
        this.EmitSensorApiError(timestamp, "get_my_station", "station_acceptance_unavailable", "cargo.accepted",
            ",\"station_id\":" + sid);
        return;
    }

    local cargo_json = "[";
    local first_cargo = true;
    local cargoes = AICargoList();
    foreach (cargo, _ in cargoes) {
        local waiting = AIStation.GetCargoWaiting(sid, cargo);
        local has_rating = AIStation.HasCargoRating(sid, cargo);
        local rating = has_rating ? AIStation.GetCargoRating(sid, cargo) : -1;
        local acceptance = -1;
        try {
            acceptance = AITile.GetCargoAcceptance(location, cargo, 1, 1, coverage_radius);
        } catch (e7) {
            this.EmitSensorApiError(timestamp, "get_my_station", "cargo_acceptance_unavailable",
                "cargo.acceptance_around_location", ",\"station_id\":" + sid + ",\"cargo_id\":" + cargo);
            return;
        }
        /* Invalid cargo returns a null label (probed); report "" rather than fake a name. */
        local label = AICargo.GetCargoLabel(cargo);
        if (label == null) label = "";
        if (!first_cargo) cargo_json += ",";
        first_cargo = false;
        cargo_json += "{\"cargo_type\":" + cargo
            + ",\"cargo_label\":\"" + this.JsonEscape(label) + "\""
            + ",\"waiting\":" + waiting
            + ",\"has_rating\":" + this.BoolJson(has_rating)
            + ",\"rating\":" + rating
            + ",\"accepted\":" + this.BoolJson(accepting.rawin(cargo))
            + ",\"acceptance_around_location\":" + acceptance
            + "}";
    }
    cargo_json += "]";

    this.EmitPrimResult(timestamp, "get_my_station", true, "", 0,
        ",\"date\":" + this.DateJson(AIDate.GetCurrentDate())
        + ",\"station_id\":" + sid
        + ",\"name\":\"" + this.JsonEscape("" + AIStation.GetName(sid)) + "\""
        + ",\"tile\":" + location
        + ",\"x\":" + AIMap.GetTileX(location)
        + ",\"y\":" + AIMap.GetTileY(location)
        + ",\"is_bus_stop\":" + this.BoolJson(is_bus_stop)
        + ",\"is_truck_stop\":" + this.BoolJson(is_truck_stop)
        + ",\"nearest_town_id\":" + nearest_town
        + ",\"construction_date\":" + this.DateJson(construction_date)
        + ",\"bus_stop_coverage_radius\":" + coverage_radius
        + ",\"bus_stop_tiles\":" + bus_stop_tiles
        + ",\"truck_stop_tiles\":" + truck_stop_tiles
        + ",\"vehicle_count\":" + vehicle_count
        + ",\"vehicle_ids\":" + vehicle_ids
        + ",\"vehicles_loading\":" + vehicles_loading
        + ",\"cargo\":" + cargo_json);
}

/* Producer/acceptor catchment sensor. Farm/coal pickup stops with
 * cargo_production_radius4=1 have registered no cargo or rating for months, so
 * the raw radius4 scalar does not predict attachment. Every API below was
 * live-probed on OpenTTD 15.1:
 *   - AIStation.GetCoverageRadius(AIStation.STATION_TRUCK_STOP) = 3, echoed as
 *     truck_coverage_radius and used as the radius for the tile-list predicates;
 *   - AITileList_IndustryProducing(industry_id, radius) / -Accepting = the
 *     game's own attach predicate: tile T is a producer/acceptor of industry I
 *     iff T is a member of I's producing/accepting list at the truck radius.
 *     Witnessed: a member stop tile accrued waiting+rating under service; a
 *     non-member trap stop tile with the same cargo_production_radius4=1
 *     stayed dead. Scalar production is NOT equivalent to membership (12
 *     scalar-positive tiles on the farm's own footprint are non-members).
 *   - AIIndustry.GetAmountOfStationsAround(industry_id) counts only attaching
 *     stations (probed 0 -> 1 member -> still 1 after a non-attaching trap -> 2).
 * The tile is the model's; the sensor enumerates AIIndustryList() and reports the
 * game's catchment fact for one site, exactly like PrimGetMyStation reports facts
 * for one station. An invalid industry id yields an empty list (probed), and the
 * IsValidIndustry guard keeps stale ids out. Facts only: no verdicts or advice. */
function McpBridge::PrimCheckTruckStopCatchment(tile, timestamp)
{
    try {
        this.PrimCheckTruckStopCatchmentFacts(tile, timestamp);
    } catch (sensor_error) {
        this.EmitSensorApiError(timestamp, "check_truck_stop_catchment", "truck_catchment_sensor_unavailable",
            "sensor", ",\"tile\":" + tile);
        return;
    }
}

function McpBridge::PrimCheckTruckStopCatchmentFacts(tile, timestamp)
{
    if (!AIMap.IsValidTile(tile)) {
        this.EmitPrimResult(timestamp, "check_truck_stop_catchment", false, "invalid_tile", 0, ",\"tile\":" + tile);
        return;
    }
    local truck_radius = AIStation.GetCoverageRadius(AIStation.STATION_TRUCK_STOP);
    local town_cargo_acceptance = this.TownCargoAcceptanceJson(tile, truck_radius);
    if (!town_cargo_acceptance.ok) {
        this.EmitSensorApiError(timestamp, "check_truck_stop_catchment",
            town_cargo_acceptance.code, town_cargo_acceptance.field,
            ",\"tile\":" + tile);
        return;
    }

    local producers = "[";
    local first_producer = true;
    local acceptors = "[";
    local first_acceptor = true;
    local industries = AIIndustryList();
    foreach (industry_id, value in industries) {
        if (!AIIndustry.IsValidIndustry(industry_id)) continue;
        /* Producer side: is the model's tile a member of this industry's
         * producing list at the truck coverage radius? */
        local is_producer = false;
        try {
            local pl = AITileList_IndustryProducing(industry_id, truck_radius);
            foreach (member_tile, member_value in pl) {
                if (member_tile == tile) { is_producer = true; break; }
            }
        } catch (e_producing) {
            this.EmitSensorApiError(timestamp, "check_truck_stop_catchment", "producer_catchment_unavailable",
                "producers", ",\"tile\":" + tile + ",\"industry_id\":" + industry_id);
            return;
        }
        if (is_producer) {
            local producer_row = this.IndustryCatchmentRowJson(industry_id, true);
            if (!producer_row.ok) {
                this.EmitSensorApiError(timestamp, "check_truck_stop_catchment", producer_row.code,
                    producer_row.field,
                    ",\"tile\":" + tile + ",\"industry_id\":" + industry_id);
                return;
            }
            if (!first_producer) producers += ",";
            first_producer = false;
            producers += producer_row.json;
        }
        /* Acceptor side: same predicate against the accepting list. */
        local is_acceptor = false;
        try {
            local al = AITileList_IndustryAccepting(industry_id, truck_radius);
            foreach (member_tile2, member_value2 in al) {
                if (member_tile2 == tile) { is_acceptor = true; break; }
            }
        } catch (e_accepting) {
            this.EmitSensorApiError(timestamp, "check_truck_stop_catchment", "acceptor_catchment_unavailable",
                "acceptors", ",\"tile\":" + tile + ",\"industry_id\":" + industry_id);
            return;
        }
        if (is_acceptor) {
            local acceptor_row = this.IndustryCatchmentRowJson(industry_id, false);
            if (!acceptor_row.ok) {
                this.EmitSensorApiError(timestamp, "check_truck_stop_catchment", acceptor_row.code,
                    acceptor_row.field,
                    ",\"tile\":" + tile + ",\"industry_id\":" + industry_id);
                return;
            }
            if (!first_acceptor) acceptors += ",";
            first_acceptor = false;
            acceptors += acceptor_row.json;
        }
    }
    producers += "]";
    acceptors += "]";

    this.EmitPrimResult(timestamp, "check_truck_stop_catchment", true, "", 0,
        ",\"date\":" + this.DateJson(AIDate.GetCurrentDate())
        + ",\"tile\":" + tile
        + ",\"x\":" + AIMap.GetTileX(tile)
        + ",\"y\":" + AIMap.GetTileY(tile)
        + ",\"truck_coverage_radius\":" + truck_radius
        + ",\"town_cargo_acceptance\":" + town_cargo_acceptance.json
        + ",\"producers\":" + producers
        + ",\"acceptors\":" + acceptors);
}

/* One industry row for check_truck_stop_catchment. produced_side=true carries produced_cargo
 * (pickup); false carries accepted_cargo (delivery). Reuses the same cargo-row and
 * industry-identity helpers as list_industries/get_industry. stations_around is
 * AIIndustry.GetAmountOfStationsAround (a competition fact; counts only attaching
 * stations). Returns { ok, code, field, json }: on an unexpected API failure ok is
 * false and code/field name the sensor error for the caller to emit, so a caller
 * checks ok before reading json. */
function McpBridge::IndustryCatchmentRowJson(industry_id, produced_side)
{
    local location = AIIndustry.GetLocation(industry_id);
    local x = -1;
    local y = -1;
    if (AIMap.IsValidTile(location)) { x = AIMap.GetTileX(location); y = AIMap.GetTileY(location); }
    local industry_type = AIIndustry.GetIndustryType(industry_id);
    local industry_type_name = "";
    try {
        industry_type_name = "" + AIIndustryType.GetName(industry_type);
    } catch (e) {
        return {
            ok = false,
            code = "industry_type_name_unavailable",
            field = "industry_type_name",
            json = ""
        };
    }
    local stations_around = -1;
    try {
        stations_around = AIIndustry.GetAmountOfStationsAround(industry_id);
    } catch (e2) {
        return {
            ok = false,
            code = "stations_around_unavailable",
            field = "stations_around",
            json = ""
        };
    }
    local s = "{\"industry_id\":" + industry_id
        + ",\"name\":\"" + this.JsonEscape("" + AIIndustry.GetName(industry_id)) + "\""
        + ",\"industry_type\":" + industry_type
        + ",\"industry_type_name\":\"" + this.JsonEscape(industry_type_name) + "\""
        + ",\"tile\":" + location
        + ",\"x\":" + x
        + ",\"y\":" + y
        + ",\"stations_around\":" + stations_around;
    local fact_errors = [];
    if (produced_side) {
        s += ",\"produced_cargo\":" + this.CargoListJson(
            AIIndustryType.GetProducedCargo(industry_type), industry_id, true, fact_errors);
    } else {
        s += ",\"accepted_cargo\":" + this.CargoListJson(
            AIIndustryType.GetAcceptedCargo(industry_type), industry_id, false, fact_errors);
    }
    if (fact_errors.len() > 0) {
        return {
            ok = false,
            code = fact_errors[0][0],
            field = fact_errors[0][1],
            json = ""
        };
    }
    s += "}";
    return { ok = true, code = "", field = "", json = s };
}

/* Mechanical road builder between two model-chosen endpoints. The bridge only
 * lays the path; the endpoints are the model's. For an off-axis pair we route
 * through the Manhattan corner (L-shape) -- a deliberately simple in-tree
 * fallback because there is no in-tree road pathfinder. Limitation noted in the
 * run summary (no silent caps). Returns a table: err = "" on success or the
 * real AIError, and on failure span_from/span_to = the exact straight
 * AIRoad.BuildRoad span that failed (for an off-axis pair, the leg through
 * the Manhattan corner). */
function McpBridge::BuildRoadSegment(from_tile, to_tile)
{
    if (!AIMap.IsValidTile(from_tile) || !AIMap.IsValidTile(to_tile)) {
        return { err = "invalid_segment_tile", span_from = -1, span_to = -1 };
    }
    if (from_tile == to_tile) return { err = "", span_from = -1, span_to = -1 };
    local fx = AIMap.GetTileX(from_tile);
    local fy = AIMap.GetTileY(from_tile);
    local tx = AIMap.GetTileX(to_tile);
    local ty = AIMap.GetTileY(to_tile);
    if (fx == tx || fy == ty) {
        if (!AIRoad.BuildRoad(from_tile, to_tile)) {
            if (AIError.GetLastError() != AIError.ERR_ALREADY_BUILT) {
                return { err = AIError.GetLastErrorString(), span_from = from_tile, span_to = to_tile };
            }
        }
        return { err = "", span_from = -1, span_to = -1 };
    }
    local corner = AIMap.GetTileIndex(tx, fy);
    if (!AIRoad.BuildRoad(from_tile, corner)) {
        if (AIError.GetLastError() != AIError.ERR_ALREADY_BUILT) {
            return { err = AIError.GetLastErrorString(), span_from = from_tile, span_to = corner };
        }
    }
    if (!AIRoad.BuildRoad(corner, to_tile)) {
        if (AIError.GetLastError() != AIError.ERR_ALREADY_BUILT) {
            return { err = AIError.GetLastErrorString(), span_from = corner, span_to = to_tile };
        }
    }
    return { err = "", span_from = -1, span_to = -1 };
}

/* Read-only costing for the exact road spans BuildRoadSegment would attempt.
 * AITestMode + AIAccounting is probe-proven on OpenTTD 15.1: the dry-run
 * reports the same clear-land cost as a following AIExecMode build while leaving cash, date, and road tiles unchanged.
 * This is a factual sensor only -- no endpoint selection or corridor ranking. */
function McpBridge::EstimateRoadSpan(span_from, span_to)
{
    local ok = true;
    local err = "";
    local error_code = 0;
    local cost = 0;
    {
        local test = AITestMode();
        local accounting = AIAccounting();
        if (!AIRoad.BuildRoad(span_from, span_to)) {
            error_code = AIError.GetLastError();
            if (error_code == AIError.ERR_ALREADY_BUILT) {
                error_code = 0;
            } else {
                ok = false;
                err = AIError.GetLastErrorString();
            }
        }
        cost = accounting.GetCosts();
    }
    return { ok = ok, err = err, error_code = error_code, cost = cost, span_from = span_from, span_to = span_to };
}

function McpBridge::EstimateRoadSegment(from_tile, to_tile)
{
    if (!AIMap.IsValidTile(from_tile) || !AIMap.IsValidTile(to_tile)) {
        return { ok = false, err = "invalid_segment_tile", error_code = -1, cost = 0, span_from = -1, span_to = -1, spans = [] };
    }
    if (from_tile == to_tile) {
        return { ok = true, err = "", error_code = 0, cost = 0, span_from = -1, span_to = -1, spans = [] };
    }
    local fx = AIMap.GetTileX(from_tile);
    local fy = AIMap.GetTileY(from_tile);
    local tx = AIMap.GetTileX(to_tile);
    local ty = AIMap.GetTileY(to_tile);
    local spans = [];
    local total_cost = 0;
    if (fx == tx || fy == ty) {
        local span = this.EstimateRoadSpan(from_tile, to_tile);
        spans.append(span);
        total_cost += span.cost;
        if (!span.ok) {
            return { ok = false, err = span.err, error_code = span.error_code, cost = total_cost, span_from = span.span_from, span_to = span.span_to, spans = spans };
        }
        return { ok = true, err = "", error_code = 0, cost = total_cost, span_from = -1, span_to = -1, spans = spans };
    }

    local corner = AIMap.GetTileIndex(tx, fy);
    local first = this.EstimateRoadSpan(from_tile, corner);
    spans.append(first);
    total_cost += first.cost;
    if (!first.ok) {
        return { ok = false, err = first.err, error_code = first.error_code, cost = total_cost, span_from = first.span_from, span_to = first.span_to, spans = spans };
    }
    local second = this.EstimateRoadSpan(corner, to_tile);
    spans.append(second);
    total_cost += second.cost;
    if (!second.ok) {
        return { ok = false, err = second.err, error_code = second.error_code, cost = total_cost, span_from = second.span_from, span_to = second.span_to, spans = spans };
    }
    return { ok = true, err = "", error_code = 0, cost = total_cost, span_from = -1, span_to = -1, spans = spans };
}

/* Factual first-blocked-tile scan for a span EstimateRoadSpan rejected, for the
 * check_build_road dry run. Test-mode replay of growing prefixes, tolerating
 * ERR_ALREADY_BUILT exactly as EstimateRoadSpan does: the first prefix that
 * fails with a real error attributes the blockage to its last tile -- except
 * when that prefix is the very first pair and span_from is not buildable and is
 * not classified as a road, road station or road depot tile, where span_from is
 * attributed instead, the same refinement RoadSpanFirstBlockedTile makes for
 * build_road. That predicate is an API classification, not a statement about
 * what the tile can carry: a bridge head fails all four checks while the bridge
 * on it carries road. `localized` records that an attribution happened. The
 * error pair always belongs to the failing replay, which is why it can name a
 * different tile than first_blocker_tile does (probed: a span crossing a
 * bridge head). When no attribution happened, the
 * placeholder {tile:-1, error_code:-1, error_name:""} goes out unchanged under
 * localized=false -- a stable shape, never a value the game reported.
 * Reachability: the last prefix this
 * loop replays is AIRoad.BuildRoad(span_from, span_to) -- the very call
 * EstimateRoadSpan just failed -- and test-mode commands read world state
 * without mutating it, so a rejected span localizes whenever both calls see
 * the same world. localized=false therefore means they did not, which needs a
 * game tick between them; no non-localized record has been observed (a
 * probe localized 72 of 72 blocked spans). The placeholder is reported as-is,
 * never guessed.
 * Facts only: no alternative path is computed or suggested. */
function McpBridge::RoadSpanFirstBlocker(span_from, span_to)
{
    local result = { tile = -1, error_code = -1, error_name = "", localized = false };
    if (!AIMap.IsValidTile(span_from) || !AIMap.IsValidTile(span_to) || span_from == span_to) return result;
    local fx = AIMap.GetTileX(span_from);
    local fy = AIMap.GetTileY(span_from);
    local tx = AIMap.GetTileX(span_to);
    local ty = AIMap.GetTileY(span_to);
    if (fx != tx && fy != ty) return result;
    local step = (fx == tx) ? ((ty > fy) ? AIMap.GetMapSizeX() : -AIMap.GetMapSizeX()) : ((tx > fx) ? 1 : -1);
    {
        local test = AITestMode();
        for (local t = span_from + step; ; t += step) {
            if (!AIRoad.BuildRoad(span_from, t) && AIError.GetLastError() != AIError.ERR_ALREADY_BUILT) {
                result.tile = t;
                result.error_code = AIError.GetLastError();
                result.error_name = AIError.GetLastErrorString();
                result.localized = true;
                break;
            }
            if (t == span_to) break;
        }
    }
    if (result.localized && result.tile == span_from + step
        && !AITile.IsBuildable(span_from) && !AIRoad.IsRoadTile(span_from)
        && !AIRoad.IsRoadStationTile(span_from) && !AIRoad.IsRoadDepotTile(span_from)) {
        result.tile = span_from;
    }
    return result;
}

/* Factual first-blocked-tile scan for a failed straight road span. Test-mode
 * replay of the
 * span with growing prefixes: the first prefix (span_from -> t) that fails
 * with a real error pinpoints t as the first tile the span cannot cross.
 * ERR_ALREADY_BUILT is tolerated exactly the way the builder tolerates it --
 * probe-proven necessary when part of the span already carries road (a fully
 * built prefix reports ERR_ALREADY_BUILT, which is not a blockage). When the
 * very first pair fails the obstacle can be span_from itself (probed: a scan
 * started ON a house reported its clear neighbour); span_from
 * is attributed instead when it is not buildable and is not classified as a
 * road, road station or road depot tile. This is an API classification, not a
 * claim about what the tile can carry. Returns -1 when the failure does not
 * reproduce in test mode (e.g. a transient vehicle-in-the-way) --
 * reported as-is, never guessed. Probed on the installed OpenTTD 15.1
 * runtime. Facts only: no alternative path is computed or suggested. */
function McpBridge::RoadSpanFirstBlockedTile(span_from, span_to)
{
    if (!AIMap.IsValidTile(span_from) || !AIMap.IsValidTile(span_to) || span_from == span_to) return -1;
    local fx = AIMap.GetTileX(span_from);
    local fy = AIMap.GetTileY(span_from);
    local tx = AIMap.GetTileX(span_to);
    local ty = AIMap.GetTileY(span_to);
    if (fx != tx && fy != ty) return -1;
    local step = (fx == tx) ? ((ty > fy) ? AIMap.GetMapSizeX() : -AIMap.GetMapSizeX()) : ((tx > fx) ? 1 : -1);
    local blocked = -1;
    {
        local test = AITestMode();
        for (local t = span_from + step; ; t += step) {
            if (!AIRoad.BuildRoad(span_from, t) && AIError.GetLastError() != AIError.ERR_ALREADY_BUILT) {
                blocked = t;
                break;
            }
            if (t == span_to) break;
        }
    }
    if (blocked == span_from + step
        && !AITile.IsBuildable(span_from) && !AIRoad.IsRoadTile(span_from)
        && !AIRoad.IsRoadStationTile(span_from) && !AIRoad.IsRoadDepotTile(span_from)) {
        blocked = span_from;
    }
    return blocked;
}

function McpBridge::PrimCheckBuildRoad(points, timestamp)
{
    foreach (p in points) {
        if (!AIMap.IsValidTile(p)) {
            this.EmitPrimResult(timestamp, "check_build_road", false, "invalid_tile_in_path", 0, "");
            return;
        }
    }
    local total_cost = 0;
    local buildable = true;
    local failed_segment_from = -1;
    local failed_segment_to = -1;
    local failed_span_from = -1;
    local failed_span_to = -1;
    local blocker = { tile = -1, error_code = -1, error_name = "", localized = false };
    local segments_evaluated = 0;
    local segments_json = "[";
    for (local i = 1; i < points.len(); i++) {
        local segment = this.EstimateRoadSegment(points[i - 1], points[i]);
        segments_evaluated++;
        total_cost += segment.cost;
        if (i > 1) segments_json += ",";
        segments_json += "{\"segment_index\":" + (i - 1)
            + ",\"from_tile\":" + points[i - 1]
            + ",\"to_tile\":" + points[i]
            + ",\"buildable\":" + this.BoolJson(segment.ok)
            + ",\"estimated_cost\":" + segment.cost
            + ",\"span_count\":" + segment.spans.len()
            + ",\"spans\":[";
        for (local j = 0; j < segment.spans.len(); j++) {
            local span = segment.spans[j];
            if (j != 0) segments_json += ",";
            segments_json += "{\"span_index\":" + j
                + ",\"from_tile\":" + span.span_from
                + ",\"to_tile\":" + span.span_to
                + ",\"buildable\":" + this.BoolJson(span.ok)
                + ",\"estimated_cost\":" + span.cost
                + ",\"error_code\":" + span.error_code
                + ",\"error_name\":\"" + this.JsonEscape(span.err) + "\"}";
        }
        segments_json += "]}";
        if (!segment.ok) {
            buildable = false;
            failed_segment_from = points[i - 1];
            failed_segment_to = points[i];
            failed_span_from = segment.span_from;
            failed_span_to = segment.span_to;
            blocker = this.RoadSpanFirstBlocker(failed_span_from, failed_span_to);
            break;
        }
    }
    segments_json += "]";

    local extra = ",\"from_tile\":" + points[0] + ",\"to_tile\":" + points[points.len() - 1]
        + ",\"path_tiles\":" + this.IntArrayJson(points)
        + ",\"path_points\":" + points.len()
        + ",\"buildable\":" + this.BoolJson(buildable)
        + ",\"total_estimated_cost\":" + total_cost
        + ",\"segments_evaluated\":" + segments_evaluated
        + ",\"pathfinder\":\"straight_or_manhattan_corner_fallback\""
        + ",\"date\":" + this.DateJson(AIDate.GetCurrentDate())
        + ",\"segments\":" + segments_json;
    if (!buildable) {
        extra += ",\"failed_segment_from\":" + failed_segment_from
            + ",\"failed_segment_to\":" + failed_segment_to
            + ",\"failed_span_from\":" + failed_span_from
            + ",\"failed_span_to\":" + failed_span_to
            + ",\"first_blocker_localized\":" + this.BoolJson(blocker.localized)
            + ",\"first_blocker_tile\":" + blocker.tile
            + ",\"blocker_error_code\":" + blocker.error_code
            + ",\"blocker_error_name\":\"" + this.JsonEscape(blocker.error_name) + "\"";
        if (blocker.localized) {
            extra += ",\"first_blocker_tile_x\":" + AIMap.GetTileX(blocker.tile)
                + ",\"first_blocker_tile_y\":" + AIMap.GetTileY(blocker.tile);
        }
    }
    this.EmitPrimResult(timestamp, "check_build_road", true, "", 0, extra);
}

/* Read-only costing for the depot-construction phase: the exact
 * AIRoad.BuildRoadDepot(tile, front_tile) primitive. AITestMode does not
 * materialize the depot, so this cannot preview the following automatic
 * connector's legality or cost. On a plain road selector,
 * front_road_connected=true positively witnesses the existing road bit toward
 * the depot tile before construction; false is not proof of absence when
 * RoadBitJson reports "unknown". Non-plain-road selectors retain stable
 * false-shaped compatibility placeholders, explicitly qualified by
 * front_road_bits_applicable=false.
 * Facts only: the bridge does not enumerate or rank depot sites. */
function McpBridge::PrimCheckBuildRoadDepot(tile, front_tile, timestamp)
{
    if (!AIMap.IsValidTile(tile) || !AIMap.IsValidTile(front_tile)) {
        this.EmitPrimResult(timestamp, "check_build_road_depot", false, "invalid_tile", 0, ",\"tile\":" + tile + ",\"front_tile\":" + front_tile);
        return;
    }
    local buildable = true;
    local err = "";
    local error_code = 0;
    local estimated_cost = 0;
    {
        local test = AITestMode();
        local accounting = AIAccounting();
        if (!AIRoad.BuildRoadDepot(tile, front_tile)) {
            buildable = false;
            error_code = AIError.GetLastError();
            err = AIError.GetLastErrorString();
        }
        estimated_cost = accounting.GetCosts();
    }

    local front_is_road = this.IsRoadLike(front_tile);
    local front_road_bits_applicable = AIRoad.IsRoadTile(front_tile);
    local fact_errors = [];
    local front_road_bits = front_road_bits_applicable
        ? this.RoadBitsJson(
            front_tile, AIMap.GetTileX(front_tile), AIMap.GetTileY(front_tile),
            "front_road_bits", fact_errors)
        : "{\"n\":false,\"e\":false,\"s\":false,\"w\":false}";
    local front_road_connected = front_road_bits_applicable
        && this.RoadBitJson(
            front_tile, tile, "front_road_connected", fact_errors) == "true";
    local extra = ",\"tile\":" + tile
        + ",\"front_tile\":" + front_tile
        + ",\"buildable\":" + this.BoolJson(buildable)
        + ",\"estimated_cost\":" + estimated_cost
        + ",\"error_code\":" + error_code
        + ",\"error_name\":\"" + this.JsonEscape(err) + "\""
        + ",\"front_is_road\":" + this.BoolJson(front_is_road)
        + ",\"front_road_connected\":" + this.BoolJson(front_road_connected)
        + ",\"front_road_bits_applicable\":" + this.BoolJson(front_road_bits_applicable)
        + ",\"front_road_bits\":" + front_road_bits
        + ",\"date\":" + this.DateJson(AIDate.GetCurrentDate())
        + this.FactErrorsJson(fact_errors);
    this.EmitPrimResult(timestamp, "check_build_road_depot", true, "", 0, extra);
}

function McpBridge::PrimBuildRoad(points, timestamp)
{
    foreach (p in points) {
        if (!AIMap.IsValidTile(p)) {
            this.EmitPrimResult(timestamp, "build_road", false, "invalid_tile_in_path", 0, "");
            return;
        }
    }
    local exec = AIExecMode();
    local accounting = AIAccounting();
    local err = "";
    local segments_built = 0;
    local failed_segment_from = -1;
    local failed_segment_to = -1;
    local failed_span_from = -1;
    local failed_span_to = -1;
    for (local i = 1; i < points.len() && err == ""; i++) {
        local segment = this.BuildRoadSegment(points[i - 1], points[i]);
        err = segment.err;
        if (err == "") {
            segments_built++;
        } else {
            failed_segment_from = points[i - 1];
            failed_segment_to = points[i];
            failed_span_from = segment.span_from;
            failed_span_to = segment.span_to;
        }
    }
    local cost = accounting.GetCosts();
    local ok = err == "";
    local connected = false;
    if (ok && points.len() >= 2) connected = this.Reachable(points[0], points[points.len() - 1]);
    local extra = ",\"from_tile\":" + points[0] + ",\"to_tile\":" + points[points.len() - 1]
        + ",\"path_points\":" + points.len() + ",\"segments_built\":" + segments_built
        + ",\"endpoints_connected\":" + this.BoolJson(connected)
        + ",\"pathfinder\":\"straight_or_manhattan_corner_fallback\"";
    /* A failed build additionally reports WHERE it failed -- otherwise the
     * model can spend many calls locating the obstacle behind a bare
     * ERR_AREA_NOT_CLEAR. Facts only. */
    if (!ok && failed_segment_from != -1) {
        local first_blocked = this.RoadSpanFirstBlockedTile(failed_span_from, failed_span_to);
        extra += ",\"failed_segment_from\":" + failed_segment_from
            + ",\"failed_segment_to\":" + failed_segment_to
            + ",\"failed_span_from\":" + failed_span_from
            + ",\"failed_span_to\":" + failed_span_to
            + ",\"first_blocked_tile\":" + first_blocked;
        if (first_blocked != -1) {
            extra += ",\"first_blocked_tile_x\":" + AIMap.GetTileX(first_blocked)
                + ",\"first_blocked_tile_y\":" + AIMap.GetTileY(first_blocked);
        }
    }
    this.EmitPrimResult(timestamp, "build_road", ok, err, cost, extra);
}

function McpBridge::PrimBuildOneWayRoad(from_tile, to_tile, timestamp)
{
    local endpoints = ",\"from_tile\":" + from_tile + ",\"to_tile\":" + to_tile;
    if (!AIMap.IsValidTile(from_tile) || !AIMap.IsValidTile(to_tile)) {
        this.EmitPrimResult(timestamp, "build_one_way_road", false, "invalid_tile", 0,
            endpoints + ",\"endpoints_connected_forward\":false,\"endpoints_connected_reverse\":false");
        return;
    }
    local fx = AIMap.GetTileX(from_tile);
    local fy = AIMap.GetTileY(from_tile);
    local tx = AIMap.GetTileX(to_tile);
    local ty = AIMap.GetTileY(to_tile);
    if (fx != tx && fy != ty) {
        local connected_forward = this.Reachable(from_tile, to_tile);
        local connected_reverse = this.Reachable(to_tile, from_tile);
        this.EmitPrimResult(timestamp, "build_one_way_road", false,
            "one_way_road_endpoints_must_share_row_or_column", 0,
            endpoints
            + ",\"endpoints_connected_forward\":" + this.BoolJson(connected_forward)
            + ",\"endpoints_connected_reverse\":" + this.BoolJson(connected_reverse));
        return;
    }

    local exec = AIExecMode();
    local accounting = AIAccounting();
    local ok = AIRoad.BuildOneWayRoad(from_tile, to_tile);
    local err = ok ? "" : AIError.GetLastErrorString();
    local cost = accounting.GetCosts();
    local connected_forward = this.Reachable(from_tile, to_tile);
    local connected_reverse = this.Reachable(to_tile, from_tile);
    local extra = endpoints
        + ",\"endpoints_connected_forward\":" + this.BoolJson(connected_forward)
        + ",\"endpoints_connected_reverse\":" + this.BoolJson(connected_reverse);
    this.EmitPrimResult(timestamp, "build_one_way_road", ok, err, cost, extra);
}

function McpBridge::PrimBuildBridge(start_tile, end_tile, bridge_id, timestamp)
{
    if (!AIMap.IsValidTile(start_tile) || !AIMap.IsValidTile(end_tile)) {
        this.EmitPrimResult(timestamp, "build_bridge", false, "invalid_tile", 0, ",\"start_tile\":" + start_tile + ",\"end_tile\":" + end_tile + ",\"bridge_id\":" + bridge_id);
        return;
    }
    // A zero-length span (start == end) shares both row and column, so it slips
    // past the row/column geometry check below and reaches BuildBridge, which
    // rejects it with the generic "Precondition failed." Name it here so the
    // failure stays in the wrapper's factual precondition vocabulary.
    if (start_tile == end_tile) {
        this.EmitPrimResult(timestamp, "build_bridge", false, "bridge_endpoints_identical", 0, ",\"start_tile\":" + start_tile + ",\"end_tile\":" + end_tile + ",\"bridge_id\":" + bridge_id);
        return;
    }
    local sx = AIMap.GetTileX(start_tile);
    local sy = AIMap.GetTileY(start_tile);
    local ex = AIMap.GetTileX(end_tile);
    local ey = AIMap.GetTileY(end_tile);
    if (sx != ex && sy != ey) {
        this.EmitPrimResult(timestamp, "build_bridge", false, "bridge_endpoints_must_share_row_or_column", 0, ",\"start_tile\":" + start_tile + ",\"end_tile\":" + end_tile + ",\"bridge_id\":" + bridge_id);
        return;
    }
    if (!AIBridge.IsValidBridge(bridge_id)) {
        this.EmitPrimResult(timestamp, "build_bridge", false, "invalid_bridge_id", 0, ",\"start_tile\":" + start_tile + ",\"end_tile\":" + end_tile + ",\"bridge_id\":" + bridge_id);
        return;
    }
    local length = AITile.GetDistanceManhattanToTile(start_tile, end_tile) + 1;
    local exec = AIExecMode();
    local accounting = AIAccounting();
    local build_ok = AIBridge.BuildBridge(AIVehicle.VT_ROAD, bridge_id, start_tile, end_tile);
    local err = build_ok ? "" : AIError.GetLastErrorString();
    local cost = accounting.GetCosts();
    local start_is_bridge = AIBridge.IsBridgeTile(start_tile);
    local end_is_bridge = AIBridge.IsBridgeTile(end_tile);
    local other_end_tile = start_is_bridge ? AIBridge.GetOtherBridgeEnd(start_tile) : -1;
    local other_matches_end = other_end_tile == end_tile;
    local observed_ok = build_ok && start_is_bridge && end_is_bridge && other_matches_end;
    if (build_ok && !observed_ok) err = "post_build_observation_failed";
    this.EmitPrimResult(timestamp, "build_bridge", observed_ok, err, cost,
        ",\"start_tile\":" + start_tile
        + ",\"end_tile\":" + end_tile
        + ",\"bridge_id\":" + bridge_id
        + ",\"length\":" + length
        + ",\"build_returned_ok\":" + this.BoolJson(build_ok)
        + ",\"start_is_bridge\":" + this.BoolJson(start_is_bridge)
        + ",\"end_is_bridge\":" + this.BoolJson(end_is_bridge)
        + ",\"other_end_tile\":" + other_end_tile
        + ",\"other_matches_end\":" + this.BoolJson(other_matches_end));
}

function McpBridge::PrimBuildTunnel(start_tile, timestamp)
{
    if (!AIMap.IsValidTile(start_tile)) {
        this.EmitPrimResult(timestamp, "build_tunnel", false, "invalid_tile", 0, ",\"start_tile\":" + start_tile);
        return;
    }
    local slope_before = AITile.GetSlope(start_tile);
    local other_end_tile_before = AITunnel.GetOtherTunnelEnd(start_tile);
    local exec = AIExecMode();
    local accounting = AIAccounting();
    local build_ok = AITunnel.BuildTunnel(AIVehicle.VT_ROAD, start_tile);
    local err = build_ok ? "" : AIError.GetLastErrorString();
    local cost = accounting.GetCosts();
    local start_is_tunnel = AITunnel.IsTunnelTile(start_tile);
    local other_end_tile = start_is_tunnel ? AITunnel.GetOtherTunnelEnd(start_tile) : other_end_tile_before;
    local other_end_is_tunnel = AIMap.IsValidTile(other_end_tile) ? AITunnel.IsTunnelTile(other_end_tile) : false;
    local observed_ok = build_ok && start_is_tunnel && other_end_is_tunnel;
    if (build_ok && !observed_ok) err = "post_build_observation_failed";
    this.EmitPrimResult(timestamp, "build_tunnel", observed_ok, err, cost,
        ",\"start_tile\":" + start_tile
        + ",\"slope_before\":" + slope_before
        + ",\"other_end_tile_before\":" + other_end_tile_before
        + ",\"other_end_tile\":" + other_end_tile
        + ",\"build_returned_ok\":" + this.BoolJson(build_ok)
        + ",\"start_is_tunnel\":" + this.BoolJson(start_is_tunnel)
        + ",\"other_end_is_tunnel\":" + this.BoolJson(other_end_is_tunnel));
}

function McpBridge::PrimTerraformLand(tile, mode, to_tile, corners, timestamp)
{
    if (!AIMap.IsValidTile(tile)) {
        this.EmitPrimResult(timestamp, "terraform_land", false, "invalid_tile", 0, ",\"tile\":" + tile + ",\"mode\":\"" + this.JsonEscape(mode) + "\"");
        return;
    }
    local slope_before = AITile.GetSlope(tile);
    local min_height_before = AITile.GetMinHeight(tile);
    local max_height_before = AITile.GetMaxHeight(tile);
    local corner_mask = 0;
    local corners_json = "[";
    for (local i = 0; i < corners.len(); i++) {
        local corner = corners[i];
        if (i != 0) corners_json += ",";
        corners_json += "\"" + this.JsonEscape(corner) + "\"";
        if (corner == "w") corner_mask = corner_mask | AITile.SLOPE_W;
        else if (corner == "s") corner_mask = corner_mask | AITile.SLOPE_S;
        else if (corner == "e") corner_mask = corner_mask | AITile.SLOPE_E;
        else if (corner == "n") corner_mask = corner_mask | AITile.SLOPE_N;
        else {
            this.EmitPrimResult(timestamp, "terraform_land", false, "invalid_corner", 0,
                ",\"tile\":" + tile + ",\"mode\":\"" + this.JsonEscape(mode) + "\""
                + ",\"to_tile\":" + to_tile
                + ",\"corners\":" + corners_json + "]"
                + ",\"corner_mask\":" + corner_mask
                + ",\"slope_before\":" + slope_before
                + ",\"height_before\":{\"min\":" + min_height_before + ",\"max\":" + max_height_before + "}");
            return;
        }
    }
    corners_json += "]";
    // One shared extra payload for every post-loop path (both errors and the
    // success result), built once from the finalized corners_json/corner_mask so
    // the seven common fields cannot desynchronize across branches. The success
    // path appends slope_after/height_after; only the mid-loop invalid_corner
    // branch above is special-cased because its corners_json/corner_mask are
    // still partial.
    local base_extra = ",\"tile\":" + tile
        + ",\"mode\":\"" + this.JsonEscape(mode) + "\""
        + ",\"to_tile\":" + to_tile
        + ",\"corners\":" + corners_json
        + ",\"corner_mask\":" + corner_mask
        + ",\"slope_before\":" + slope_before
        + ",\"height_before\":{\"min\":" + min_height_before + ",\"max\":" + max_height_before + "}";
    if ((mode == "raise" || mode == "lower") && corner_mask == 0) {
        this.EmitPrimResult(timestamp, "terraform_land", false, "raise_lower_requires_corner", 0, base_extra);
        return;
    }
    if (mode == "level_to" && !AIMap.IsValidTile(to_tile)) {
        this.EmitPrimResult(timestamp, "terraform_land", false, "invalid_to_tile", 0, base_extra);
        return;
    }

    local exec = AIExecMode();
    local accounting = AIAccounting();
    local ok = false;
    if (mode == "raise") {
        ok = AITile.RaiseTile(tile, corner_mask);
    } else if (mode == "lower") {
        ok = AITile.LowerTile(tile, corner_mask);
    } else if (mode == "level_to") {
        ok = AITile.LevelTiles(tile, to_tile);
    } else {
        this.EmitPrimResult(timestamp, "terraform_land", false, "invalid_mode", 0, base_extra);
        return;
    }
    local cost = accounting.GetCosts();
    local err = ok ? "" : AIError.GetLastErrorString();
    local slope_after = AITile.GetSlope(tile);
    local min_height_after = AITile.GetMinHeight(tile);
    local max_height_after = AITile.GetMaxHeight(tile);
    this.EmitPrimResult(timestamp, "terraform_land", ok, err, cost,
        base_extra
        + ",\"slope_after\":" + slope_after
        + ",\"height_after\":{\"min\":" + min_height_after + ",\"max\":" + max_height_after + "}");
}

function McpBridge::PrimBuildBusStopDriveThrough(tile, front_tile, join_arg, timestamp)
{
    local join_info = this.ResolveStationJoinArg(join_arg);
    if (!join_info.valid) {
        this.EmitPrimResult(timestamp, "build_bus_stop_drive_through", false, "invalid_join_argument", 0,
            ",\"tile\":" + tile + ",\"front_tile\":" + front_tile
            + ",\"join_arg\":\"" + this.JsonEscape("" + join_arg) + "\"");
        return;
    }
    local join = join_info.join;
    local station_arg = join_info.station_arg;
    if (!AIMap.IsValidTile(tile) || !AIMap.IsValidTile(front_tile)) {
        this.EmitPrimResult(timestamp, "build_bus_stop_drive_through", false, "invalid_tile", 0,
            ",\"tile\":" + tile + ",\"front_tile\":" + front_tile + ",\"join\":\"" + this.JsonEscape(join) + "\"");
        return;
    }
    if (join == "station_id" && !AIStation.IsValidStation(station_arg)) {
        this.EmitPrimResult(timestamp, "build_bus_stop_drive_through", false, "invalid_station_id", 0,
            ",\"tile\":" + tile + ",\"front_tile\":" + front_tile
            + ",\"join\":\"station_id\",\"station_id\":" + station_arg);
        return;
    }
    local exec = AIExecMode();
    local accounting = AIAccounting();
    local ok = AIRoad.BuildDriveThroughRoadStation(tile, front_tile, AIRoad.ROADVEHTYPE_BUS, station_arg);
    local err = ok ? "" : AIError.GetLastErrorString();
    local cost = accounting.GetCosts();
    local station_id = (ok && AIRoad.IsRoadStationTile(tile)) ? AIStation.GetStationID(tile) : -1;
    local fact_errors = [];
    local extra = ",\"tile\":" + tile + ",\"front_tile\":" + front_tile + ",\"station_id\":" + station_id
        + ",\"join\":\"" + this.JsonEscape(join) + "\""
        + ",\"drive_through\":" + this.BoolJson(ok && AIRoad.IsDriveThroughRoadStationTile(tile));
    /* Attach the factual notes for OpenTTD 15.1's unmapped
     * ERR_UNKNOWN rejection sources (corner/junction road bits, ambiguous
     * adjacent join); see RoadStopErrUnknownExtra. */
    if (!ok && err == "ERR_UNKNOWN") {
        extra += this.RoadStopErrUnknownExtra(tile, join, true, fact_errors);
    }
    extra += this.FactErrorsJson(fact_errors);
    this.EmitPrimResult(timestamp, "build_bus_stop_drive_through", ok, err, cost, extra);
}

function McpBridge::PrimBuildTruckStopDriveThrough(tile, front_tile, join_arg, timestamp)
{
    local join_info = this.ResolveStationJoinArg(join_arg);
    if (!join_info.valid) {
        this.EmitPrimResult(timestamp, "build_truck_stop_drive_through", false, "invalid_join_argument", 0,
            ",\"tile\":" + tile + ",\"front_tile\":" + front_tile
            + ",\"join_arg\":\"" + this.JsonEscape("" + join_arg) + "\"");
        return;
    }
    local join = join_info.join;
    local station_arg = join_info.station_arg;
    if (!AIMap.IsValidTile(tile) || !AIMap.IsValidTile(front_tile)) {
        this.EmitPrimResult(timestamp, "build_truck_stop_drive_through", false, "invalid_tile", 0,
            ",\"tile\":" + tile + ",\"front_tile\":" + front_tile + ",\"join\":\"" + this.JsonEscape(join) + "\"");
        return;
    }
    if (join == "station_id" && !AIStation.IsValidStation(station_arg)) {
        this.EmitPrimResult(timestamp, "build_truck_stop_drive_through", false, "invalid_station_id", 0,
            ",\"tile\":" + tile + ",\"front_tile\":" + front_tile
            + ",\"join\":\"station_id\",\"station_id\":" + station_arg);
        return;
    }
    local exec = AIExecMode();
    local accounting = AIAccounting();
    local ok = AIRoad.BuildDriveThroughRoadStation(tile, front_tile, AIRoad.ROADVEHTYPE_TRUCK, station_arg);
    local err = ok ? "" : AIError.GetLastErrorString();
    local cost = accounting.GetCosts();
    local station_id = (ok && AIRoad.IsRoadStationTile(tile)) ? AIStation.GetStationID(tile) : -1;
    local is_truck_stop = false;
    local fact_errors = [];
    if (station_id != -1) {
        try {
            is_truck_stop = AIStation.HasStationType(station_id, AIStation.STATION_TRUCK_STOP);
        } catch (e) {
            fact_errors.append(["station_type_unavailable", "is_truck_stop"]);
        }
    }
    local extra = ",\"tile\":" + tile
        + ",\"front_tile\":" + front_tile
        + ",\"station_id\":" + station_id
        + ",\"join\":\"" + this.JsonEscape(join) + "\""
        + ",\"drive_through\":" + this.BoolJson(ok && AIRoad.IsDriveThroughRoadStationTile(tile))
        + ",\"is_truck_stop\":" + this.BoolJson(is_truck_stop);
    /* Parity with PrimBuildBusStopDriveThrough: attach the factual
     * notes for OpenTTD 15.1's unmapped ERR_UNKNOWN rejection sources
     * (corner/junction road bits, ambiguous adjacent join); see
     * RoadStopErrUnknownExtra. */
    if (!ok && err == "ERR_UNKNOWN") {
        extra += this.RoadStopErrUnknownExtra(tile, join, true, fact_errors);
    }
    extra += this.FactErrorsJson(fact_errors);
    this.EmitPrimResult(timestamp, "build_truck_stop_drive_through", ok, err, cost, extra);
}

function McpBridge::PrimBuildBusStopBay(tile, front_tile, join_arg, timestamp)
{
    local join_info = this.ResolveStationJoinArg(join_arg);
    if (!join_info.valid) {
        this.EmitPrimResult(timestamp, "build_bus_stop_bay", false, "invalid_join_argument", 0,
            ",\"tile\":" + tile + ",\"front_tile\":" + front_tile
            + ",\"join_arg\":\"" + this.JsonEscape("" + join_arg) + "\"");
        return;
    }
    local join = join_info.join;
    local station_arg = join_info.station_arg;
    if (!AIMap.IsValidTile(tile) || !AIMap.IsValidTile(front_tile)) {
        this.EmitPrimResult(timestamp, "build_bus_stop_bay", false, "invalid_tile", 0,
            ",\"tile\":" + tile + ",\"front_tile\":" + front_tile + ",\"join\":\"" + this.JsonEscape(join) + "\"");
        return;
    }
    if (join == "station_id" && !AIStation.IsValidStation(station_arg)) {
        this.EmitPrimResult(timestamp, "build_bus_stop_bay", false, "invalid_station_id", 0,
            ",\"tile\":" + tile + ",\"front_tile\":" + front_tile
            + ",\"join\":\"station_id\",\"station_id\":" + station_arg);
        return;
    }

    /* BuildRoadStation's API contract requires ordinary road, unlike the
     * drive-through builder's broader available-road-type precondition. Keep
     * this atomic action independent of any earlier bridge road-type state. */
    AIRoad.SetCurrentRoadType(AIRoad.ROADTYPE_ROAD);
    local exec = AIExecMode();
    local accounting = AIAccounting();
    local ok = AIRoad.BuildRoadStation(tile, front_tile, AIRoad.ROADVEHTYPE_BUS, station_arg);
    local err = ok ? "" : AIError.GetLastErrorString();
    local cost = accounting.GetCosts();
    local is_station_tile = ok && AIRoad.IsRoadStationTile(tile);
    local station_id = is_station_tile ? AIStation.GetStationID(tile) : -1;
    local is_bus_stop = false;
    local fact_errors = [];
    if (station_id != -1) {
        try {
            is_bus_stop = AIStation.HasStationType(station_id, AIStation.STATION_BUS_STOP);
        } catch (e) {
            fact_errors.append(["station_type_unavailable", "is_bus_stop"]);
        }
    }
    local drive_through = is_station_tile && AIRoad.IsDriveThroughRoadStationTile(tile);
    local extra = ",\"tile\":" + tile
        + ",\"front_tile\":" + front_tile
        + ",\"station_id\":" + station_id
        + ",\"join\":\"" + this.JsonEscape(join) + "\""
        + ",\"drive_through\":" + this.BoolJson(drive_through)
        + ",\"bay_station\":" + this.BoolJson(is_station_tile && !drive_through)
        + ",\"is_bus_stop\":" + this.BoolJson(is_bus_stop);
    if (!ok && err == "ERR_UNKNOWN" && join == "adjacent") {
        extra += this.RoadStopErrUnknownExtra(tile, join, false, fact_errors);
    }
    extra += this.FactErrorsJson(fact_errors);
    this.EmitPrimResult(timestamp, "build_bus_stop_bay", ok, err, cost, extra);
}

function McpBridge::PrimBuildTruckStopBay(tile, front_tile, join_arg, timestamp)
{
    local join_info = this.ResolveStationJoinArg(join_arg);
    if (!join_info.valid) {
        this.EmitPrimResult(timestamp, "build_truck_stop_bay", false, "invalid_join_argument", 0,
            ",\"tile\":" + tile + ",\"front_tile\":" + front_tile
            + ",\"join_arg\":\"" + this.JsonEscape("" + join_arg) + "\"");
        return;
    }
    local join = join_info.join;
    local station_arg = join_info.station_arg;
    if (!AIMap.IsValidTile(tile) || !AIMap.IsValidTile(front_tile)) {
        this.EmitPrimResult(timestamp, "build_truck_stop_bay", false, "invalid_tile", 0,
            ",\"tile\":" + tile + ",\"front_tile\":" + front_tile + ",\"join\":\"" + this.JsonEscape(join) + "\"");
        return;
    }
    if (join == "station_id" && !AIStation.IsValidStation(station_arg)) {
        this.EmitPrimResult(timestamp, "build_truck_stop_bay", false, "invalid_station_id", 0,
            ",\"tile\":" + tile + ",\"front_tile\":" + front_tile
            + ",\"join\":\"station_id\",\"station_id\":" + station_arg);
        return;
    }

    AIRoad.SetCurrentRoadType(AIRoad.ROADTYPE_ROAD);
    local exec = AIExecMode();
    local accounting = AIAccounting();
    local ok = AIRoad.BuildRoadStation(tile, front_tile, AIRoad.ROADVEHTYPE_TRUCK, station_arg);
    local err = ok ? "" : AIError.GetLastErrorString();
    local cost = accounting.GetCosts();
    local is_station_tile = ok && AIRoad.IsRoadStationTile(tile);
    local station_id = is_station_tile ? AIStation.GetStationID(tile) : -1;
    local is_truck_stop = false;
    local fact_errors = [];
    if (station_id != -1) {
        try {
            is_truck_stop = AIStation.HasStationType(station_id, AIStation.STATION_TRUCK_STOP);
        } catch (e) {
            fact_errors.append(["station_type_unavailable", "is_truck_stop"]);
        }
    }
    local drive_through = is_station_tile && AIRoad.IsDriveThroughRoadStationTile(tile);
    local extra = ",\"tile\":" + tile
        + ",\"front_tile\":" + front_tile
        + ",\"station_id\":" + station_id
        + ",\"join\":\"" + this.JsonEscape(join) + "\""
        + ",\"drive_through\":" + this.BoolJson(drive_through)
        + ",\"bay_station\":" + this.BoolJson(is_station_tile && !drive_through)
        + ",\"is_truck_stop\":" + this.BoolJson(is_truck_stop);
    if (!ok && err == "ERR_UNKNOWN" && join == "adjacent") {
        extra += this.RoadStopErrUnknownExtra(tile, join, false, fact_errors);
    }
    extra += this.FactErrorsJson(fact_errors);
    this.EmitPrimResult(timestamp, "build_truck_stop_bay", ok, err, cost, extra);
}

function McpBridge::PrimBuildRoadDepot(tile, front_tile, timestamp)
{
    if (!AIMap.IsValidTile(tile) || !AIMap.IsValidTile(front_tile)) {
        this.EmitPrimResult(timestamp, "build_road_depot", false, "invalid_tile", 0, ",\"tile\":" + tile + ",\"front_tile\":" + front_tile);
        return;
    }
    local exec = AIExecMode();
    local accounting = AIAccounting();
    local depot_succeeded = AIRoad.BuildRoadDepot(tile, front_tile);
    local err = depot_succeeded ? "" : AIError.GetLastErrorString();
    local depot_present = AIRoad.IsRoadDepotTile(tile);
    local actual_front_tile = -1;
    local fact_errors = [];
    if (depot_present) {
        try {
            actual_front_tile = AIRoad.GetRoadDepotFrontTile(tile);
        } catch (e) {
            actual_front_tile = -1;
            fact_errors.append(["road_depot_front_unavailable", "actual_front_tile"]);
        }
        if (!AIMap.IsValidTile(actual_front_tile)) {
            actual_front_tile = -1;
            if (fact_errors.len() == 0) {
                fact_errors.append(["road_depot_front_unavailable", "actual_front_tile"]);
            }
        }
    }
    local connector_attempted = false;
    local connector_succeeded = false;
    if (depot_succeeded && AIMap.IsValidTile(actual_front_tile)) {
        local connected_after_depot = this.DirectConnected(tile, actual_front_tile)
            && this.DirectConnected(actual_front_tile, tile);
        if (!connected_after_depot) {
            connector_attempted = true;
            connector_succeeded = AIRoad.BuildRoad(tile, actual_front_tile);
            if (!connector_succeeded) err = AIError.GetLastErrorString();
        }
    } else if (depot_succeeded && depot_present) {
        err = "depot_front_unavailable";
    }
    local cost = accounting.GetCosts();
    local front_is_road = AIMap.IsValidTile(actual_front_tile)
        && this.IsRoadLike(actual_front_tile);
    local front_road_connected = depot_present && front_is_road
        && this.DirectConnected(tile, actual_front_tile)
        && this.DirectConnected(actual_front_tile, tile);
    local ok = depot_succeeded
        && (!connector_attempted || connector_succeeded)
        && depot_present
        && front_road_connected;
    if (!ok && err == "" && depot_succeeded) err = "front_connection_not_observed";
    local game_mutated = depot_succeeded || connector_succeeded;
    this.EmitPrimResult(timestamp, "build_road_depot", ok, err, cost,
        ",\"tile\":" + tile + ",\"front_tile\":" + front_tile
        + ",\"actual_front_tile\":" + actual_front_tile
        + ",\"is_road_depot\":" + this.BoolJson(depot_present)
        + ",\"front_is_road\":" + this.BoolJson(front_is_road)
        + ",\"front_road_connected\":" + this.BoolJson(front_road_connected)
        + ",\"connector_attempted\":" + this.BoolJson(connector_attempted)
        + ",\"connector_succeeded\":" + this.BoolJson(connector_succeeded)
        + ",\"game_mutated\":" + this.BoolJson(game_mutated)
        + this.FactErrorsJson(fact_errors));
}

function McpBridge::PrimBuyVehicle(depot_tile, engine_id, timestamp)
{
    if (!AIMap.IsValidTile(depot_tile)) {
        this.EmitPrimResult(timestamp, "buy_vehicle", false, "invalid_depot_tile", 0, ",\"depot_tile\":" + depot_tile);
        return;
    }
    if (!AIEngine.IsValidEngine(engine_id) || !AIEngine.IsBuildable(engine_id)) {
        this.EmitPrimResult(timestamp, "buy_vehicle", false, "invalid_or_unbuildable_engine", 0, ",\"engine_id\":" + engine_id);
        return;
    }
    local exec = AIExecMode();
    local accounting = AIAccounting();
    local vehicle = AIVehicle.BuildVehicle(depot_tile, engine_id);
    local ok = AIVehicle.IsValidVehicle(vehicle);
    local err = ok ? "" : AIError.GetLastErrorString();
    local cost = accounting.GetCosts();
    this.EmitPrimResult(timestamp, "buy_vehicle", ok, err, cost,
        ",\"depot_tile\":" + depot_tile + ",\"engine_id\":" + engine_id + ",\"vehicle_id\":" + (ok ? vehicle : -1));
}

function McpBridge::PrimCloneVehicle(depot_tile, vehicle_id, share_orders, timestamp)
{
    if (!AIMap.IsValidTile(depot_tile)) {
        this.EmitPrimResult(timestamp, "clone_vehicle", false, "invalid_depot_tile", 0,
            ",\"depot_tile\":" + depot_tile + ",\"source_vehicle_id\":" + vehicle_id
            + ",\"vehicle_id\":-1,\"share_orders\":" + this.BoolJson(share_orders));
        return;
    }
    local exec = AIExecMode();
    local accounting = AIAccounting();
    local new_vehicle = AIVehicle.CloneVehicle(depot_tile, vehicle_id, share_orders);
    local ok = AIVehicle.IsValidVehicle(new_vehicle);
    local err = ok ? "" : AIError.GetLastErrorString();
    local cost = accounting.GetCosts();
    local source_valid = AIVehicle.IsValidVehicle(vehicle_id);
    local order_count = ok ? AIOrder.GetOrderCount(new_vehicle) : -1;
    local source_order_count = source_valid ? AIOrder.GetOrderCount(vehicle_id) : -1;
    local state = ok ? AIVehicle.GetState(new_vehicle) : -1;
    local in_depot = ok ? AIVehicle.IsInDepot(new_vehicle) : false;
    local stopped_in_depot = ok ? AIVehicle.IsStoppedInDepot(new_vehicle) : false;
    local engine_id = ok ? AIVehicle.GetEngineType(new_vehicle) : -1;
    this.EmitPrimResult(timestamp, "clone_vehicle", ok, err, cost,
        ",\"depot_tile\":" + depot_tile
        + ",\"source_vehicle_id\":" + vehicle_id
        + ",\"vehicle_id\":" + (ok ? new_vehicle : -1)
        + ",\"share_orders\":" + this.BoolJson(share_orders)
        + ",\"engine_id\":" + engine_id
        + ",\"source_order_count\":" + source_order_count
        + ",\"order_count\":" + order_count
        + ",\"source_order_destinations\":" + this.OrderDestinationsJson(vehicle_id)
        + ",\"order_destinations\":" + this.OrderDestinationsJson(new_vehicle)
        + ",\"source_order_stations\":" + this.OrderStationsJson(vehicle_id)
        + ",\"order_stations\":" + this.OrderStationsJson(new_vehicle)
        + ",\"source_has_shared_orders\":" + this.BoolJson(source_valid && AIVehicle.HasSharedOrders(vehicle_id))
        + ",\"clone_has_shared_orders\":" + this.BoolJson(ok && AIVehicle.HasSharedOrders(new_vehicle))
        + ",\"state\":" + state
        + ",\"in_depot\":" + this.BoolJson(in_depot)
        + ",\"stopped_in_depot\":" + this.BoolJson(stopped_in_depot));
}

/* Groups/autoreplace. OpenTTD 15.1 script_group.hpp exposes
 * AIGroup.CreateGroup, SetName, MoveVehicle, SetAutoReplace, and
 * GetEngineReplacement; script_vehicle.hpp exposes AIVehicle.GetGroupID. */
function McpBridge::PrimCreateGroup(name, timestamp)
{
    if (name == null || name.len() == 0) {
        this.EmitPrimResult(timestamp, "create_group", false, "empty_group_name", 0, ",\"group_id\":-1,\"group_name\":\"\",\"groups\":" + this.GroupsJson());
        return;
    }
    local exec = AIExecMode();
    local accounting = AIAccounting();
    local group_id = AIGroup.CreateGroup(AIVehicle.VT_ROAD, AIGroup.GROUP_INVALID);
    local created = AIGroup.IsValidGroup(group_id);
    local name_set = created ? AIGroup.SetName(group_id, name) : false;
    local err = (created && name_set) ? "" : AIError.GetLastErrorString();
    /* CreateGroup persists an unnamed group even when SetName later fails (name
     * too long or already taken). Reporting ok:false while leaving it behind
     * makes the model retry and stack an orphan every attempt, so roll the
     * half-built group back before returning failure. */
    if (created && !name_set) {
        AIGroup.DeleteGroup(group_id);
        created = false;
    }
    local ok = created && name_set;
    local cost = accounting.GetCosts();
    this.EmitPrimResult(timestamp, "create_group", ok, err, cost,
        ",\"group_id\":" + (ok ? group_id : -1)
        + ",\"group_name\":\"" + this.JsonEscape(ok ? this.GroupName(group_id) : "") + "\""
        + ",\"created\":" + this.BoolJson(created)
        + ",\"name_set\":" + this.BoolJson(name_set)
        + ",\"groups\":" + this.GroupsJson());
}

function McpBridge::PrimMoveVehicleToGroup(vehicle_id, group_id, timestamp)
{
    if (!AIVehicle.IsValidVehicle(vehicle_id) || AIVehicle.GetVehicleType(vehicle_id) != AIVehicle.VT_ROAD) {
        this.EmitPrimResult(timestamp, "move_vehicle_to_group", false, "invalid_road_vehicle_id", 0,
            ",\"vehicle_id\":" + vehicle_id + ",\"group_id\":" + group_id + ",\"groups\":" + this.GroupsJson());
        return;
    }
    if (!AIGroup.IsValidGroup(group_id) || AIGroup.GetVehicleType(group_id) != AIVehicle.VT_ROAD) {
        this.EmitPrimResult(timestamp, "move_vehicle_to_group", false, "invalid_road_group_id", 0,
            ",\"vehicle_id\":" + vehicle_id + ",\"group_id\":" + group_id + ",\"groups\":" + this.GroupsJson());
        return;
    }
    local exec = AIExecMode();
    local accounting = AIAccounting();
    local ok = AIGroup.MoveVehicle(group_id, vehicle_id);
    local err = ok ? "" : AIError.GetLastErrorString();
    local cost = accounting.GetCosts();
    local after_group = AIVehicle.GetGroupID(vehicle_id);
    this.EmitPrimResult(timestamp, "move_vehicle_to_group", ok, err, cost,
        ",\"vehicle_id\":" + vehicle_id
        + ",\"group_id\":" + group_id
        + ",\"vehicle_group_id\":" + after_group
        + ",\"vehicle_group_name\":\"" + this.JsonEscape(this.GroupName(after_group)) + "\""
        + ",\"groups\":" + this.GroupsJson());
}

function McpBridge::PrimSetGroupAutoReplace(group_id, engine_id_old, engine_id_new, timestamp)
{
    if (!AIGroup.IsValidGroup(group_id) || AIGroup.GetVehicleType(group_id) != AIVehicle.VT_ROAD) {
        this.EmitPrimResult(timestamp, "set_group_autoreplace", false, "invalid_road_group_id", 0,
            ",\"group_id\":" + group_id + ",\"engine_id_old\":" + engine_id_old + ",\"engine_id_new\":" + engine_id_new
            + ",\"replacement_engine_id\":-1,\"groups\":" + this.GroupsJson());
        return;
    }
    if (!AIEngine.IsValidEngine(engine_id_old) || AIEngine.GetVehicleType(engine_id_old) != AIVehicle.VT_ROAD) {
        this.EmitPrimResult(timestamp, "set_group_autoreplace", false, "invalid_old_road_engine", 0,
            ",\"group_id\":" + group_id + ",\"engine_id_old\":" + engine_id_old + ",\"engine_id_new\":" + engine_id_new
            + ",\"replacement_engine_id\":-1,\"groups\":" + this.GroupsJson());
        return;
    }
    if (!AIEngine.IsValidEngine(engine_id_new) || AIEngine.GetVehicleType(engine_id_new) != AIVehicle.VT_ROAD || !AIEngine.IsBuildable(engine_id_new)) {
        this.EmitPrimResult(timestamp, "set_group_autoreplace", false, "invalid_or_unbuildable_new_road_engine", 0,
            ",\"group_id\":" + group_id + ",\"engine_id_old\":" + engine_id_old + ",\"engine_id_new\":" + engine_id_new
            + ",\"replacement_engine_id\":-1,\"groups\":" + this.GroupsJson());
        return;
    }
    /* AIGroup.SetAutoReplace only preconditions on the new engine being
     * buildable — there is no cargo-match requirement. OpenTTD refits the
     * replacement toward the old vehicles' cargo where it can, so a bridge-side
     * cargo-equality gate wrongly rejected valid rules (e.g. a mail-default bus
     * refittable to passengers). The cargo types are still echoed for context. */
    local old_cargo = AIEngine.GetCargoType(engine_id_old);
    local new_cargo = AIEngine.GetCargoType(engine_id_new);
    local exec = AIExecMode();
    local accounting = AIAccounting();
    local ok = AIGroup.SetAutoReplace(group_id, engine_id_old, engine_id_new);
    local err = ok ? "" : AIError.GetLastErrorString();
    local cost = accounting.GetCosts();
    local replacement = AIGroup.GetEngineReplacement(group_id, engine_id_old);
    this.EmitPrimResult(timestamp, "set_group_autoreplace", ok, err, cost,
        ",\"group_id\":" + group_id
        + ",\"group_name\":\"" + this.JsonEscape(this.GroupName(group_id)) + "\""
        + ",\"engine_id_old\":" + engine_id_old
        + ",\"engine_id_new\":" + engine_id_new
        + ",\"engine_cargo_type_old\":" + old_cargo
        + ",\"engine_cargo_type_new\":" + new_cargo
        + ",\"replacement_engine_id\":" + replacement
        + ",\"replacement_engine_name\":\"" + this.JsonEscape(AIEngine.IsValidEngine(replacement) ? AIEngine.GetName(replacement) : "invalid") + "\""
        + ",\"autoreplace_rules\":" + this.AutoreplaceRulesJson(group_id, this.RoadEngineCandidates())
        + ",\"groups\":" + this.GroupsJson());
}

/* Order surgery. OpenTTD 15.1 script_order.hpp exposes
 * AIOrder.InsertOrder(vehicle, position, destination, flags),
 * AIOrder.RemoveOrder(vehicle, position), AIOrder.SetOrderFlags, and
 * AIOrder.GetOrderFlags. Positions are 0-based manual order indices and
 * InsertOrder inserts before an existing valid position. Shared order lists are
 * intentionally not hidden: these commands mutate the selected vehicle's actual
 * order list. */
function McpBridge::PrimInsertOrder(vehicle_id, position, stop_tile, order_flags, timestamp)
{
    if (!AIVehicle.IsValidVehicle(vehicle_id)) {
        this.EmitPrimResult(timestamp, "insert_order", false, "invalid_vehicle_id", 0,
            ",\"vehicle_id\":" + vehicle_id + ",\"position\":" + position + ",\"stop_tile\":" + stop_tile
            + ",\"requested_flags\":" + order_flags + ",\"order_flags\":[]");
        return;
    }
    if (!AIMap.IsValidTile(stop_tile)) {
        this.EmitPrimResult(timestamp, "insert_order", false, "invalid_stop_tile", 0,
            ",\"vehicle_id\":" + vehicle_id + ",\"position\":" + position + ",\"stop_tile\":" + stop_tile
            + ",\"requested_flags\":" + order_flags
            + ",\"order_count\":" + AIOrder.GetOrderCount(vehicle_id)
            + ",\"order_destinations\":" + this.OrderDestinationsJson(vehicle_id)
            + ",\"order_stations\":" + this.OrderStationsJson(vehicle_id)
            + ",\"order_flags\":" + this.OrderFlagsJson(vehicle_id));
        return;
    }
    /* AIOrder.InsertOrder accepts position in [0, order_count]: position ==
     * order_count appends at the end, which is also the only way to seed the
     * first order of an empty list. AIOrder.IsValidVehicleOrder is false for
     * that boundary, so validate the numeric range directly instead. */
    local order_count = AIOrder.GetOrderCount(vehicle_id);
    if (position < 0 || position > order_count) {
        this.EmitPrimResult(timestamp, "insert_order", false, "invalid_order_position", 0,
            ",\"vehicle_id\":" + vehicle_id + ",\"position\":" + position + ",\"stop_tile\":" + stop_tile
            + ",\"requested_flags\":" + order_flags
            + ",\"order_count\":" + order_count
            + ",\"order_destinations\":" + this.OrderDestinationsJson(vehicle_id)
            + ",\"order_stations\":" + this.OrderStationsJson(vehicle_id)
            + ",\"order_flags\":" + this.OrderFlagsJson(vehicle_id));
        return;
    }
    if (!AIOrder.AreOrderFlagsValid(stop_tile, order_flags)) {
        this.EmitPrimResult(timestamp, "insert_order", false, "invalid_order_flags", 0,
            ",\"vehicle_id\":" + vehicle_id + ",\"position\":" + position + ",\"stop_tile\":" + stop_tile
            + ",\"requested_flags\":" + order_flags
            + ",\"order_count\":" + AIOrder.GetOrderCount(vehicle_id)
            + ",\"order_destinations\":" + this.OrderDestinationsJson(vehicle_id)
            + ",\"order_stations\":" + this.OrderStationsJson(vehicle_id)
            + ",\"order_flags\":" + this.OrderFlagsJson(vehicle_id));
        return;
    }
    local exec = AIExecMode();
    local accounting = AIAccounting();
    local ok = AIOrder.InsertOrder(vehicle_id, position, stop_tile, order_flags);
    local err = ok ? "" : AIError.GetLastErrorString();
    local cost = accounting.GetCosts();
    this.EmitPrimResult(timestamp, "insert_order", ok, err, cost,
        ",\"vehicle_id\":" + vehicle_id + ",\"position\":" + position + ",\"stop_tile\":" + stop_tile
        + ",\"requested_flags\":" + order_flags
        + ",\"order_count\":" + AIOrder.GetOrderCount(vehicle_id)
        + ",\"order_destinations\":" + this.OrderDestinationsJson(vehicle_id)
        + ",\"order_stations\":" + this.OrderStationsJson(vehicle_id)
        + ",\"order_flags\":" + this.OrderFlagsJson(vehicle_id));
}

function McpBridge::PrimDeleteOrder(vehicle_id, position, timestamp)
{
    if (!AIVehicle.IsValidVehicle(vehicle_id)) {
        this.EmitPrimResult(timestamp, "delete_order", false, "invalid_vehicle_id", 0,
            ",\"vehicle_id\":" + vehicle_id + ",\"position\":" + position);
        return;
    }
    if (!AIOrder.IsValidVehicleOrder(vehicle_id, position)) {
        this.EmitPrimResult(timestamp, "delete_order", false, "invalid_order_position", 0,
            ",\"vehicle_id\":" + vehicle_id + ",\"position\":" + position
            + ",\"order_count\":" + AIOrder.GetOrderCount(vehicle_id)
            + ",\"order_destinations\":" + this.OrderDestinationsJson(vehicle_id)
            + ",\"order_stations\":" + this.OrderStationsJson(vehicle_id)
            + ",\"order_flags\":" + this.OrderFlagsJson(vehicle_id));
        return;
    }
    local exec = AIExecMode();
    local accounting = AIAccounting();
    local ok = AIOrder.RemoveOrder(vehicle_id, position);
    local err = ok ? "" : AIError.GetLastErrorString();
    local cost = accounting.GetCosts();
    this.EmitPrimResult(timestamp, "delete_order", ok, err, cost,
        ",\"vehicle_id\":" + vehicle_id + ",\"position\":" + position
        + ",\"order_count\":" + AIOrder.GetOrderCount(vehicle_id)
        + ",\"order_destinations\":" + this.OrderDestinationsJson(vehicle_id)
        + ",\"order_stations\":" + this.OrderStationsJson(vehicle_id)
        + ",\"order_flags\":" + this.OrderFlagsJson(vehicle_id));
}

function McpBridge::PrimSetOrderFlags(vehicle_id, position, order_flags, timestamp)
{
    if (!AIVehicle.IsValidVehicle(vehicle_id)) {
        this.EmitPrimResult(timestamp, "set_order_flags", false, "invalid_vehicle_id", 0,
            ",\"vehicle_id\":" + vehicle_id + ",\"position\":" + position
            + ",\"requested_flags\":" + order_flags + ",\"order_flags\":[]");
        return;
    }
    if (!AIOrder.IsValidVehicleOrder(vehicle_id, position)) {
        this.EmitPrimResult(timestamp, "set_order_flags", false, "invalid_order_position", 0,
            ",\"vehicle_id\":" + vehicle_id + ",\"position\":" + position
            + ",\"requested_flags\":" + order_flags
            + ",\"order_count\":" + AIOrder.GetOrderCount(vehicle_id)
            + ",\"order_destinations\":" + this.OrderDestinationsJson(vehicle_id)
            + ",\"order_stations\":" + this.OrderStationsJson(vehicle_id)
            + ",\"order_flags\":" + this.OrderFlagsJson(vehicle_id));
        return;
    }
    if (AIOrder.IsConditionalOrder(vehicle_id, position) || AIOrder.IsVoidOrder(vehicle_id, position)) {
        this.EmitPrimResult(timestamp, "set_order_flags", false, "not_a_station_or_depot_order", 0,
            ",\"vehicle_id\":" + vehicle_id + ",\"position\":" + position
            + ",\"requested_flags\":" + order_flags
            + ",\"order_count\":" + AIOrder.GetOrderCount(vehicle_id)
            + ",\"order_destinations\":" + this.OrderDestinationsJson(vehicle_id)
            + ",\"order_stations\":" + this.OrderStationsJson(vehicle_id)
            + ",\"order_flags\":" + this.OrderFlagsJson(vehicle_id));
        return;
    }
    local dest = AIOrder.GetOrderDestination(vehicle_id, position);
    if (!AIOrder.AreOrderFlagsValid(dest, order_flags)) {
        this.EmitPrimResult(timestamp, "set_order_flags", false, "invalid_order_flags", 0,
            ",\"vehicle_id\":" + vehicle_id + ",\"position\":" + position
            + ",\"requested_flags\":" + order_flags
            + ",\"order_count\":" + AIOrder.GetOrderCount(vehicle_id)
            + ",\"order_destinations\":" + this.OrderDestinationsJson(vehicle_id)
            + ",\"order_stations\":" + this.OrderStationsJson(vehicle_id)
            + ",\"order_flags\":" + this.OrderFlagsJson(vehicle_id));
        return;
    }
    local exec = AIExecMode();
    local accounting = AIAccounting();
    local ok = AIOrder.SetOrderFlags(vehicle_id, position, order_flags);
    local err = ok ? "" : AIError.GetLastErrorString();
    local cost = accounting.GetCosts();
    this.EmitPrimResult(timestamp, "set_order_flags", ok, err, cost,
        ",\"vehicle_id\":" + vehicle_id + ",\"position\":" + position
        + ",\"requested_flags\":" + order_flags
        + ",\"order_count\":" + AIOrder.GetOrderCount(vehicle_id)
        + ",\"order_destinations\":" + this.OrderDestinationsJson(vehicle_id)
        + ",\"order_stations\":" + this.OrderStationsJson(vehicle_id)
        + ",\"order_flags\":" + this.OrderFlagsJson(vehicle_id));
}

function McpBridge::PrimAppendOrders(vehicle_id, stop_tiles, order_flags, timestamp)
{
    if (!AIVehicle.IsValidVehicle(vehicle_id)) {
        this.EmitPrimResult(timestamp, "append_orders", false, "invalid_vehicle_id", 0, ",\"vehicle_id\":" + vehicle_id);
        return;
    }
    /* stop_tiles and order_flags are always built in lockstep by the append_orders
     * decoder (one flag entry per stop), so no length-mismatch guard is needed. */
    local exec = AIExecMode();
    local accounting = AIAccounting();
    local err = "";
    local appended = 0;
    for (local idx = 0; idx < stop_tiles.len(); idx++) {
        if (err != "") break;
        local stop = stop_tiles[idx];
        if (!AIMap.IsValidTile(stop)) { err = "invalid_stop_tile:" + stop; break; }
        if (!AIOrder.AreOrderFlagsValid(stop, order_flags[idx])) { err = "invalid_order_flags:" + stop + ":" + order_flags[idx]; break; }
        if (!AIOrder.AppendOrder(vehicle_id, stop, order_flags[idx])) { err = "append_order_failed:" + AIError.GetLastErrorString(); break; }
        appended++;
    }
    local cost = accounting.GetCosts();
    local ok = err == "";
    this.EmitPrimResult(timestamp, "append_orders", ok, err, cost,
        ",\"vehicle_id\":" + vehicle_id + ",\"orders_appended\":" + appended + ",\"order_count\":" + AIOrder.GetOrderCount(vehicle_id)
        + ",\"order_destinations\":" + this.OrderDestinationsJson(vehicle_id)
        + ",\"order_stations\":" + this.OrderStationsJson(vehicle_id)
        + ",\"order_flags\":" + this.OrderFlagsJson(vehicle_id));
}

/* Conditional order controls. One call creates one model-chosen conditional branch;
 * the bridge validates only the live AI API's legality rules and never chooses
 * a condition, comparison, value, target, or surrounding order program. */
function McpBridge::PrimInsertConditionalOrder(vehicle_id, position, condition, compare, value, jump_to, timestamp)
{
    if (!AIVehicle.IsValidVehicle(vehicle_id)) {
        this.EmitPrimResult(timestamp, "insert_conditional_order", false, "invalid_vehicle_id", 0,
            ",\"vehicle_id\":" + vehicle_id + ",\"position\":" + position
            + ",\"requested_condition_code\":" + condition + ",\"requested_compare_code\":" + compare
            + ",\"requested_compare_value\":" + value + ",\"requested_jump_to\":" + jump_to
            + ",\"order_count\":-1,\"order_destinations\":[],\"order_stations\":[],\"order_flags\":[],\"orders\":[]");
        return;
    }
    local order_count = AIOrder.GetOrderCount(vehicle_id);
    if (position < 0 || position > order_count) {
        this.EmitPrimResult(timestamp, "insert_conditional_order", false, "invalid_order_position", 0,
            ",\"vehicle_id\":" + vehicle_id + ",\"position\":" + position
            + ",\"requested_condition_code\":" + condition + ",\"requested_compare_code\":" + compare
            + ",\"requested_compare_value\":" + value + ",\"requested_jump_to\":" + jump_to
            + ",\"order_count\":" + order_count
            + ",\"order_destinations\":" + this.OrderDestinationsJson(vehicle_id)
            + ",\"order_stations\":" + this.OrderStationsJson(vehicle_id)
            + ",\"order_flags\":" + this.OrderFlagsJson(vehicle_id)
            + ",\"orders\":" + this.OrderListJson(vehicle_id));
        return;
    }
    /* jump_to names the final list position after insertion, so it may name
     * the new conditional itself. A conditional cannot seed an empty list
     * because Append/InsertConditionalOrder require an existing temporary
     * target before SetOrderJumpTo can apply the requested final target. */
    if (order_count == 0 || jump_to < 0 || jump_to > order_count) {
        local jump_error = order_count == 0 ? "conditional_order_requires_existing_target_order" : "invalid_jump_to_position";
        this.EmitPrimResult(timestamp, "insert_conditional_order", false, jump_error, 0,
            ",\"vehicle_id\":" + vehicle_id + ",\"position\":" + position
            + ",\"requested_condition_code\":" + condition + ",\"requested_compare_code\":" + compare
            + ",\"requested_compare_value\":" + value + ",\"requested_jump_to\":" + jump_to
            + ",\"order_count\":" + order_count
            + ",\"order_destinations\":" + this.OrderDestinationsJson(vehicle_id)
            + ",\"order_stations\":" + this.OrderStationsJson(vehicle_id)
            + ",\"order_flags\":" + this.OrderFlagsJson(vehicle_id)
            + ",\"orders\":" + this.OrderListJson(vehicle_id));
        return;
    }
    /* OC_MAX_RELIABILITY exists as value 7, but OpenTTD 15.1's live
     * SetOrderCondition rejects it. OC_INVALID is a read sentinel. */
    if (condition < AIOrder.OC_LOAD_PERCENTAGE || condition > AIOrder.OC_REMAINING_LIFETIME) {
        this.EmitPrimResult(timestamp, "insert_conditional_order", false, "invalid_or_unsettable_order_condition", 0,
            ",\"vehicle_id\":" + vehicle_id + ",\"position\":" + position
            + ",\"requested_condition_code\":" + condition + ",\"requested_compare_code\":" + compare
            + ",\"requested_compare_value\":" + value + ",\"requested_jump_to\":" + jump_to
            + ",\"order_count\":" + order_count
            + ",\"order_destinations\":" + this.OrderDestinationsJson(vehicle_id)
            + ",\"order_stations\":" + this.OrderStationsJson(vehicle_id)
            + ",\"order_flags\":" + this.OrderFlagsJson(vehicle_id)
            + ",\"orders\":" + this.OrderListJson(vehicle_id));
        return;
    }
    local valid_shape = true;
    if (condition == AIOrder.OC_REQUIRES_SERVICE) {
        valid_shape = (compare == AIOrder.CF_IS_TRUE || compare == AIOrder.CF_IS_FALSE) && value == 0;
    } else if (condition == AIOrder.OC_UNCONDITIONALLY) {
        valid_shape = compare == AIOrder.CF_EQUALS && value == 0;
    } else {
        valid_shape = compare >= AIOrder.CF_EQUALS && compare <= AIOrder.CF_MORE_EQUALS
            && value >= 0 && value < 2048;
        if (condition == AIOrder.OC_LOAD_PERCENTAGE || condition == AIOrder.OC_RELIABILITY) {
            valid_shape = valid_shape && value <= 100;
        }
    }
    if (!valid_shape) {
        this.EmitPrimResult(timestamp, "insert_conditional_order", false, "invalid_condition_compare_or_value", 0,
            ",\"vehicle_id\":" + vehicle_id + ",\"position\":" + position
            + ",\"requested_condition_code\":" + condition + ",\"requested_compare_code\":" + compare
            + ",\"requested_compare_value\":" + value + ",\"requested_jump_to\":" + jump_to
            + ",\"order_count\":" + order_count
            + ",\"order_destinations\":" + this.OrderDestinationsJson(vehicle_id)
            + ",\"order_stations\":" + this.OrderStationsJson(vehicle_id)
            + ",\"order_flags\":" + this.OrderFlagsJson(vehicle_id)
            + ",\"orders\":" + this.OrderListJson(vehicle_id));
        return;
    }

    local exec = AIExecMode();
    local accounting = AIAccounting();
    local ok = position == order_count
        ? AIOrder.AppendConditionalOrder(vehicle_id, 0)
        : AIOrder.InsertConditionalOrder(vehicle_id, position, 0);
    local created = ok;
    local err = ok ? "" : AIError.GetLastErrorString();
    local failed_step = ok ? "" : "insert";
    if (ok) {
        ok = AIOrder.SetOrderCondition(vehicle_id, position, condition);
        if (!ok) { err = AIError.GetLastErrorString(); failed_step = "condition"; }
    }
    if (ok && condition != AIOrder.OC_UNCONDITIONALLY) {
        ok = AIOrder.SetOrderCompareFunction(vehicle_id, position, compare);
        if (!ok) { err = AIError.GetLastErrorString(); failed_step = "compare"; }
    }
    if (ok && condition != AIOrder.OC_UNCONDITIONALLY && condition != AIOrder.OC_REQUIRES_SERVICE) {
        ok = AIOrder.SetOrderCompareValue(vehicle_id, position, value);
        if (!ok) { err = AIError.GetLastErrorString(); failed_step = "value"; }
    }
    if (ok) {
        ok = AIOrder.SetOrderJumpTo(vehicle_id, position, jump_to);
        if (!ok) { err = AIError.GetLastErrorString(); failed_step = "jump_to"; }
    }
    local inserted = created && AIOrder.IsValidVehicleOrder(vehicle_id, position)
        && AIOrder.IsConditionalOrder(vehicle_id, position);
    local rolled_back = false;
    local rollback_error = "";
    if (!ok && inserted) {
        rolled_back = AIOrder.RemoveOrder(vehicle_id, position);
        if (!rolled_back) rollback_error = AIError.GetLastErrorString();
    }
    local configured = ok && inserted;
    local actual_condition = configured ? AIOrder.GetOrderCondition(vehicle_id, position) : AIOrder.OC_INVALID;
    local actual_compare = configured ? AIOrder.GetOrderCompareFunction(vehicle_id, position) : AIOrder.CF_INVALID;
    local actual_value = configured ? AIOrder.GetOrderCompareValue(vehicle_id, position) : -1;
    local actual_jump_to = configured ? AIOrder.GetOrderJumpTo(vehicle_id, position) : -1;
    local cost = accounting.GetCosts();
    this.EmitPrimResult(timestamp, "insert_conditional_order", ok, err, cost,
        ",\"vehicle_id\":" + vehicle_id + ",\"position\":" + position
        + ",\"requested_condition_code\":" + condition + ",\"requested_compare_code\":" + compare
        + ",\"requested_compare_value\":" + value + ",\"requested_jump_to\":" + jump_to
        + ",\"failed_step\":\"" + failed_step + "\""
        + ",\"rolled_back\":" + this.BoolJson(rolled_back)
        + ",\"rollback_error\":\"" + this.JsonEscape(rollback_error) + "\""
        + ",\"condition\":\"" + this.OrderConditionName(actual_condition) + "\""
        + ",\"condition_code\":" + actual_condition
        + ",\"compare\":\"" + this.OrderCompareName(actual_compare) + "\""
        + ",\"compare_code\":" + actual_compare
        + ",\"compare_value\":" + actual_value + ",\"jump_to\":" + actual_jump_to
        + ",\"order_count\":" + AIOrder.GetOrderCount(vehicle_id)
        + ",\"order_destinations\":" + this.OrderDestinationsJson(vehicle_id)
        + ",\"order_stations\":" + this.OrderStationsJson(vehicle_id)
        + ",\"order_flags\":" + this.OrderFlagsJson(vehicle_id)
        + ",\"orders\":" + this.OrderListJson(vehicle_id));
}

function McpBridge::PrimSkipToOrder(vehicle_id, position, timestamp)
{
    if (!AIVehicle.IsValidVehicle(vehicle_id)) {
        this.EmitPrimResult(timestamp, "skip_to_order", false, "invalid_vehicle_id", 0,
            ",\"vehicle_id\":" + vehicle_id + ",\"position\":" + position
            + ",\"previous_position\":-1,\"current_position\":-1,\"order_count\":-1"
            + ",\"order_destinations\":[],\"order_stations\":[],\"order_flags\":[],\"orders\":[]");
        return;
    }
    local previous = AIOrder.ResolveOrderPosition(vehicle_id, AIOrder.ORDER_CURRENT);
    local exec = AIExecMode();
    local accounting = AIAccounting();
    local ok = AIOrder.SkipToOrder(vehicle_id, position);
    local err = ok ? "" : AIError.GetLastErrorString();
    local cost = accounting.GetCosts();
    this.EmitPrimResult(timestamp, "skip_to_order", ok, err, cost,
        ",\"vehicle_id\":" + vehicle_id + ",\"position\":" + position
        + ",\"previous_position\":" + previous
        + ",\"current_position\":" + AIOrder.ResolveOrderPosition(vehicle_id, AIOrder.ORDER_CURRENT)
        + ",\"order_count\":" + AIOrder.GetOrderCount(vehicle_id)
        + ",\"order_destinations\":" + this.OrderDestinationsJson(vehicle_id)
        + ",\"order_stations\":" + this.OrderStationsJson(vehicle_id)
        + ",\"order_flags\":" + this.OrderFlagsJson(vehicle_id)
        + ",\"orders\":" + this.OrderListJson(vehicle_id));
}

function McpBridge::PrimCopyShareOrders(action, vehicle_id, source_vehicle_id, share, timestamp)
{
    local exec = AIExecMode();
    local accounting = AIAccounting();
    local ok = share
        ? AIOrder.ShareOrders(vehicle_id, source_vehicle_id)
        : AIOrder.CopyOrders(vehicle_id, source_vehicle_id);
    local err = ok ? "" : AIError.GetLastErrorString();
    local vehicle_valid = AIVehicle.IsValidVehicle(vehicle_id);
    local source_valid = AIVehicle.IsValidVehicle(source_vehicle_id);
    local cost = accounting.GetCosts();
    this.EmitPrimResult(timestamp, action, ok, err, cost,
        ",\"vehicle_id\":" + vehicle_id + ",\"source_vehicle_id\":" + source_vehicle_id
        + ",\"order_count\":" + (vehicle_valid ? AIOrder.GetOrderCount(vehicle_id) : -1)
        + ",\"source_order_count\":" + (source_valid ? AIOrder.GetOrderCount(source_vehicle_id) : -1)
        + ",\"order_destinations\":" + this.OrderDestinationsJson(vehicle_id)
        + ",\"source_order_destinations\":" + this.OrderDestinationsJson(source_vehicle_id)
        + ",\"order_stations\":" + this.OrderStationsJson(vehicle_id)
        + ",\"source_order_stations\":" + this.OrderStationsJson(source_vehicle_id)
        + ",\"order_flags\":" + this.OrderFlagsJson(vehicle_id)
        + ",\"source_order_flags\":" + this.OrderFlagsJson(source_vehicle_id)
        + ",\"orders\":" + this.OrderListJson(vehicle_id)
        + ",\"source_orders\":" + this.OrderListJson(source_vehicle_id)
        + ",\"has_shared_orders\":" + this.BoolJson(vehicle_valid && AIVehicle.HasSharedOrders(vehicle_id))
        + ",\"source_has_shared_orders\":" + this.BoolJson(source_valid && AIVehicle.HasSharedOrders(source_vehicle_id)));
}

function McpBridge::PrimUnshareOrders(vehicle_id, timestamp)
{
    local exec = AIExecMode();
    local accounting = AIAccounting();
    local ok = AIOrder.UnshareOrders(vehicle_id);
    local err = ok ? "" : AIError.GetLastErrorString();
    local vehicle_valid = AIVehicle.IsValidVehicle(vehicle_id);
    local cost = accounting.GetCosts();
    this.EmitPrimResult(timestamp, "unshare_orders", ok, err, cost,
        ",\"vehicle_id\":" + vehicle_id
        + ",\"order_count\":" + (vehicle_valid ? AIOrder.GetOrderCount(vehicle_id) : -1)
        + ",\"order_destinations\":" + this.OrderDestinationsJson(vehicle_id)
        + ",\"order_stations\":" + this.OrderStationsJson(vehicle_id)
        + ",\"order_flags\":" + this.OrderFlagsJson(vehicle_id)
        + ",\"orders\":" + this.OrderListJson(vehicle_id)
        + ",\"has_shared_orders\":" + this.BoolJson(vehicle_valid && AIVehicle.HasSharedOrders(vehicle_id)));
}

function McpBridge::PrimRefitVehicle(vehicle_id, cargo_id, timestamp)
{
    if (!AIVehicle.IsValidVehicle(vehicle_id)) {
        this.EmitPrimResult(timestamp, "refit_vehicle", false, "invalid_vehicle_id", 0, ",\"vehicle_id\":" + vehicle_id + ",\"cargo_id\":" + cargo_id);
        return;
    }
    if (!AICargo.IsValidCargo(cargo_id)) {
        this.EmitPrimResult(timestamp, "refit_vehicle", false, "invalid_cargo_id", 0,
            ",\"vehicle_id\":" + vehicle_id + ",\"cargo_id\":" + cargo_id);
        return;
    }
    local engine_id = AIVehicle.GetEngineType(vehicle_id);
    local engine_valid = AIEngine.IsValidEngine(engine_id);
    local stopped_in_depot = AIVehicle.IsStoppedInDepot(vehicle_id);
    local can_refit = false;
    local fact_errors = [];
    if (engine_valid) {
        try {
            can_refit = AIEngine.CanRefitCargo(engine_id, cargo_id);
        } catch (e) {
            fact_errors.append(["refit_capability_unavailable", "can_refit"]);
        }
    } else {
        fact_errors.append(["engine_unavailable", "can_refit"]);
    }
    /* capacity_before/after are both GetCapacity for the TARGET cargo_id, not the
     * vehicle's current cargo: capacity_before is therefore typically 0 unless the
     * vehicle already carries cargo_id. REFIT_VEHICLE_DESCRIPTION discloses this so
     * the model does not read capacity_before:0 as "the vehicle had no capacity". */
    local capacity_before = -1;
    try {
        capacity_before = AIVehicle.GetCapacity(vehicle_id, cargo_id);
    } catch (e2) {
        fact_errors.append(["capacity_before_unavailable", "capacity_before"]);
    }
    local exec = AIExecMode();
    local accounting = AIAccounting();
    local ok = AIVehicle.RefitVehicle(vehicle_id, cargo_id);
    local err = ok ? "" : AIError.GetLastErrorString();
    local cost = accounting.GetCosts();
    local capacity_after = -1;
    try {
        capacity_after = AIVehicle.GetCapacity(vehicle_id, cargo_id);
    } catch (e3) {
        fact_errors.append(["capacity_after_unavailable", "capacity_after"]);
    }
    local label = AICargo.GetCargoLabel(cargo_id);
    if (label == null) label = "";
    this.EmitPrimResult(timestamp, "refit_vehicle", ok, err, cost,
        ",\"vehicle_id\":" + vehicle_id
        + ",\"cargo_id\":" + cargo_id
        + ",\"cargo_label\":\"" + this.JsonEscape(label) + "\""
        + ",\"engine_id\":" + engine_id
        + ",\"engine_valid\":" + this.BoolJson(engine_valid)
        + ",\"can_refit\":" + this.BoolJson(can_refit)
        + ",\"stopped_in_depot\":" + this.BoolJson(stopped_in_depot)
        + ",\"capacity_before\":" + capacity_before
        + ",\"capacity_after\":" + capacity_after
        + this.FactErrorsJson(fact_errors));
}

function McpBridge::PrimStartStopVehicle(vehicle_id, want_running, timestamp)
{
    local action = want_running ? "start_vehicle" : "stop_vehicle";
    if (!AIVehicle.IsValidVehicle(vehicle_id)) {
        this.EmitPrimResult(timestamp, action, false, "invalid_vehicle_id", 0, ",\"vehicle_id\":" + vehicle_id);
        return;
    }
    local exec = AIExecMode();
    local is_stopped = AIVehicle.GetState(vehicle_id) == AIVehicle.VS_STOPPED || AIVehicle.IsStoppedInDepot(vehicle_id);
    local err = "";
    local toggled = false;
    if (want_running && is_stopped) {
        if (!AIVehicle.StartStopVehicle(vehicle_id)) err = AIError.GetLastErrorString(); else toggled = true;
    } else if (!want_running && !is_stopped) {
        if (!AIVehicle.StartStopVehicle(vehicle_id)) err = AIError.GetLastErrorString(); else toggled = true;
    }
    local ok = err == "";
    this.EmitPrimResult(timestamp, action, ok, err, 0,
        ",\"vehicle_id\":" + vehicle_id + ",\"toggled\":" + this.BoolJson(toggled)
        + ",\"state\":" + AIVehicle.GetState(vehicle_id)
        + ",\"stopped_in_depot\":" + this.BoolJson(AIVehicle.IsStoppedInDepot(vehicle_id)));
}

/* reverse_vehicle: AIVehicle.ReverseVehicle — CmdTurnRoadVeh for road,
 * CmdReverseTrainDirection for rail. A true return means the vehicle has
 * started to turn, nothing more (live-probed on 15.1: the reversing bus held
 * its tile for ~30 ticks before retracing), so the result claims only
 * started_to_turn. The probed refusals — road vehicle stopped, road vehicle
 * inside a depot — return false with the generic ERR_UNKNOWN and change
 * nothing; a non-road, non-rail vehicle returns ERR_PRECONDITION_FAILED.
 * Every refusal is reported as-is, never retried around. */
function McpBridge::PrimReverseVehicle(vehicle_id, timestamp)
{
    if (!AIVehicle.IsValidVehicle(vehicle_id)) {
        this.EmitPrimResult(timestamp, "reverse_vehicle", false, "invalid_vehicle_id", 0, ",\"vehicle_id\":" + vehicle_id);
        return;
    }
    local exec = AIExecMode();
    local ok = AIVehicle.ReverseVehicle(vehicle_id);
    local err = ok ? "" : AIError.GetLastErrorString();
    this.EmitPrimResult(timestamp, "reverse_vehicle", ok, err, 0,
        ",\"vehicle_id\":" + vehicle_id
        + ",\"started_to_turn\":" + this.BoolJson(ok)
        + ",\"state_name\":\"" + this.VehicleStateName(AIVehicle.GetState(vehicle_id)) + "\""
        + ",\"stopped_in_depot\":" + this.BoolJson(AIVehicle.IsStoppedInDepot(vehicle_id)));
}

function McpBridge::PrimSendVehicleToDepot(vehicle_id, timestamp)
{
    if (!AIVehicle.IsValidVehicle(vehicle_id)) {
        this.EmitPrimResult(timestamp, "send_vehicle_to_depot", false, "invalid_vehicle_id", 0, ",\"vehicle_id\":" + vehicle_id);
        return;
    }
    local exec = AIExecMode();
    local ok = AIVehicle.SendVehicleToDepot(vehicle_id);
    local err = ok ? "" : AIError.GetLastErrorString();
    this.EmitPrimResult(timestamp, "send_vehicle_to_depot", ok, err, 0, ",\"vehicle_id\":" + vehicle_id);
}

function McpBridge::PrimSellVehicle(vehicle_id, timestamp)
{
    if (!AIVehicle.IsValidVehicle(vehicle_id)) {
        this.EmitPrimResult(timestamp, "sell_vehicle", false, "invalid_vehicle_id", 0, ",\"vehicle_id\":" + vehicle_id);
        return;
    }
    local exec = AIExecMode();
    local accounting = AIAccounting();
    local ok = AIVehicle.SellVehicle(vehicle_id);
    local err = ok ? "" : AIError.GetLastErrorString();
    local cost = accounting.GetCosts();
    this.EmitPrimResult(timestamp, "sell_vehicle", ok, err, cost, ",\"vehicle_id\":" + vehicle_id);
}

function McpBridge::PrimSetLoan(amount, timestamp)
{
    local exec = AIExecMode();
    local accounting = AIAccounting();
    local ok = AICompany.SetLoanAmount(amount);
    local err = ok ? "" : AIError.GetLastErrorString();
    local cost = accounting.GetCosts();
    this.EmitPrimResult(timestamp, "set_loan", ok, err, cost,
        ",\"requested_amount\":" + amount + ",\"loan_amount\":" + AICompany.GetLoanAmount() + ",\"max_loan_amount\":" + AICompany.GetMaxLoanAmount());
}

function McpBridge::PrimDemolishTile(tile, timestamp)
{
    if (!AIMap.IsValidTile(tile)) {
        this.EmitPrimResult(timestamp, "demolish_tile", false, "invalid_tile", 0, ",\"tile\":" + tile);
        return;
    }
    local exec = AIExecMode();
    local accounting = AIAccounting();
    local ok = AITile.DemolishTile(tile);
    local err = ok ? "" : AIError.GetLastErrorString();
    local cost = accounting.GetCosts();
    this.EmitPrimResult(timestamp, "demolish_tile", ok, err, cost, ",\"tile\":" + tile);
}

/* remove_road: the AI-API path that issues CmdRemoveLongRoad.
 * AIRoad.RemoveRoad only handles a single-axis span; an off-axis pair returns
 * false with the real ERR_PRECONDITION_FAILED (live-probed on OpenTTD 15.1),
 * which is reported as-is. */
function McpBridge::PrimRemoveRoad(from_tile, to_tile, timestamp)
{
    if (!AIMap.IsValidTile(from_tile) || !AIMap.IsValidTile(to_tile)) {
        this.EmitPrimResult(timestamp, "remove_road", false, "invalid_tile", 0, ",\"from_tile\":" + from_tile + ",\"to_tile\":" + to_tile);
        return;
    }
    local exec = AIExecMode();
    local accounting = AIAccounting();
    local ok = AIRoad.RemoveRoad(from_tile, to_tile);
    local err = ok ? "" : AIError.GetLastErrorString();
    local cost = accounting.GetCosts();
    this.EmitPrimResult(timestamp, "remove_road", ok, err, cost, ",\"from_tile\":" + from_tile + ",\"to_tile\":" + to_tile);
}
