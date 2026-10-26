# EcoPulse AI — Predict Tomorrow. Protect Today.

EcoPulse is a responsive community resilience platform with React + TypeScript, FastAPI, Supabase Auth/PostgreSQL, and PostGIS. Resident, municipal, and admin workspaces share the FastAPI service and database. Supabase credentials and a configured database are required for real account and shared-data flows.

## Run locally

1. Install Node.js 20+ and Python 3.11+.
2. Copy `frontend/.env.example` to `frontend/.env.local` and `backend/.env.example` to `backend/.env`. Fill in the Supabase settings; keep server credentials in the backend file only.
3. Install frontend packages in `frontend/` with `npm ci` using the committed lockfile.
4. Create a Python virtual environment in `backend/`, then run `pip install -r requirements.txt`.
5. Start the API in `backend/` with `uvicorn app.main:app --reload` and the UI in `frontend/` with `npm run dev`.
6. Open `http://localhost:5173`; API documentation is at `http://localhost:8000/docs`.

The sample keeps local SQLite demo mode off. To intentionally run an isolated demo without Supabase, set `ECOPULSE_LOCAL_DEMO=true` in your local `backend/.env`; leave it `false` for any shared or deployed system.

## Supabase setup

Configure `VITE_SUPABASE_URL` and `VITE_SUPABASE_PUBLISHABLE_KEY` in the frontend. Configure `SUPABASE_URL`, `DATABASE_URL`, and a supported JWT verification setup in the backend. Keep `SUPABASE_SECRET_KEY` backend-only; it is required for invitation email and private incident media. Legacy `anon` and `service_role` variable names remain as migration fallbacks. Set Supabase Auth email confirmation and redirect URLs for the deployed UI.

Review every SQL file in `database/migrations/`, from `001_resident_portal.sql` through `016_community_resources_and_sharing.sql`, in filename order. Both files beginning with `010_` and both beginning with `011_` are required. After the geography schema is present, run `backend/venv/Scripts/python.exe database/seed_sa_geography.py` from the repository root to populate the municipality reference directory. Apply first to a development Supabase project and verify Auth, Storage, and RLS configuration. Existing installations should review and apply only missing migrations; no migration is applied automatically when the API starts.

## Deployment

Render and Netlify deployment configuration and environment-variable setup are documented in [docs/deployment.md](docs/deployment.md).

For the evaluation links, demonstration steps, and verified scope, see [SUBMISSION.md](SUBMISSION.md).

Public registration provisions resident accounts only. On first login, a resident can use their location or select an existing province and municipality, enter a community/suburb, and choose notification preferences. The administrator's Add municipal official form creates a municipal account and membership; existing invitation acceptance remains supported. Role assignment stays server-side.

## Notification email setup

For local development, set `RESEND_API_KEY` and `RESEND_FROM` in `backend/.env`, then restart the API. Use a sender address on a verified Resend domain. For the deployed API, set both variables in the Render service environment and redeploy; local `.env` values are not deployed. Resend takes priority when its API key is set; otherwise the existing `SMTP_*` settings are used. Supabase Auth email configuration is separate from application notifications.

Incident updates and municipal service alert updates email the resident's account address, respecting their `incident_updates` and `municipal_notices` preferences. Delivery runs in the background; provider failures leave the in-app notification intact. Check API logs for Resend acceptance or HTTP rejection status. Acceptance does not confirm inbox delivery; check the Resend dashboard for delivery/bounce status. These background tasks are not a durable queue and do not retry failed deliveries.

Residents can use **Profile & settings → Test email delivery** to send one diagnostic message to their own account address. The endpoint requires resident authentication, respects the municipal-notices opt-out, and is limited to one test per minute. An accepted response means the provider accepted the message; check the inbox, spam folder, or provider delivery logs for final delivery.

## Incident image analysis

Residents can take or select a JPEG, PNG, or WebP photo, optionally request analysis, review or change the suggested category, and submit with or without a photo. FastAPI checks the decoded image, restricts it to 10 MB and 25 megapixels, fixes orientation, converts to RGB, resizes it, and strips metadata before inference/storage. Supabase Storage is private; the API issues short-lived signed links only after checking the resident or municipal incident scope. The optional model-training consent is false by default and is stored separately from AI and resident-confirmed labels.

The supplied custom YOLO model at `backend/models/best.pt` now powers infrastructure detection. Gemini provides evidence-bound structured interpretation for uncertain predictions, written reports and resident corrections. Keys stay in backend environment variables. See [infrastructure reporting setup and verification](docs/infrastructure-reporting.md) for department routing, permissions, model configuration and demonstration steps.

## Main API flows

- `POST /api/v1/incidents/analyze-image` validates and analyzes a photo before submission; requires a resident access token.
- `POST /api/v1/incidents/submit` accepts `report` JSON plus an optional `image` multipart part, stores the record/media, and returns a unique public reference.
- `GET /api/v1/incidents` and `GET /api/v1/incidents/{id}` are limited to the authenticated resident's own reports and public updates.
- `GET /api/v1/resident/onboarding` and `PUT /api/v1/resident/onboarding` provide trusted municipality/community selection and preferences.
- Municipal incident detail/status routes remain scoped to active municipal memberships. AI suggestions, resident-confirmed categories, and municipal-verified categories are distinct fields.
- Admin AI model and analysis endpoints show model/version and analysis metadata; confidence is not accuracy.

## Tests and current limits

Run backend checks from the repository root with `backend/venv/Scripts/python.exe -m pytest backend/tests -q`. Image preprocessing tests require Pillow; multipart routes require `python-multipart`; both are declared in `backend/requirements.txt`. From `frontend/`, run `npm run typecheck`, `npm run test:checks`, and `npm run build`. Release builds require HTTPS API and Supabase URLs; keep local HTTP settings for development. Browser checks and their prerequisites are listed in [SUBMISSION.md](SUBMISSION.md).

Implemented modules include the community resources map and consented approximate issue pins, household energy intelligence, water consumption simulation, climate guidance, and EcoMove demonstration routes. Resource coverage depends on the external providers and selected area. Transport simulation is labelled and does not provide live vehicle tracking. The supplied custom YOLO weights load, but representative-image accuracy has not been evaluated. Privacy/terms pages remain drafts; the existing session and privileged-MFA limitations are recorded in [docs/security-baseline.md](docs/security-baseline.md). This submission demonstrates the application and does not certify it for production use. SQLite mode is isolated local demo storage and is not suitable for real resident records.

Weather uses Open-Meteo and transparent rule-based screening; it is not emergency guidance. Keep `SUPABASE_SECRET_KEY` and the legacy `SUPABASE_SERVICE_ROLE_KEY` out of frontend configuration and Git.


### Local resident registration without email

For localhost development, set `ECOPULSE_LOCAL_PASSWORD_SIGNUP=true` in `backend/.env` and restart the backend after changing the setting. Vite development builds on localhost call `POST /api/v1/auth/register-local`; new accounts are confirmed server-side and immediately sign in with email/password. This endpoint requires an explicit backend flag and a loopback client connection. The server service-role key stays on the backend. Registration accepts no role field and verifies that the created profile is a resident. Existing accounts/passwords are never overwritten; sign in instead of re-registering.

Keep this flag false when deploying (the example defaults to false). Production builds retain the normal Supabase signup/email-confirmation flow. Existing confirmed accounts continue to use the same sign-in flow. A live temporary resident registration/password sign-in check passed and its test account was deleted; no confirmation email was sent.
