"use client";

import React, { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, type Me } from "./api";

interface UserContextValue {
  me: Me | null;
  setMe: (user: Me | null) => void;
  loading: boolean;
  error: string;
  refresh: () => Promise<void>;
}

const UserContext = createContext<UserContextValue>({
  me: null,
  setMe: () => {},
  loading: true,
  error: "",
  refresh: async () => {},
});

export function UserProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string>("");

  const refresh = useCallback(async () => {
    try {
      const user = await api<Me>("/me");
      setMe(user);
      setError("");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let mounted = true;
    api<Me>("/me")
      .then((user) => {
        if (mounted) {
          setMe(user);
          setError("");
          setLoading(false);
        }
      })
      .catch((err) => {
        if (mounted) {
          setError((err as Error).message);
          setLoading(false);
        }
      });
    return () => {
      mounted = false;
    };
  }, []);

  return (
    <UserContext.Provider value={{ me, setMe, loading, error, refresh }}>
      {children}
    </UserContext.Provider>
  );
}

export function useUser() {
  return useContext(UserContext);
}
