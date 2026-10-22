import { getAccessToken } from './auth';
const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8000/api/v1';
export async function getJson<T>(path: string): Promise<T> {
  const token = getAccessToken();
  const response = await fetch(`${API}${path}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!response.ok) throw new Error(`Request failed (${response.status})`);
  return response.json() as Promise<T>;
}
export async function postJson<T>(path: string, body: unknown): Promise<T> {
  const token = getAccessToken();
  const response = await fetch(`${API}${path}`, { method: 'POST', headers: {'Content-Type':'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {})}, body: JSON.stringify(body) });
  if (!response.ok) { const detail = await response.json().catch(() => ({})); throw new Error(detail.detail ?? `Request failed (${response.status})`); }
  return response.json() as Promise<T>;
}
export async function postForm<T>(path: string, body: FormData): Promise<T> {
  const token = getAccessToken();
  const response = await fetch(`${API}${path}`, { method: 'POST', headers: token ? { Authorization: `Bearer ${token}` } : {}, body });
  if (!response.ok) { const detail = await response.json().catch(() => ({})); throw new Error(detail.detail ?? `Request failed (${response.status})`); }
  return response.json() as Promise<T>;
}
export async function putJson<T>(path: string, body: unknown): Promise<T> {
  const token = getAccessToken();
  const response = await fetch(`${API}${path}`, { method: 'PUT', headers: {'Content-Type':'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {})}, body: JSON.stringify(body) });
  if (!response.ok) { const detail = await response.json().catch(() => ({})); throw new Error(detail.detail ?? `Request failed (${response.status})`); }
  return response.json() as Promise<T>;
}
export async function patchJson<T>(path: string, body: unknown): Promise<T> {
  const token = getAccessToken();
  const response = await fetch(`${API}${path}`, { method: 'PATCH', headers: {'Content-Type':'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {})}, body: JSON.stringify(body) });
  if (!response.ok) { const detail = await response.json().catch(() => ({})); throw new Error(detail.detail ?? `Request failed (${response.status})`); }
  return response.json() as Promise<T>;
}
export type Weather = {
  location:{latitude:number;longitude:number;timezone:string};
  current:{temperature:number|null;feels_like:number|null;humidity:number|null;precipitation:number|null;wind_speed:number|null;wind_direction:number|null;wind_gusts:number|null;cloud_cover:number|null;uv_index:number|null;weather_code:number|null;condition:string};
  hourly:{time:string;temperature:number|null;feels_like:number|null;precipitation_probability:number|null;precipitation:number|null;wind_speed:number|null;wind_direction:number|null;wind_gusts:number|null;humidity:number|null;cloud_cover:number|null;uv_index:number|null;weather_code:number|null}[];
  daily:{date:string;weather_code:number|null;high:number|null;low:number|null;feels_like_high:number|null;feels_like_low:number|null;precipitation_sum:number|null;precipitation_probability_max:number|null;wind_speed_max:number|null;wind_gusts_max:number|null}[];
  updated_at:string;source:string;data_kind:string;units:Record<string,string>;
};
