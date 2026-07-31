const API_BASE_URL = import.meta.env?.VITE_API_URL ?? 'http://localhost:8000';

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

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      ...(isFormData ? {} : { 'Content-Type': 'application/json' }),
      ...options.headers,
    },
  });

  if (!response.ok) {
    const errorBody = await response.json().catch(() => ({}));
    throw new Error(formatDetail(errorBody.detail) ?? `Request failed (${response.status}).`);
  }

  return response.status === 204 ? null : response.json();
}
