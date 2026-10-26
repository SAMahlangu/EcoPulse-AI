# Smart Energy Consumption Intelligence

Energy intelligence extends the existing **Energy management** page at `/resident/energy`. The sidebar and route are unchanged. **Daily Energy Consumption** displays actual household records; **Energy Consumption Simulation** is an internal demo tab. The community electricity service panel remains on the page. Water Management is unchanged.

## What is included

- Six cards: today's consumption, configurable daily target, month-to-date consumption, estimated Rand cost, consumption status, and a transparent target/stability score. Selected-day details include estimated cost above the target.
- Daily consumption create/edit, cumulative meter readings and reset baselines, household target, tariff and timezone settings.
- Interactive Recharts history with 7/14/30/90-day and custom ranges, daily target, seven-day average, anomaly markers and selected-day analysis. Missing dates are gaps; zero is a recorded value.
- Deduplicated target, approaching-target, historical-increase, consecutive-high-use and cost alerts, read/dismiss states, and daily reports in the existing notification system.
- Saved Gemini or rule-based explanations, editable multi-reason household context, appliance modelling with duty cycle, bar chart, server-calculated potential savings, and optional prepaid purchase/balance tracking.
- Isolated, persistent simulation sessions, seven scenarios, next-day/spike/geyser controls, animated graph playback with pause/skip, explanation and analysis, and reset.

## Database and ownership

Apply `database/migrations/015_energy_consumption_intelligence.sql` after the existing application migrations. It creates `energy_households`, `energy_consumption_daily`, `energy_meter_readings`, `energy_consumption_explanations`, `energy_ai_insights`, `energy_alerts`, `energy_daily_reports`, `energy_prepaid_purchases`, `energy_prepaid_balances`, and `energy_simulation_sessions`. It extends the existing `appliances` inventory with quantity and duty cycle rather than creating a second inventory. Existing `energy_readings`, profiles and notification records are reused.

The backend derives ownership from the verified active resident. Callers cannot select a household. RLS allows residents to read their own energy rows; energy record writes use the authenticated API and trusted backend database connection. Composite foreign keys prevent explanations/insights referencing another household's daily entry. Simulation data, appliance models, explanations, alerts and advice stay in the session JSON and never enter actual consumption or customer notifications. Reset deletes only the selected owned session.

Use the verifier from the repository root:

```powershell
backend\venv\Scripts\python.exe database\verify_energy_intelligence.py --verify
backend\venv\Scripts\python.exe database\verify_energy_intelligence.py --apply-schema
```

`--verify` runs SQL/API/RLS checks using synthetic residents and always rolls back, including an unapplied schema. `--apply-schema` commits only the migration and creates no demo records. The verifier detects a partial schema and stops for review. Applied migrations are preserved on subsequent runs.

## Calculation and AI rules

Python calculates kWh, target differences and percentages, costs, trends, anomalies, scores, appliance use, savings and prepaid estimates. Meter deltas are allocated by elapsed time; multi-day and partial-day allocations are explicitly estimated. A complete midnight-to-midnight interval can be measured daily use. The first meter reading and an explicit reset establish baselines without inventing consumption. Manual daily records take priority over overlapping meter estimates; timestamped readings take priority over legacy intervals.

Target bands are below 90% normal, 90–100% approaching target, over 100% above target and over 150% critical. Historical anomaly screening is separate from the target band. It needs enough recent measured observations and does not prove an appliance caused the increase. The score exposes its target-adherence and stability factors; it is not a certified efficiency rating. Monthly comparisons require comparable coverage. Costs use a configurable flat Rand/kWh tariff and exclude fixed charges, block tariffs and taxes; they are estimates rather than bills.

Set these existing variables in **backend/.env**:

```dotenv
GEMINI_API_KEY=your_backend_only_key
GEMINI_MODEL=gemini-2.5-flash
```

The backend uses the existing Gemini HTTP integration. Responses must pass a Pydantic schema and prose validation. Numerical facts and savings are rendered from Python results, not model-generated arithmetic. Household explanations are self-reported possibilities. Advice must remain qualified and safe. Provider errors, missing configuration or rejected output yield clearly labelled rule advice. Identical evidence and explanations reuse saved insights; successful Gemini advice is cached for 24 hours, fallback briefly to allow recovery. New actual daily entries attempt analysis after the record commits, so provider failure cannot discard saved consumption.

Appliance estimates use `watts × hours × quantity × duty_cycle / 1000`; a month means the displayed calendar month's day count. The analyser's percentages refer to the modelled inventory only, never measured household consumption. Savings show assumptions and uncertainty. Prepaid balance estimates require an observed kWh balance; purchases alone cannot establish remaining units. Missing consumption or ambiguous timing may make a balance estimate unavailable. Purchase prices are not assumed to be the configured consumption tariff.

## API endpoints

All paths below start with `/api/v1/energy` and require the existing resident bearer session.

| Method | Path | Purpose |
| --- | --- | --- |
| GET, PUT | `/household` or `/settings` | Household target, tariff, size, timezone |
| GET | `/dashboard`, `/analytics` | Calculated dashboard; optional `days`, `start_date`, `end_date`, `session_id` |
| GET, POST | `/consumption` | Paginated daily entries / add daily consumption |
| PUT | `/consumption/{id}` | Correct an owned entry |
| GET, POST | `/meter-readings` | Cumulative reading history / record a reading or reset |
| GET | `/alerts` | Owned actual or session alerts |
| PATCH | `/alerts/{id}` | Mark read or dismissed; optional `session_id` |
| POST | `/explanations`, `/ai/analyse` | Save/update context and analysis / request cached analysis |
| GET | `/insights` | Saved selected-day advice and explanation |
| GET, POST | `/appliances` | Analyse inventory / add appliance; optional `session_id` |
| PUT, DELETE | `/appliances/{id}` | Update/remove an owned actual or simulated model |
| GET | `/advisor` | Calculated savings suggestions and available analysis |
| GET | `/prepaid` | Purchases, observed balances and estimate |
| POST | `/prepaid/purchases`, `/prepaid/balances` | Actual purchase / known meter balance |
| GET | `/simulation` | Restore latest owned demo session |
| POST | `/simulation/generate` | Create 7, 14 or 30 days; `normal`, `excessive`, `geyser`, `heater`, `weekend`, `gradual_increase`, `saving` |
| POST | `/simulation/next-day`, `/simulation/trigger-spike`, `/simulation/geyser-overuse`, `/simulation/reset` | Advance/change/reset an owned session |

Legacy `/api/v1/energy/readings` remains supported.

## Verification and presentation

```powershell
backend\venv\Scripts\python.exe -m pytest backend\tests -q
Set-Location frontend
npm.cmd run build -- --mode development
node --check scripts\test-energy-browser.cjs
node scripts\test-energy-browser.cjs
```

The browser test expects the migrated database, backend on port 8000, Vite on port 5173, and an isolated Chrome/Edge CDP session on port 9230. It reuses the existing temporary resident fixture, cleans up that account afterward, and saves desktop/mobile screenshots in `diagnostics/`. It verifies rendered controls and isolation, not just endpoint JSON.

For a two-minute presentation, open **Energy management → Energy Consumption Simulation**, choose 30 days and a 15 kWh target, and generate the session. Pause or skip playback if desired. Click **Simulate Geyser Overuse**, show the graph, alert and additional estimated cost, then **Analyse with Gemini**. Enter “We used the geyser for longer than usual because we had visitors” and click **Analyse My Explanation**. Open **Appliance Energy Analyser** to show modelled consumption and the saving advisor. **Reset Simulation** clears the demo while preserving actual records.

## Limits

There is no utility meter integration, tariff-block billing engine or scheduled daily job. Actual use is manually entered or derived from supplied meter readings. Reports are generated when records change. Household access follows the application's existing single-owner resident model. Provider availability determines whether advice comes from Gemini or the labelled rule fallback. Electrical causes are hypotheses and modelled savings are estimates. These additions preserve the application's existing authentication design.
