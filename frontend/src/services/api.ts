const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8000/api/v1';
let csrfToken: string | null = null;
let browserAuthenticated = false;
let sessionExpiresAt = 0;
let authConfigured = false;
let bootstrap: Promise<void> | null = null;
let refresh: Promise<boolean> | null = null;

function detailMessage(detail: unknown, fallback: string): string {
  if (typeof detail === 'string') return detail;
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === 'string') return message;
  }
  if (Array.isArray(detail)) {
    const messages = detail.map((item: unknown) => {
      if (!item || typeof item !== 'object') return '';
      const error = item as { msg?: unknown; loc?: unknown[] };
      const message = typeof error.msg === 'string' ? error.msg.replace(/^Value error,\s*/i, '') : '';
      const field = Array.isArray(error.loc) ? error.loc[error.loc.length - 1] : undefined;
      return message ? `${field && field !== 'body' ? `${String(field).replace(/_/g, ' ')}: ` : ''}${message}` : '';
    }).filter(Boolean);
    if (messages.length) return messages.join('. ');
  }
  return fallback;
}

async function bootstrapSession(): Promise<void> {
  if (!bootstrap) {
    bootstrap = fetch(`${API}/auth/session`, { credentials: 'include', cache: 'no-store' })
      .then(async response => {
        if (!response.ok) throw new Error('Could not initialise a secure EcoPulse session.');
        const result = await response.json() as { csrf_token?: string; authenticated?: boolean; expires_at?: number; configured?: boolean };
        csrfToken = result.csrf_token ?? null;
        browserAuthenticated = Boolean(result.authenticated);
        sessionExpiresAt = result.expires_at ?? 0;
        authConfigured = Boolean(result.configured);
      })
      .finally(() => { bootstrap = null; });
  }
  await bootstrap;
}

export function initializeBrowserSession() {
  return bootstrapSession().then(() => ({ authenticated: browserAuthenticated, expiresAt: sessionExpiresAt, configured: authConfigured }));
}

export function updateBrowserSession(authenticated: boolean, csrf?: string | null, expiresAt?: number) {
  browserAuthenticated = authenticated;
  if (csrf !== undefined) csrfToken = csrf;
  if (expiresAt !== undefined) sessionExpiresAt = expiresAt;
}

async function refreshSession(): Promise<boolean> {
  if (!refresh) {
    refresh = (async () => {
      await bootstrapSession();
      const response = await fetch(`${API}/auth/gateway/token?grant_type=refresh_token`, {
        method: 'POST', credentials: 'include', cache: 'no-store',
        headers: { 'Content-Type': 'application/json', ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}) }, body: '{}',
      });
      if (!response.ok) {
        if (response.status === 401) browserAuthenticated = false;
        return false;
      }
      const result = await response.json() as { csrf_token?: string; expires_at?: number };
      if (result.csrf_token) csrfToken = result.csrf_token;
      sessionExpiresAt = result.expires_at ?? Math.floor(Date.now() / 1000) + 3600;
      browserAuthenticated = true;
      return true;
    })().finally(() => { refresh = null; });
  }
  return refresh;
}

export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  await bootstrapSession();
  const method = (init.method ?? 'GET').toUpperCase();
  const headers = new Headers(init.headers);
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    if (!csrfToken) await bootstrapSession();
    if (csrfToken) headers.set('X-CSRF-Token', csrfToken);
  }
  const request = () => fetch(`${API}${path}`, {
    ...init, method, headers, credentials: 'include', cache: 'no-store',
  });
  let response = await request();
  if (response.status === 401 && browserAuthenticated) {
    const refreshed = await refreshSession();
    if (refreshed) response = await request();
  }
  return response;
}

export async function authGateway<T = Record<string, unknown>>(action: string, body: unknown = {}, method: 'POST' | 'PUT' = 'POST'): Promise<T> {
  await bootstrapSession();
  const response = await fetch(`${API}/auth/gateway/${action}`, {
    method, credentials: 'include', cache: 'no-store',
    headers: { 'Content-Type': 'application/json', ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}) },
    body: JSON.stringify(body),
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(detailMessage((result as { detail?: unknown }).detail, `Request failed (${response.status})`));
  const sessionResult = result as { csrf_token?: unknown; authenticated?: unknown; expires_at?: unknown };
  if (typeof sessionResult.csrf_token === 'string') csrfToken = sessionResult.csrf_token;
  if (typeof sessionResult.authenticated === 'boolean') browserAuthenticated = sessionResult.authenticated;
  if (typeof sessionResult.expires_at === 'number') sessionExpiresAt = sessionResult.expires_at;
  return result as T;
}

async function jsonResponse<T>(response: Response): Promise<T> {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(detailMessage((payload as { detail?: unknown }).detail, `Request failed (${response.status})`));
  const sessionResult = payload as { csrf_token?: unknown; authenticated?: unknown; expires_at?: unknown };
  if (typeof sessionResult.csrf_token === 'string') csrfToken = sessionResult.csrf_token;
  if (typeof sessionResult.authenticated === 'boolean') browserAuthenticated = sessionResult.authenticated;
  if (typeof sessionResult.expires_at === 'number') sessionExpiresAt = sessionResult.expires_at;
  return payload as T;
}

export async function getJson<T>(path: string): Promise<T> {
  return jsonResponse<T>(await apiFetch(path));
}
export async function postJson<T>(path: string, body: unknown): Promise<T> {
  return jsonResponse<T>(await apiFetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }));
}
export async function postForm<T>(path: string, body: FormData): Promise<T> {
  return jsonResponse<T>(await apiFetch(path, { method: 'POST', body }));
}
export async function putJson<T>(path: string, body: unknown): Promise<T> {
  return jsonResponse<T>(await apiFetch(path, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }));
}
export async function patchJson<T>(path: string, body: unknown): Promise<T> {
  return jsonResponse<T>(await apiFetch(path, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }));
}

export type Weather = {
  location:{latitude:number;longitude:number;timezone:string};
  current:{temperature:number|null;feels_like:number|null;humidity:number|null;precipitation:number|null;wind_speed:number|null;wind_direction:number|null;wind_gusts:number|null;cloud_cover:number|null;uv_index:number|null;weather_code:number|null;condition:string};
  hourly:{time:string;temperature:number|null;feels_like:number|null;precipitation_probability:number|null;precipitation:number|null;wind_speed:number|null;wind_direction:number|null;wind_gusts:number|null;humidity:number|null;cloud_cover:number|null;uv_index:number|null;weather_code:number|null}[];
  daily:{date:string;weather_code:number|null;high:number|null;low:number|null;feels_like_high:number|null;feels_like_low:number|null;precipitation_sum:number|null;precipitation_probability_max:number|null;wind_speed_max:number|null;wind_gusts_max:number|null}[];
  updated_at:string;source:string;data_kind:string;units:Record<string,string>;
};
