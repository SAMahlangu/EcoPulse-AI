# EcoPulse AI - project submission

EcoPulse connects South African residents and municipal teams through community information, infrastructure reporting, and household resource awareness. The application uses React and TypeScript, FastAPI, Supabase Auth/PostgreSQL, PostGIS, and Leaflet.

## Submission links

- Website: https://ecopulseaifnb.netlify.app/
- Source repository: https://github.com/SAMahlangu/EcoPulse-AI
- API health: https://ecopulse-api-mxfn.onrender.com/api/v1/health
- API documentation: https://ecopulse-api-mxfn.onrender.com/docs

Render's free service may need time to start after inactivity. A health response checks API availability; authenticated features also require the configured database and provider integrations.

## Suggested demonstration

1. Open the landing page, register with an email address you control, confirm the email, and sign in. Use an existing evaluator account only if the project owner supplies one privately. No account passwords are included in the repository.
2. On **Your community**, use **Use my location** or manually select the province and municipality. Review the suggested suburb and save. Location access is optional; exact device coordinates are not saved to the profile.
3. Open the community resources map and inspect local facilities such as clinics, police stations, schools, and community services. Results identify their sources and can vary by area. Only explicitly shared resident reports appear as approximate issue pins.
4. Open **Report an issue**, add a description and optional infrastructure photo, review the location and suggestions, and submit only if an evaluation report is appropriate. The report receives a reference and is visible under **My reports**. A submitted report persists in the shared database.
5. Open **Energy management** to inspect actual daily consumption separately from the labelled simulation. Open **Water management** for its labelled water simulation. Demonstration records remain separate from actual household data.
6. Open **EcoMove** and explore the configured Pretoria demonstration coverage. Vehicle movement and the displayed demo journeys are illustrative rather than live taxi tracking.
7. If an authorised municipal evaluator account is available, inspect its scoped reports and status workflow. Resident registration does not grant staff access. Sign out through the initials menu in the top-right corner.

For a demonstration that leaves household readings unchanged, use the labelled consumption simulations. Normal report submission and actual consumption entry save real records.

## Run from source on Windows

Use Python 3.11+ and Node.js 20+. From the repository root in CMD, create the backend environment:

```cmd
py -3 -m venv backend\venv
backend\venv\Scripts\python.exe -m pip install -r backend\requirements.txt
copy backend\.env.example backend\.env
copy frontend\.env.example frontend\.env.local
```

Fill in the backend database/Auth settings and the frontend public Supabase settings. Review all migrations in `database/migrations/` in filename order, including both `010_` files and both `011_` files. Apply only missing migrations to the intended Supabase project, then populate its geography directory:

```cmd
backend\venv\Scripts\python.exe database\seed_sa_geography.py
```

Run the API and frontend in separate terminals from the repository root:

```cmd
backend\venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload --port 8000
```

```cmd
cd frontend
npm ci
npm run dev
```

Open `http://localhost:5173`; local API documentation is at `http://localhost:8000/docs`. Local registration without email requires explicitly enabling `ECOPULSE_LOCAL_PASSWORD_SIGNUP` in the backend; keep that flag false when deployed. For an email-confirmed registration demonstration, use the deployed website. See [deployment setup](docs/deployment.md) for HTTPS build variables and Supabase redirect settings.

## Verification

The final location implementation passed 352 backend tests and seven isolated browser scenarios. Live sample coordinates in Soweto, Pretoria, and Durban resolved to their corresponding municipalities. These checks do not establish the accuracy of every location or third-party record.

From the repository root:

```cmd
backend\venv\Scripts\python.exe -m pytest backend\tests -q
backend\venv\Scripts\python.exe diagnostics\check_text_encoding.py
backend\venv\Scripts\python.exe database\seed_sa_geography.py --validate-only
```

From `frontend/`:

```cmd
npm run typecheck
npm run test:checks
npm run build
```

The release build requires public HTTPS values for `VITE_API_URL` and `VITE_SUPABASE_URL`. Local HTTP development settings deliberately fail the release check. The non-browser suites check geography, registration, municipal forms, security-policy generation, water investigation drafts, map-tile fallback, and transport simulation. `scripts/test-onboarding-location.cjs` additionally checks the rendered form using fixture-only auth, API responses, and geolocation; it needs a running local frontend and an isolated Chromium CDP endpoint on port 9234. The fixture check does not save real profiles or certify production login.

The production dependency audit (`npm audit --omit=dev --audit-level=high`) passes its high-severity threshold after removing an unused Zod dependency. It still reports two moderate React Router findings. The SSR finding excludes this application's declarative routing mode; the navigation finding remains tracked pending a major router upgrade. See the [navigation advisory](https://github.com/advisories/GHSA-wrjc-x8rr-h8h6) and [SSR advisory](https://github.com/advisories/GHSA-337j-9hxr-rhxg). Development-tool dependencies are outside that production audit; the current Vite/esbuild toolchain also has a development-server advisory, so no clean full dependency audit is claimed.

## Scope and limitations

- Weather, geographic labels, facilities, and AI depend on third-party services. Provider failures and missing data are surfaced; resource listings do not guarantee opening hours, availability, or emergency response.
- Consumption forecasts, tariffs, appliance estimates, and AI advice expose their assumptions. Simulations are labelled; EcoMove demonstration vehicles are not live GPS data. The current Water page presents the simulation interface.
- Incident images are private and access is scoped by role and ownership. Public issue sharing is opt-in and uses approximate coordinates.
- Legal pages remain drafts, model accuracy has not been independently evaluated, and session storage/privileged-MFA hardening remains documented in [the security baseline](docs/security-baseline.md). This is a project evaluation submission, not a certification for public or high-risk use.
- Email delivery needs a configured backend provider. Provider acceptance is not proof of inbox delivery. No test emails, production migrations, or real account changes are required to review the source.
