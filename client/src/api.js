// Thin fetch wrapper. One place that knows the base URL and the bearer token.

const BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

const TOKEN_KEY = 'watchlist.token';
const USER_KEY = 'watchlist.user';

export function storedSession() {
  const token = localStorage.getItem(TOKEN_KEY);
  const raw = localStorage.getItem(USER_KEY);
  if (!token || !raw) return null;
  try {
    return { token, user: JSON.parse(raw) };
  } catch {
    return null;
  }
}

export function storeSession(token, user) {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `request failed (${status})`);
    this.status = status;
  }
}

async function request(path, { method = 'GET', body, token } = {}) {
  const response = await fetch(`${BASE}${path}`, {
    method,
    headers: {
      ...(body ? { 'Content-Type': 'application/json' } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });

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
};
