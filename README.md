# EcoPulse AI — Predict Tomorrow. Protect Today.

EcoPulse is a responsive community resilience platform with React + TypeScript, FastAPI, Supabase Auth/PostgreSQL, and PostGIS. Resident, municipal, and admin workspaces share the FastAPI service and database. Supabase credentials and a configured database are required for real account and shared-data flows.

## Run locally

1. Install Node.js 20+ and Python 3.11+.
2. Copy `.env.example` to `frontend/.env.local` and `backend/.env`.
3. Install frontend packages in `frontend/` with `npm install`.
4. Create a Python virtual environment in `backend/`, then run `pip install -r requirements.txt`.
5. Start the API in `backend/` with `uvicorn app.main:app --reload` and the UI in `frontend/` with `npm run dev`.
6. Open `http://localhost:5173`; API documentation is at `http://localhost:8000/docs`.

## Supabase setup

Configure `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY` in the frontend. Configure `SUPABASE_URL`, `DATABASE_URL`, and a supported JWT verification setup in the backend. Keep `SUPABASE_SERVICE_ROLE_KEY` backend-only; it is required for invitation email and private incident media. Set Supabase Auth email confirmation and redirect URLs for the deployed UI.

Review and apply migrations in order: `001_resident_portal.sql`, `002_climate_intelligence.sql`, `003_multi_role_platform.sql`, and `004_incident_reporting_onboarding.sql`. Apply first to a development Supabase project, then verify the RLS policies and Auth/Storage configuration before production. Migration 004 adds the private `incident-media` Storage bucket, report references and AI provenance, plus resident onboarding fields. It is additive; do not apply against an unreviewed production schema.

Public registration provisions resident accounts only. On first login, a resident selects an existing province and municipality, enters a community/suburb, and chooses notification preferences. A trusted admin invitation grants municipal membership; admin role assignment remains server-side.

## Incident image analysis

Residents can take or select a JPEG, PNG, or WebP photo, optionally request analysis, review or change the suggested category, and submit with or without a photo. FastAPI checks the decoded image, restricts it to 10 MB and 25 megapixels, fixes orientation, converts to RGB, resizes it, and strips metadata before inference/storage. Supabase Storage is private; the API issues short-lived signed links only after checking the resident or municipal incident scope. The optional model-training consent is false by default and is stored separately from AI and resident-confirmed labels.

No incident classifier or model weights ship with this repository. With `ECOPULSE_INCIDENT_MODEL` unset, the AI endpoint explicitly reports that analysis is unavailable and residents can continue manually. A deployment may set it to a trusted installed `module:function` adapter. That callable receives a normalized Pillow image and must return `category`, `observations` (actual visual evidence), and optionally `subcategory`, `confidence`, `severity`, `model_name`, and `model_version`. The adapter must be evaluated against a representative held-out dataset before use; EcoPulse does not claim accuracy. The generated explanation only paraphrases the adapter's observations and says the image cannot verify the cause.

## Main API flows

- `POST /api/v1/incidents/analyze-image` validates and analyzes a photo before submission; requires a resident access token.
- `POST /api/v1/incidents/submit` accepts `report` JSON plus an optional `image` multipart part, stores the record/media, and returns a unique public reference.
- `GET /api/v1/incidents` and `GET /api/v1/incidents/{id}` are limited to the authenticated resident's own reports and public updates.
- `GET /api/v1/resident/onboarding` and `PUT /api/v1/resident/onboarding` provide trusted municipality/community selection and preferences.
- Municipal incident detail/status routes remain scoped to active municipal memberships. AI suggestions, resident-confirmed categories, and municipal-verified categories are distinct fields.
- Admin AI model and analysis endpoints show model/version and analysis metadata; confidence is not accuracy.

## Tests and current limits

Run backend checks from the repository root with `PYTHONPATH=.` and `backend/venv/Scripts/python.exe -m pytest backend/tests -q`. Image preprocessing tests require Pillow; multipart routes require `python-multipart`; both are declared in `backend/requirements.txt`. Frontend type checking uses `frontend/node_modules/.bin/tsc -b`.

The Supabase migrations have not been applied in this workspace because database credentials are not configured. No pretrained incident model is included. Privacy/terms pages remain drafts; some community-resources, household management, and advanced analytics sections still depend on future data integration. SQLite mode is isolated local demo storage and is not appropriate for real resident records or public deployment.

Weather uses Open-Meteo and transparent rule-based screening; it is not emergency guidance. Keep `SUPABASE_SERVICE_ROLE_KEY` out of frontend configuration and Git.


### Local resident registration without email

For localhost development, set `ECOPULSE_LOCAL_PASSWORD_SIGNUP=true` in `backend/.env` and restart the backend after changing the setting. Vite development builds on localhost call `POST /api/v1/auth/register-local`; new accounts are confirmed server-side and immediately sign in with email/password. This endpoint requires an explicit backend flag and a loopback client connection. The server service-role key stays on the backend. Registration accepts no role field and verifies that the created profile is a resident. Existing accounts/passwords are never overwritten; sign in instead of re-registering.

Keep this flag false when deploying (the example defaults to false). Production builds retain the normal Supabase signup/email-confirmation flow. Existing confirmed accounts continue to use the same sign-in flow. A live temporary resident registration/password sign-in check passed and its test account was deleted; no confirmation email was sent.
