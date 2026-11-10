"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, formatWhen, label, type Bucket, type Dashboard, type Hackathon } from "@/lib/api";
import { useUser } from "@/lib/UserContext";

const sections: { key: Bucket; title: string; badgeColor?: string }[] = [
  { key: "needs_action", title: "Needs action" },
  { key: "in_progress", title: "In progress" },
  { key: "upcoming", title: "Upcoming" },
  { key: "done", title: "Done" },
  { key: "archived", title: "Archived" },
];

export default function DashboardPage() {
  const { me, loading: meLoading, error: meError } = useUser();
  const [data, setData] = useState<Dashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [mode, setMode] = useState("");
  const [search, setSearch] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api<Dashboard>("/dashboard")
      .then((res) => {
        setData(res);
        setError("");
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (error || meError) {
    return (
      <div className="stack" style={{ maxWidth: 600, margin: "40px auto" }}>
        <p className="error" role="alert">
          {error || meError}
        </p>
        <button onClick={() => window.location.reload()} className="button primary">
          Retry
        </button>
      </div>
    );
  }

  if (loading || meLoading || !data || !me) {
    return (
      <div className="stack">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <div className="skeleton skeleton-title" style={{ width: 180 }} />
          <div className="skeleton" style={{ width: 120, height: 36 }} />
        </div>
        <div className="stats">
          {[1, 2, 3, 4, 5].map((i) => (
            <div key={i} className="skeleton skeleton-stat" />
          ))}
        </div>
        <div className="grid">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="skeleton skeleton-card" />
          ))}
        </div>
      </div>
    );
  }

  const total = Object.values(data.buckets).reduce((n, b) => n + b.length, 0);

  const filterItem = (h: Hackathon) => {
    if (mode && h.mode !== mode) return false;
    if (search.trim()) {
      const q = search.toLowerCase();
      const matchTitle = h.title.toLowerCase().includes(q);
      const matchOrg = (h.organizer || "").toLowerCase().includes(q);
      const matchLoc = (h.location || "").toLowerCase().includes(q);
      if (!matchTitle && !matchOrg && !matchLoc) return false;
    }
    return true;
  };
  const visibleTotal = Object.values(data.buckets).reduce((count, items) => count + items.filter(filterItem).length, 0);

  return (
    <>
      <div className="page-eyebrow"><span>Workspace</span><span aria-hidden="true">/</span><span>Dashboard</span></div>
      <div className="page-header">
        <div>
          <h1>Your hackathons</h1>
          <p className="muted" style={{ margin: 0 }}>
            Keep track of what matters next. Times shown in {me.timezone}.
          </p>
        </div>
        <div className="toolbar">
          <input
            type="search"
            placeholder="Search events…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="Search events"
          />
          <select value={mode} onChange={(e) => setMode(e.target.value)} aria-label="Filter by mode">
            <option value="">All modes</option>
            <option value="ONLINE">Online</option>
            <option value="OFFLINE">Offline</option>
            <option value="HYBRID">Hybrid</option>
          </select>
          <Link href="/hackathons/new" className="button primary">
            + Add event
          </Link>
        </div>
      </div>

      <div className="stats">
        {[
          { key: "registered", label: "Registered" },
          { key: "started", label: "Started" },
          { key: "submitted", label: "Submitted" },
          { key: "missed", label: "Missed" },
          { key: "upcoming", label: "Upcoming" },
        ].map(({ key, label: statLabel }) => (
          <div className="panel" key={key}>
            <strong>{data.stats[key] ?? 0}</strong>
            <span>{statLabel}</span>
          </div>
        ))}
      </div>

      {total === 0 ? (
        <div className="empty-state">
          <div className="empty-icon" aria-hidden="true">✦</div>
          <h2>No hackathons tracked yet</h2>
          <p className="muted" style={{ maxWidth: 420, margin: "0 auto 16px" }}>
            Add an upcoming event manually or capture it directly from Devpost, Unstop, or LinkedIn.
          </p>
          <div className="row" style={{ justifyContent: "center" }}>
            <Link href="/hackathons/new" className="button primary">
              Add your first hackathon
            </Link>
            <Link href="/capture" className="button">
              Capture from link
            </Link>
          </div>
        </div>
      ) : visibleTotal === 0 ? (
        <div className="empty-state">
          <h2>No matching hackathons</h2>
          <p className="muted">Try a different search or show all modes.</p>
          <button type="button" onClick={() => { setSearch(""); setMode(""); }}>Clear filters</button>
        </div>
      ) : (
        sections.map(({ key, title }) => {
          const items = data.buckets[key].filter(filterItem);
          if (items.length === 0) return null;
          return (
            <section key={key}>
              <h2>
                {title} <span className="badge">{items.length}</span>
              </h2>
              <ul className="event-list">
                {items.map((h) => (
                  <li key={h.id}><EventRow h={h} tz={me.timezone} /></li>
                ))}
              </ul>
            </section>
          );
        })
      )}
    </>
  );
}

function EventRow({ h, tz }: { h: Hackathon; tz: string }) {
  const n = h.next_action;
  const modeBadgeClass =
    h.mode === "ONLINE" ? "ok" : h.mode === "HYBRID" ? "primary" : "amber";

  return (
    <Link href={`/hackathons/${h.id}`} className="event-row">
      <span className={`event-indicator${n?.overdue ? " overdue" : ""}`} aria-hidden="true" />
      <div className="event-main">
        <div className="event-heading"><strong className="card-title">{h.title}</strong><span className={`badge ${modeBadgeClass}`}>{label(h.mode)}</span></div>
        <div className="event-meta">
          {h.organizer && <span>{h.organizer}</span>}
          {h.starts_at && <span>Starts {h.starts_date ? formatWhen(null, tz, "DATE", h.starts_date) : formatWhen(h.starts_at, tz)}</span>}
        </div>
      </div>
      <div className="event-action">
        <span className="event-action-label">Next action</span>
        {n ? <>
          <span className="event-action-title">{n.title} {n.overdue && <span className="badge warn">Overdue</span>}</span>
          {n.type !== "OUTCOME" && <span className="event-action-detail">{formatWhen(n.due_at, tz, n.due_precision, n.due_date)} · {n.confidence}</span>}
        </> : <span className="event-action-detail">No pending action</span>}
      </div>
      <div className="event-status">
        <span className="badge">{label(h.registration_state)}</span>
        {h.outcome && <span className="badge ok">{label(h.outcome)}</span>}
        </div>
      <span className="event-arrow" aria-hidden="true">→</span>
    </Link>
  );
}
