const API_BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

export async function httpClient(path, options = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
    ...options,
  });

  if (!response.ok) {
    const errorBody = await response.json().catch(() => ({}));
    throw new Error(errorBody.detail ?? 'Request failed.');
  }

  return response.status === 204 ? null : response.json();
}
