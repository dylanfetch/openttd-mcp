# OpenTTD MCP tool catalog

This file is generated from the server's served MCP registry; edit the registry or generator instead of this file.

Catalog profile:

- `enable_skill=true`
- `enable_vision=true`
- `end_episode_mode=standard`

This profile includes the optional skill and vision surfaces rather than the command-line defaults. Vision also adds fields to existing subject-tool schemas. The `end_episode` description shown is the standard-mode version rather than the distress-mode version. `openttd-mcp play` serves the command-line defaults: no `get_bus_route_skill`, no `view_map`, and no vision-added schema fields — the rest of this catalog matches what it serves.

Regenerate it with:

```console
uv run python tools/generate_tool_catalog.py
```

Detect drift without rewriting the file with:

```console
uv run python tools/generate_tool_catalog.py --check
```

## `get_tile`

Mutates game state: no

Read the full per-tile facts for one tile id: the same per-tile record get_region returns with detail='tiles'. Optional cargo_id adds cargo_id/cargo_label plus cargo_acceptance, cargo_production, cargo_acceptance_radius4, and cargo_production_radius4 measured for that cargo; omitting cargo_id returns only the default passenger fields. cargo_id is the integer cargo type reported as the cargo_type field by list_engines and by list_industries/get_industry produced_cargo/accepted_cargo rows (same integer, different field name). Plain road tiles additionally report per-direction road_bits facts (true / false / "unknown"), distinct from road_connections: a one-sided half-road stub toward a roadless neighbour is road_bits true while road_connections stays false. "unknown" means road material exists on the shared edge but the game cannot attribute the side. industry_id identifies the industry occupying the tile anywhere on its footprint; a tile no industry occupies reports industry_id -1. The station tile of an industry's own dock or heliport reports no industry. A numeric cargo slot unavailable in the current game returns invalid_cargo_id before any cargo facts or mutation are attempted. If an unexpected bridge/API failure affects only some returned facts, the enclosing row carries fact_errors entries with bounded semantic code and field names; a listed fallback value is not a game fact. Optional screenshot=true returns these JSON facts plus one fresh 384-pixel image requested around the subject tile reported by this read. At map pixel edges, the same clamping as view_map can move the actual crop center; rendered_center_tile reports it. It remains one tool call: the subject is read first, and no image is attempted when that read fails. If the image cannot be captured, the facts remain available and screenshot reports its own bounded failure. The image is the same unannotated OpenTTD rendering and crop used by view_map. The game runs in real time: rendering happens after the factual read, so a moving vehicle can leave the reported subject tile before the image is drawn.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "cargo_id": {
      "maximum": 63,
      "minimum": 0,
      "type": "integer"
    },
    "screenshot": {
      "default": false,
      "type": "boolean"
    },
    "tile": {
      "type": "integer"
    }
  },
  "required": [
    "tile"
  ],
  "type": "object"
}
```

## `get_region`

Mutates game state: no

Survey a square region around a center tile: detail='grid' returns a one-character-per-tile category map of the whole region (about 1.5-2.7KB), and detail='tiles' (the default) returns the full per-tile facts (about 120KB at radius 6). radius starts at 0 and its maximum depends on the mode: 12 with detail='grid' (a 25x25 region) and 6 with detail='tiles' (a 13x13 region), because tiles carries every per-tile record while grid carries one character per tile. A radius above the mode's maximum is rejected and the error names that maximum; nothing is silently reduced. The region is cropped at the map edge. In grid mode each row reports its y, a row_start_tile, and a cells string, with tile = row_start_tile + (x - x_min); the result carries its own legend and the category_precedence that decides which single category a tile in several of them shows. In tiles mode, where=[...] keeps only the tiles whose named fields are all true and fields=[...] keeps only the named fields plus tile/x/y; the filter runs before the projection, so a where name need not also be listed in fields. fields and where draw on two different name lists and are not interchangeable: fields accepts any name under 'Valid fields' below, while where accepts only the boolean category names under 'Valid where filters', a strict subset of that list. Measurement and identifier fields such as cargo_acceptance, passenger_production, industry_id, or slope can be selected with fields but are rejected as where filters, and the rejection names every filter where does accept. fields, where, and cargo_id are rejected with detail='grid'. Optional cargo_id adds cargo_id/cargo_label/cargo_acceptance/cargo_production and the radius4 cargo fields for that cargo on top of the passenger_* fields every tile already carries; naming a cargo_* field in fields without cargo_id is rejected rather than silently returning nothing. cargo_id is the integer cargo type reported as the cargo_type field by list_engines and by list_industries/get_industry produced_cargo/accepted_cargo rows (same integer, different field name). passenger_acceptance and passenger_production are the raw passenger-cargo acceptance and production units a single-tile bus-stop catchment at that tile collects. A station accepts a cargo when the acceptance summed over its catchment is at least 8 units, and the game recomputes that sum periodically from the surrounding tiles, so a measurement near 8 units can start and stop being accepted over time as town buildings appear and disappear. cargo_acceptance and cargo_production are the same measurement over a single-tile truck-stop catchment, and the *_radius4 variants measure a fixed radius-4 catchment instead. get_tile reports these same per-tile facts for one tile id. Valid fields: buildable, cargo_acceptance, cargo_acceptance_radius4, cargo_id, cargo_label, cargo_production, cargo_production_radius4, closest_town_id, coast, drive_through_back_tile, drive_through_road_station, fact_errors, farm, house, in_largest_town_influence, industry_id, max_height, min_height, owner, passenger_acceptance, passenger_acceptance_radius4, passenger_production, passenger_production_radius4, river, road_bits, road_connections, road_depot_front_tile, road_depot_tile, road_station_front_tile, road_station_tile, road_tile, rock, rough, sea, slope, station_tile, terrain_type, tile, tree, water, x, y. Valid where filters: buildable, coast, drive_through_road_station, farm, house, in_largest_town_influence, river, road_depot_tile, road_station_tile, road_tile, rock, rough, sea, station_tile, tree, water. A numeric cargo slot unavailable in the current game returns invalid_cargo_id before any cargo facts or mutation are attempted. Unexpected per-tile API failures keep stable placeholder types but qualify them with bounded semantic errors: tiles mode preserves each row's fact_errors through fields projection, where_fact_error_tiles names rows a where filter could not classify, and grid.fact_error_tiles names category inputs that were unavailable. Each entry carries only code and field, never raw exception text.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "cargo_id": {
      "description": "detail='tiles' only. Cargo type integer (the cargo_type field from list_engines and list_industries); adds the cargo_* fields for that cargo to every tile.",
      "maximum": 63,
      "minimum": 0,
      "type": "integer"
    },
    "center_tile": {
      "type": "integer"
    },
    "detail": {
      "default": "tiles",
      "description": "'tiles' returns full per-tile facts (about 120KB at radius 6, its maximum); 'grid' returns a one-character-per-tile category map of the same region (about 1.5KB at radius 6, about 2.7KB at radius 12, its maximum).",
      "enum": [
        "tiles",
        "grid"
      ],
      "type": "string"
    },
    "fields": {
      "default": [],
      "description": "detail='tiles' only. Tile field names to keep; tile/x/y are always kept. Accepts any name in the 'Valid fields' list in this tool's description.",
      "items": {
        "type": "string"
      },
      "type": "array"
    },
    "radius": {
      "description": "Maximum depends on detail: 6 with detail='tiles', 12 with detail='grid'. A radius above the mode's maximum is rejected, never reduced.",
      "maximum": 12,
      "minimum": 0,
      "type": "integer"
    },
    "where": {
      "default": [],
      "description": "detail='tiles' only. Keeps just the tiles where every named field is true, so it accepts only these boolean tile categories: buildable, coast, drive_through_road_station, farm, house, in_largest_town_influence, river, road_depot_tile, road_station_tile, road_tile, rock, rough, sea, station_tile, tree, water. Measurement and identifier fields such as cargo_acceptance, passenger_production, or industry_id are valid in fields but are rejected here.",
      "items": {
        "type": "string"
      },
      "type": "array"
    }
  },
  "required": [
    "center_tile",
    "radius"
  ],
  "type": "object"
}
```

## `view_map`

Mutates game state: no

See any part of the map as an image: returns a square crop centered on `tile`, freshly rendered by default, showing the world exactly as the game draws it for a human player - terrain and water barriers, roads and bridges as actually built, town and industry surroundings, and your own stations, depots, and vehicles at their current positions. A single crop shows things no text query reports: whether a route physically connects end to end, how a station sits relative to its town or industry, the lay of land and water between two points, and where vehicles stand - including many vehicles bunched at one station or depot. `refresh=false` re-crops the most recent render instead of drawing a new one, letting several regions be viewed from one consistent moment; the returned `age_seconds` and `mutations_since_capture` metadata report that frame's currency. `window` is the crop side length in pixels, accepted from 64 through 2048 inclusive (default 384; larger shows more area with less detail). Coordinate convention: tile index = y * map_width + x. `frame_corner_tiles` reports the tile-space x/y coordinate and `in_map` status at each visible corner; out-of-map corners are not clamped. `rendered_center_tile` reports the tile-space coordinate actually at the crop center after pixel-edge clamping; when in-map, its `tile` is the containing tile id from int(x), int(y), and is otherwise omitted. `tile_extent` reports the frame's extent along the map x/y axes. `visible_diamond_tile_bbox` is the axis-aligned tile-space bounding box of the visible diamond: because the map is isometric, it is not a claim that every tile in that box is shown. The image is the game's own unannotated rendering; exact tile identities come from the JSON metadata and the query tools, not from reading the image. The image contains no tile ids.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "refresh": {
      "default": true,
      "type": "boolean"
    },
    "tile": {
      "type": "integer"
    },
    "window": {
      "default": 384,
      "maximum": 2048,
      "minimum": 64,
      "type": "integer"
    }
  },
  "required": [
    "tile"
  ],
  "type": "object"
}
```

## `list_towns`

Mutates game state: no

List every town on the map with its town_id, name, population, and the x/y of its location tile. The location tile is reported as x/y only, not as a tile id; tile id = y * map width + x.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {},
  "required": [],
  "type": "object"
}
```

## `get_town`

Mutates game state: no

Read one town's cargo statistics by town_id: what its houses produced last month, how much of that reached station waiting queues, and what it received toward growth — where list_towns only enumerates every town's id, name, population, and location. The envelope repeats that town's town_id, name, population, and x/y with the same meanings list_towns gives them. cargo_stats carries one row per cargo type, with cargo_type, cargo_label, last_month_production (units the town's houses produced), last_month_to_stations (how much of that production was moved into the waiting queues of stations in range — cargo entering a queue, not cargo loaded onto a vehicle and not cargo delivered anywhere), and last_month_transported_percentage (0-100, the game's own percentage of that month's production counted under last_month_to_stations). Rows whose three figures are all zero are left out, so a cargo the town has no record for is absent rather than listed. growth_cargo carries one row per town effect the cargoes in this game map to, with towneffect_id, towneffect_name (passengers, mail, goods, water, food), last_month_received (units of that effect's cargo the town accepted) and cargo_goal (units per month of that effect the game requires for the town to grow); several cargo types can share one town effect, and cargoes with no town effect are not listed. Every last_month_* figure covers the last fully-closed economy month and never the current partial month, a different period than a station or vehicle reading taken now. A town founded or expanded within the last month can report a seeded population-based estimate for passenger and mail production rather than a completed month's count. Return values are passed through as the game reports them: -1 means the game reports nothing for that town or cargo, 0 from a valid town means the town holds no record in that slot, and the two are not merged. A row carries metric_error when a figure could not be read at all. An unknown or stale town_id comes back as invalid_town_id. If an unexpected bridge/API failure affects only some returned facts, the enclosing row carries fact_errors entries with bounded semantic code and field names; a listed fallback value is not a game fact. If an unexpected bridge/API failure prevents this record from being read confidently, the call fails with error sensor_api_error and a sensor_error object containing bounded semantic code and field names.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "town_id": {
      "type": "integer"
    }
  },
  "required": [
    "town_id"
  ],
  "type": "object"
}
```

## `list_my_vehicles`

Mutates game state: no

List every road vehicle your company owns, with its current state, age, and group. Each vehicle row carries the game's raw vehicle-state integer as state plus a stable state_name enum string (running, stopped, in_depot, at_station, broken, crashed, invalid/unknown), the in_depot and stopped_in_depot booleans, running_cost, has_shared_orders, age_days/max_age_days/age_left_days/reliability, the derived needs_replacement flag with the age_left threshold it uses, and group_id/group_name. When has_shared_orders is true, an order edit made through any vehicle sharing that order list applies to every vehicle sharing it. The routes array restates those same vehicles by the station stops their orders visit (station_ids and station_tiles) with per-vehicle profit this year and last year; the groups array reports your road-vehicle groups and their autoreplace rules. Stations appear here only as the stops a vehicle's orders point at — list_my_stations lists the stations your company owns.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {},
  "required": [],
  "type": "object"
}
```

## `get_vehicle`

Mutates game state: no

Read one road-class vehicle (bus, truck, or tram) your company owns by vehicle_id, without advancing game time. The result carries a vehicle object with the vehicle row fields from list_my_vehicles, plus top-level detailed orders and cargo_capacities arrays. The vehicle row fields include identity and engine, current tile/x/y, raw state and state_name, group, speed, depot state, current-order facts, profit, current value, running_cost, has_shared_orders, age, reliability, and the derived needs_replacement flag. When has_shared_orders is true, an order edit made through any vehicle sharing that order list applies to every vehicle sharing it. The orders array carries one object per 0-based order position. Every object carries position, is_conditional, and is_void. A conditional position also carries condition and compare names with their condition_code and compare_code OpenTTD values, plus compare_value and jump_to. A position that is neither conditional nor void also carries is_goto_station, is_goto_depot, destination, flags, and station_id when the destination maps to a valid station. Void positions carry only position and the two booleans, so they omit destination and flags. A station order stores only the station, so station_id is its stable identity; destination is one representative tile of that station, re-computed by the game on every read, and on a station with several stops it need not be the tile the order was created with nor a stop this vehicle's type uses. A destination of -1 means the game exposes no concrete tile for that order: the ordered station or depot no longer exists, or the order is a nearest-depot order. The vehicle row's cargo_type and cargo_load describe the engine's default cargo and the load of that cargo. cargo_capacities reports the vehicle's actual per-cargo capacity and load as cargo_type, cargo_label, capacity, and load, omitting cargo types with zero capacity. current_order_index resolves the manual order-list position, while current_order_destination reports the active order's destination when OpenTTD exposes a concrete tile; an automatic or manual depot trip can therefore make them refer to different orders, and a nearest-depot order reports -1. The result also reports the age-left threshold used by needs_replacement. An unknown, stale, other-company, or non-road vehicle_id returns invalid_road_vehicle_id instead of another subject's facts. Optional screenshot=true returns these JSON facts plus one fresh 384-pixel image requested around the subject tile reported by this read. At map pixel edges, the same clamping as view_map can move the actual crop center; rendered_center_tile reports it. It remains one tool call: the subject is read first, and no image is attempted when that read fails. If the image cannot be captured, the facts remain available and screenshot reports its own bounded failure. The image is the same unannotated OpenTTD rendering and crop used by view_map. The game runs in real time: rendering happens after the factual read, so a moving vehicle can leave the reported subject tile before the image is drawn.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "screenshot": {
      "default": false,
      "type": "boolean"
    },
    "vehicle_id": {
      "type": "integer"
    }
  },
  "required": [
    "vehicle_id"
  ],
  "type": "object"
}
```

## `list_my_stations`

Mutates game state: no

List every road station your company owns, including ones no vehicle is routed through. Each row reports station_id, name, the station's location tile (the tile its sign stands on — not guaranteed to be a station tile) with x/y, and the is_bus_stop/is_truck_stop flags — one station id can carry both stop kinds. waiting_passengers and passenger_rating cover the passenger cargo only. Both are -1 when no passenger cargo resolves, which happens when this game has no buildable passenger road engine to read one from; otherwise waiting_passengers is the real count (0 at a station nothing is waiting at) and passenger_rating alone is -1 until the game tracks a rating for that station. get_my_station reports one station in full: every cargo it holds, what it accepts, its stop tiles, and the vehicles with orders to it. If an unexpected bridge/API failure affects only some returned facts, the enclosing row carries fact_errors entries with bounded semantic code and field names; a listed fallback value is not a game fact.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {},
  "required": [],
  "type": "object"
}
```

## `list_my_depots`

Mutates game state: no

List your company's road-depot inventory; vehicles and stations have their own list_my_vehicles and list_my_stations inventories. Depots are addressed by tile here: the script API this server is built on exposes no depot object, so a depot carries no id and no name on this surface. Each depot row reports tile, x, y, front_tile, front_x, front_y, and vehicles_inside. tile is the depot tile buy_vehicle takes as its depot_tile; front_tile is the physical entrance that build_road_depot returns as actual_front_tile and check_route_connectivity can take. It is not the build_road_depot or check_build_road_depot front_tile direction selector, which can be distant. send_vehicle_to_depot names no depot at all — the game picks the closest one the vehicle can drive to. vehicles_inside is the number of your vehicles physically inside that depot right now, counted by scanning your vehicles. Read-only. If an unexpected bridge/API failure affects only some returned facts, the enclosing row carries fact_errors entries with bounded semantic code and field names; a listed fallback value is not a game fact.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {},
  "required": [],
  "type": "object"
}
```

## `get_my_finances`

Mutates game state: no

Report your company's money right now: bank balance, loan, maximum loan and loan step, plus income and expenses for the quarter in progress and the one before it. interest_rate_percent is the game's configured loan interest rate, the same integer percent the game's finance window shows; a normally generated game charges loan interest at this yearly rate on the loan and on a negative bank balance. interest_rate_percent is -1 only if the game reports no such setting. loan_amount must be a multiple of loan_interval and no more than max_loan_amount; set_loan rejects any other amount. quarterly_income_0/quarterly_expenses_0 cover the quarter in progress and quarterly_income_1/quarterly_expenses_1 the quarter before it. Those quarterly figures count only recurring money — vehicle income on one side, vehicle running costs, maintenance and loan interest on the other — so construction spending, vehicle purchases and vehicle sales are absent from them and a quarter can show income above expenses while the bank balance falls. Expense figures are reported as negative numbers. Also reports the current in-game date. get_my_company returns these same balance and loan facts alongside every quarter the game still keeps, per-quarter cargo delivered, performance rating and company value, and your vehicle counts. Read-only.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {},
  "required": [],
  "type": "object"
}
```

## `list_engines`

Mutates game state: no

List the road engines this game has: engine_id, name, buildable, price, running_cost, capacity, cargo_type/cargo_label, is_passenger, max_speed, reliability, design_date, max_age_days. reliability is the engine's current reliability percent on a 0-100 scale, which varies over the engine's model life; design_date is the date the engine's design was introduced, as a {raw, year, month, day} object; max_age_days is the engine's maximum age in calendar days. The result also carries the current in-game date. Engines not yet introduced at the current date are absent. The only_buildable and passenger_only filters restrict which engines are listed.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "only_buildable": {
      "default": true,
      "type": "boolean"
    },
    "passenger_only": {
      "default": false,
      "type": "boolean"
    }
  },
  "required": [],
  "type": "object"
}
```

## `list_industries`

Mutates game state: no

List every industry currently on the map with its industry_id, name, type, location tile/x/y, stations_around, and its produced_cargo and accepted_cargo entries with cargo_type and cargo_label. stations_around counts the stations whose catchment covers that industry, the caller's and competitors' alike, whatever cargo each station handles. Each produced_cargo row also carries that cargo's last_month_production, last_month_transported, last_month_transported_percentage (0-100; transported counts output moved into the waiting queues of stations in range last month, not cargo loaded or delivered) and stockpiled counts, and each accepted_cargo row its accept_state and stockpiled count. stockpiled is the accepted-cargo input pile, so it reads -1 on a produced cargo the industry does not also accept; un-collected output is not observable. A figure the game cannot read at all comes back as -1 with a metric_error on that row. If an unexpected bridge/API failure affects only some returned facts, the enclosing row carries fact_errors entries with bounded semantic code and field names; a listed fallback value is not a game fact.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {},
  "required": [],
  "type": "object"
}
```

## `list_subsidies`

Mutates game state: no

List every subsidy the game currently holds, open offers and awarded subsidies alike. Each row reports subsidy_id, cargo type/label/name, factual offered-or-awarded status, the awarded company id and whether it is awarded to your resolved company when awarded, expire_date as the last valid economy-date, and source/destination participant facts (kind town/industry, the raw participant-type number, id, validity, name, and location). The expiry is the offer expiry while offered and changes to the awarded subsidy expiry when awarded; expired subsidies are absent, because only subsidies that currently exist in the game are listed.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {},
  "required": [],
  "type": "object"
}
```

## `get_industry`

Mutates game state: no

Read one industry's record by industry_id: the same per-industry facts list_industries reports, for a single id. A closed or stale industry_id comes back as invalid_industry_id instead of merely being absent from a list. Returns location/type plus stations_around, which counts the stations whose catchment covers that industry, the caller's and competitors' alike, whatever cargo each station handles. Returns produced_cargo and accepted_cargo with cargo labels, each produced cargo's last_month_production, last_month_transported and last_month_transported_percentage (0-100; transported counts output moved into the waiting queues of stations in range during the last closed month, not cargo loaded onto vehicles or delivered), and stockpiled counts. stockpiled is the accepted-cargo input pile waiting to be processed, so it reads -1 on a produced cargo the industry does not also accept; an industry's un-collected output pile is not observable through any sensor. If an unexpected bridge/API failure prevents this record from being read confidently, the call fails with error sensor_api_error and a sensor_error object containing bounded semantic code and field names.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "industry_id": {
      "type": "integer"
    }
  },
  "required": [
    "industry_id"
  ],
  "type": "object"
}
```

## `list_bridge_types`

Mutates game state: no

List the road bridge types that can span the given length, each with its bridge_id, max_speed, price, and length limits. A type whose own min_length/max_length limits do not cover the requested length is absent from the list. length is the total number of tiles the bridge occupies, which equals the Manhattan distance between the two bridge-head tiles + 1 (both ramps included) — the same length build_bridge derives and reports for that start/end pair, so query the length you will actually build.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "length": {
      "minimum": 2,
      "type": "integer"
    }
  },
  "required": [
    "length"
  ],
  "type": "object"
}
```

## `get_my_company`

Mutates game state: no

Report your company's quarter-by-quarter history: income, expenses, cargo delivered, performance rating and company value for every quarter the game still keeps, plus company identity, vehicle counts, and the same balance and loan facts get_my_finances reports. quarters[] runs from quarter 0 (current_quarter, the quarter in progress, whose income, expenses and cargo_delivered are partial) back to earliest_quarter, the oldest quarter OpenTTD retains. The quarter 0 row reports performance_rating -1 because the game computes a rating only when a quarter closes, so the newest real rating is in quarter 1; quarters[0].company_value is instead the live company value, while older rows hold the value recorded when that quarter closed. Quarterly income and expenses count only recurring money — vehicle income on one side, vehicle running costs, maintenance and loan interest on the other — so construction spending, vehicle purchases and vehicle sales are absent from them. Also reports company_id and company_name, vehicle_count (every vehicle you own) with road_vehicle_count (the road subset), bank_balance, loan_amount, max_loan_amount, loan_interval, and the current in-game date. Read-only.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {},
  "required": [],
  "type": "object"
}
```

## `get_my_station`

Mutates game state: no

Read factual cargo state for one station you own, identified by station_id or by tile (exactly one; station IDs resolve only for your own company's stations). Per cargo: waiting amount, whether the game tracks a rating (has_rating, with rating -1 otherwise), whether the station currently accepts the cargo, and the tile cargo acceptance measured around the station's location tile (the tile its sign stands on — not guaranteed to be one of its own station tiles) at the bus-stop coverage radius (for a station with more than one tile, this is not summed across all station tiles; the game lists a cargo as accepted when the acceptance summed over the station's catchment is at least 8 units, recomputed periodically as the surrounding tiles change, and a bus-stop-only station never accepts non-passenger cargo regardless of catchment). Also reports the station's bus-stop tiles and truck-stop tiles (a joined station can hold several road-stop bays) and the vehicles with orders to it. construction_date is the game date the station records as its construction date — the date the most recent part of it was added, so a station later extended with another bay carries that later date; the result's own date field carries the current date to measure it against. vehicles_loading is counted by scanning your vehicles: of the vehicles with orders to this station, the number currently in the loading/unloading state on one of this station's stop tiles; a vehicle loading at another station its orders also visit is not counted. If an unexpected bridge/API failure prevents a reported fact from being read confidently, the call fails with error sensor_api_error and a sensor_error object naming a bounded semantic code and the affected field; it does not replace that fact with false, zero, an empty list, or -1. Optional screenshot=true returns these JSON facts plus one fresh 384-pixel image requested around the subject tile reported by this read. At map pixel edges, the same clamping as view_map can move the actual crop center; rendered_center_tile reports it. It remains one tool call: the subject is read first, and no image is attempted when that read fails. If the image cannot be captured, the facts remain available and screenshot reports its own bounded failure. The image is the same unannotated OpenTTD rendering and crop used by view_map. The game runs in real time: rendering happens after the factual read, so a moving vehicle can leave the reported subject tile before the image is drawn.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "screenshot": {
      "default": false,
      "type": "boolean"
    },
    "station_id": {
      "type": "integer"
    },
    "tile": {
      "type": "integer"
    }
  },
  "required": [],
  "type": "object"
}
```

## `check_truck_stop_catchment`

Mutates game state: no

Report the factual catchment consequences of a hypothetical new standalone station containing one truck-stop tile: raw town-cargo acceptance units and the industries a stop there would attach to. truck_coverage_radius is the coverage radius the game uses for truck stops. The catchment is the same for a bay and a drive-through stop: the game's coverage radius depends on the station type, not the stop layout. town_cargo_acceptance[] enumerates every cargo in this game that both has a town effect and is not passenger-class, because this standalone truck-only station has no facility that accepts passenger-class cargo. A truck stop joined to a station that also has a bus, train, airport, or dock facility can accept passenger-class cargo; this hypothetical does not model such a join. Each row reports cargo_type, cargo_label, name, towneffect_id, towneffect_name, and the raw acceptance unit count for a 1x1 footprint at truck_coverage_radius. The count sums every accepting tile in that radius, including houses, industries, and headquarters; town cargo means cargo with a town effect, not cargo accepted only by town buildings. A station accepts that cargo when its periodically recomputed catchment count is at least 8 units; the row remains the integer count. get_tile/get_region can report the same exact-radius acceptance for one caller-selected cargo_id; their *_radius4 field is a fixed-radius comparison, not the truck-stop catchment used here. get_tile separately reports whether the hypothetical station's tile is legal to build on. producers[] lists each industry whose produced cargo would attach to a stop at this tile — i.e. the tile lies inside that industry's producing catchment at truck_coverage_radius — with industry_id, name, industry_type/industry_type_name, location tile/x/y, produced_cargo rows (cargo_type, cargo_label, last-month production/transported, stockpiled), and stations_around, which counts only the stations that already attach to that industry, the caller's and competitors' alike. acceptors[] is the delivery-side twin, the same membership test against the industry's accepting catchment, with accepted_cargo rows. Each row's cargo_type is the integer cargo type list_engines and list_industries/get_industry also report under that name, and get_tile/get_region/refit_vehicle take as cargo_id (same integer, different field name). Membership in those catchments is the game's own attach test: a stop outside them does not attach even when that tile's cargo_production_radius4 is positive (the raw radius4 field does not predict attachment). An invalid tile returns invalid_tile. If an unexpected bridge/API failure prevents a reported fact from being read confidently, the call fails with error sensor_api_error and a sensor_error object naming a bounded semantic code and the affected field; it does not treat the industry as absent from a catchment or substitute false, zero, an empty list, or -1. This includes cargo facts inside a producer or acceptor row.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "tile": {
      "type": "integer"
    }
  },
  "required": [
    "tile"
  ],
  "type": "object"
}
```

## `list_my_route_economics`

Mutates game state: no

Report profit per route: your road vehicles grouped by the stops they serve, with each vehicle's profit this year and last year and the group's profit totals. A group's route_signature is the distinct valid road-station stop tiles taken from that vehicle's order list, in order; a two-stop signature is sorted so the same pair of stops is one route whichever stop comes first, and vehicles with no valid road-station order (a new vehicle with no orders yet, or one whose stops no longer exist) share the empty signature. Each group reports route_signature, vehicle_count, vehicle_ids, profit_this_year_total, and profit_last_year_total; each vehicle row adds station_tiles, order_count, engine_id/engine_name, its current tile/x/y, state, stopped_in_depot, cargo_type and cargo_load, current_value, running_cost, age_days/max_age_days/age_left_days, and reliability. profit_this_year and profit_last_year are OpenTTD's own per-vehicle figures: delivery income minus the running cost charged while the vehicle moves, with the vehicle's purchase price never subtracted. profit_this_year covers the current year only and resets at the year boundary, where its value becomes profit_last_year. The result also reports road_vehicle_count and the current in-game date. Groups are ordered by their signature tile ids, not by profit.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {},
  "required": [],
  "type": "object"
}
```

## `get_cargo_payment_rate`

Mutates game state: no

Return OpenTTD's current payment for one unit of cargo_id transported distance Manhattan tiles in days_in_transit economy-days. This is the game's own payment calculation for exactly those three inputs and nothing else; route revenue and a vehicle's recorded last delivered payment are separate facts unavailable through this call. cargo_id is the integer cargo type reported as cargo_type by list_engines and list_industries/get_industry. Valid cargo slots are 0..63, but unavailable slots in the current game return invalid_cargo_id; distance is 0..4294967295 and days_in_transit is 0..163838. The result echoes the inputs, cargo name/label, current date, and income; active game settings and NewGRFs can affect that current-game calculation.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "cargo_id": {
      "maximum": 63,
      "minimum": 0,
      "type": "integer"
    },
    "days_in_transit": {
      "maximum": 163838,
      "minimum": 0,
      "type": "integer"
    },
    "distance": {
      "maximum": 4294967295,
      "minimum": 0,
      "type": "integer"
    }
  },
  "required": [
    "cargo_id",
    "distance",
    "days_in_transit"
  ],
  "type": "object"
}
```

## `check_build_road`

Mutates game state: no

Answer whether a road path would build and what it would cost, without laying any road. Read-only dry-run of build_road for the same from_tile -> waypoints... -> to_tile path semantics. Reports buildable, total_estimated_cost, per-segment/spans attempted, and on blockage the failing segment/span plus first_blocker_localized, first_blocker_tile and blocker_error_code/blocker_error_name. first_blocker_localized is true exactly when a test-mode replay of the failing span stopped succeeding at one tile. first_blocker_tile (+x/y) is then that replay's last tile — the span cannot be built that far, and the obstacle is that tile or the one before it — except when the first replayed pair is the failing one and failed_span_from is not buildable and is not classified as a road tile, a road station tile or a road depot tile, where the blockage is attributed to failed_span_from. blocker_error_code/blocker_error_name are the game's own error for that replay. When it is false the replay did not reproduce the failure, and first_blocker_tile=-1, blocker_error_code=-1 and blocker_error_name="" are stable placeholders, not game-reported values. Uses OpenTTD test mode: it builds nothing, spends no money, and advances no game time.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "from_tile": {
      "type": "integer"
    },
    "to_tile": {
      "type": "integer"
    },
    "waypoints": {
      "default": [],
      "items": {
        "type": "integer"
      },
      "type": "array"
    }
  },
  "required": [
    "from_tile",
    "to_tile"
  ],
  "type": "object"
}
```

## `check_build_road_depot`

Mutates game state: no

Answer whether the depot-construction phase would accept tile/front_tile and what that one phase would cost, without building it. front_tile is the same distinct, aligned direction selector accepted by build_road_depot, but OpenTTD test mode does not materialize the depot, so this sensor cannot report the resulting adjacent actual_front_tile or preview the automatic connector's legality or cost. Reports buildable, estimated_cost, the game's own error_code/error_name when not buildable, front_is_road, front_road_bits_applicable (true exactly when front_tile is a plain road tile to which the directional-bit sensor applies), front_road_connected, and front_road_bits. When front_road_bits_applicable is true, front_road_connected=true positively observes a road bit facing the depot tile; false means the bit was not positively observed, so consult that direction in front_road_bits, which can be "unknown" when the game cannot attribute a half-road to one side. When applicability is false, front_road_connected=false and the all-false front_road_bits object are stable compatibility placeholders, not observed topology facts. These front facts describe the supplied selector's current tile, not a hypothetical physical entrance. Uses OpenTTD test mode: it builds nothing, spends no money, and advances no game time. If an unexpected bridge/API failure affects only some returned facts, the enclosing row carries fact_errors entries with bounded semantic code and field names; a listed fallback value is not a game fact.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "front_tile": {
      "type": "integer"
    },
    "tile": {
      "type": "integer"
    }
  },
  "required": [
    "tile",
    "front_tile"
  ],
  "type": "object"
}
```

## `get_bus_route_skill`

Mutates game state: no

Context-only skill retrieval. Returns the stored skill body and guidance; runs no construction.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "engine_id": {
      "type": "integer"
    },
    "town_id": {
      "type": "integer"
    }
  },
  "required": [
    "town_id"
  ],
  "type": "object"
}
```

## `build_road`

Mutates game state: yes

Build road between from_tile and to_tile, optionally via waypoint tiles; the path is built as straight spans in order from_tile -> waypoints... -> to_tile. Failures report the game's own error name plus where it failed: the failing segment (failed_segment_from/failed_segment_to), the exact straight span attempted (failed_span_from/failed_span_to), and first_blocked_tile (+x/y) — the first tile at which a test-mode replay of the failing span stops succeeding (-1 when the failure does not reproduce in test mode, e.g. a vehicle transiently in the way).

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "from_tile": {
      "type": "integer"
    },
    "to_tile": {
      "type": "integer"
    },
    "waypoints": {
      "default": [],
      "items": {
        "type": "integer"
      },
      "type": "array"
    }
  },
  "required": [
    "from_tile",
    "to_tile"
  ],
  "type": "object"
}
```

## `build_one_way_road`

Mutates game state: yes

Build road that permits travel only from from_tile toward to_tile, as one straight span along a single axis: the endpoints must share a row or column, there are no waypoints and no corner fallback, so each straight segment is its own call and the call succeeds or fails as a whole. On empty land it lays road and marking together at plain road cost. The marking sits only on full straight road pieces; the two endpoint tiles receive half pieces that cannot carry it, so a fresh span's from_tile boundary still reads connected in both directions, and side roads join legally at those unmarked endpoint tiles while joining a marked tile is refused. Chaining collinear calls that share an anchor tile merges the anchor's half pieces into a marked full piece, so a chained route is seamless. Over existing straight road whose covered tiles are all full pieces the call costs nothing and toggles marking state per direction: repeating the same call restores two-way, and calling against an existing opposite marking blocks travel in both directions rather than flipping it, so a flip is two calls — the same-direction call to clear, then the reversed call — or remove_road followed by a reversed rebuild. When any covered tile cannot carry the marking (a junction tile, or a half-piece tile such as this tool's own endpoints), the whole call fails with ERR_ROAD_ONE_WAY_ROADS_CANNOT_HAVE_JUNCTIONS, the game's one generic one-way conflict error, raised for these non-junction conflicts too. Results report real cost, endpoints_connected_forward and endpoints_connected_reverse (directional road-graph reachability from_tile->to_tile and to_tile->from_tile).

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "from_tile": {
      "minimum": 0,
      "type": "integer"
    },
    "to_tile": {
      "minimum": 0,
      "type": "integer"
    }
  },
  "required": [
    "from_tile",
    "to_tile"
  ],
  "type": "object"
}
```

## `build_bridge`

Mutates game state: yes

Build a road bridge between start_tile and end_tile bridge-head tiles using a bridge_id from list_bridge_types. The bridge length is the Manhattan distance between start_tile and end_tile + 1 (both ramps included); pass that same length to list_bridge_types so the chosen bridge_id's max_length covers this span. For a road bridge the game also tries to lay a road stub on the tile just outside each bridge head facing the bridge; joining each head to the rest of your road network is a separate build. The result reports cost, bank_balance, factual start/end bridge observations, other_end_tile, and the derived length.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "bridge_id": {
      "type": "integer"
    },
    "end_tile": {
      "type": "integer"
    },
    "start_tile": {
      "type": "integer"
    }
  },
  "required": [
    "start_tile",
    "end_tile",
    "bridge_id"
  ],
  "type": "object"
}
```

## `build_tunnel`

Mutates game state: yes

Dig a road tunnel under the hill at start_tile; the exit tile cannot be chosen, because the game derives it from the start tile's slope, running the tunnel in the direction that slope rises and surfacing at the first tile whose ground is back at the start tile's height. The start tile must carry a simple inclined slope rising toward exactly one of north, south, east or west; anything else is rejected as ERR_TUNNEL_START_SITE_UNSUITABLE, and a tunnel whose exit cannot be excavated, that meets another tunnel, or that would run off the map fails with the game's corresponding error. Cost grows with the length the game derives. For a road tunnel the game also tries to lay a road stub on the tile just outside each mouth facing the tunnel, and the tunnel still counts as built when those stubs fail; joining each mouth to the rest of your road network is a separate build. The result reports the derived exit as other_end_tile, the exit predicted from the pre-build slope as other_end_tile_before, slope_before, and whether each mouth is a tunnel tile afterwards; ok is true only when the command succeeded and both mouths were observed.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "start_tile": {
      "type": "integer"
    }
  },
  "required": [
    "start_tile"
  ],
  "type": "object"
}
```

## `terraform_land`

Mutates game state: yes

Reshape terrain: mode raise and lower move the named corners of one tile (w/s/e/n are that tile's west, south, east and north corners) up or down by one height step, while mode level_to flattens whole tiles, levelling every tile in the rectangle spanned by tile and to_tile to a single height. Several corners can be named in one raise/lower call; the game applies them in the order west, south, east, north, and moving one corner can pull an adjacent corner with it, so a tile can end up changed by more than one step. level_to levels its rectangle to the height of tile's north corner, raising some tiles and lowering others as needed, and can level part of the rectangle while failing on the rest. raise and lower require at least one corner and reject to_tile; level_to requires to_tile and rejects corners. Reports real cost and the game's own error name on failure (land that is not clear, or a corner already at the game's height limit, is refused) plus slope_before/slope_after and height_before/height_after min and max for tile, so the terrain change that actually happened is visible.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "corners": {
      "default": [],
      "items": {
        "enum": [
          "w",
          "s",
          "e",
          "n"
        ],
        "type": "string"
      },
      "type": "array"
    },
    "mode": {
      "enum": [
        "raise",
        "lower",
        "level_to"
      ],
      "type": "string"
    },
    "tile": {
      "type": "integer"
    },
    "to_tile": {
      "type": "integer"
    }
  },
  "required": [
    "tile",
    "mode"
  ],
  "type": "object"
}
```

## `build_bus_stop_drive_through`

Mutates game state: yes

Build a drive-through bus stop on tile: vehicles drive straight through it instead of pulling into a dead end. front_tile picks the axis the stop is driven along: it must differ from tile and share its row or column, and the stop tile carries road along that line only, open at both ends. The default join:"new" creates a separate new station with its own station_id and per-station cargo pool; it does not join an existing station even on an adjacent tile. join:"adjacent" uses the game's adjacent join mode: when exactly one own station has tiles adjoining the new stop's area expanded by 1 tile, the stop joins it; when no own station adjoins, the game builds a new station; when more than one distinct own station adjoins, OpenTTD 15.1 rejects the build and reports ERR_UNKNOWN. join:"station_id" joins the supplied existing station id regardless of distance; it requires the distant_join_stations game setting (on by default), and the joined station's bounding rectangle is capped by the station_spread setting (default 12), with excess spread failing as ERR_STATION_TOO_SPREAD_OUT. Joined stops share one station_id and one per-station cargo pool, and a road stop may join a company-owned station of a different facility type. The result reports station_id, join, and drive_through; get_my_station reads a station's cargo state by station_id or tile. Failures report the game's own error name; OpenTTD 15.1 reports corner/junction rejections (road bits off the station axis) as the literal ERR_UNKNOWN, and the result then also carries the tile's factual per-direction tile_road_bits. If a post-command diagnostic cannot be read, fact_errors names its bounded semantic code and field; that diagnostic failure does not change the command's own ok or error result.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "front_tile": {
      "type": "integer"
    },
    "join": {
      "default": "new",
      "enum": [
        "new",
        "adjacent",
        "station_id"
      ],
      "type": "string"
    },
    "station_id": {
      "minimum": 0,
      "type": "integer"
    },
    "tile": {
      "type": "integer"
    }
  },
  "required": [
    "tile",
    "front_tile"
  ],
  "type": "object"
}
```

## `build_truck_stop_drive_through`

Mutates game state: yes

Build a drive-through truck stop on tile: vehicles drive straight through it instead of pulling into a dead end. This is not a bus stop; it is the freight road-station counterpart to build_bus_stop_drive_through. front_tile picks the axis the stop is driven along: it must differ from tile and share its row or column, and the stop tile carries road along that line only, open at both ends. The default join:"new" creates a separate new station with its own station_id and per-station cargo pool; it does not join an existing station even on an adjacent tile. join:"adjacent" uses the game's adjacent join mode: when exactly one own station has tiles adjoining the new stop's area expanded by 1 tile, the stop joins it; when no own station adjoins, the game builds a new station; when more than one distinct own station adjoins, OpenTTD 15.1 rejects the build and reports ERR_UNKNOWN. join:"station_id" joins the supplied existing station id regardless of distance; it requires the distant_join_stations game setting (on by default), and the joined station's bounding rectangle is capped by the station_spread setting (default 12), with excess spread failing as ERR_STATION_TOO_SPREAD_OUT. Joined stops share one station_id and one per-station cargo pool, and a road stop may join a company-owned station of a different facility type. Result fields include station_id, join, drive_through, and is_truck_stop. Failures report the game's own error name; OpenTTD 15.1 reports corner/junction rejections (road bits off the station axis) as the literal ERR_UNKNOWN, and the result then also carries the tile's factual per-direction tile_road_bits. If a post-command diagnostic cannot be read, fact_errors names its bounded semantic code and field; that diagnostic failure does not change the command's own ok or error result.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "front_tile": {
      "type": "integer"
    },
    "join": {
      "default": "new",
      "enum": [
        "new",
        "adjacent",
        "station_id"
      ],
      "type": "string"
    },
    "station_id": {
      "minimum": 0,
      "type": "integer"
    },
    "tile": {
      "type": "integer"
    }
  },
  "required": [
    "tile",
    "front_tile"
  ],
  "type": "object"
}
```

## `build_bus_stop_bay`

Mutates game state: yes

Build a one-tile bay bus station on tile: a dead end whose only road opening is on the front_tile side. A vehicle both enters and leaves through that one side, unlike the drive-through build_bus_stop_drive_through, which vehicles pass through. front_tile is a direction selector: it must differ from tile and share its row or column; the station entrance faces the adjacent side in that direction. The default join:"new" creates a separate new station with its own station_id and per-station cargo pool; it does not join an existing station even on an adjacent tile. join:"adjacent" uses the game's adjacent join mode: when exactly one own station has tiles adjoining the new stop's area expanded by 1 tile, the stop joins it; when no own station adjoins, the game builds a new station; when more than one distinct own station adjoins, OpenTTD 15.1 rejects the build and reports ERR_UNKNOWN. join:"station_id" joins the supplied existing station id regardless of distance; it requires the distant_join_stations game setting (on by default), and the joined station's bounding rectangle is capped by the station_spread setting (default 12), with excess spread failing as ERR_STATION_TOO_SPREAD_OUT. Joined stops share one station_id and one per-station cargo pool, and a road stop may join a company-owned station of a different facility type. Result fields include station_id, join, drive_through=false, bay_station, and is_bus_stop. If a post-command diagnostic cannot be read, fact_errors names its bounded semantic code and field; that diagnostic failure does not change the command's own ok or error result.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "front_tile": {
      "type": "integer"
    },
    "join": {
      "default": "new",
      "enum": [
        "new",
        "adjacent",
        "station_id"
      ],
      "type": "string"
    },
    "station_id": {
      "minimum": 0,
      "type": "integer"
    },
    "tile": {
      "type": "integer"
    }
  },
  "required": [
    "tile",
    "front_tile"
  ],
  "type": "object"
}
```

## `build_truck_stop_bay`

Mutates game state: yes

Build a one-tile bay truck station on tile: a dead end whose only road opening is on the front_tile side. A vehicle both enters and leaves through that one side, unlike the drive-through build_truck_stop_drive_through, which vehicles pass through. front_tile is a direction selector: it must differ from tile and share its row or column; the station entrance faces the adjacent side in that direction. The default join:"new" creates a separate new station with its own station_id and per-station cargo pool; it does not join an existing station even on an adjacent tile. join:"adjacent" uses the game's adjacent join mode: when exactly one own station has tiles adjoining the new stop's area expanded by 1 tile, the stop joins it; when no own station adjoins, the game builds a new station; when more than one distinct own station adjoins, OpenTTD 15.1 rejects the build and reports ERR_UNKNOWN. join:"station_id" joins the supplied existing station id regardless of distance; it requires the distant_join_stations game setting (on by default), and the joined station's bounding rectangle is capped by the station_spread setting (default 12), with excess spread failing as ERR_STATION_TOO_SPREAD_OUT. Joined stops share one station_id and one per-station cargo pool, and a road stop may join a company-owned station of a different facility type. Result fields include station_id, join, drive_through=false, bay_station, and is_truck_stop. If a post-command diagnostic cannot be read, fact_errors names its bounded semantic code and field; that diagnostic failure does not change the command's own ok or error result.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "front_tile": {
      "type": "integer"
    },
    "join": {
      "default": "new",
      "enum": [
        "new",
        "adjacent",
        "station_id"
      ],
      "type": "string"
    },
    "station_id": {
      "minimum": 0,
      "type": "integer"
    },
    "tile": {
      "type": "integer"
    }
  },
  "required": [
    "tile",
    "front_tile"
  ],
  "type": "object"
}
```

## `build_road_depot`

Mutates game state: yes

Build a road depot at tile, facing toward front_tile, and leave its physical entrance connected in both directions. front_tile is a direction selector: it must differ from tile and share its row or column, but it need not be adjacent. After the depot builds, the game-selected adjacent entrance is read as actual_front_tile; if either direction is disconnected, the action issues one road-edge build between tile and actual_front_tile. Road vehicles use depots for purchase, servicing, and autoreplacement: buy_vehicle and clone_vehicle build a vehicle inside a depot tile, and a vehicle the game finds due for service, or carrying a pending autoreplacement, is sent to the nearest depot its road network reaches, and is not sent at all when no reachable depot is within the game's maximum go-to-depot path limit. A vehicle visiting for service leaves its orders temporarily, enters the depot, and then resumes its route where it left off; an autoreplacement is likewise carried out on a vehicle standing inside a depot. Results retain front_tile as the input selector and report actual_front_tile, the final observed is_road_depot, front_is_road, and bidirectional front_road_connected facts, whether the connector was attempted and succeeded, and the combined real cost. If the connector fails, ok is false but the paid depot is retained: game_mutated and the observed fields make that partial mutation explicit. If reading a present depot's physical entrance fails or returns an invalid tile, the action returns ok=false: actual_front_tile=-1 with a bounded fact_errors entry means that entrance was unavailable; actual_front_tile=-1 with is_road_depot=false and no fact_errors means no depot resulted. is_road_depot=true with game_mutated=false can identify a pre-existing depot after this build attempt was refused. A refused depot command never attempts the automatic connector, even when a pre-existing depot is observed. This is only the local depot-front fact; use check_route_connectivity for network-level reachability.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "front_tile": {
      "type": "integer"
    },
    "tile": {
      "type": "integer"
    }
  },
  "required": [
    "tile",
    "front_tile"
  ],
  "type": "object"
}
```

## `buy_vehicle`

Mutates game state: yes

Buy a road vehicle in a depot; the vehicle type follows the chosen engine_id, so this buys either a bus or a freight truck depending on the engine. It is the only vehicle-purchase path. Engine ids come from list_engines, whose is_passenger flag and cargo fields distinguish bus from truck engines.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "depot_tile": {
      "type": "integer"
    },
    "engine_id": {
      "type": "integer"
    }
  },
  "required": [
    "depot_tile",
    "engine_id"
  ],
  "type": "object"
}
```

## `clone_vehicle`

Mutates game state: yes

Buy a copy of an existing vehicle in a depot: same engine, same cargo, that vehicle's order list, and that vehicle's group. buy_vehicle, by contrast, builds a new vehicle from an engine_id alone, with no orders. share_orders=false gives the clone its own copied order list; share_orders=true makes the source and clone share one order list, so later append_orders appends on either sharer change the order list for both. The result returns the new clone as vehicle_id and the copied vehicle as source_vehicle_id. The clone is built stopped in the depot and is not auto-started; call start_vehicle when you want it running.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "depot_tile": {
      "type": "integer"
    },
    "share_orders": {
      "type": "boolean"
    },
    "vehicle_id": {
      "type": "integer"
    }
  },
  "required": [
    "depot_tile",
    "vehicle_id",
    "share_orders"
  ],
  "type": "object"
}
```

## `refit_vehicle`

Mutates game state: yes

Refit an existing vehicle to a new cargo_id while it is stopped in a depot. Anywhere else the game rejects the command and the result reports the game's own error name. cargo_id is the integer cargo type reported as the cargo_type field by list_engines (and by list_industries/get_industry produced_cargo/accepted_cargo rows) — same integer, different field name. Result facts include engine_id, engine_valid, can_refit, stopped_in_depot, and capacity_before/capacity_after. Both capacities are measured FOR the target cargo_id before and after the refit, so capacity_before is typically 0 unless the vehicle already carries cargo_id; it is not the vehicle's current carrying capacity in its present cargo. A numeric cargo slot unavailable in the current game returns invalid_cargo_id before any cargo facts or mutation are attempted. If a post-command diagnostic cannot be read, fact_errors names its bounded semantic code and field; that diagnostic failure does not change the command's own ok or error result.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "cargo_id": {
      "maximum": 63,
      "minimum": 0,
      "type": "integer"
    },
    "vehicle_id": {
      "type": "integer"
    }
  },
  "required": [
    "vehicle_id",
    "cargo_id"
  ],
  "type": "object"
}
```

## `create_group`

Mutates game state: yes

Create an empty named road-vehicle group: the container move_vehicle_to_group moves vehicles into and set_group_autoreplace scopes its engine-replacement rules to. A group is bookkeeping only — membership does not change how a vehicle drives, what orders it holds, or what it earns and costs. The name must be non-empty, short enough for the game, and not already used by another group; a group whose name is rejected is deleted again rather than left behind, and the call reports created/name_set with group_id -1. On success the result returns group_id, group_name, and the current groups array (group_id, name, vehicle_count, autoreplace_rules) that list_my_vehicles also reports.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "name": {
      "type": "string"
    }
  },
  "required": [
    "name"
  ],
  "type": "object"
}
```

## `move_vehicle_to_group`

Mutates game state: yes

Move one road vehicle you own into an existing road-vehicle group. A vehicle belongs to exactly one group, so this also takes it out of the group it was in. The result echoes the requested group_id alongside vehicle_group_id/vehicle_group_name — the group the vehicle is actually in after the attempt — plus the groups array.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "group_id": {
      "type": "integer"
    },
    "vehicle_id": {
      "type": "integer"
    }
  },
  "required": [
    "vehicle_id",
    "group_id"
  ],
  "type": "object"
}
```

## `set_group_autoreplace`

Mutates game state: yes

Set a standing replacement rule on one group: whenever a vehicle in that group built from engine_id_old enters a depot, the game attempts to sell it and put a newly bought engine_id_new in its place. Nothing changes at the moment you set the rule — an attempt happens only once a vehicle is in a depot — and the rule keeps applying to vehicles that join the group later. An attempt can fail (not enough money for the new engine, for one), and a failed attempt leaves the old vehicle in service with the rule still set, so the game tries again on the next depot visit. A successful swap charges the new engine's price less the old vehicle's sale value, and the replacement keeps the old vehicle's orders and group membership but is a new vehicle with a new vehicle_id. group_id must be an existing road group and both engines must be road engines; the new engine must be buildable at the current date, and OpenTTD refits the replacement toward the old vehicles' cargo where it can, so cross-cargo pairs are accepted or rejected by the game rather than pre-filtered here. The result echoes replacement_engine_id/replacement_engine_name and the group's autoreplace_rules; list_my_vehicles groups[].autoreplace_rules reports the rules still in force.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "engine_id_new": {
      "type": "integer"
    },
    "engine_id_old": {
      "type": "integer"
    },
    "group_id": {
      "type": "integer"
    }
  },
  "required": [
    "group_id",
    "engine_id_old",
    "engine_id_new"
  ],
  "type": "object"
}
```

## `insert_order`

Mutates game state: yes

Insert one station order into a vehicle's order list before a 0-based position, leaving the rest of the list in place; a position equal to the current order_count inserts at the end, so this also seeds the first order of an empty list. If the vehicle shares its order list, the insert changes every vehicle sharing it. Optional flags is a list of flag names: FULL_LOAD (wait until the vehicle is fully loaded), FULL_LOAD_ANY (wait until at least one cargo type is fully loaded), NO_LOAD (load no cargo), NON_STOP (do not stop at stations passed on the way to this order's station). FULL_LOAD, FULL_LOAD_ANY, and NO_LOAD are mutually exclusive; any other name, a duplicate, or two of those three in one list is rejected without touching the vehicle. An order's destination is the station that owns stop_tile, never the tile itself: the game stores only the station, so two tiles of one station create two orders to the same station. The result echoes order_count, order_stations — the station_id each order resolved to, -1 for a position with no station destination — order_destinations, and order_flags — the flags of every order after the command, as the game's integer bitmask values rather than names. Each entry of order_destinations is one representative tile of that order's station, re-computed by the game on every read; it can differ from the tile the order was created with.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "flags": {
      "default": [],
      "items": {
        "enum": [
          "FULL_LOAD",
          "FULL_LOAD_ANY",
          "NO_LOAD",
          "NON_STOP"
        ],
        "type": "string"
      },
      "type": "array"
    },
    "position": {
      "type": "integer"
    },
    "stop_tile": {
      "type": "integer"
    },
    "vehicle_id": {
      "type": "integer"
    }
  },
  "required": [
    "vehicle_id",
    "position",
    "stop_tile"
  ],
  "type": "object"
}
```

## `delete_order`

Mutates game state: yes

Delete one order at a 0-based order position, leaving the rest of the vehicle's order list in place. Only that one order is removed, and only on the named vehicle/position; shared orders mutate every vehicle sharing that order list. The result echoes order_count, order_stations (the station_id each remaining order resolved to, -1 for a position with no station destination), and order_destinations after the command; each entry of order_destinations is one representative tile of that order's station, re-computed by the game on every read.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "position": {
      "type": "integer"
    },
    "vehicle_id": {
      "type": "integer"
    }
  },
  "required": [
    "vehicle_id",
    "position"
  ],
  "type": "object"
}
```

## `set_order_flags`

Mutates game state: yes

Set the loading and stopping behaviour of one station order a vehicle already has: FULL_LOAD, FULL_LOAD_ANY, NO_LOAD, NON_STOP. The order is picked by 0-based position and which station it goes to does not change. flags is a list of flag names: FULL_LOAD (wait until the vehicle is fully loaded), FULL_LOAD_ANY (wait until at least one cargo type is fully loaded), NO_LOAD (load no cargo), NON_STOP (do not stop at stations passed on the way to this order's station). The list replaces the order's flags rather than adding to them, so an empty list clears them; FULL_LOAD, FULL_LOAD_ANY, and NO_LOAD are mutually exclusive, and any other name, a duplicate, or two of those three in one list is rejected without touching the vehicle. The result echoes the flags of every order after the command as the game's integer bitmask values rather than names, plus order_stations (the station_id each order resolved to, -1 for a position with no station destination) and order_destinations (one representative tile of each order's station, re-computed by the game on every read).

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "flags": {
      "default": [],
      "items": {
        "enum": [
          "FULL_LOAD",
          "FULL_LOAD_ANY",
          "NO_LOAD",
          "NON_STOP"
        ],
        "type": "string"
      },
      "type": "array"
    },
    "position": {
      "type": "integer"
    },
    "vehicle_id": {
      "type": "integer"
    }
  },
  "required": [
    "vehicle_id",
    "position",
    "flags"
  ],
  "type": "object"
}
```

## `append_orders`

Mutates game state: yes

Append one station order per stop tile to the end of a vehicle's order list, keeping any orders it already has. Optional flags is either one list of flag names applied to every appended order, or a list of one such list per stop tile, in the same order as stop_tiles. The flag names are FULL_LOAD (wait until the vehicle is fully loaded), FULL_LOAD_ANY (wait until at least one cargo type is fully loaded), NO_LOAD (load no cargo), and NON_STOP (do not stop at stations passed on the way to that order's station); FULL_LOAD, FULL_LOAD_ANY, and NO_LOAD are mutually exclusive. set_order_flags changes the flags of an order that already exists, and insert_order adds one order at a chosen position. An order's destination is the station that owns the stop tile, never the tile itself: the game stores only the station, so two tiles of one station create two orders to the same station, and the vehicle picks whichever of the station's compatible stops to use as it drives. The result echoes orders_appended, order_count, order_stations — the station_id each order resolved to, -1 for a position with no station destination — order_destinations, and order_flags. Each entry of order_destinations is one representative tile of that order's station, re-computed by the game on every read; it can differ from the stop_tiles entry the order was created with and need not be a stop this vehicle's type can use.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "flags": {
      "anyOf": [
        {
          "items": {
            "enum": [
              "FULL_LOAD",
              "FULL_LOAD_ANY",
              "NO_LOAD",
              "NON_STOP"
            ],
            "type": "string"
          },
          "type": "array"
        },
        {
          "items": {
            "items": {
              "enum": [
                "FULL_LOAD",
                "FULL_LOAD_ANY",
                "NO_LOAD",
                "NON_STOP"
              ],
              "type": "string"
            },
            "type": "array"
          },
          "type": "array"
        }
      ]
    },
    "stop_tiles": {
      "items": {
        "type": "integer"
      },
      "minItems": 1,
      "type": "array"
    },
    "vehicle_id": {
      "type": "integer"
    }
  },
  "required": [
    "vehicle_id",
    "stop_tiles"
  ],
  "type": "object"
}
```

## `insert_conditional_order`

Mutates game state: yes

Insert one fully configured conditional order before a 0-based position; position equal to the current order_count appends it. jump_to names a position in the resulting list after insertion, including the new conditional's own position; the list must already contain at least one order. The condition tests this vehicle when execution reaches the conditional and jumps to that position when the comparison is true. The tool creates exactly one branch. The seven conditions the OpenTTD 15.1 AI API can set are LOAD_PERCENTAGE and RELIABILITY (percentage value 0 through 100), MAX_SPEED (value 0 through 2047 in the same internal speed unit as list_engines max_speed), AGE and REMAINING_LIFETIME (calendar-year value 0 through 2047), REQUIRES_SERVICE (compare IS_TRUE or IS_FALSE and value 0), and UNCONDITIONALLY (compare EQUALS and value 0). The numeric conditions accept EQUALS, NOT_EQUALS, LESS_THAN, LESS_EQUALS, MORE_THAN, and MORE_EQUALS. OpenTTD converts a MAX_SPEED value internally, so the read-back value can be one lower than requested. MAX_RELIABILITY is absent because the live API exposes its constant but rejects it in SetOrderCondition; INVALID is only a read sentinel. A conditional order has no station destination, so its entries in order_stations and order_destinations are both -1. The result reports requested_compare_value beside the actual condition, compare, compare_value, and jump_to read back from the new order, plus the complete orders array, order_count, order_stations, and order_destinations.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "compare": {
      "enum": [
        "EQUALS",
        "NOT_EQUALS",
        "LESS_THAN",
        "LESS_EQUALS",
        "MORE_THAN",
        "MORE_EQUALS",
        "IS_TRUE",
        "IS_FALSE"
      ],
      "type": "string"
    },
    "condition": {
      "enum": [
        "LOAD_PERCENTAGE",
        "RELIABILITY",
        "MAX_SPEED",
        "AGE",
        "REQUIRES_SERVICE",
        "UNCONDITIONALLY",
        "REMAINING_LIFETIME"
      ],
      "type": "string"
    },
    "jump_to": {
      "minimum": 0,
      "type": "integer"
    },
    "position": {
      "minimum": 0,
      "type": "integer"
    },
    "value": {
      "maximum": 2047,
      "minimum": 0,
      "type": "integer"
    },
    "vehicle_id": {
      "minimum": 0,
      "type": "integer"
    }
  },
  "required": [
    "vehicle_id",
    "position",
    "condition",
    "compare",
    "value",
    "jump_to"
  ],
  "type": "object"
}
```

## `skip_to_order`

Mutates game state: yes

Make a vehicle begin with the order at a 0-based position instead of its current order. This changes only the current execution position; it does not move, add, remove, or rewrite any order. The result reports previous_position and current_position plus the complete orders array, order_count, order_stations, and order_destinations.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "position": {
      "minimum": 0,
      "type": "integer"
    },
    "vehicle_id": {
      "minimum": 0,
      "type": "integer"
    }
  },
  "required": [
    "vehicle_id",
    "position"
  ],
  "type": "object"
}
```

## `copy_orders`

Mutates game state: yes

Replace one vehicle's entire order list with an independent copy of another vehicle's list. Later edits through either vehicle do not affect the other. This is distinct from clone_vehicle with share_orders=false because it operates on an existing vehicle, and from unshare_orders because OpenTTD empties the departing vehicle's list when unsharing. The game requires compatible vehicle types and destinations and reports its own error when they are incompatible. The result reports both vehicles' order counts, orders arrays, order_stations, order_destinations, and has_shared_orders facts after the command.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "source_vehicle_id": {
      "minimum": 0,
      "type": "integer"
    },
    "vehicle_id": {
      "minimum": 0,
      "type": "integer"
    }
  },
  "required": [
    "vehicle_id",
    "source_vehicle_id"
  ],
  "type": "object"
}
```

## `share_orders`

Mutates game state: yes

Replace one vehicle's entire order list with the source vehicle's shared order list. Later order edits through either sharer change the same list for every sharing vehicle. The game requires compatible vehicle types and destinations and reports its own error when they are incompatible. The result reports both vehicles' order counts, orders arrays, order_stations, order_destinations, and has_shared_orders facts after the command.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "source_vehicle_id": {
      "minimum": 0,
      "type": "integer"
    },
    "vehicle_id": {
      "minimum": 0,
      "type": "integer"
    }
  },
  "required": [
    "vehicle_id",
    "source_vehicle_id"
  ],
  "type": "object"
}
```

## `unshare_orders`

Mutates game state: yes

Remove one vehicle from a shared order list. OpenTTD leaves that vehicle with an empty order list; it does not retain a private copy, while the remaining sharers keep the shared list. Use copy_orders instead when the destination vehicle must retain an independent copy. The result reports the vehicle's empty orders array, order_count, order_stations, order_destinations, and has_shared_orders fact after the command.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "vehicle_id": {
      "minimum": 0,
      "type": "integer"
    }
  },
  "required": [
    "vehicle_id"
  ],
  "type": "object"
}
```

## `start_vehicle`

Mutates game state: yes

Start a stopped vehicle.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "vehicle_id": {
      "type": "integer"
    }
  },
  "required": [
    "vehicle_id"
  ],
  "type": "object"
}
```

## `stop_vehicle`

Mutates game state: yes

Stop a running vehicle.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "vehicle_id": {
      "type": "integer"
    }
  },
  "required": [
    "vehicle_id"
  ],
  "type": "object"
}
```

## `reverse_vehicle`

Mutates game state: yes

Turn one road vehicle around so it heads back the way it came. A true result reports only that the vehicle has started to turn — the game reports nothing about the turn completing, so where the vehicle actually ends up is read later from get_vehicle or wait_and_observe_vehicle, never from this result. The game refuses the call for a road vehicle that is stopped or inside a depot: the refusal changes nothing and reports the game's own error name, which for both of those situations is only the generic ERR_UNKNOWN — the state_name and stopped_in_depot facts in the result describe the circumstances but do not identify why the game refused the call.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "vehicle_id": {
      "minimum": 0,
      "type": "integer"
    }
  },
  "required": [
    "vehicle_id"
  ],
  "type": "object"
}
```

## `send_vehicle_to_depot`

Mutates game state: yes

Send a vehicle to halt in a depot the game picks: the closest one it can drive to. On arrival the vehicle stops inside that depot instead of carrying on with its orders; its order list is left as it is, and start_vehicle sends the vehicle back out to continue those orders. Calling this again while the vehicle is already on its way to a depot cancels that trip, and the vehicle goes back to its orders. The game refuses the command with its own error name when the vehicle has crashed, is already stopped in a depot, or can drive to no depot at all.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "vehicle_id": {
      "type": "integer"
    }
  },
  "required": [
    "vehicle_id"
  ],
  "type": "object"
}
```

## `sell_vehicle`

Mutates game state: yes

Sell one vehicle that is stopped inside a depot, for its current value. That value (the same current_value list_my_route_economics reports) is credited to the company and appears in the result as a negative cost, and the vehicle ceases to exist, so its vehicle_id becomes invalid. The vehicle must be stopped inside a depot; otherwise the game rejects the sale with ERR_VEHICLE_NOT_IN_DEPOT and nothing is sold.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "vehicle_id": {
      "type": "integer"
    }
  },
  "required": [
    "vehicle_id"
  ],
  "type": "object"
}
```

## `set_loan`

Mutates game state: yes

Set the company's loan to amount — an absolute figure, not a change to the current loan. Raising the loan pays the difference into the bank balance; lowering it repays the difference out of the bank balance. amount must be a whole multiple of loan_interval and no larger than max_loan_amount (get_my_finances and get_my_company report both), and a reduction the bank balance cannot cover is refused; a refused amount leaves the loan unchanged and reports the game's own error name. The result reports requested_amount, loan_amount, and max_loan_amount.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "amount": {
      "type": "integer"
    }
  },
  "required": [
    "amount"
  ],
  "type": "object"
}
```

## `demolish_tile`

Mutates game state: yes

Clear one tile of whatever stands on it: road, a station or depot tile, a house, trees. The tile is left bare and the game's clearing cost is charged. remove_road, the narrower tool, takes road pieces off a straight span of tiles and leaves anything else on those tiles standing. What may be cleared is the game's decision: a refusal reports the game's own error name and leaves the tile untouched.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "tile": {
      "type": "integer"
    }
  },
  "required": [
    "tile"
  ],
  "type": "object"
}
```

## `remove_road`

Mutates game state: yes

Remove the road span between from_tile and to_tile on one axis (same row or column).

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "from_tile": {
      "type": "integer"
    },
    "to_tile": {
      "type": "integer"
    }
  },
  "required": [
    "from_tile",
    "to_tile"
  ],
  "type": "object"
}
```

## `wait`

Mutates game state: yes

Advance in-game time by 1..90 days. On success returns status='complete' with the advanced date and bank_balance, plus dated records of vehicles newly lost, newly waiting in a depot, and newly flagged unprofitable during the window. If the advance is not confirmed before this call stops waiting it returns status='timeout' (ok=false) with a note: the game may still have advanced and the company kept operating, so call get_my_company or get_my_finances (both report the current date) to read the true current date before retrying.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "duration_days": {
      "maximum": 90,
      "minimum": 1,
      "type": "integer"
    }
  },
  "required": [
    "duration_days"
  ],
  "type": "object"
}
```

## `check_route_connectivity`

Mutates game state: no

Answer whether a road vehicle could actually drive between the stop tiles and the depot you name: per stop, whether the depot reaches it and it reaches the depot; whether every stop reaches every other stop; and whether depot_tile and depot_front_tile join in both directions. stop_access_connected reports per stop tile whether a bay stop joins its front tile in both directions or a drive-through stop joins either end in both directions, and stop_to_stop_distances reports one row for each direction of every pair of different stop_tiles list positions, for 1..32 positions. Repeated positions produce repeated directed rows with 0 road_steps when both endpoints are the same road tile. A position that is not a road stop tile has its stop_access_connected row false; a position carrying no road at all is additionally unreachable in every direction. Bay stops use their station tile as the endpoint, like drive-through stops. Each row's from_tile/to_tile are tile-centre endpoints; road_steps is the shortest traced count of edge-adjacent directed road-graph transitions in that direction (0 when two positions name the same road tile), with reachable=false and road_steps=null when that direction has no traced path. all_on_one_component is true only when every connection and reachability check above holds; distance magnitudes do not affect it. Reachability is traced over edge-adjacent connected road tiles, road stations and road depots only: the trace does not enter bridge or tunnel tiles, so a path that crosses one is reported as not reachable. Nothing is built and no game time passes. component_size is the size of the connected road component containing the first stop tile (counted up to 512 tiles, whatever towns it spans); town_road_component_size counts only that component's tiles within town_id's influence, seeded at the same stop, and is 0 when the seed lies outside it (see seed_within_town_influence).

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "depot_front_tile": {
      "type": "integer"
    },
    "depot_tile": {
      "type": "integer"
    },
    "stop_tiles": {
      "items": {
        "type": "integer"
      },
      "maxItems": 32,
      "minItems": 1,
      "type": "array"
    },
    "town_id": {
      "type": "integer"
    }
  },
  "required": [
    "town_id",
    "depot_tile",
    "depot_front_tile",
    "stop_tiles"
  ],
  "type": "object"
}
```

## `wait_and_observe_vehicle`

Mutates game state: yes

Advance in-game time by advance_days while sampling the vehicle every game tick, then report window facts and a verdict. verdict: operating (the vehicle had at least one sampled station arrival in the window, counted as a transition into the game's loading-at-station state), moving_without_station_visit (it moved or progressed orders but had no sampled station arrival — a short window can end mid-leg), running_pending_progress (running with orders, no movement or progress), stuck, lost, or crashed. The result carries station_visits and stations_visited for the window, plus dated waiting_in_depot_events and unprofitable_events records for this vehicle with *_event_in_window flags; the game announces waiting_in_depot once when a vehicle sent to a depot arrives and halts, and unprofitable once per year on January 1 for a vehicle at least 2 years old whose closed year's profit was negative. If bridge confirmation times out, the failure reports game_mutated=true and time_may_have_advanced=true because game time may already have advanced; read the current date before deciding whether to retry.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "advance_days": {
      "default": 24,
      "maximum": 90,
      "minimum": 1,
      "type": "integer"
    },
    "vehicle_id": {
      "type": "integer"
    }
  },
  "required": [
    "vehicle_id"
  ],
  "type": "object"
}
```

## `wait_and_observe_station`

Mutates game state: yes

Advance in-game time by advance_days while sampling one current-company road station every 5 script ticks, then return at most one row per observed calendar date. The station-wide near counts scan current-company primary road vehicles and count each vehicle once when its Manhattan distance from the union of all bus and truck stop tiles is at most 4; zero-speed means current speed equals zero, and both near counts include vehicles inside depots within that radius. Each stop tile also reports sampled maxima for exact-location vehicles and for exact-location vehicles whose game state is loading or unloading. All traffic maxima are independent sampled maxima and need not have occurred simultaneously. Waiting cargo is the last sampled snapshot for that date, includes only positive entries, and is not a maximum. Each date row is bounded: it lists at most max_stop_rows_per_date stop tiles, named in observed_stop_tiles, and at most max_waiting_cargo_rows_per_date waiting-cargo entries, with observed_stop_tiles_truncated and per-row last_sample_waiting_cargo_truncated reporting when something was dropped. The station-wide near counts still scan every road-stop tile, and each row's waiting-cargo total and type count still cover every positive cargo. The selected station must resolve to at least one current-company road-stop tile; an existing unowned station can pass the Script API validity check but is rejected when it has no such tile. Both checks happen before time advances. Unexpected sensor failures return sensor_api_error; a failure or timeout after the observation could have started explicitly reports that game time may have advanced.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "advance_days": {
      "default": 14,
      "maximum": 90,
      "minimum": 1,
      "type": "integer"
    },
    "station_id": {
      "minimum": 0,
      "type": "integer"
    }
  },
  "required": [
    "station_id"
  ],
  "type": "object"
}
```

## `report_feedback`

Mutates game state: no

Whenever you wish you could see or do something the tools don't offer, file it at that moment; it costs nothing, advances no game time, and grants no capability during this episode. Kinds: missing_observation = facts you wanted to inspect; missing_action = command you wanted to execute; ambiguous_tool_result = result was unclear or underspecified; strategic_note = lesson that may improve future play.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "current_blocker": {
      "type": "string"
    },
    "description": {
      "type": "string"
    },
    "kind": {
      "enum": [
        "missing_observation",
        "missing_action",
        "ambiguous_tool_result",
        "strategic_note"
      ],
      "type": "string"
    },
    "why_it_matters": {
      "type": "string"
    }
  },
  "required": [
    "kind",
    "description",
    "why_it_matters",
    "current_blocker"
  ],
  "type": "object"
}
```

## `end_episode`

Mutates game state: no

End the run: this is irreversible, and all gameplay tools return stop from then on. The run's final save is taken when the episode shuts down, and the game goes on running until that moment, so the saved state can differ slightly from the state at this call. Requires your reason (recorded as run evidence). report_feedback stays available and your closing POSTMORTEM message is still expected.

Input schema:

```json
{
  "additionalProperties": false,
  "properties": {
    "reason": {
      "type": "string"
    }
  },
  "required": [
    "reason"
  ],
  "type": "object"
}
```
