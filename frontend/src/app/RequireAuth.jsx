import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from '../features/auth/AuthContext';

export function RequireAuth({ children }) {
  const { user, loading } = useAuth();
  const location = useLocation();

  // A stored token is still being checked against the API. Deciding now would
  // bounce a signed-in user to /auth on every page refresh.
  if (loading) return null;

  return user ? children : <Navigate to={`/auth?returnTo=${encodeURIComponent(location.pathname)}`} replace />;
}
