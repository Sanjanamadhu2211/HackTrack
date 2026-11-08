"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { api, formatWhen, label, localToISO, type Detail, type Milestone } from "@/lib/api";
import { useUser } from "@/lib/UserContext";

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
  const { me, loading: meLoading, error: meError } = useUser();
  const [h, setH] = useState<Detail | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [evidence, setEvidence] = useState("");

  const load = useCallback(() => {
    return api<Detail>(`/hackathons/${id}`)
      .then((res) => {
        setH(res);
        setError("");
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  async function run(actionName: string, fn: () => Promise<unknown>): Promise<boolean> {
    setBusyAction(actionName);
    setError("");
    try {
      await fn();
      await load();
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      setBusyAction(null);
    }
  }

  // Optimistic milestone update
  const patchMilestone = async (mid: string, body: { status?: "DONE" | "SKIPPED" | "TODO" | "MISSED"; [key: string]: unknown }) => {
    if (!h) return;
    const prev = h;
    if (body.status) {
      setH({
        ...h,
        milestones: h.milestones.map((m) => (m.id === mid ? { ...m, status: body.status! } : m)),
        next_action: h.next_action?.milestone_id === mid ? null : h.next_action,
      });
    }
    try {
      await api(`/milestones/${mid}`, { method: "PATCH", body });
      load();
    } catch (e) {
      setH(prev);
      setError((e as Error).message);
    }
  };

  const transition = (field: string, to: string) =>
    run(`trans-${field}-${to}`, () =>
      api(`/hackathons/${id}/transitions`, {
        method: "POST",
        body: {
          field,
          to,
          evidence: to === "REGISTERED" && evidence ? { type: "LINK", reference: evidence } : undefined,
        },
      }),
    );

  if (meError && !me) return <p className="error">{meError}</p>;
  if (loading || meLoading || !h || !me) {
    return (
      <div className="stack">
        <div className="skeleton skeleton-title" style={{ width: 260 }} />
        <div className="skeleton skeleton-card" />
        <div className="skeleton skeleton-card" style={{ height: 200 }} />
      </div>
    );
  }

  const tz = me.timezone;
  const n = h.next_action;
  const canParticipate = h.registration_state === "REGISTERED" || h.registration_state === "ACCEPTED";

  return (
    <div className="stack" style={{ gap: 24 }}>
      <div>
        <Link href="/" className="muted" style={{ display: "inline-flex", alignItems: "center", gap: 4, marginBottom: 12 }}>
          ← Back to Dashboard
        </Link>
        <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
          <div>
            <h1>{h.title}</h1>
            <p className="muted" style={{ margin: "4px 0 0" }}>
              {[h.organizer, h.venue, h.location].filter(Boolean).join(" · ")}
              {h.starts_at && (
                <>
                  {" · "}
                  {h.starts_date ? formatWhen(null, tz, "DATE", h.starts_date) : formatWhen(h.starts_at, tz)} to{" "}
                  {h.ends_date ? formatWhen(null, tz, "DATE", h.ends_date) : formatWhen(h.ends_at, tz)}
                </>
              )}
              {h.source_url && (
                <>
                  {" · "}
                  <a href={h.source_url} target="_blank" rel="noreferrer" style={{ fontWeight: 500 }}>
                    Official event page ↗
                  </a>
                </>
              )}
            </p>
          </div>
          <div className="row">
            <span className="badge ok">{label(h.mode)}</span>
            <span className="badge primary">{label(h.bucket)}</span>
          </div>
        </div>
      </div>

      {error && <p className="error" role="alert">{error}</p>}

      {n && (
        <div className={`next${n.overdue ? " overdue" : ""}`}>
          <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
            <div>
              <div className="row" style={{ gap: 6, marginBottom: 2 }}>
                <strong>Next action:</strong>
                <span>{n.title}</span>
                {n.overdue && <span className="badge warn">Overdue</span>}
              </div>
              {n.type !== "OUTCOME" && (
                <div className="muted">
                  Due {formatWhen(n.due_at, tz, n.due_precision, n.due_date)} · {n.confidence}
                </div>
              )}
            </div>
            {n.milestone_id && (
              <button
                className="primary"
                onClick={() => patchMilestone(n.milestone_id!, { status: "DONE" })}
              >
                ✓ Mark Done
              </button>
            )}
          </div>
        </div>
      )}

      <div className="panel stack">
        <h2>Lifecycle Status</h2>
        <div className="row" style={{ alignItems: "center" }}>
          <span style={{ minWidth: 140, fontWeight: 500 }}>
            Registration: <span className="badge primary">{label(h.registration_state)}</span>
          </span>
          <div className="row">
            {(registrationMoves[h.registration_state] ?? []).map((to) => {
              const busy = busyAction === `trans-registration_state-${to}`;
              return (
                <button
                  key={to}
                  disabled={!!busyAction}
                  className={to === "REGISTERED" ? "primary" : ""}
                  onClick={() => transition("registration_state", to)}
                >
                  {busy ? "Updating…" : to === "REGISTERED" ? "Mark registered" : label(to)}
                </button>
              );
            })}
          </div>
          {(h.registration_state === "PLANNING" || h.registration_state === "DISCOVERED") && (
            <input
              placeholder="Confirmation link (optional)"
              value={evidence}
              onChange={(e) => setEvidence(e.target.value)}
              style={{ flex: 1, minWidth: 200 }}
            />
          )}
        </div>

        {canParticipate && (
          <div className="row" style={{ alignItems: "center" }}>
            <span style={{ minWidth: 140, fontWeight: 500 }}>
              Participation: <span className="badge ok">{label(h.participation_state)}</span>
            </span>
            <div className="row">
              {(participationMoves[h.participation_state] ?? []).map((to) => {
                const busy = busyAction === `trans-participation_state-${to}`;
                return (
                  <button
                    key={to}
                    disabled={!!busyAction}
                    onClick={() => transition("participation_state", to)}
                  >
                    {busy ? "Updating…" : label(to)}
                  </button>
                );
              })}
            </div>
          </div>
        )}

        <div className="row" style={{ alignItems: "center" }}>
          <span style={{ minWidth: 140, fontWeight: 500 }}>
            Outcome:{" "}
            {h.outcome ? (
              <span className="badge ok">{label(h.outcome)}</span>
            ) : (
              <span className="badge">Not set</span>
            )}
          </span>
          <div className="row">
            {!h.outcome &&
              outcomes.map((o) => {
                const busy = busyAction === `trans-outcome-${o}`;
                return (
                  <button
                    key={o}
                    disabled={!!busyAction}
                    onClick={() => transition("outcome", o)}
                  >
                    {busy ? "Updating…" : label(o)}
                  </button>
                );
              })}
          </div>
        </div>
      </div>

      <div>
        <h2>Milestones & Deadlines</h2>
        <ol className="timeline">
          {h.milestones.map((m) => (
            <MilestoneRow
              key={m.id}
              m={m}
              tz={tz}
              patch={(b) => patchMilestone(m.id, b)}
              remove={() => run(`del-${m.id}`, () => api(`/milestones/${m.id}`, { method: "DELETE" }))}
            />
          ))}
          {h.milestones.length === 0 && <li className="muted">No deadlines or milestones added yet.</li>}
        </ol>
        <AddMilestone
          onAdd={(body) => run("add-m", () => api(`/hackathons/${id}/milestones`, { method: "POST", body }))}
        />
      </div>

      {h.history && h.history.length > 0 && (
        <div className="panel stack">
          <h2>Activity Log</h2>
          <ul className="muted" style={{ margin: 0, paddingLeft: 20 }}>
            {h.history.map((t, i) => (
              <li key={i} style={{ marginBottom: 4 }}>
                {formatWhen(t.at, tz)}:{" "}
                {t.entity === "milestone"
                  ? (h.milestones.find((m) => m.id === t.entity_id)?.title ?? "milestone")
                  : label(t.field)}{" "}
                {t.from ? `${label(t.from)} → ` : ""}
                <strong style={{ color: "var(--ink)" }}>{label(t.to)}</strong> ({label(t.actor)})
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="row" style={{ marginTop: 24, justifyContent: "space-between" }}>
        <button
          onClick={() => run("archive", () => api(`/hackathons/${id}`, { method: "PATCH", body: { archived: !h.archived_at } }))}
        >
          {h.archived_at ? "Unarchive Event" : "Archive Event"}
        </button>
        <button
          className="danger"
          onClick={async () => {
            if (!confirm("Are you sure you want to delete this hackathon and all associated milestones?")) return;
            await api(`/hackathons/${id}`, { method: "DELETE" });
            router.push("/");
          }}
        >
          Delete Event
        </button>
      </div>
    </div>
  );
}

function MilestoneRow({
  m,
  tz,
  patch,
  remove,
}: {
  m: Milestone;
  tz: string;
  patch: (b: { status?: "DONE" | "SKIPPED" | "TODO" | "MISSED"; [key: string]: unknown }) => void;
  remove: () => void;
}) {
  const [due, setDue] = useState("");
  const isDone = m.status === "DONE";

  return (
    <li className={`panel${m.status !== "TODO" ? " done" : ""}`}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <div className="row" style={{ gap: 8, marginBottom: 4 }}>
            <strong style={{ textDecoration: isDone ? "line-through" : "none" }}>{m.title}</strong>
            <span className="badge">{label(m.type)}</span>
            {!m.required && <span className="badge">Optional</span>}
            {m.status !== "TODO" && (
              <span className={`badge ${m.status === "MISSED" ? "warn" : "ok"}`}>
                {label(m.status)}
              </span>
            )}
          </div>
          <div className="muted">
            {formatWhen(m.due_at, tz, m.due_precision, m.due_date)}
            {m.due_at && !m.date_confirmed && " · not confirmed, no reminders yet"}
            {m.depart_at && ` · leave by ${formatWhen(m.depart_at, tz)}`}
          </div>
        </div>

        <div className="row">
          {m.status === "TODO" && (
            <button className="primary" onClick={() => patch({ status: "DONE" })}>
              Done
            </button>
          )}
          {m.status === "DONE" && (
            <button onClick={() => patch({ status: "TODO" })}>Reopen</button>
          )}
          <button onClick={() => patch({ status: "SKIPPED" })}>Skip</button>
          <button className="danger" onClick={remove} title="Delete milestone">
            ✕
          </button>
        </div>
      </div>

      <details style={{ marginTop: 10 }}>
        <summary className="muted" style={{ cursor: "pointer", fontSize: "0.85rem" }}>
          Edit deadline / departure
        </summary>
        <div className="row" style={{ marginTop: 8 }}>
          <input
            type="datetime-local"
            value={due}
            onChange={(e) => setDue(e.target.value)}
            aria-label="New deadline"
          />
          <button
            onClick={() => {
              if (due) patch({ due_at: localToISO(due), date_confirmed: true });
            }}
          >
            Update & confirm
          </button>
          <button onClick={() => patch({ clear_due_at: true })}>Clear deadline</button>
        </div>
      </details>
    </li>
  );
}

function AddMilestone({ onAdd }: { onAdd: (b: object) => Promise<boolean> }) {
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({
    type: "SUBMIT",
    title: "",
    due_at: "",
    date_confirmed: true,
    required: true,
  });

  if (!open) {
    return (
      <button onClick={() => setOpen(true)} style={{ marginTop: 12 }}>
        + Add custom milestone
      </button>
    );
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const ok = await onAdd({
      type: f.type,
      title: f.title || f.type,
      due_at: localToISO(f.due_at),
      date_confirmed: f.date_confirmed,
      required: f.required,
    });
    if (ok) {
      setOpen(false);
      setF({ type: "SUBMIT", title: "", due_at: "", date_confirmed: true, required: true });
    }
  }

  return (
    <form className="panel stack" onSubmit={submit} style={{ marginTop: 12 }}>
      <h3>New milestone</h3>
      <div className="row">
        <label style={{ flex: 1 }}>
          Type
          <select value={f.type} onChange={(e) => setF({ ...f, type: e.target.value })}>
            {types.map((t) => (
              <option key={t} value={t}>
                {label(t)}
              </option>
            ))}
          </select>
        </label>
        <label style={{ flex: 2 }}>
          Title
          <input
            placeholder="e.g. Project submission"
            value={f.title}
            onChange={(e) => setF({ ...f, title: e.target.value })}
          />
        </label>
      </div>
      <label>
        Deadline
        <input
          type="datetime-local"
          value={f.due_at}
          onChange={(e) => setF({ ...f, due_at: e.target.value })}
        />
      </label>
      <div className="row">
        <label className="row" style={{ cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={f.required}
            onChange={(e) => setF({ ...f, required: e.target.checked })}
          />
          Required step
        </label>
        <label className="row" style={{ cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={f.date_confirmed}
            onChange={(e) => setF({ ...f, date_confirmed: e.target.checked })}
          />
          Confirmed date
        </label>
      </div>
      <div className="row">
        <button type="submit" className="primary">
          Save milestone
        </button>
        <button type="button" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
    </form>
  );
}
