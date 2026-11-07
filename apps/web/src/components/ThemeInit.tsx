"use client";

import { useEffect } from "react";

export default function ThemeInit() {
  useEffect(() => {
    try {
      const stored = window.localStorage.getItem("ht-theme");
      if (stored === "light" || stored === "dark") {
        document.documentElement.dataset.theme = stored;
      } else {
        delete document.documentElement.dataset.theme;
      }
    } catch {
      // storage unavailable; fall back to system preference via CSS media query
    }
  }, []);
  return null;
}
