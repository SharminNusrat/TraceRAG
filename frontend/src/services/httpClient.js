import { getToken } from './authToken';

export const API_BASE_URL = import.meta.env?.VITE_API_URL ?? 'http://localhost:8000';

/** FastAPI returns `detail` as a string, or an array of issues for 422s. */
function formatDetail(detail) {
  if (!detail) return null;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail.map((issue) => issue.msg ?? JSON.stringify(issue)).join('; ');
  }
  return JSON.stringify(detail);
}

export async function httpClient(path, options = {}) {
  // The browser must set its own multipart boundary, so only JSON gets a
  // Content-Type from us.
  const isFormData = options.body instanceof FormData;
  // Attached to every request when signed in. Endpoints that allow anonymous
  // callers simply ignore it, so there is nothing to opt out of here.
  const token = getToken();

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      ...(isFormData ? {} : { 'Content-Type': 'application/json' }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  });

  if (!response.ok) {
    const errorBody = await response.json().catch(() => ({}));
    throw new Error(formatDetail(errorBody.detail) ?? `Request failed (${response.status}).`);
  }

  return response.status === 204 ? null : response.json();
}
