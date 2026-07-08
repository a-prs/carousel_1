import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import * as api from "../lib/api";

type User = { slug: string; display_name: string | null; ig_handle: string | null };

type AuthState = {
  user: User | null;
  loading: boolean;
  login: (u: string, p: string) => Promise<void>;
  logout: () => void;
};

const Ctx = createContext<AuthState>(null as any);
export const useAuth = () => useContext(Ctx);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  // Session restore on mount: if a token exists, verify it via /me.
  useEffect(() => {
    (async () => {
      if (api.getToken()) {
        try {
          setUser(await api.me());
        } catch {
          api.clearToken();
        }
      }
      setLoading(false);
    })();
  }, []);

  const login = async (u: string, p: string) => {
    await api.login(u, p);
    setUser(await api.me());
  };
  const logout = () => {
    api.clearToken();
    setUser(null);
  };

  return <Ctx.Provider value={{ user, loading, login, logout }}>{children}</Ctx.Provider>;
}
