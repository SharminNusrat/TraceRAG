import { createContext, useContext, useEffect, useMemo, useState } from 'react';
import { fetchCurrentUser, loginAccount, registerAccount } from './api/authApi';
import { clearToken, getToken, setToken } from '../../services/authToken';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  // Only a stored token makes the startup check necessary; without one there
  // is nothing to wait for and guarded routes can decide immediately.
  const [loading, setLoading] = useState(() => Boolean(getToken()));

  useEffect(() => {
    if (!getToken()) return undefined;

    let active = true;
    fetchCurrentUser()
      .then((account) => {
        if (active) setUser(account);
      })
      .catch(() => {
        // Expired, revoked, or signed with a key the server no longer has.
        clearToken();
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, []);

  const value = useMemo(() => {
    // Register and login return the same payload, so both land here.
    const accept = (response) => {
      setToken(response.access_token);
      setUser(response.user);
      return response.user;
    };

    return {
      user,
      loading,
      signIn: async (credentials) => accept(await loginAccount(credentials)),
      signUp: async (details) => accept(await registerAccount(details)),
      signOut: () => {
        clearToken();
        setUser(null);
      },
    };
  }, [user, loading]);

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
