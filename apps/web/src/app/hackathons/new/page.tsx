"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api, localToISO } from "@/lib/api";
import { clearPendingCreate, pendingCreateKey } from "@/lib/pendingCreate";

const browserTZ = () => Intl.DateTimeFormat().resolvedOptions().timeZone;

export default function NewHackathonPage() {
  const router = useRouter();
  const [f, setF] = useState({
    title: "",
    organizer: "",
    source_url: "",
    mode: "ONLINE",
    venue: "",
    location: "",
    event_timezone: browserTZ(),
    starts_at: "",
    ends_at: "",
    registered: false,
    register_by: "",
    submit_by: "",
    submit_date_only: false,
  });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setF({ ...f, [k]: e.target.type === "checkbox" ? (e.target as HTMLInputElement).checked : e.target.value });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const milestones: object[] = [];
      if (f.register_by && !f.registered) {
        milestones.push({ type: "REGISTER", title: "Register", due_at: localToISO(f.register_by) });
      }
      if (f.submit_by) {
        const due = f.submit_date_only ? { due_date: f.submit_by.slice(0, 10) } : { due_at: localToISO(f.submit_by) };
        milestones.push({ type: "SUBMIT", title: "Submit project", ...due });
      }
      const payload = {
        title: f.title,
        organizer: f.organizer,
        source_url: f.source_url,
        mode: f.mode,
        venue: f.venue,
        location: f.location,
        event_timezone: f.event_timezone,
        starts_at: localToISO(f.starts_at),
        ends_at: localToISO(f.ends_at),
        registration_state: f.registered ? "REGISTERED" : "PLANNING",
        milestones,
      };
      const idempotencyKey = await pendingCreateKey(payload);
      const { id } = await api<{ id: string }>("/hackathons", {
        method: "POST",
        body: { ...payload, idempotency_key: idempotencyKey },
      });
      await clearPendingCreate(payload);
      router.push(`/hackathons/${id}`);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="stack" style={{ maxWidth: 720, margin: "0 auto", gap: 24 }}>
      <div>
        <Link href="/" className="muted" style={{ display: "inline-flex", alignItems: "center", gap: 4, marginBottom: 12 }}>
          ← Back to Dashboard
        </Link>
        <h1>Add a Hackathon</h1>
        <p className="muted">
          Add an event manually with key deadlines. Dates you specify here are automatically confirmed for reminders.
        </p>
      </div>

      <form className="panel stack" onSubmit={submit}>
        <label>
          Event Title *
          <input required placeholder="e.g. HackMIT 2026" value={f.title} onChange={set("title")} autoFocus />
        </label>

        <label>
          Event Page / Registration URL
          <input type="url" value={f.source_url} onChange={set("source_url")} placeholder="https://..." />
        </label>

        <label>
          Organizer
          <input placeholder="e.g. MIT Tech Club, MLH, Devpost" value={f.organizer} onChange={set("organizer")} />
        </label>

        <div className="row">
          <label style={{ flex: 1 }}>
            Mode
            <select value={f.mode} onChange={set("mode")}>
              <option value="ONLINE">Online</option>
              <option value="OFFLINE">Offline</option>
              <option value="HYBRID">Hybrid</option>
            </select>
          </label>
          <label style={{ flex: 1 }}>
            Event Timezone
            <input value={f.event_timezone} onChange={set("event_timezone")} />
          </label>
        </div>

        {f.mode !== "ONLINE" && (
          <div className="row">
            <label style={{ flex: 1 }}>
              Venue
              <input placeholder="Building, hall, or room" value={f.venue} onChange={set("venue")} />
            </label>
            <label style={{ flex: 1 }}>
              City / State
              <input placeholder="e.g. San Francisco, CA" value={f.location} onChange={set("location")} />
            </label>
          </div>
        )}

        <div className="row">
          <label style={{ flex: 1 }}>
            Event Starts
            <input type="datetime-local" value={f.starts_at} onChange={set("starts_at")} />
          </label>
          <label style={{ flex: 1 }}>
            Event Ends
            <input type="datetime-local" value={f.ends_at} onChange={set("ends_at")} />
          </label>
        </div>

        <div className="panel" style={{ background: "var(--panel)", padding: 14 }}>
          <label className="row" style={{ cursor: "pointer", marginBottom: 12 }}>
            <input type="checkbox" checked={f.registered} onChange={set("registered")} />
            <strong>I have already registered for this event</strong>
          </label>

          {!f.registered && (
            <label style={{ marginBottom: 12 }}>
              Registration Deadline (optional)
              <input type="datetime-local" value={f.register_by} onChange={set("register_by")} />
            </label>
          )}

          <label>
            Submission Deadline (optional)
            <input type="datetime-local" value={f.submit_by} onChange={set("submit_by")} />
          </label>

          <label className="row" style={{ cursor: "pointer", marginTop: 8 }}>
            <input type="checkbox" checked={f.submit_date_only} onChange={set("submit_date_only")} />
            <span className="muted">Only date is known (end of day in the event timezone)</span>
          </label>
        </div>

        <p className="muted faint" style={{ margin: 0 }}>
          Times entered above use your browser timezone ({browserTZ()}).
        </p>

        <div className="row" style={{ justifyContent: "flex-end", marginTop: 8 }}>
          <button type="button" onClick={() => router.push("/")}>
            Cancel
          </button>
          <button className="primary" disabled={busy}>
            {busy ? "Saving event…" : "Save Event"}
          </button>
        </div>

        {error && <p className="error" role="alert">{error}</p>}
      </form>
    </div>
  );
}
