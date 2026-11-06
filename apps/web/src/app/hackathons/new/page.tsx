"use client";

import { useState } from "react";
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
  // The idempotency key lives on the payload's fingerprint, not the form
  // instance: a retry after a reload or a lost response reuses the server
  // request, so the server returns the original event instead of a duplicate.
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
      // Confirmed: the create succeeded, so the next same-shaped save is a
      // new event with a new key.
      await clearPendingCreate(payload);
      router.push(`/hackathons/${id}`);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <>
      <h1>Add a hackathon</h1>
      <form className="stack panel" onSubmit={submit}>
        <label>
          Title
          <input required value={f.title} onChange={set("title")} autoFocus />
        </label>
        <label>
          Event page URL
          <input type="url" value={f.source_url} onChange={set("source_url")} placeholder="https://" />
        </label>
        <label>
          Organizer
          <input value={f.organizer} onChange={set("organizer")} />
        </label>
        <div className="row">
          <label>
            Mode
            <select value={f.mode} onChange={set("mode")}>
              <option value="ONLINE">Online</option>
              <option value="OFFLINE">Offline</option>
              <option value="HYBRID">Hybrid</option>
            </select>
          </label>
          <label>
            Event timezone
            <input value={f.event_timezone} onChange={set("event_timezone")} />
          </label>
        </div>
        {f.mode !== "ONLINE" && (
          <div className="row">
            <label>
              Venue
              <input value={f.venue} onChange={set("venue")} />
            </label>
            <label>
              City
              <input value={f.location} onChange={set("location")} />
            </label>
          </div>
        )}
        <div className="row">
          <label>
            Starts
            <input type="datetime-local" value={f.starts_at} onChange={set("starts_at")} />
          </label>
          <label>
            Ends
            <input type="datetime-local" value={f.ends_at} onChange={set("ends_at")} />
          </label>
        </div>
        <label className="row">
          <input type="checkbox" checked={f.registered} onChange={set("registered")} /> I have already registered
        </label>
        {!f.registered && (
          <label>
            Registration deadline (optional)
            <input type="datetime-local" value={f.register_by} onChange={set("register_by")} />
          </label>
        )}
        <label>
          Submission deadline (optional)
          <input type="datetime-local" value={f.submit_by} onChange={set("submit_by")} />
        </label>
        <label className="row">
          <input type="checkbox" checked={f.submit_date_only} onChange={set("submit_date_only")} /> Only the date is known (end of
          day in the event timezone)
        </label>
        <p className="muted">Times are in your browser&apos;s timezone. Dates you type here count as confirmed.</p>
        <button className="primary" disabled={busy}>
          Save event
        </button>
        {error && <p className="error">{error}</p>}
      </form>
    </>
  );
}
