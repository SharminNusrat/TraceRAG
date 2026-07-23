import { createContext, useContext, useMemo, useState } from 'react';

const AuthContext = createContext(null);
const STORAGE_KEY = 'tracerag-user';

export function AuthProvider({ children }) {
  const [user, setUser] = useState(() => (
    JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null')
  ));

  const value = useMemo(() => ({
    user,
    signIn: (details) => {
      const nextUser = {
        name: details.name || 'Alex Morgan',
        email: details.email,
      };

      localStorage.setItem(STORAGE_KEY, JSON.stringify(nextUser));
      setUser(nextUser);
    },
    signOut: () => {
      localStorage.removeItem(STORAGE_KEY);
      setUser(null);
    },
  }), [user]);

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
