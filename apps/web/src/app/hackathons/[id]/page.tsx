"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { api, formatWhen, label, localToISO, type Detail, type Milestone } from "@/lib/api";
import { useMe } from "@/lib/useMe";

const registrationMoves: Record<string, string[]> = {
  DISCOVERED: ["PLANNING", "REGISTERED", "SKIPPED"],
  PLANNING: ["REGISTERED", "SKIPPED"],
  REGISTERED: ["ACCEPTED", "WAITLISTED", "REJECTED"],
  WAITLISTED: ["ACCEPTED", "REJECTED"],
  SKIPPED: ["PLANNING"],
};
const participationMoves: Record<string, string[]> = {
  NOT_STARTED: ["PREPARING", "ACTIVE"],
  PREPARING: ["ACTIVE"],
  ACTIVE: ["ENDED"],
};
const outcomes = ["SUBMITTED", "ATTENDED_NO_SUBMISSION", "WITHDRAWN", "MISSED", "EVENT_CANCELLED"];
const types = ["REGISTER", "RSVP", "TEAM", "CHECK_IN", "START", "SUBMIT", "PRESENT", "OTHER"];

export default function HackathonPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [me, , meError] = useMe();
  const [h, setH] = useState<Detail | null>(null);
  const [error, setError] = useState("");
  const [evidence, setEvidence] = useState("");

  const load = useCallback(() => api<Detail>(`/hackathons/${id}`).then(setH, (e) => setError(e.message)), [id]);
  useEffect(() => {
    load();
  }, [load]);

  async function run(fn: () => Promise<unknown>): Promise<boolean> {
    setError("");
    try {
      await fn();
      await load();
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    }
  }
  const transition = (field: string, to: string) =>
    run(() =>
      api(`/hackathons/${id}/transitions`, {
        method: "POST",
        body: {
          field,
          to,
          evidence: to === "REGISTERED" && evidence ? { type: "LINK", reference: evidence } : undefined,
        },
      }),
    );
  const patchMilestone = (mid: string, body: object) => run(() => api(`/milestones/${mid}`, { method: "PATCH", body }));

  if (meError && !me) return <p className="error">{meError}</p>;
  if (!h || !me) return error ? <p className="error">{error}</p> : <p className="muted">Loading…</p>;
  const tz = me.timezone;
  const n = h.next_action;
  const canParticipate = h.registration_state === "REGISTERED" || h.registration_state === "ACCEPTED";

  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>{h.title}</h1>
        <div className="row">
          <span className="badge">{label(h.mode)}</span>
          <span className="badge">{label(h.bucket)}</span>
        </div>
      </div>
      <p className="muted">
        {[h.organizer, h.venue, h.location].filter(Boolean).join(" · ")}
        {h.starts_at && ` · ${h.starts_date ? formatWhen(null, tz, "DATE", h.starts_date) : formatWhen(h.starts_at, tz)} to ${h.ends_date ? formatWhen(null, tz, "DATE", h.ends_date) : formatWhen(h.ends_at, tz)}`}
        {h.source_url && (
          <>
            {" · "}
            <a href={h.source_url} target="_blank" rel="noreferrer">
              Event page
            </a>
          </>
        )}
      </p>

      {n && (
        <div className={`next${n.overdue ? " overdue" : ""}`}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <div>
              <strong>Next action:</strong> {n.title}
              {n.type !== "OUTCOME" && (
                <div className="muted">
                  {n.overdue ? "Overdue · " : ""}
                  {formatWhen(n.due_at, tz, n.due_precision, n.due_date)} · {n.confidence}
                </div>
              )}
            </div>
            {n.milestone_id && (
              <button className="primary" onClick={() => patchMilestone(n.milestone_id!, { status: "DONE" })}>
                Done
              </button>
            )}
          </div>
        </div>
      )}
      {error && <p className="error">{error}</p>}

      <h2>Status</h2>
      <div className="panel" style={{ display: "grid", gap: 12 }}>
        <div className="row">
          <span>
            Registration: <strong>{label(h.registration_state)}</strong>
          </span>
          {(registrationMoves[h.registration_state] ?? []).map((to) => (
            <button key={to} className={to === "REGISTERED" ? "primary" : ""} onClick={() => transition("registration_state", to)}>
              {to === "REGISTERED" ? "Mark registered" : label(to)}
            </button>
          ))}
          {(h.registration_state === "PLANNING" || h.registration_state === "DISCOVERED") && (
            <input placeholder="Confirmation link (optional)" value={evidence} onChange={(e) => setEvidence(e.target.value)} />
          )}
        </div>
        {canParticipate && (
          <div className="row">
            <span>
              Participation: <strong>{label(h.participation_state)}</strong>
            </span>
            {(participationMoves[h.participation_state] ?? []).map((to) => (
              <button key={to} onClick={() => transition("participation_state", to)}>
                {label(to)}
              </button>
            ))}
          </div>
        )}
        <div className="row">
          <span>
            Outcome: <strong>{h.outcome ? label(h.outcome) : "Not set"}</strong>
          </span>
          {!h.outcome &&
            outcomes.map((o) => (
              <button key={o} onClick={() => transition("outcome", o)}>
                {label(o)}
              </button>
            ))}
        </div>
      </div>

      <h2>Timeline</h2>
      <ol className="timeline">
        {h.milestones.map((m) => (
          <MilestoneRow key={m.id} m={m} tz={tz} patch={(b) => patchMilestone(m.id, b)} remove={() => run(() => api(`/milestones/${m.id}`, { method: "DELETE" }))} />
        ))}
        {h.milestones.length === 0 && <li className="muted">No milestones yet.</li>}
      </ol>
      <AddMilestone onAdd={(body) => run(() => api(`/hackathons/${id}/milestones`, { method: "POST", body }))} />

      <h2>History</h2>
      <ul className="muted">
        {h.history.map((t, i) => (
          <li key={i}>
            {formatWhen(t.at, tz)}: {t.entity === "milestone" ? (h.milestones.find((m) => m.id === t.entity_id)?.title ?? "milestone") : label(t.field)}{" "}
            {t.from ? `${label(t.from)} → ` : ""}
            {label(t.to)} ({label(t.actor)})
          </li>
        ))}
      </ul>

      <div className="row" style={{ marginTop: 32 }}>
        <button onClick={() => run(() => api(`/hackathons/${id}`, { method: "PATCH", body: { archived: !h.archived_at } }))}>
          {h.archived_at ? "Unarchive" : "Archive"}
        </button>
        <button
          onClick={async () => {
            if (!confirm("Delete this event and its reminders?")) return;
            await api(`/hackathons/${id}`, { method: "DELETE" });
            router.push("/");
          }}
        >
          Delete
        </button>
      </div>
    </>
  );
}

function MilestoneRow({ m, tz, patch, remove }: { m: Milestone; tz: string; patch: (b: object) => void; remove: () => void }) {
  const [due, setDue] = useState("");
  return (
    <li className={`panel${m.status !== "TODO" ? " done" : ""}`}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div>
          <strong>{m.title}</strong> <span className="badge">{label(m.type)}</span>{" "}
          {!m.required && <span className="badge">Optional</span>}{" "}
          {m.status !== "TODO" && <span className={`badge${m.status === "MISSED" ? " warn" : " ok"}`}>{label(m.status)}</span>}
          <div className="muted">
            {formatWhen(m.due_at, tz, m.due_precision, m.due_date)}
            {m.due_at && !m.date_confirmed && " · not confirmed, no reminders yet"}
            {m.depart_at && ` · leave by ${formatWhen(m.depart_at, tz)}`}
          </div>
        </div>
        <div className="row">
          {m.due_at && !m.date_confirmed && <button onClick={() => patch({ date_confirmed: true })}>Confirm date</button>}
          {(m.status === "TODO" || m.status === "MISSED") && <button onClick={() => patch({ status: "DONE" })}>Done</button>}
          {m.status === "TODO" && <button onClick={() => patch({ status: "SKIPPED" })}>Skip</button>}
          <button onClick={remove} aria-label="Delete milestone">
            Delete
          </button>
        </div>
      </div>
      {m.status === "TODO" && (
        <div className="row" style={{ marginTop: 8 }}>
          <input type="datetime-local" value={due} onChange={(e) => setDue(e.target.value)} aria-label="New due time" />
          <button disabled={!due} onClick={() => patch({ due_at: localToISO(due), date_confirmed: true })}>
            Change date
          </button>
        </div>
      )}
    </li>
  );
}

function AddMilestone({ onAdd }: { onAdd: (body: object) => Promise<boolean> }) {
  const [f, setF] = useState({ type: "SUBMIT", title: "", due: "", dateOnly: false, required: true, depart: "" });
  return (
    <form
      className="panel row"
      style={{ marginTop: 12 }}
      onSubmit={async (e) => {
        e.preventDefault();
        const due = !f.due ? {} : f.dateOnly ? { due_date: f.due.slice(0, 10) } : { due_at: localToISO(f.due) };
        const ok = await onAdd({
          type: f.type,
          title: f.title || undefined,
          required: f.required,
          depart_at: f.depart ? localToISO(f.depart) : undefined,
          ...due,
        });
        // Retain inputs on failure so the user can fix and retry.
        if (ok) setF({ ...f, title: "", due: "", depart: "" });
      }}
    >
      <select value={f.type} onChange={(e) => setF({ ...f, type: e.target.value })} aria-label="Milestone type">
        {types.map((t) => (
          <option key={t} value={t}>
            {label(t)}
          </option>
        ))}
      </select>
      <input placeholder="Title" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} />
      <input type="datetime-local" value={f.due} onChange={(e) => setF({ ...f, due: e.target.value })} aria-label="Due" />
      <label className="row">
        <input type="checkbox" checked={f.dateOnly} onChange={(e) => setF({ ...f, dateOnly: e.target.checked })} /> Date only
      </label>
      <label className="row">
        <input type="checkbox" checked={f.required} onChange={(e) => setF({ ...f, required: e.target.checked })} /> Required
      </label>
      {(f.type === "CHECK_IN" || f.type === "START") && (
        <label>
          Leave by
          <input type="datetime-local" value={f.depart} onChange={(e) => setF({ ...f, depart: e.target.value })} />
        </label>
      )}
      <button className="primary">Add milestone</button>
    </form>
  );
}
