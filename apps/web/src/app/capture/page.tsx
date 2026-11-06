"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { clearPendingCreate, pendingCreateKey } from "@/lib/pendingCreate";

type DraftMilestone = { type: string; title: string; due_at?: string; due_date?: string; due_precision: string; source_quote?: string; confirmed?: boolean };

type Draft = {
  title?: string; organizer?: string; mode?: string; location?: string; timezone?: string;
  starts_at?: string; starts_date?: string; ends_at?: string; ends_date?: string; field_sources: Record<string, string>;
  milestones?: DraftMilestone[];
};

function localDateTime(value?: string) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

export default function CapturePage() {
  const router = useRouter();
  const [url, setURL] = useState("");
  const [text, setText] = useState("");
  const [useAI, setUseAI] = useState(false);
  const [registered, setRegistered] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [duplicate, setDuplicate] = useState("");
  const [saved, setSaved] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // The save's idempotency key is bound to the drafted payload, not the
  // preview click: re-previewing the same capture, or re-entering the same
  // no-URL draft after a lost response, reuses the server request.

  function updateMilestone(index: number, patch: Partial<DraftMilestone>) {
    setDraft(current => current ? { ...current, milestones: current.milestones?.map((m, i) => i === index ? { ...m, ...patch } : m) } : current);
  }

  async function extract(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError(""); setDuplicate(""); setDraft(null);
    try {
      const result = await api<{ draft?: Draft; duplicate_id?: string }>("/capture", { method: "POST", body: { url, text, use_ai: useAI } });
      if (result.duplicate_id) setDuplicate(result.duplicate_id);
      else if (result.draft) setDraft(result.draft);
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  }
  async function save(e: React.FormEvent) {
    e.preventDefault(); if (!draft) return;
    setBusy(true); setError("");
    try {
      // Single atomic create: event + initial milestones commit together.
      const payload = {
        title: draft.title, organizer: draft.organizer || "", source_url: url,
        mode: draft.mode || "ONLINE", location: draft.location || "", event_timezone: draft.timezone || "UTC",
        starts_at: draft.starts_at || undefined, starts_date: draft.starts_date || undefined,
        ends_at: draft.ends_at || undefined, ends_date: draft.ends_date || undefined,
        description: text, registration_state: registered ? "REGISTERED" : "PLANNING",
        milestones: (draft.milestones || []).map(m => ({
          type: m.type, title: m.title,
          due_at: m.due_precision === "DATE" ? undefined : m.due_at || undefined,
          due_date: m.due_precision === "DATE" ? (m.due_date || m.due_at?.slice(0, 10)) : undefined,
          due_precision: m.due_at || m.due_date ? m.due_precision : "UNKNOWN", date_confirmed: !!m.confirmed,
        })),
      };
      const idempotencyKey = await pendingCreateKey(payload);
      const result = await api<{ id: string }>("/hackathons", { method: "POST", body: { ...payload, idempotency_key: idempotencyKey } });
      // Confirmed: the create succeeded, so the next same-shaped save is a
      // new event with a new key.
      await clearPendingCreate(payload);
      setSaved(result.id);
      router.push(`/hackathons/${result.id}`);
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  }
  return <>
    <h1>Capture an event</h1>
    <form className="stack panel" onSubmit={extract}>
      <label>Event page URL<input type="url" value={url} onChange={e => setURL(e.target.value)} placeholder="https://" /></label>
      <label>Or paste the event text<textarea rows={7} value={text} onChange={e => setText(e.target.value)} /></label>
      <label><input type="checkbox" checked={useAI} onChange={e => setUseAI(e.target.checked)} /> Use AI to extract missing dates</label>
      <p className="muted">For Instagram and LinkedIn, paste the post text. AI sends the text to the configured provider.</p>
      <button className="primary" disabled={busy || (!url.trim() && !text.trim())}>Preview</button>
    </form>
    {duplicate && <p>This event is already saved. <Link href={`/hackathons/${duplicate}`}>Open event</Link></p>}
    {draft && !saved && <form className="stack panel" onSubmit={save}>
      <h2>Review draft</h2>
      <label>Title ({draft.field_sources.title || "enter manually"})<input required value={draft.title || ""} onChange={e => setDraft({ ...draft, title: e.target.value })} /></label>
      <label>Organizer ({draft.field_sources.organizer || "enter manually"})<input value={draft.organizer || ""} onChange={e => setDraft({ ...draft, organizer: e.target.value })} /></label>
      <label>Mode ({draft.field_sources.mode || "enter manually"})<select value={draft.mode || "ONLINE"} onChange={e => setDraft({ ...draft, mode: e.target.value })}><option value="ONLINE">Online</option><option value="OFFLINE">Offline</option><option value="HYBRID">Hybrid</option></select></label>
      <label>Location ({draft.field_sources.location || "enter manually"})<input value={draft.location || ""} onChange={e => setDraft({ ...draft, location: e.target.value })} /></label>
      <label>Event timezone ({draft.field_sources.timezone || "enter manually"})<input value={draft.timezone || "UTC"} onChange={e => setDraft({ ...draft, timezone: e.target.value, milestones: draft.milestones?.map(m => m.due_precision === "DATE" ? { ...m, confirmed: false } : m) })} /></label>
      <label>Starts ({draft.field_sources.starts_at || "enter manually"})<input type="datetime-local" value={localDateTime(draft.starts_at)} onChange={e => setDraft({ ...draft, starts_at: e.target.value ? new Date(e.target.value).toISOString() : undefined })} /></label>
      <label>Ends ({draft.field_sources.ends_at || "enter manually"})<input type="datetime-local" value={localDateTime(draft.ends_at)} onChange={e => setDraft({ ...draft, ends_at: e.target.value ? new Date(e.target.value).toISOString() : undefined })} /></label>
      <p className="muted">Date and time inputs use your browser&apos;s timezone; date-only deadlines use the event timezone.</p>
      {(draft.milestones || []).map((m, i) => <fieldset className="stack" key={i}>
        <legend>Deadline {i + 1} ({draft.field_sources.milestones || "extracted"})</legend>
        <label>Title<input required value={m.title} onChange={e => updateMilestone(i, { title: e.target.value })} /></label>
        <label>Type<select value={m.type} onChange={e => updateMilestone(i, { type: e.target.value })}>{["REGISTER", "RSVP", "TEAM", "CHECK_IN", "START", "SUBMIT", "PRESENT", "OTHER"].map(type => <option key={type} value={type}>{type.replaceAll("_", " ")}</option>)}</select></label>
        <label>Precision<select value={m.due_precision === "DATE" ? "DATE" : "DATETIME"} onChange={e => updateMilestone(i, { due_precision: e.target.value, due_at: undefined, confirmed: false })}><option value="DATETIME">Date and time</option><option value="DATE">Date only</option></select></label>
        <label>Deadline<input type={m.due_precision === "DATE" ? "date" : "datetime-local"} value={m.due_precision === "DATE" ? m.due_date || m.due_at?.slice(0, 10) || "" : localDateTime(m.due_at)} onChange={e => updateMilestone(i, m.due_precision === "DATE" ? { due_date: e.target.value || undefined, due_at: e.target.value ? new Date(e.target.value + "T23:59:00Z").toISOString() : undefined, due_precision: "DATE", confirmed: false } : { due_at: e.target.value ? new Date(e.target.value).toISOString() : undefined, due_precision: "DATETIME", confirmed: false })} /></label>
        {m.source_quote && <p>Source: <q>{m.source_quote}</q></p>}
        <label><input type="checkbox" disabled={!m.due_at} checked={!!m.confirmed} onChange={e => updateMilestone(i, { confirmed: e.target.checked })} /> I confirm this deadline</label>
        <button type="button" onClick={() => setDraft({ ...draft, milestones: draft.milestones?.filter((_, index) => index !== i) })}>Remove deadline</button>
      </fieldset>)}
      <label><input type="checkbox" checked={registered} onChange={e => setRegistered(e.target.checked)} /> I have already registered</label>
      <p className="muted">Only deadlines you explicitly confirm can schedule reminders.</p>
      <button className="primary" disabled={busy}>Save event</button>
    </form>}
    {saved && <p>Event saved. <Link href={`/hackathons/${saved}`}>Open it to finish reviewing deadlines</Link>.</p>}
    {error && <p role="alert" className="error">{error}</p>}
  </>;
}
