# Smart Water Consumption Intelligence

Smart Water Intelligence is integrated into the existing resident Water Management page at `/resident/water`. The page presents only **Water Consumption Simulation** as an interactive demonstration. It generates isolated demo records and labels them as simulated; resetting the demo does not change household readings, alerts or notifications. The simulation chart defaults to the current month and begins on its first day.

## Data and privacy

The feature uses the existing resident login and derives the household from the signed-in resident. A request cannot choose another household. The additive migration creates `water_households`, `water_consumption_daily`, `water_meter_readings`, `water_consumption_explanations`, `water_ai_insights`, `water_alerts`, `water_daily_reports` and `water_simulation_sessions`. It reuses the existing `water_readings` table as a legacy source and the existing `notifications` table for deduplicated daily reports. RLS permits residents to read their own records; mutations go through the authenticated backend using its existing trusted database connection.

Missing dates remain missing in stored data and calculations rather than being treated as zero. The monthly graph starts on the first day of the current month and may show a separate, orange dashed line of deterministic illustrative values for visual continuity; those samples are never saved or included in totals, alerts, incident drafts or AI analysis. Meter usage across an interval is estimated across covered days and labelled as estimated. A meter reset establishes a new baseline. The server calculates usage and supplies those facts to the advisor; Gemini does not calculate household totals. Provider failures and missing keys return clearly labelled rule-based guidance.

Set `GEMINI_API_KEY` in `backend/.env` to enable Gemini explanations. `GEMINI_MODEL` is optional and defaults to `gemini-2.5-flash`. Keep provider credentials on the backend; do not add them to frontend environment variables.

## Database setup

Review and apply `database/migrations/014_water_consumption_intelligence.sql` to the project database after the existing application schema is present. The migration is additive and expects the established `profiles` and `notifications` tables. It was not applied as part of local implementation. The optional verifier supports `--verify` (transactional schema, isolation and API checks) and `--apply-schema` (commits the migration); use these only against a database where you intend to run those checks.

After applying the migration, run the resident application and visit **Water management**. The backend creates each resident's household record on first access. To check API readiness, authenticate as an active resident and load `/api/v1/water/dashboard`; `/health` alone does not verify the water schema.

## Local checks

From the repository root in PowerShell:

```powershell
backend\venv\Scripts\python.exe -m pytest backend\tests\test_water_intelligence.py backend\tests\test_water_api.py -q
backend\venv\Scripts\python.exe -m pytest backend\tests -q
Set-Location frontend
npm.cmd run build -- --mode development
node --test scripts\test-water-investigation.cjs
node --check scripts\test-water-browser.cjs
```

An authenticated browser run is available as `frontend/scripts/test-water-browser.cjs`. It expects the migrated database, local backend on port 8000, Vite on port 5173, and an isolated Chromium CDP endpoint on port 9230. It creates and removes a temporary resident account and writes desktop/mobile screenshots under `diagnostics/`. Review those prerequisites before running it against a shared database.

## Simulation and incident reporting

The simulation tab supports 7, 14 or 30 initial days, deterministic normal, excessive-use, possible-leak, weekend, gradual-increase and saving scenarios, next-day generation and an explicit leak trigger. Simulation records, explanations and analysis are stored under a separate owner-scoped simulation session. They do not issue household water notifications. A simulated leak can open the existing incident workflow with a visible demo warning and a prefilled draft; the resident must review and submit it.
