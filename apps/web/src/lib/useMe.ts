"use client";

import { useUser } from "./UserContext";
import type { Me } from "./api";

/** Loads the signed-in user from UserContext; redirects to /login on 401 (see api()). */
export function useMe(): [Me | null, (m: Me) => void, string] {
  const { me, setMe, error } = useUser();
  const updateMe = (newMe: Me) => setMe(newMe);
  return [me, updateMe, error];
}
