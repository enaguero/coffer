import { createContext, useEffect, useState, type ReactNode } from "react";

import { api } from "../api/client";
import type { User } from "../api/types";

export interface AuthContextValue {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  signup: (email: string, password: string, fullName?: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

// eslint-disable-next-line react-refresh/only-export-components
export const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get<User>("/api/v1/auth/me")
      .then(({ data }) => setUser(data))
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  async function login(email: string, password: string) {
    const form = new URLSearchParams();
    form.append("username", email);
    form.append("password", password);
    await api.post("/api/v1/auth/login", form, {
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
    });
    const me = await api.get<User>("/api/v1/auth/me");
    setUser(me.data);
  }

  async function signup(email: string, password: string, fullName?: string) {
    await api.post("/api/v1/auth/signup", {
      email,
      password,
      full_name: fullName,
    });
    const me = await api.get<User>("/api/v1/auth/me");
    setUser(me.data);
  }

  async function logout() {
    try {
      await api.post("/api/v1/auth/logout");
    } finally {
      setUser(null);
    }
  }

  async function refresh() {
    const me = await api.get<User>("/api/v1/auth/me");
    setUser(me.data);
  }

  return (
    <AuthContext.Provider value={{ user, loading, login, signup, logout, refresh }}>
      {children}
    </AuthContext.Provider>
  );
}
