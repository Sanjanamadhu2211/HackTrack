"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, formatWhen, label, type Bucket, type Dashboard, type Hackathon } from "@/lib/api";
import { useMe } from "@/lib/useMe";

const sections: { key: Bucket; title: string }[] = [
  { key: "needs_action", title: "Needs action" },
  { key: "in_progress", title: "In progress" },
  { key: "upcoming", title: "Upcoming" },
  { key: "done", title: "Done" },
  { key: "archived", title: "Archived" },
];

export default function DashboardPage() {
  const [me, , meError] = useMe();
  const [data, setData] = useState<Dashboard | null>(null);
  const [mode, setMode] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api<Dashboard>("/dashboard").then(setData, (e) => setError(e.message));
  }, []);

  if (error || meError) return <p className="error">{error || meError}</p>;
  if (!data || !me) return <p className="muted">Loading…</p>;

  const total = Object.values(data.buckets).reduce((n, b) => n + b.length, 0);
  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>Dashboard</h1>
        <div className="row">
          <select value={mode} onChange={(e) => setMode(e.target.value)} aria-label="Filter by mode">
            <option value="">All modes</option>
            <option value="ONLINE">Online</option>
            <option value="OFFLINE">Offline</option>
            <option value="HYBRID">Hybrid</option>
          </select>
          <Link href="/hackathons/new" className="button primary">
            Add event
          </Link>
        </div>
      </div>
      <div className="stats">
        {["registered", "started", "submitted", "missed", "upcoming"].map((k) => (
          <div className="panel" key={k}>
            <strong>{data.stats[k] ?? 0}</strong>
            <span className="muted">{label(k)}</span>
          </div>
        ))}
      </div>
      {total === 0 && (
        <p className="muted" style={{ marginTop: 24 }}>
          No events yet. <Link href="/hackathons/new">Add your first hackathon</Link>.
        </p>
      )}
      {sections.map(({ key, title }) => {
        const items = data.buckets[key].filter((h) => !mode || h.mode === mode);
        if (items.length === 0) return null;
        return (
          <section key={key}>
            <h2>
              {title} <span className="muted">({items.length})</span>
            </h2>
            <div className="grid">
              {items.map((h) => (
                <EventCard key={h.id} h={h} tz={me.timezone} />
              ))}
            </div>
          </section>
        );
      })}
    </>
  );
}

function EventCard({ h, tz }: { h: Hackathon; tz: string }) {
  const n = h.next_action;
  return (
    <Link href={`/hackathons/${h.id}`} className="panel card">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <strong>{h.title}</strong>
        <span className="badge">{label(h.mode)}</span>
      </div>
      <div className="muted">
        {label(h.registration_state)}
        {h.outcome ? ` · ${label(h.outcome)}` : ""}
        {h.starts_at ? ` · starts ${h.starts_date ? formatWhen(null, tz, "DATE", h.starts_date) : formatWhen(h.starts_at, tz)}` : ""}
      </div>
      {n && (
        <div className={`next${n.overdue ? " overdue" : ""}`}>
          <div>
            <strong>Next:</strong> {n.title}
          </div>
          {n.type !== "OUTCOME" && (
            <div className="muted">
              {n.overdue ? "Overdue · " : ""}
              {formatWhen(n.due_at, tz, n.due_precision, n.due_date)} · {n.confidence}
            </div>
          )}
        </div>
      )}
    </Link>
  );
}
