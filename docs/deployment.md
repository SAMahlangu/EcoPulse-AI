# Render and Netlify deployment

The repository includes a Render Blueprint (`render.yaml`) for the FastAPI service and a Netlify build configuration (`netlify.toml`) for the Vite frontend. Both deploy from the repository's `main` branch. Do not commit `.env` files or production credentials.

## Before deploying

- Confirm the production Supabase project has the required database schema, RLS policies, Auth configuration, and migrations applied. Review the SQL files in `database/migrations/` against the project's current schema before applying any missing migrations.
- Have these Supabase values available for the Render service: project URL, publishable key, server-only secret key, and Postgres connection URL.
- Choose a Netlify site name so its public URL is known. The Netlify URL is needed by the API's `CORS_ORIGINS` and `APP_PUBLIC_URL`.

## Deploy the API to Render

1. In Render, create a new Blueprint instance and connect `SAMahlangu/EcoPulse-AI` on `main`. Render reads `render.yaml` from the repository root.
2. Enter the requested values for `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SECRET_KEY`, and `DATABASE_URL`. Set `CORS_ORIGINS` to the exact Netlify origin (for example, `https://your-site.netlify.app`, with no trailing slash), and `APP_PUBLIC_URL` to that same origin.
3. To send resident email alerts, configure one backend email provider in the Render service environment:
   - **Gmail SMTP:** set `SMTP_HOST` to `smtp.gmail.com`, `SMTP_PORT` to `465`, `SMTP_USERNAME` to the Gmail address, `SMTP_PASSWORD` to a newly generated Gmail App Password, `SMTP_FROM` to a sender using that address, `SMTP_USE_SSL` to `true`, and `SMTP_TIMEOUT_SECONDS` to `10`.
   - **Resend:** set `RESEND_API_KEY` and `RESEND_FROM`. Use a sender address on a verified Resend domain. When `RESEND_API_KEY` is non-empty, Resend takes priority over SMTP.

   These are backend-only values; do not add them to Netlify or any `VITE_*` variable. If the deployment already exists, add the selected provider's variables under **Render Dashboard → ecopulse-api → Environment** and redeploy. Without a configured provider, in-app notifications can still be created, but no emails are sent.
4. Leave `ECOPULSE_LOCAL_DEMO` and `ECOPULSE_LOCAL_PASSWORD_SIGNUP` set to `false`. Do not add service keys or database credentials to Netlify or any `VITE_*` variable.
5. Wait for the deploy and check `https://<render-service>.onrender.com/api/v1/health`; it should return `{"status":"ok", ...}`.

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

Set optional provider variables in Render's environment dashboard only when enabling those integrations. For Gemini-powered AI features, add `GEMINI_API_KEY`; `GEMINI_MODEL` defaults to `gemini-2.5-flash`. For notification email, configure `RESEND_API_KEY` and `RESEND_FROM` (or the `SMTP_*` settings) on the backend. Keep all provider credentials backend-only. The API logs whether a provider is unconfigured, accepts a message, or rejects it; use Resend's dashboard to check final delivery or bounces.

After the API is deployed with email settings, sign in as a resident and use **Profile & settings → Test email delivery** to send a test only to that account. The test respects the municipal-notices preference and can be sent once per minute. Provider acceptance confirms submission to the provider, not inbox delivery.
