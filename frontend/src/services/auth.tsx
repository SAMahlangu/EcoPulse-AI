import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { authGateway, getJson, initializeBrowserSession, updateBrowserSession } from './api';

export type Role = 'resident' | 'municipal' | 'admin';
export type User = { id: string; email: string; full_name: string | null; role: Role; municipality_id: string | null; onboarding_completed_at?: string | null; aal?: string; mfa_required?: boolean; session_expires_at?: number | null };
type AuthValue = { user: User | null; loading: boolean; configured: boolean; hasSession: boolean; error: string | null; login(email: string, password: string): Promise<User>; register(name: string, email: string, password: string): Promise<boolean>; requestPasswordReset(email: string): Promise<void>; updatePassword(password: string): Promise<void>; exchangeLink(accessToken: string, refreshToken: string): Promise<void>; logout(): Promise<void>; refreshUser(): Promise<User>; accessToken(): string | null };
const AuthContext = createContext<AuthValue | null>(null);

class AuthRequestError extends Error { constructor(message: string, readonly status: number) { super(message); } }
function statusOf(reason: unknown) { return reason instanceof AuthRequestError ? reason.status : undefined; }
function messageOf(reason: unknown, fallback: string) { return reason instanceof Error ? reason.message : fallback; }

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [configured, setConfigured] = useState(false);
  const [hasSession, setHasSession] = useState(false);
  const [expiresAt, setExpiresAt] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const accessToken = useCallback(() => null, []);

  const resolveUser = useCallback(async () => {
    try { return await getJson<User>('/auth/me'); }
    catch (reason) { throw new AuthRequestError(messageOf(reason, 'Unable to verify your account.'), reason instanceof Error && /401/.test(reason.message) ? 401 : 0); }
  }, []);

  const acceptSession = useCallback(async (nextExpiry?: number) => {
    const verified = await resolveUser();
    setUser(verified);
    setHasSession(true);
    if (nextExpiry) setExpiresAt(nextExpiry);
    setError(null);
    return verified;
  }, [resolveUser]);

  useEffect(() => {
    let active = true;
    const boot = async () => {
      // Remove the previous browser token store once. It is never read or sent.
      try { localStorage.removeItem('ecopulse.auth.session'); } catch { /* storage may be unavailable */ }
      try {
        const state = await initializeBrowserSession();
        if (!active) return;
        setConfigured(state.configured);
        setHasSession(state.authenticated);
        setExpiresAt(state.expiresAt);

        const params = new URLSearchParams(window.location.search);
        const code = params.get('code');
        const hash = new URLSearchParams(window.location.hash.slice(1));
        const access = hash.get('access_token');
        const refresh = hash.get('refresh_token');
        if (code) {
          await authGateway('exchange', { code });
          params.delete('code');
          params.delete('type');
          const clean = `${window.location.pathname}${params.size ? `?${params}` : ''}`;
          window.history.replaceState({}, document.title, clean);
          setHasSession(true);
        } else if (access && refresh) {
          await authGateway('exchange-link', { access_token: access, refresh_token: refresh });
          window.history.replaceState({}, document.title, window.location.pathname + window.location.search);
          setHasSession(true);
        }
        const sessionState = await initializeBrowserSession();
        setHasSession(sessionState.authenticated);
        setExpiresAt(sessionState.expiresAt);
        if (sessionState.authenticated) await acceptSession(sessionState.expiresAt);
        else setUser(null);
      } catch (reason) {
        if (active) {
          setUser(null);
          setError(messageOf(reason, 'Unable to restore the EcoPulse browser session.'));
        }
      } finally { if (active) setLoading(false); }
    };
    void boot();
    return () => { active = false; };
  }, [acceptSession]);

  useEffect(() => {
    if (!hasSession || !expiresAt || !configured) return;
    const delay = Math.max(1000, expiresAt * 1000 - Date.now() - 60_000);
    const timer = window.setTimeout(async () => {
      try {
        const result = await authGateway<{ authenticated: boolean; expires_at: number }>('token?grant_type=refresh_token', {});
        if (result.authenticated) await acceptSession(result.expires_at);
      } catch (reason) {
        const status = statusOf(reason);
        if (status === 401) { setHasSession(false); setUser(null); updateBrowserSession(false); }
        setError(messageOf(reason, 'Unable to refresh your session. Please retry.'));
      }
    }, delay);
    return () => window.clearTimeout(timer);
  }, [hasSession, expiresAt, configured, acceptSession]);

  const login = useCallback(async (email: string, password: string) => {
    setLoading(true); setError(null); setUser(null);
    try {
      const result = await authGateway<{ authenticated: boolean; expires_at: number }>('token?grant_type=password', { email, password });
      setHasSession(result.authenticated);
      setExpiresAt(result.expires_at);
      return await acceptSession(result.expires_at);
    } catch (reason) {
      setError(messageOf(reason, 'Sign in could not be completed.'));
      throw reason;
    } finally { setLoading(false); }
  }, [acceptSession]);

  const register = useCallback(async (name: string, email: string, password: string) => {
    if (import.meta.env.DEV && ['localhost', '127.0.0.1', '[::1]'].includes(window.location.hostname)) {
      const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8000/api/v1';
      const { apiFetch } = await import('./api');
      const response = await apiFetch('/auth/register-local', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ full_name: name, email, password }) });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new AuthRequestError((result as { detail?: string }).detail ?? 'Registration failed.', response.status);
      return true;
    }
    await authGateway('signup', { email, password, data: { full_name: name } });
    return false;
  }, []);

  const requestPasswordReset = useCallback(async (email: string) => {
    const redirectTo = `${window.location.origin}/reset-password`;
    await authGateway('recover', { email, redirectTo });
  }, []);

  const updatePassword = useCallback(async (password: string) => {
    const { authGateway: request } = await import('./api');
    await request('user', { password }, 'PUT');
  }, []);

  const exchangeLink = useCallback(async (accessTokenValue: string, refreshTokenValue: string) => {
    const result = await authGateway<{ authenticated: boolean; expires_at: number }>('exchange-link', { access_token: accessTokenValue, refresh_token: refreshTokenValue });
    setHasSession(result.authenticated);
    setExpiresAt(result.expires_at);
  }, []);

  const logout = useCallback(async () => {
    // Clear the UI and its query cache immediately, even when the API is offline.
    setUser(null); setHasSession(false); setExpiresAt(0); setError(null); updateBrowserSession(false);
    try { await authGateway('logout', {}); } catch { /* local UI is signed out; cookie expires if API is unreachable */ }
  }, []);

  const refreshUser = useCallback(async () => {
    try { return await acceptSession(); }
    catch (reason) {
      if (statusOf(reason) === 401) { setUser(null); setHasSession(false); updateBrowserSession(false); }
      setError(messageOf(reason, 'Unable to verify your account right now.'));
      throw reason;
    }
  }, [acceptSession]);

  const value = useMemo(() => ({ user, loading, configured, hasSession, error, login, register,
    requestPasswordReset, updatePassword, exchangeLink, logout, refreshUser, accessToken }),
    [user, loading, configured, hasSession, error, login, register, requestPasswordReset, updatePassword, exchangeLink, logout, refreshUser, accessToken]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() { const value = useContext(AuthContext); if (!value) throw new Error('useAuth must be inside AuthProvider'); return value; }
