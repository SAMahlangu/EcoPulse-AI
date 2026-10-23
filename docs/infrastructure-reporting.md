# Infrastructure reporting

The feature extends the existing resident incidents, municipal portal, Supabase
authentication, private `incident-media` bucket and status history. It does not
introduce a second report or user system.

## Login when Windows DNS is unavailable

The frontend sends authentication requests through the local FastAPI auth
gateway. Supabase still performs password verification, token issuance, refresh
and logout. The gateway uses the existing public anon key, never the service-role
key. Backend JWT verification and role/profile checks remain required.

On this workstation, `ECOPULSE_DNS_FALLBACK=true` enables an application-only
fallback when Windows cannot resolve an upstream hostname. Current public DNS
answers are fetched over certificate-verified HTTPS from Cloudflare and cached
for their TTL. Requests retain the original HTTPS Host and TLS server name.
Database connections retain their configured hostname, credentials and TLS
options while using libpq's `hostaddr` to avoid the failing system lookup.
No administrator rights or network-wide DNS changes are required. Normal DNS is
always tried first, and the fallback defaults to off in the example settings.

The same network transport supports private image storage, signing keys,
geocoding, weather and Gemini so the reporting flow can function on this machine.
Both servers must be restarted after changing backend environment settings.

Live login verification uses a temporary confirmed resident, tests a real
password sign-in, incorrect-password rejection, JWT/profile loading, session
refresh and logout, and deletes the temporary account afterward:

```cmd
backend\venv\Scripts\python.exe backend\tests\verify_login_connection.py --check-ai
```

## AI configuration

Backend `.env` settings:

```dotenv
YOLO_MODEL_PATH=models/best.pt
YOLO_MIN_CONFIDENCE=0.25
YOLO_CONFIRM_CONFIDENCE=0.40
YOLO_HIGH_CONFIDENCE=0.70
YOLO_DEVICE=cpu
GEMINI_API_KEY=your-backend-only-key
GEMINI_MODEL=gemini-2.5-flash
```

Relative model paths resolve from `backend/`. The supplied weights remain at
`backend/models/best.pt`. The seven class names are validated against the model
metadata. Weights load once per backend process, inference is serialized and runs
in a worker thread. Restart after changing the model path or replacing weights.

YOLO returns all boxes, labels and confidence values and an annotated preview.
Gemini handles low-confidence results, resident corrections and written reports.
Its system instructions restrict it to municipal infrastructure evidence; a
Pydantic schema separates observations, resident statements and assumptions.
Neither model controls the final category, emergency decisions or department
routing. The resident can correct the suggestion or submit manually during an
AI outage. Photo analysis may send the image to Gemini; the reporting interface
discloses this before analysis. Camera access requires localhost or HTTPS.

Analysis provenance and individual detections live in the existing
`incident_ai_analyses.result_metadata` JSON, linked to the report. A source-image
digest prevents attaching an analysis to a different submitted photo. Submitted
photos are safely decoded, oriented and stripped of metadata before private
storage; original and annotated versions remain separate objects.

## Database and department setup

Apply migrations through `008_infrastructure_departments.sql`. Migration 008
was applied to the connected development database after rollback verification.
It fills missing municipality types from the existing canonical source view;
it does not infer boundaries. No boundary geometries were present during live
inspection, so resident municipality confirmation is required. Historical source
municipalities remain as existing reference rows; their current validity requires
the existing geography review process.

In **Administrator → Settings → Infrastructure departments and routing**:

1. Choose a municipality and create its service departments.
2. Give an existing municipal employee access to their department. The employee
   must already have active membership of that municipality.
3. Set a category route, for example `water` → Water and Sanitation.
4. Give a responsible official access to the municipality's default triage queue.

Unconfigured categories route to the municipality's triage department. Membership
does not automatically grant department access. Existing municipal officials
therefore need explicit department grants. Department managers may assign
technicians and reassign reports inside their municipality. Broad municipality
access is available only through the trusted `can_manage_all_departments` flag;
it defaults to false and cannot be changed by residents or employees.

Existing municipal dashboard counts, report lists, maps, analytics, exports,
detail views, status writes and staff notes enforce department access. PostgreSQL
RLS also protects report, history, media, assignment and storage-object access.
The backend SQL connection is privileged, so its report queries additionally
apply `can_manage_incident`; frontend filtering is never the security boundary.

## Workflow

Residents take or upload a photo, optionally request analysis, confirm/correct the
issue, select a map point, confirm a municipality, review and submit. References
are generated on the server using year plus a random UUID suffix and a unique
database index. Repeated submissions use the existing idempotency key.

Municipal staff use the existing incident detail dialog to post public updates,
write internal notes, assign technicians, reassign departments and upload repair
evidence. Assignment/reassignment requires manager privileges. Residents see only
their own reports and public updates, refreshed every 15 seconds.

The municipal map supports department, municipality, category, status, priority
and date filters after authorization. Town/suburb searches use the existing
Open-Meteo geocoding integration on explicit search, not Nominatim autocomplete.
The marker can be dragged or set by clicking the map.

## Verification

From the repository root:

```cmd
backend\venv\Scripts\python.exe -m pytest backend\tests -q
```

Live verification uses the configured Supabase database and private Storage:

```cmd
backend\venv\Scripts\python.exe backend\tests\verify_infrastructure_database.py
```

The live verifier creates temporary users and reports inside a transaction, checks
automatic routing, HTTP/API and PostgreSQL RLS isolation, updates statuses,
checks resident tracking, uploads a synthetic photo and signs its URL. It deletes
the temporary storage object and rolls back database fixtures. Run against a
development project. Do not run concurrent copies.

From `frontend/`, run `npm.cmd run build` plus the existing `scripts/test-*.cjs`
regression checks. Model loading and a blank-image inference were checked; no
claim is made about accuracy on real infrastructure images. Physical camera,
device geolocation and representative pothole photographs still require a
device/demo acceptance check.

References used for integration:
[Ultralytics prediction](https://docs.ultralytics.com/modes/predict/) and
[Gemini structured output](https://ai.google.dev/gemini-api/docs/generate-content/structured-output).
