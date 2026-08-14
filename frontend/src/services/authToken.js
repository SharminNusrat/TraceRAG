/**
 * The access token lives in localStorage so a refresh does not sign the user
 * out. Kept in its own module because both the auth context and the HTTP
 * client need it, and neither should own the storage key.
 */

const STORAGE_KEY = 'tracerag-token';

export const getToken = () => localStorage.getItem(STORAGE_KEY);

export const setToken = (token) => localStorage.setItem(STORAGE_KEY, token);

export const clearToken = () => localStorage.removeItem(STORAGE_KEY);
