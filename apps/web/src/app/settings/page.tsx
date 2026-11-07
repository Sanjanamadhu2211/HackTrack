"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, type Me } from "@/lib/api";
import { useMe } from "@/lib/useMe";

export default function SettingsPage() {
  const [me, setMe, meError] = useMe();
  if (meError && !me) return <p className="error">{meError}</p>;
  if (!me) return <p className="muted">Loading…</p>;
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
  const [pairURL, setPairURL] = useState("");
  const [calendarConnected, setCalendarConnected] = useState<boolean | null>(null);

  useEffect(() => {
    api<{ connected: boolean; last_error: string }>("/integrations/google/calendar").then(
      (result) => { setCalendarConnected(result.connected); if (result.last_error) setMsg(result.last_error); },
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

  async function save(e: React.FormEvent) {
    e.preventDefault();
    try {
      setMe(await api<Me>("/me", { method: "PATCH", body: f }));
      setMsg("Saved. Pending reminders were re-planned.");
    } catch (err) {
      setMsg((err as Error).message);
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
    if (!window.confirm("Delete your account and all its data? This cannot be undone.")) return;
    try {
      await api("/me", { method: "DELETE" });
      router.push("/login");
    } catch (err) {
      setMsg((err as Error).message);
    }
  }

  return (
    <>
      <h1>Settings</h1>
      <p className="muted">Signed in as {me.email}</p>

      <h2>Reminders</h2>
      <form className="stack panel" onSubmit={save}>
        <label>
          Timezone
          <div className="row">
            <input value={f.timezone} onChange={(e) => setF({ ...f, timezone: e.target.value })} />
            <button type="button" onClick={() => setF({ ...f, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone })}>
              Use this device&apos;s
            </button>
          </div>
        </label>
        <div className="row">
          <label>
            Quiet hours from
            <input type="time" value={f.quiet_hours_start} onChange={(e) => setF({ ...f, quiet_hours_start: e.target.value })} />
          </label>
          <label>
            until
            <input type="time" value={f.quiet_hours_end} onChange={(e) => setF({ ...f, quiet_hours_end: e.target.value })} />
          </label>
          <label>
            Max reminders per day
            <input
              type="number"
              min={1}
              max={50}
              value={f.reminder_cap_per_day}
              onChange={(e) => setF({ ...f, reminder_cap_per_day: Number(e.target.value) })}
            />
          </label>
        </div>
        <p className="muted">
          Reminders that fall in quiet hours move earlier if the deadline is before they end, otherwise to when they end. Final reminders
          for required steps are never dropped by the daily limit.
        </p>
        <button className="primary">Save</button>
        {msg && <p className="muted">{msg}</p>}
      </form>

      <h2>Telegram</h2>
      <div className="panel">
        {me.telegram_linked ? (
          <div className="row">
            <span className="badge ok">Connected</span>
            <button onClick={unlink}>Disconnect</button>
          </div>
        ) : pairURL ? (
          <p>
            <a href={pairURL} target="_blank" rel="noreferrer" className="button primary">
              Open Telegram and press Start
            </a>{" "}
            <span className="muted">The link works once and expires in 15 minutes. Reload this page after linking.</span>
          </p>
        ) : (
          <button className="primary" onClick={pair}>
            Connect Telegram
          </button>
        )}
      </div>

      <h2>Google Calendar</h2>
      <div className="panel">
        {calendarConnected === null ? <p>Checking Calendar connection…</p> : calendarConnected ? (
          <div className="row"><span>Connected</span><button onClick={disconnectCalendar}>Disconnect</button></div>
        ) : (
          <a className="button" href="/api/integrations/google/calendar/start">Connect Google Calendar</a>
        )}
        <p className="muted">Confirmed deadlines are synced to a dedicated HackTrack calendar. Disconnecting revokes access but leaves that calendar in Google.</p>
      </div>

      <h2>Account</h2>
      <div className="row">
        <a className="button" href="/api/me/export">Download my data</a>
        <button onClick={deleteAccount}>Delete account</button>
        <button onClick={logout}>Sign out</button>
      </div>
      {msg && <p role="status">{msg}</p>}
    </>
  );
}
