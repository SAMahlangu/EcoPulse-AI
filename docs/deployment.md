# Render and Netlify deployment

The repository includes a Render Blueprint (`render.yaml`) for the FastAPI service and a Netlify build configuration (`netlify.toml`) for the Vite frontend. Both deploy from the repository's `main` branch. Do not commit `.env` files or production credentials.

## Before deploying

- Confirm the production Supabase project has the required database schema, RLS policies, Auth configuration, and migrations applied. Review the SQL files in `database/migrations/` against the project's current schema before applying any missing migrations.
- Have these Supabase values available for the Render service: project URL, publishable key, server-only secret key, and Postgres connection URL.
- Choose a Netlify site name so its public URL is known. The Netlify URL is needed by the API's `CORS_ORIGINS` and `APP_PUBLIC_URL`.

## Deploy the API to Render

1. In Render, create a new Blueprint instance and connect `SAMahlangu/EcoPulse-AI` on `main`. Render reads `render.yaml` from the repository root.
2. Enter the requested values for `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SECRET_KEY`, and `DATABASE_URL`. Set `CORS_ORIGINS` to the exact Netlify origin (for example, `https://your-site.netlify.app`, with no trailing slash), and `APP_PUBLIC_URL` to that same origin.
3. Leave `ECOPULSE_LOCAL_DEMO` and `ECOPULSE_LOCAL_PASSWORD_SIGNUP` set to `false`. Do not add service keys or database credentials to Netlify or any `VITE_*` variable.
4. Wait for the deploy and check `https://<render-service>.onrender.com/api/v1/health`; it should return `{"status":"ok", ...}`.

The Blueprint uses Render's Free plan as requested. Free services can spin down when idle and have limited memory. The API loads its 53 MB YOLO model only when image analysis is first requested; model inference may exceed Free-tier memory. If image analysis fails or the service restarts during inference, upgrade the Render service to a plan with more memory.

## Deploy the frontend to Netlify

1. In Netlify, add a new site by importing `SAMahlangu/EcoPulse-AI` from Git and select `main`. The settings are provided by `netlify.toml`.
2. In the Netlify site's environment variables, set:
   - `VITE_API_URL` to `https://<render-service>.onrender.com/api/v1`
   - `VITE_SUPABASE_URL` to the Supabase project URL
   - `VITE_SUPABASE_PUBLISHABLE_KEY` to the Supabase publishable key
3. Trigger a new deploy after saving the variables. The Vite production build requires HTTPS API and Supabase URLs.
4. In Supabase Auth settings, add the Netlify site URL to the allowed redirect URLs and configure the production email-confirmation flow.
5. If the Netlify URL changed from the value used in Render, update `CORS_ORIGINS` and `APP_PUBLIC_URL` on Render and redeploy the API.

`netlify.toml` includes an SPA fallback so client-side routes such as `/assistant` continue to work after refresh. Its production build also generates the Content Security Policy using the configured API and Supabase origins.

## Optional backend integrations

Set optional provider variables in Render's environment dashboard only when enabling those integrations. Examples include `GEMINI_API_KEY` for AI-assisted features, and `RESEND_API_KEY` plus `RESEND_FROM` (or the `SMTP_*` settings) for notification email. Keep all provider credentials backend-only.
