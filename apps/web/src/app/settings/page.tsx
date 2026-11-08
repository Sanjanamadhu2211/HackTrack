"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api, type Me } from "@/lib/api";
import { useUser } from "@/lib/UserContext";

export default function SettingsPage() {
  const { me, setMe, loading, error } = useUser();
  if (error && !me) return <p className="error" role="alert">{error}</p>;
  if (loading || !me) {
    return (
      <div className="stack" style={{ maxWidth: 760, margin: "0 auto" }}>
        <div className="skeleton skeleton-title" style={{ width: 180 }} />
        <div className="skeleton skeleton-card" />
        <div className="skeleton skeleton-card" />
      </div>
    );
  }
  return <SettingsForm me={me} setMe={setMe} />;
}

function SettingsForm({ me, setMe }: { me: Me; setMe: (m: Me) => void }) {
  const [f, setF] = useState({
    timezone: me.timezone,
    quiet_hours_start: me.quiet_hours_start,
    quiet_hours_end: me.quiet_hours_end,
    reminder_cap_per_day: me.reminder_cap_per_day,
  });
  const router = useRouter();
  const [msg, setMsg] = useState("");
  const [saving, setSaving] = useState(false);
  const [pairURL, setPairURL] = useState("");
  const [calendarConnected, setCalendarConnected] = useState<boolean | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [syncError, setSyncError] = useState("");
  const [syncOk, setSyncOk] = useState(false);

  useEffect(() => {
    api<{ connected: boolean; last_error: string }>("/integrations/google/calendar").then(
      (result) => {
        setCalendarConnected(result.connected);
        if (result.last_error) setMsg(result.last_error);
      },
      (err) => setMsg(err.message),
    );
  }, []);

  async function disconnectCalendar() {
    try {
      await api("/integrations/google/calendar", { method: "DELETE" });
      setCalendarConnected(false);
    } catch (err) {
      setMsg((err as Error).message);
    }
  }

  async function syncCalendar() {
    setSyncing(true);
    setSyncError("");
    setSyncOk(false);
    try {
      await api<{ status: string }>("/integrations/google/calendar/sync", { method: "POST" });
      setSyncOk(true);
    } catch (err) {
      setSyncError((err as Error).message);
    } finally {
      setSyncing(false);
    }
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      const updated = await api<Me>("/me", { method: "PATCH", body: f });
      setMe(updated);
      setMsg("Preferences saved successfully. Pending reminders were re-planned.");
      setTimeout(() => setMsg(""), 4000);
    } catch (err) {
      setMsg((err as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function pair() {
    try {
      const { url } = await api<{ url: string }>("/integrations/telegram/pair", { method: "POST" });
      setPairURL(url);
    } catch (err) {
      setMsg((err as Error).message);
    }
  }

  async function unlink() {
    await api("/integrations/telegram", { method: "DELETE" });
    setMe({ ...me, telegram_linked: false });
  }

  async function logout() {
    await api("/auth/logout", { method: "POST" });
    router.push("/login");
  }

  async function deleteAccount() {
    if (!window.confirm("Delete your account and all associated hackathons, reminders, and data? This cannot be undone.")) return;
    try {
      await api("/me", { method: "DELETE" });
      router.push("/login");
    } catch (err) {
      setMsg((err as Error).message);
    }
  }

  return (
    <div className="stack" style={{ maxWidth: 760, margin: "0 auto", gap: 24 }}>
      <div>
        <Link href="/" className="muted" style={{ display: "inline-flex", alignItems: "center", gap: 4, marginBottom: 12 }}>
          ← Back to Dashboard
        </Link>
        <h1>Account & Settings</h1>
        <p className="muted">
          Signed in as <strong>{me.email}</strong>
        </p>
      </div>

      {msg && <p className="badge ok" role="status" style={{ padding: 10 }}>{msg}</p>}

      <section className="panel stack">
        <h2>Reminder Schedule & Quiet Hours</h2>
        <form className="stack" onSubmit={save}>
          <label>
            Your Timezone
            <div className="row">
              <input
                style={{ flex: 1 }}
                value={f.timezone}
                onChange={(e) => setF({ ...f, timezone: e.target.value })}
              />
              <button
                type="button"
                onClick={() => setF({ ...f, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone })}
              >
                Use Device Timezone
              </button>
            </div>
          </label>

          <div className="row">
            <label style={{ flex: 1 }}>
              Quiet Hours Start
              <input
                type="time"
                value={f.quiet_hours_start}
                onChange={(e) => setF({ ...f, quiet_hours_start: e.target.value })}
              />
            </label>
            <label style={{ flex: 1 }}>
              Quiet Hours End
              <input
                type="time"
                value={f.quiet_hours_end}
                onChange={(e) => setF({ ...f, quiet_hours_end: e.target.value })}
              />
            </label>
            <label style={{ flex: 1 }}>
              Max Reminders / Day
              <input
                type="number"
                min={1}
                max={50}
                value={f.reminder_cap_per_day}
                onChange={(e) => setF({ ...f, reminder_cap_per_day: Number(e.target.value) })}
              />
            </label>
          </div>

          <p className="muted faint" style={{ margin: 0 }}>
            Reminders scheduled during quiet hours are pushed to when quiet hours end. Final reminders for required steps are never dropped.
          </p>

          <div className="row" style={{ justifyContent: "flex-end" }}>
            <button type="submit" className="primary" disabled={saving}>
              {saving ? "Saving…" : "Save Preferences"}
            </button>
          </div>
        </form>
      </section>

      <section className="panel stack">
        <h2>Telegram Notifications</h2>
        <p className="muted" style={{ margin: 0 }}>
          Receive instant reminders and confirm milestones directly in Telegram.
        </p>

        {me.telegram_linked ? (
          <div className="row" style={{ justifyContent: "space-between" }}>
            <div className="row">
              <span className="badge ok">✓ Connected</span>
              <span className="muted">Ready to receive alerts</span>
            </div>
            <button onClick={unlink} className="danger">
              Disconnect Telegram
            </button>
          </div>
        ) : pairURL ? (
          <div className="panel stack" style={{ background: "var(--panel)" }}>
            <p className="muted">Your one-time pairing link is ready (expires in 15 minutes):</p>
            <div className="row">
              <a href={pairURL} target="_blank" rel="noreferrer" className="button primary">
                Open Telegram & Press Start ↗
              </a>
              <button type="button" onClick={() => window.location.reload()}>
                Check Status
              </button>
            </div>
          </div>
        ) : (
          <div>
            <button className="primary" onClick={pair}>
              Connect Telegram Bot
            </button>
          </div>
        )}
      </section>

      <section className="panel stack">
        <h2>Google Calendar Sync</h2>
        <p className="muted" style={{ margin: 0 }}>
          Automatically sync confirmed hackathon deadlines and schedule milestones to your Google Calendar.
        </p>

        {calendarConnected === null ? (
          <p className="muted">Checking Calendar connection…</p>
        ) : calendarConnected ? (
          <div className="row" style={{ justifyContent: "space-between" }}>
            <div className="row">
              <span className="badge ok">✓ Connected</span>
              <button
                disabled={syncing}
                onClick={syncCalendar}
                className="button primary"
                aria-busy={syncing}
              >
                {syncing ? "Syncing…" : "Sync Now"}
              </button>
            </div>
            <button onClick={disconnectCalendar} className="danger">
              Disconnect
            </button>
          </div>
        ) : (
          <div>
            <a className="button primary" href="/api/integrations/google/calendar/start">
              Connect Google Calendar
            </a>
          </div>
        )}

        {syncOk && <p className="badge ok" role="status">Calendar synced successfully.</p>}
        {syncError && <p className="error" role="alert">{syncError}</p>}
      </section>

      <section className="panel stack">
        <h2>Account & Privacy</h2>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <div className="row">
            <a className="button" href="/api/me/export">
              Download My Data (JSON)
            </a>
            <button onClick={logout}>Sign Out</button>
          </div>
          <button onClick={deleteAccount} className="danger">
            Delete Account
          </button>
        </div>
      </section>
    </div>
  );
}
