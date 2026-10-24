/** Build a restrictive policy from the exact API and Supabase origins in use. */
export function securityPolicyPlugin({ apiOrigin, supabaseOrigin }) {
  const connectSources = ["'self'", apiOrigin, supabaseOrigin].filter(Boolean);
  const imageSources = [
    "'self'",
    "data:",
    "blob:",
    "https://*.tile.openstreetmap.org",
    supabaseOrigin,
  ].filter(Boolean);

  const policy = [
    "default-src 'self'",
    "script-src 'self'",
    `connect-src ${connectSources.join(" ")}`,
    `img-src ${imageSources.join(" ")}`,
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' data: https://fonts.gstatic.com",
    "frame-src https://www.openstreetmap.org",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
  ].join("; ");
  const escapedPolicy = policy.replaceAll("&", "&amp;").replaceAll('"', "&quot;");

  return {
    name: "ecopulse-content-security-policy",
    transformIndexHtml(html) {
      return html.replace(
        /<head>/i,
        `<head><meta http-equiv="Content-Security-Policy" content="${escapedPolicy}">`,
      );
    },
  };
}

export function httpsOrigin(value, settingName) {
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error(`${settingName} must be set to its production HTTPS address before a release build.`);
  }
  if (parsed.protocol !== "https:") {
    throw new Error(`${settingName} must use HTTPS in a release build.`);
  }
  if (["localhost", "127.0.0.1", "::1", "[::1]"].includes(parsed.hostname)) {
    throw new Error(`${settingName} must use a public service address in a release build.`);
  }
  if (parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error(`${settingName} must not contain credentials, a query, or a fragment.`);
  }
  return parsed.origin;
}
