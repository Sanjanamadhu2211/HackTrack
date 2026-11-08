"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";

const devLogin = process.env.NEXT_PUBLIC_DEV_LOGIN === "1";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function dev(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/auth/dev-login", { method: "POST", body: { email } });
      router.push("/");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ minHeight: "75vh", display: "flex", alignItems: "center", justifyContent: "center", padding: "20px 0" }}>
      <div className="panel stack" style={{ maxWidth: 440, width: "100%", padding: 32, textAlign: "center" }}>
        <div style={{ fontSize: "2.5rem", marginBottom: 4 }}>⚡</div>
        <h1 style={{ marginBottom: 6 }}>Welcome to HackTrack</h1>
        <p className="muted" style={{ margin: "0 0 24px" }}>
          Never miss an RSVP, project submission, or demo deadline. Every hackathon has one clear next action.
        </p>

        <a
          className="button primary"
          href="/api/auth/google/start"
          style={{
            padding: "12px 20px",
            fontSize: "1rem",
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 12,
            boxShadow: "var(--shadow)",
          }}
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
            <path
              d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
              fill="#4285F4"
            />
            <path
              d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
              fill="#34A853"
            />
            <path
              d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z"
              fill="#FBBC05"
            />
            <path
              d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z"
              fill="#EA4335"
            />
          </svg>
          Sign in with Google
        </a>

        {devLogin && (
          <div style={{ marginTop: 28, paddingTop: 24, borderTop: "1px dashed var(--line)" }}>
            <span className="badge" style={{ marginBottom: 12 }}>Developer Testing</span>
            <form className="stack" onSubmit={dev}>
              <label style={{ textAlign: "left" }}>
                Dev login email
                <input
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="test@example.com"
                />
              </label>
              <button type="submit" disabled={busy}>
                {busy ? "Signing in…" : "Continue with Dev Login"}
              </button>
            </form>
          </div>
        )}

        {error && <p className="error" role="alert" style={{ marginTop: 16 }}>{error}</p>}
      </div>
    </div>
  );
}
