"use client";

import { useEffect, useState } from "react";
import { api, type Me } from "./api";

/** Loads the signed-in user; redirects to /login on 401 (see api()). */
export function useMe(): [Me | null, (m: Me) => void, string] {
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api<Me>("/me").then(setMe, (e) => setError((e as Error).message));
  }, []);
  return [me, setMe, error];
}
