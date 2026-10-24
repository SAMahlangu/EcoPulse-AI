# EcoMove AI

EcoMove is an additional Resident Dashboard page at `/resident/ecomove`, reached through **EcoMove AI** in the existing navigation. It preserves the existing authentication, household dashboards, infrastructure model and municipality relationships.

Residents can explore stored routes, request GPS permission with a button, pin or drag a pickup, search a place, select a configured destination or pin a destination, expand the search radius, and find direct or explicitly configured single-transfer journeys. Route, taxi and stop details appear on the Leaflet map. The mobile journey panel collapses to keep the map usable.

The persistent banner says **DEMO MODE — Taxi positions are simulated. Not live GPS tracking.** All twelve demo assignments are simulated vehicles, never a claim about real taxi availability.

## Database and intentional setup

Existing Supabase/PostGIS was inspected before implementation: PostGIS 3.3.7 was enabled and no taxi/transport tables existed. The canonical City of Tshwane municipality is reused. No province, municipality or incident tables are copied or replaced.

Apply these migrations in order on another installation:

- `database/migrations/010_ecomove.sql`: four configuration tables, indexes, constraints, foreign keys, active-account read policies and write restrictions.
- `database/migrations/011_ecomove_stop_validation.sql`: validates known arrival offsets even when an intermediate offset is unknown.

Tables:

| Table | Purpose |
| --- | --- |
| `taxi_routes` | Municipality, direction, road geometry, source/demo status, optional time/fare and explicit simulation mode |
| `taxi_stops` | Stored boarding/alighting locations, stop type and explicit interchange status |
| `taxi_route_stops` | Ordered stops and optional cumulative arrival offsets |
| `taxi_simulation_vehicles` | Vehicle IDs, route assignments, speed and initial progress; no position history |

All four tables have RLS enabled. Active authenticated profiles may read configuration; ordinary residents have no write grant. Configuration changes use trusted server/database administration. There is no resident route-edit endpoint. Geography columns have GiST indexes; municipality, stop ordering and assignment lookups are indexed.

From CMD at the repository root:

```cmd
cd /d C:\Users\student\Desktop\FNB
backend\venv\Scripts\python.exe database\seed_ecomove.py --verify
backend\venv\Scripts\python.exe database\seed_ecomove.py --apply-schema
backend\venv\Scripts\python.exe database\seed_ecomove.py --seed-demo
```

`--verify` validates the schema, seed, actual HTTP handlers, PostGIS searches and RLS in a transaction that rolls back. Committing schema and inserting demonstration data require their explicit flags. Stable UUIDs and conflict checks preserve existing records. Re-running the seed does not overwrite route configurations. An incomplete existing transport schema causes setup to fail for inspection.

The prepared fixture contains four **demonstration corridors**: Soshanguve → Pretoria CBD, Mabopane → Pretoria CBD, Mamelodi → Pretoria CBD, and Pretoria CBD → Centurion. Six illustrative stops include a deliberately shared CBD interchange; twelve vehicles are assigned across these routes. No official ranks, operator participation, departures or verified fares are asserted.

The stored fixture has 2,054 road vertices retrieved from [OSRM's route service](https://project-osrm.org/docs/v5.22.0/api/) using OpenStreetMap road data. Road-following car geometry does not verify an actual taxi route. The fixture records source, retrieval time, attribution and limitations. Optional regeneration downloads only fixed public demonstration coordinates:

```cmd
backend\venv\Scripts\python.exe database\seed_ecomove.py --download-geometry
```

Regeneration changes the fixture, not existing database routes. There is no straight-line fallback if routing fails. Preserve OpenStreetMap attribution and its ODbL obligations when redistributing the road data.

## Configuring routes

Use a trusted database role and a transaction to insert a `taxi_routes` row referencing an existing municipality, with an SRID 4326 `geography(LineString)` and an honest `data_source`/`is_demo` value. Coordinates in GeoJSON are **longitude, latitude**. Set `direction='forward'`; provide a separate, independently configured route for the return direction.

Insert or reuse stops, then insert ordered `taxi_route_stops`. Stop municipality must match the route, stop location must be within 150 metres of its geometry, and stop sequence must follow the forward geometry. Known cumulative offsets cannot decrease; omit unknown times/fares rather than estimating them silently. Use `pickup`, `dropoff`, `rank` or `interchange` to explicitly control boarding/alighting. A transfer requires both routes to share the **same stop UUID**, with `is_interchange=true`. Nearby but unrelated stops never imply a transfer.

Add simulation assignments only where wanted. Speeds are kilometres per hour, initial progress is 0–1. `simulation_mode='once'` finishes at the destination. Only a route explicitly configured as `loop` with closed geometry may wrap; no implicit reverse or restart exists.

## HTTP API

All paths below use the existing bearer authentication and `/api/v1` prefix.

| Method | Path | Result |
| --- | --- | --- |
| GET | `/ecomove/routes` | Paginated route geometries and ordered stops |
| GET | `/ecomove/routes/{route_id}` | One configured route |
| GET | `/ecomove/stops` | Paginated configured stops |
| GET | `/ecomove/stops/nearby` | Stored stops within a validated radius |
| GET | `/ecomove/routes/nearby` | Routes with an eligible configured boarding stop nearby |
| POST | `/ecomove/journey/search` | Direction-aware direct journeys, or valid single transfers when no direct route exists |
| GET | `/ecomove/simulation/vehicles` | Configuration assignments, not live positions |
| GET | `/ecomove/disruptions` | Safe fields from existing relevant infrastructure reports near routes |

List responses use `{items,total,limit,offset}` with a maximum page size of 100. Route/stop/vehicle/disruption lists support municipality and viewport bounding-box filters. Radius defaults to 1 km for route search and is limited to 10 km; road hazards default to 200 metres and are limited to 1 km. Nonfinite/invalid coordinates, malformed bounds and unsupported IDs are rejected. SQL is parameterized and has a statement timeout. Journey candidate counts are bounded; dense ambiguous searches return an explicit error instead of truncated recommendations.

Journey body:

```json
{
  "pickup": {"latitude": -25.521042, "longitude": 28.10096},
  "destination": {"latitude": -25.746, "longitude": 28.188},
  "radius_meters": 1000,
  "municipality_id": "<existing-municipality-uuid>"
}
```

These coordinates illustrate the request format. Use actual stored stop coordinates for reproducible journeys. Results include configured leg and stop IDs, transfers, combined pickup/dropoff walking estimate, optional travel time/fare and source/demo status. A route merely passing nearby is insufficient for a boarding recommendation.

Disruptions reuse the existing incidents data and municipality relationships. Only relevant unresolved road/stormwater/environmental reports near configured geometry are returned. The projection omits reporter identity, original description, private images and contact details. The map shows report category, location, timestamp, status and distance. A report does not imply a closure, and does not automatically reroute vehicles.

## Frontend and simulation

- `EcoMoveDashboard`: data loading, planner, GPS permission handling, area choice, route/journey cards and details.
- `EcoMoveMap`: existing React Leaflet map, route selection, stops, draggable pickup/destination, road reports, bounds and map controls.
- `TaxiSimulationController`: imperative Leaflet vehicle markers outside React's frame-by-frame render cycle.
- `simulation.ts`: pure arc-length interpolation, bearing, configured stop dwell and route-completion behavior.
- `types/ecomove.ts`: shared route, stop, vehicle, position, journey and disruption interfaces.

Simulation uses elapsed active time and physical speed, not frame count. Independent initial progress separates the twelve assignments. Stop dwell defaults to five seconds. Only relevant/offscreen-filtered markers render; stable geometry is cached and viewport requests are debounced. Hidden tabs pause work. Component cleanup cancels animation frames and removes listeners/layers. No simulated positions are written to Supabase and there is no per-second vehicle polling.

`VehiclePositionSource` separates `simulation` from future `live_gps`. A future consented driver/operator source can provide positions through a separate controller/adapter using the same map and details interfaces. This MVP does not collect driver locations or implement live GPS tracking.

GPS is requested only after a resident clicks the button. Manual selection remains available after denial. Pickup/destination are held in page state, not written to a location-history table. EcoMove access logs strip query strings so nearby searches do not put precise coordinates in Uvicorn access logs. Deployment proxies should likewise avoid retaining location-query parameters or journey bodies.

## Running and verification

Start backend and frontend in separate CMD windows:

```cmd
cd /d C:\Users\student\Desktop\FNB
backend\venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

```cmd
cd /d C:\Users\student\Desktop\FNB\frontend
npm.cmd run dev -- --host 127.0.0.1 --port 5173
```

Open `http://127.0.0.1:5173/resident/ecomove`, sign in with an existing resident account, and choose **Pretoria demo coverage** if the resident's municipality has no configured routes.

```cmd
cd /d C:\Users\student\Desktop\FNB
backend\venv\Scripts\python.exe -m pytest backend\tests -q
cd frontend
node --test scripts\test-ecomove-simulation.cjs
npm.cmd run build
```

Live database checks are available through the `--verify` command above. `frontend/scripts/test-ecomove-browser.cjs` uses a separately launched isolated Edge CDP instance on port 9229. It provisions a temporary real-auth resident with `backend/tests/ecomove_browser_session.py`, never prints credentials/tokens, exercises the running app, and deletes the temporary account in `finally`. Desktop/mobile screenshots are local ignored diagnostics, not published assets.

## Limits

- Demonstration coverage is restricted to the configured Pretoria corridors. Empty areas and unsupported journeys display explicit empty states.
- Boarding points and routes are illustrative, not verified real taxi operations. Car-routing times are estimates, not schedules; transfer waiting time and reliable fare data are unavailable.
- Walking distances are straight-line estimates to configured stops, not accessible pedestrian directions.
- Standard OpenStreetMap basemap tiles and external place search require internet availability. Stored road geometry and browser simulation do not require a routing API call on every visit.
- Matching supports zero or one explicitly configured transfer, and only forward stop order. Cross-lap journeys on closed loops are not inferred.
- No driver app, live vehicle telemetry, operator integrations, fare collection or resident route administration is implemented.

## Files changed

New backend files: `backend/app/ecomove.py`, `backend/app/services/ecomove_matching.py`, `backend/tests/test_ecomove.py`, `backend/tests/ecomove_browser_session.py`.

New database files: migrations `010_ecomove.sql` and `011_ecomove_stop_validation.sql`, `database/seed_ecomove.py`, `database/seeds/ecomove_pretoria_demo.json`.

New frontend files: `frontend/src/pages/EcoMoveDashboard.tsx`, `frontend/src/pages/ecomove.css`, `frontend/src/types/ecomove.ts`, `frontend/src/components/ecomove/EcoMoveMap.tsx`, `TaxiSimulationController.tsx`, `simulation.ts`, `frontend/scripts/test-ecomove-simulation.cjs`, `frontend/scripts/test-ecomove-browser.cjs`.

Modified integration files: `backend/app/main.py` registers the router; `frontend/src/App.tsx` adds the lazy resident page and transport navigation icon; `.gitignore` excludes isolated browser state/screenshots. This document records setup and operating limits.
