"use client";

import { useEffect, useState } from "react";

type Theme = "light" | "dark" | "system";

const order: Theme[] = ["light", "dark", "system"];

function apply(t: Theme) {
  if (t === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = t;
}

export default function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("system");

  useEffect(() => {
    try {
      const stored = window.localStorage.getItem("ht-theme");
      setTheme(stored === "light" || stored === "dark" ? stored : "system");
    } catch {
      setTheme("system");
    }
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => {
      try {
        const s = window.localStorage.getItem("ht-theme");
        if (!s || s === "system") delete document.documentElement.dataset.theme;
      } catch {
        /* ignore */
      }
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  function cycle() {
    const next = order[(order.indexOf(theme) + 1) % order.length];
    setTheme(next);
    try {
      window.localStorage.setItem("ht-theme", next);
    } catch {
      /* ignore */
    }
    apply(next);
  }

  const label = `Theme: ${theme}. Activate to change theme.`;
  return (
    <button
      type="button"
      className="theme-toggle"
      onClick={cycle}
      aria-label={label}
      title={label}
    >
      <span aria-hidden="true">
        {theme === "light" ? (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <circle cx="12" cy="12" r="4" />
            <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41" />
          </svg>
        ) : theme === "dark" ? (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
          </svg>
        ) : (
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <circle cx="12" cy="12" r="9" />
            <path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor" stroke="none" />
          </svg>
        )}
      </span>
    </button>
  );
}
