import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';

const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL?.replace(/\/$/, '');
const SUPABASE_KEY = import.meta.env.VITE_SUPABASE_ANON_KEY;
const SESSION_KEY = 'ecopulse.auth.session';

export type Role = 'resident' | 'municipal' | 'admin';
export type User = { id: string; email: string; full_name: string | null; role: Role; municipality_id: string | null; onboarding_completed_at?: string | null };
type Session = { access_token: string; refresh_token: string; expires_at: number; user: { id: string; email: string } };
type AuthValue = { user: User | null; loading: boolean; configured: boolean; hasSession: boolean; error: string | null; login(email: string, password: string): Promise<User>; register(name: string, email: string, password: string): Promise<boolean>; requestPasswordReset(email: string): Promise<void>; updatePassword(password: string): Promise<void>; logout(): Promise<void>; refreshUser(): Promise<User>; accessToken(): string | null };
const AuthContext = createContext<AuthValue | null>(null);
const apiBase = import.meta.env.VITE_API_URL ?? 'http://localhost:8000/api/v1';

function session(): Session | null {
  try {
    const value = localStorage.getItem(SESSION_KEY);
    if (!value) return null;
    const parsed = JSON.parse(value) as Partial<Session>;
    if (typeof parsed.access_token !== 'string' || typeof parsed.refresh_token !== 'string' || typeof parsed.expires_at !== 'number') {
      localStorage.removeItem(SESSION_KEY);
      return null;
    }
    return parsed as Session;
  } catch { localStorage.removeItem(SESSION_KEY); return null; }
}
export function getAccessToken() { return session()?.access_token ?? null; }
function saveSession(value: Session | null) { if (value) localStorage.setItem(SESSION_KEY, JSON.stringify(value)); else localStorage.removeItem(SESSION_KEY); }
class AuthRequestError extends Error { constructor(message: string, readonly status: number) { super(message); } }
function errorStatus(reason: unknown) { return reason instanceof AuthRequestError ? reason.status : undefined; }
function errorMessage(reason: unknown, fallback: string) { return reason instanceof Error ? reason.message : fallback; }
async function authRequest(path: string, body: unknown) {
  if (!SUPABASE_URL || !SUPABASE_KEY) throw new Error('Authentication is not configured. Set the Supabase URL and public anon key in frontend/.env.local.');
  const response = await fetch(`${SUPABASE_URL}/auth/v1/${path}`, { method: 'POST', headers: { apikey: SUPABASE_KEY, 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new AuthRequestError(result.msg ?? result.message ?? result.error_description ?? 'Authentication request failed.', response.status);
  return result;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [currentSession, setCurrentSession] = useState<Session | null>(() => session());
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const configured = Boolean(SUPABASE_URL && SUPABASE_KEY);
  const accessToken = useCallback(() => currentSession?.access_token ?? null, [currentSession]);

  const resolveUser = useCallback(async (token: string) => {
    const response = await fetch(`${apiBase}/auth/me`, { headers: { Authorization: `Bearer ${token}` } });
    if (!response.ok) {
      const result = await response.json().catch(() => ({}));
      throw new AuthRequestError(result.detail ?? (response.status === 401 ? 'Your session expired. Please sign in again.' : `Unable to verify your account (HTTP ${response.status}).`), response.status);
    }
    return await response.json() as User;
  }, []);

  useEffect(() => {
    let active = true;
    (async () => {
      if (!currentSession) { if (active) { setUser(null); setLoading(false); } return; }
      try {
        let next = currentSession;
        if (next.expires_at <= Math.floor(Date.now() / 1000) + 30) {
          const refreshed = await authRequest('token?grant_type=refresh_token', { refresh_token: next.refresh_token });
          next = { access_token: refreshed.access_token, refresh_token: refreshed.refresh_token, expires_at: Math.floor(Date.now() / 1000) + refreshed.expires_in, user: refreshed.user };
          saveSession(next); if (active) setCurrentSession(next);
        }
        const verified = await resolveUser(next.access_token);
        if (active) { setUser(verified); setError(null); }
      } catch (reason) {
        const status = errorStatus(reason);
        if (status === 400 || status === 401) { saveSession(null); if (active) setCurrentSession(null); }
        if (active) { setUser(null); setError(errorMessage(reason, 'Unable to restore session.')); }
      } finally { if (active) setLoading(false); }
    })();
    return () => { active = false; };
  }, [currentSession, resolveUser]);

  useEffect(() => {
    if (!currentSession || !configured) return;
    const delay = Math.max(1000, currentSession.expires_at * 1000 - Date.now() - 60_000);
    const timer = window.setTimeout(async () => {
      try {
        const refreshed = await authRequest('token?grant_type=refresh_token', { refresh_token: currentSession.refresh_token });
        const next: Session = { access_token: refreshed.access_token, refresh_token: refreshed.refresh_token, expires_at: Math.floor(Date.now() / 1000) + refreshed.expires_in, user: refreshed.user };
        saveSession(next); setCurrentSession(next); setError(null);
      } catch (reason) {
        const status = errorStatus(reason);
        if (status === 400 || status === 401) { saveSession(null); setCurrentSession(null); setUser(null); }
        setError(errorMessage(reason, 'Unable to refresh your session. Please retry.'));
      }
    }, delay);
    return () => window.clearTimeout(timer);
  }, [currentSession, configured]);

  const login = useCallback(async (email: string, password: string) => {
    setError(null); setUser(null);
    const result = await authRequest('token?grant_type=password', { email, password });
    const next: Session = { access_token: result.access_token, refresh_token: result.refresh_token, expires_at: Math.floor(Date.now() / 1000) + result.expires_in, user: result.user };
    saveSession(next); setCurrentSession(next);
    try { const verified = await resolveUser(next.access_token); setUser(verified); setError(null); return verified; }
    catch (reason) { setError(errorMessage(reason, 'Unable to verify your account right now.')); throw reason; }
  }, [resolveUser]);
  const register = useCallback(async (name: string, email: string, password: string) => {
    if (import.meta.env.DEV && ['localhost', '127.0.0.1', '[::1]'].includes(window.location.hostname)) {
      const response = await fetch(`${apiBase}/auth/register-local`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ full_name: name, email, password }),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new AuthRequestError(result.detail ?? 'Registration failed.', response.status);
      return true;
    }
    const result = await authRequest('signup', { email, password, data: { full_name: name } });
    return Boolean(result.access_token);
  }, []);
  const requestPasswordReset = useCallback(async (email: string) => { await authRequest('recover', { email, redirectTo: `${window.location.origin}/reset-password` }); }, []);
  const updatePassword = useCallback(async (password: string) => {
    const hashToken = new URLSearchParams(window.location.hash.slice(1)).get('access_token');
    const token = currentSession?.access_token ?? hashToken;
    if (!token) throw new Error('Use the password reset link sent to your email first.');
    const response = await fetch(`${SUPABASE_URL}/auth/v1/user`, { method: 'PUT', headers: { apikey: SUPABASE_KEY!, Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ password }) });
    if (!response.ok) throw new Error('Could not update your password. Request a fresh reset link and try again.');
  }, [currentSession]);
  const logout = useCallback(async () => {
    const token = currentSession?.access_token;
    if (token && SUPABASE_URL && SUPABASE_KEY) await fetch(`${SUPABASE_URL}/auth/v1/logout`, { method: 'POST', headers: { apikey: SUPABASE_KEY, Authorization: `Bearer ${token}` } }).catch(() => undefined);
    saveSession(null); setCurrentSession(null); setUser(null);
  }, [currentSession]);

  const refreshUser = useCallback(async () => {
    if (!currentSession?.access_token) throw new Error('Sign in to continue.');
    try {
      let activeSession = currentSession;
      if (activeSession.expires_at <= Math.floor(Date.now() / 1000) + 30) {
        const refreshed = await authRequest('token?grant_type=refresh_token', { refresh_token: activeSession.refresh_token });
        activeSession = { access_token: refreshed.access_token, refresh_token: refreshed.refresh_token, expires_at: Math.floor(Date.now() / 1000) + refreshed.expires_in, user: refreshed.user };
        saveSession(activeSession); setCurrentSession(activeSession);
      }
      const verified = await resolveUser(activeSession.access_token); setUser(verified); setError(null); return verified;
    } catch (reason) {
      const status = errorStatus(reason);
      if (status === 400 || status === 401) { saveSession(null); setCurrentSession(null); }
      setUser(null); setError(errorMessage(reason, 'Unable to verify your account right now.')); throw reason;
    }
  }, [currentSession, resolveUser]);

  const value = useMemo(() => ({ user, loading, configured, hasSession: Boolean(currentSession), error, login, register, requestPasswordReset, updatePassword, logout, refreshUser, accessToken }), [user, loading, configured, currentSession, error, login, register, requestPasswordReset, updatePassword, logout, refreshUser, accessToken]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
export function useAuth() { const value = useContext(AuthContext); if (!value) throw new Error('useAuth must be inside AuthProvider'); return value; }
