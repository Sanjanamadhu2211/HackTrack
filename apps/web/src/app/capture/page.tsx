"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { clearPendingCreate, pendingCreateKey } from "@/lib/pendingCreate";

type DraftMilestone = {
  type: string;
  title: string;
  due_at?: string;
  due_date?: string;
  due_precision: string;
  source_quote?: string;
  confirmed?: boolean;
};

type Draft = {
  title?: string;
  organizer?: string;
  mode?: string;
  location?: string;
  timezone?: string;
  starts_at?: string;
  starts_date?: string;
  ends_at?: string;
  ends_date?: string;
  field_sources: Record<string, string>;
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

  function updateMilestone(index: number, patch: Partial<DraftMilestone>) {
    setDraft((current) =>
      current
        ? {
            ...current,
            milestones: current.milestones?.map((m, i) => (i === index ? { ...m, ...patch } : m)),
          }
        : current,
    );
  }

  async function extract(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setDuplicate("");
    setDraft(null);
    try {
      const result = await api<{ draft?: Draft; duplicate_id?: string }>("/capture", {
        method: "POST",
        body: { url, text, use_ai: useAI },
      });
      if (result.duplicate_id) setDuplicate(result.duplicate_id);
      else if (result.draft) setDraft(result.draft);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!draft) return;
    setBusy(true);
    setError("");
    try {
      const payload = {
        title: draft.title,
        organizer: draft.organizer || "",
        source_url: url,
        mode: draft.mode || "ONLINE",
        location: draft.location || "",
        event_timezone: draft.timezone || "UTC",
        starts_at: draft.starts_at || undefined,
        starts_date: draft.starts_date || undefined,
        ends_at: draft.ends_at || undefined,
        ends_date: draft.ends_date || undefined,
        description: text,
        registration_state: registered ? "REGISTERED" : "PLANNING",
        milestones: (draft.milestones || []).map((m) => ({
          type: m.type,
          title: m.title,
          due_at: m.due_precision === "DATE" ? undefined : m.due_at || undefined,
          due_date: m.due_precision === "DATE" ? m.due_date || m.due_at?.slice(0, 10) : undefined,
          due_precision: m.due_at || m.due_date ? m.due_precision : "UNKNOWN",
          date_confirmed: !!m.confirmed,
        })),
      };
      const idempotencyKey = await pendingCreateKey(payload);
      const result = await api<{ id: string }>("/hackathons", {
        method: "POST",
        body: { ...payload, idempotency_key: idempotencyKey },
      });
      await clearPendingCreate(payload);
      setSaved(result.id);
      router.push(`/hackathons/${result.id}`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack" style={{ maxWidth: 720, margin: "0 auto", gap: 24 }}>
      <div>
        <h1>Capture an Event</h1>
        <p className="muted">
          Paste a link or text from Devpost, Unstop, Luma, MLH, or social media to automatically extract dates and deadlines.
        </p>
      </div>

      <form className="panel stack" onSubmit={extract}>
        <label>
          Event page URL
          <input
            type="url"
            value={url}
            onChange={(e) => setURL(e.target.value)}
            placeholder="https://devpost.com/software/..."
          />
        </label>

        <label>
          Or paste event text / announcement
          <textarea
            rows={5}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Paste description, schedule, or submission deadlines here…"
          />
        </label>

        <label className="row" style={{ cursor: "pointer", userSelect: "none" }}>
          <input
            type="checkbox"
            checked={useAI}
            onChange={(e) => setUseAI(e.target.checked)}
          />
          <span>Use AI to detect nuanced deadlines and dates</span>
        </label>

        <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <span className="muted faint">Supports all major hackathon platforms</span>
          <button
            type="submit"
            className="primary"
            disabled={busy || (!url.trim() && !text.trim())}
          >
            {busy ? "Analyzing event…" : "Preview & Extract"}
          </button>
        </div>
      </form>

      {duplicate && (
        <div className="panel stack" style={{ borderLeft: "3px solid var(--accent)" }}>
          <h3>Already saved!</h3>
          <p className="muted">This event is already tracked in your dashboard.</p>
          <Link href={`/hackathons/${duplicate}`} className="button primary">
            Open existing event
          </Link>
        </div>
      )}

      {draft && !saved && (
        <form className="panel stack" onSubmit={save}>
          <h2>Review Extracted Draft</h2>

          <label>
            Title <span className="faint">({draft.field_sources.title || "manual"})</span>
            <input
              required
              value={draft.title || ""}
              onChange={(e) => setDraft({ ...draft, title: e.target.value })}
            />
          </label>

          <div className="row">
            <label style={{ flex: 1 }}>
              Organizer <span className="faint">({draft.field_sources.organizer || "manual"})</span>
              <input
                value={draft.organizer || ""}
                onChange={(e) => setDraft({ ...draft, organizer: e.target.value })}
              />
            </label>
            <label style={{ flex: 1 }}>
              Mode <span className="faint">({draft.field_sources.mode || "manual"})</span>
              <select
                value={draft.mode || "ONLINE"}
                onChange={(e) => setDraft({ ...draft, mode: e.target.value })}
              >
                <option value="ONLINE">Online</option>
                <option value="OFFLINE">Offline</option>
                <option value="HYBRID">Hybrid</option>
              </select>
            </label>
          </div>

          <div className="row">
            <label style={{ flex: 1 }}>
              Location <span className="faint">({draft.field_sources.location || "manual"})</span>
              <input
                value={draft.location || ""}
                onChange={(e) => setDraft({ ...draft, location: e.target.value })}
              />
            </label>
            <label style={{ flex: 1 }}>
              Timezone <span className="faint">({draft.field_sources.timezone || "manual"})</span>
              <input
                value={draft.timezone || "UTC"}
                onChange={(e) => setDraft({ ...draft, timezone: e.target.value })}
              />
            </label>
          </div>

          <div className="row">
            <label style={{ flex: 1 }}>
              Starts
              <input
                type="datetime-local"
                value={localDateTime(draft.starts_at)}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    starts_at: e.target.value ? new Date(e.target.value).toISOString() : undefined,
                  })
                }
              />
            </label>
            <label style={{ flex: 1 }}>
              Ends
              <input
                type="datetime-local"
                value={localDateTime(draft.ends_at)}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    ends_at: e.target.value ? new Date(e.target.value).toISOString() : undefined,
                  })
                }
              />
            </label>
          </div>

          <h3>Deadlines ({draft.milestones?.length || 0})</h3>
          {(draft.milestones || []).map((m, i) => (
            <div key={i} className="panel stack" style={{ background: "var(--panel)", padding: 14 }}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <strong>Deadline {i + 1}</strong>
                <button
                  type="button"
                  className="danger"
                  style={{ padding: "2px 8px", fontSize: "0.8rem" }}
                  onClick={() =>
                    setDraft({
                      ...draft,
                      milestones: draft.milestones?.filter((_, index) => index !== i),
                    })
                  }
                >
                  Remove
                </button>
              </div>

              <div className="row">
                <label style={{ flex: 2 }}>
                  Title
                  <input
                    required
                    value={m.title}
                    onChange={(e) => updateMilestone(i, { title: e.target.value })}
                  />
                </label>
                <label style={{ flex: 1 }}>
                  Type
                  <select
                    value={m.type}
                    onChange={(e) => updateMilestone(i, { type: e.target.value })}
                  >
                    {[
                      "REGISTER",
                      "RSVP",
                      "TEAM",
                      "CHECK_IN",
                      "START",
                      "SUBMIT",
                      "PRESENT",
                      "OTHER",
                    ].map((type) => (
                      <option key={type} value={type}>
                        {type.replaceAll("_", " ")}
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              <label>
                Due Date & Time
                <input
                  type={m.due_precision === "DATE" ? "date" : "datetime-local"}
                  value={
                    m.due_precision === "DATE"
                      ? m.due_date || m.due_at?.slice(0, 10) || ""
                      : localDateTime(m.due_at)
                  }
                  onChange={(e) =>
                    updateMilestone(
                      i,
                      m.due_precision === "DATE"
                        ? {
                            due_date: e.target.value || undefined,
                            due_at: e.target.value
                              ? new Date(e.target.value + "T23:59:00Z").toISOString()
                              : undefined,
                            due_precision: "DATE",
                            confirmed: false,
                          }
                        : {
                            due_at: e.target.value ? new Date(e.target.value).toISOString() : undefined,
                            due_precision: "DATETIME",
                            confirmed: false,
                          },
                    )
                  }
                />
              </label>

              {m.source_quote && (
                <p className="faint" style={{ margin: 0 }}>
                  Detected text: <q>{m.source_quote}</q>
                </p>
              )}

              <label className="row" style={{ cursor: "pointer" }}>
                <input
                  type="checkbox"
                  disabled={!m.due_at}
                  checked={!!m.confirmed}
                  onChange={(e) => updateMilestone(i, { confirmed: e.target.checked })}
                />
                Confirm this deadline for automatic reminders
              </label>
            </div>
          ))}

          <label className="row" style={{ cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={registered}
              onChange={(e) => setRegistered(e.target.checked)}
            />
            I have already registered for this hackathon
          </label>

          <button className="primary" disabled={busy}>
            {busy ? "Saving event…" : "Save Event to Dashboard"}
          </button>
        </form>
      )}

      {saved && (
        <p className="badge ok" style={{ padding: 12 }}>
          Event saved successfully!{" "}
          <Link href={`/hackathons/${saved}`}>Open hackathon view</Link>.
        </p>
      )}

      {error && <p className="error" role="alert">{error}</p>}
    </div>
  );
}
