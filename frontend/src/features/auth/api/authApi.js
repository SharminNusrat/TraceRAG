import { httpClient } from '../../../services/httpClient';

/** POST /auth/register - returns { access_token, token_type, user }. */
export const registerAccount = ({ fullName, email, password }) => httpClient('/auth/register', {
  method: 'POST',
  body: JSON.stringify({ full_name: fullName, email, password }),
});

/** POST /auth/login - same response shape as register. */
export const loginAccount = ({ email, password }) => httpClient('/auth/login', {
  method: 'POST',
  body: JSON.stringify({ email, password }),
});

/** GET /auth/me - used to validate a stored token on startup. */
export const fetchCurrentUser = () => httpClient('/auth/me');
