"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";

const devLogin = process.env.NEXT_PUBLIC_DEV_LOGIN === "1";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [error, setError] = useState("");

  async function dev(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api("/auth/dev-login", { method: "POST", body: { email } });
      router.push("/");
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <div className="panel" style={{ maxWidth: 420, margin: "40px auto" }}>
      <h1>Sign in</h1>
      <p className="muted">HackTrack keeps one clear next action for every hackathon you join.</p>
      {/* A full navigation, not fetch: the API redirects to Google. */}
      <a className="button primary" href="/api/v1/auth/google/start">
        Sign in with Google
      </a>
      {devLogin && (
        <form className="stack" onSubmit={dev} style={{ marginTop: 24 }}>
          <label>
            Dev login (local only)
            <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" />
          </label>
          <button type="submit">Continue</button>
          {error && <p className="error">{error}</p>}
        </form>
      )}
    </div>
  );
}
