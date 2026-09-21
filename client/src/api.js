// Thin fetch wrapper. One place that knows the base URL and the bearer token.

const BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

const TOKEN_KEY = 'watchlist.token';
const REFRESH_KEY = 'watchlist.refresh';
const USER_KEY = 'watchlist.user';

export function storedSession() {
  const token = localStorage.getItem(TOKEN_KEY);
  const refresh = localStorage.getItem(REFRESH_KEY);
  const raw = localStorage.getItem(USER_KEY);
  if (!token || !refresh || !raw) return null;
  try {
    return { token, refresh, user: JSON.parse(raw) };
  } catch {
    return null;
  }
}

export function storeSession(token, refresh, user) {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(REFRESH_KEY, refresh);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(REFRESH_KEY);
  localStorage.removeItem(USER_KEY);
}

// One refresh in flight at a time. Ten polls hitting a 401 together must not
// spend ten refresh tokens - the second would be a reuse and revoke them all.
let refreshing = null;

async function refreshSession() {
  if (!refreshing) {
    refreshing = (async () => {
      const refresh = localStorage.getItem(REFRESH_KEY);
      if (!refresh) throw new ApiError(401, 'no refresh token');
      const response = await fetch(`${BASE}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refresh }),
      });
      if (!response.ok) throw new ApiError(response.status, 'refresh failed');
      const data = await response.json();
      storeSession(data.access_token, data.refresh_token, data.user);
      window.dispatchEvent(new CustomEvent('watchlist:refreshed', { detail: data }));
      return data.access_token;
    })().finally(() => {
      refreshing = null;
    });
  }
  return refreshing;
}

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `request failed (${status})`);
    this.status = status;
  }
}

async function request(path, { method = 'GET', body, token, retried = false } = {}) {
  const response = await fetch(`${BASE}${path}`, {
    method,
    headers: {
      ...(body ? { 'Content-Type': 'application/json' } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });

  if (response.status === 401 && token && !retried) {
    // The access token has expired - by design, every 15 minutes. Refresh
    // once and retry. If the refresh itself fails the session is really over.
    try {
      const fresh = await refreshSession();
      return request(path, { method, body, token: fresh, retried: true });
    } catch {
      clearSession();
      window.dispatchEvent(new Event('watchlist:unauthorized'));
    }
  }

  if (!response.ok) {
    let detail;
    try {
      detail = (await response.json()).detail;
    } catch {
      detail = null;
    }
    throw new ApiError(response.status, detail);
  }
  return response.status === 204 ? null : response.json();
}

export const api = {
  login: (username, password) =>
    request('/auth/login', { method: 'POST', body: { username, password } }),
  register: (username, password) =>
    request('/auth/register', { method: 'POST', body: { username, password } }),
  search: (token, q) => request(`/securities/search?q=${encodeURIComponent(q)}`, { token }),
  watchlist: (token) => request('/watchlist', { token }),
  addItem: (token, securityId) =>
    request('/watchlist/items', { method: 'POST', token, body: { security_id: securityId } }),
  removeItem: (token, securityId) =>
    request(`/watchlist/items/${securityId}`, { method: 'DELETE', token }),
  logout: (refresh) => request('/auth/logout', { method: 'POST', body: { refresh_token: refresh } }),
  realtimeToken: (token) => request('/realtime/token', { method: 'POST', token }),
};
