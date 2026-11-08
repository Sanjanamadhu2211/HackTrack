"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";

type Candidate = {
  id: string;
  email: {
    subject: string;
    from: string;
    kind: string;
    hackathon_name?: string;
    deadline?: string;
    deadline_date?: string;
    deadline_precision?: string;
    deadline_quote?: string;
  };
};

export default function InboxPage() {
  const router = useRouter();
  const [items, setItems] = useState<Candidate[]>([]);
  const [events, setEvents] = useState<{ id: string; title: string }[]>([]);
  const [address, setAddress] = useState("");
  const [choices, setChoices] = useState<Record<string, string>>({});
  const [titles, setTitles] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    Promise.all([
      api<{ items: Candidate[] }>("/inbox"),
      api<{ hackathons: { id: string; title: string }[] }>("/hackathons?summary=1"),
    ])
      .then(([inbox, hackathons]) => {
        setItems(inbox.items);
        setEvents(hackathons.hackathons);
      })
      .catch((err) => setError((err as Error).message));
  }, []);

  async function addressAction(method: "POST" | "DELETE") {
    setBusy(true);
    setError("");
    try {
      if (method === "POST") {
        const res = await api<{ address: string }>("/integrations/email", { method });
        setAddress(res.address);
      } else {
        await api("/integrations/email", { method });
        setAddress("");
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function resolve(item: Candidate, action: "match" | "create" | "ignore") {
    setBusy(true);
    setError("");
    try {
      const result = await api<{ hackathon_id: string }>(`/inbox/${item.id}/resolve`, {
        method: "POST",
        body: {
          action,
          hackathon_id: choices[item.id] || "",
          title: titles[item.id] || item.email.hackathon_name || "",
        },
      });
      setItems(items.filter((i) => i.id !== item.id));
      if (result.hackathon_id) router.push(`/hackathons/${result.hackathon_id}`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const copyAddress = () => {
    if (!address) return;
    navigator.clipboard.writeText(address);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="stack" style={{ maxWidth: 760, margin: "0 auto", gap: 24 }}>
      <div>
        <h1>Email Inbox</h1>
        <p className="muted">
          Forward confirmation and acceptance emails from Devpost, Unstop, or organizers to automatically update your milestones.
        </p>
      </div>

      <div className="panel stack">
        <h2>Inbound Email Forwarding</h2>
        <p className="muted">
          Generate a personal email address. Any email forwarded to this address will be parsed and queued below for review.
        </p>

        {address ? (
          <div className="panel" style={{ background: "var(--panel)", display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 10 }}>
            <div>
              <span className="muted" style={{ display: "block", fontSize: "0.8rem" }}>Your Forwarding Address:</span>
              <strong style={{ fontSize: "1.05rem" }}>{address}</strong>
            </div>
            <div className="row">
              <button type="button" onClick={copyAddress} className="primary">
                {copied ? "Copied!" : "Copy Address"}
              </button>
              <button type="button" disabled={busy} onClick={() => addressAction("DELETE")} className="danger">
                Disconnect
              </button>
            </div>
          </div>
        ) : (
          <button type="button" disabled={busy} onClick={() => addressAction("POST")} className="primary">
            Generate forwarding address
          </button>
        )}
      </div>

      {error && <p className="error" role="alert">{error}</p>}

      <h2>Pending Emails ({items.length})</h2>

      {items.length === 0 ? (
        <div className="empty-state">
          <div className="empty-icon">📬</div>
          <p className="muted">No emails waiting for review. You&apos;re all caught up!</p>
        </div>
      ) : (
        items.map((item) => (
          <section className="panel stack" key={item.id}>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <strong style={{ fontSize: "1.1rem" }}>{item.email.subject || "Untitled Email"}</strong>
              <span className="badge primary">{item.email.kind.toLowerCase()}</span>
            </div>

            <p className="muted" style={{ margin: 0 }}>
              From: {item.email.from}
            </p>

            {item.email.deadline && (
              <div className="next" style={{ margin: "4px 0" }}>
                <strong>Detected Deadline:</strong>{" "}
                {item.email.deadline_precision === "DATE" && item.email.deadline_date
                  ? item.email.deadline_date
                  : item.email.deadline}
                {item.email.deadline_quote && (
                  <p className="faint" style={{ margin: "4px 0 0" }}>
                    &ldquo;{item.email.deadline_quote}&rdquo;
                  </p>
                )}
              </div>
            )}

            <div className="row" style={{ marginTop: 8, gap: 16 }}>
              <div style={{ flex: 1 }}>
                <label>
                  Match to existing hackathon
                  <select
                    value={choices[item.id] || ""}
                    onChange={(e) => setChoices({ ...choices, [item.id]: e.target.value })}
                  >
                    <option value="">Select an event</option>
                    {events.map((ev) => (
                      <option key={ev.id} value={ev.id}>
                        {ev.title}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  type="button"
                  style={{ marginTop: 8 }}
                  disabled={busy || !choices[item.id]}
                  onClick={() => resolve(item, "match")}
                  className="primary"
                >
                  Link to Event
                </button>
              </div>

              <div style={{ flex: 1 }}>
                <label>
                  Or create as new event
                  <input
                    value={titles[item.id] ?? item.email.hackathon_name ?? ""}
                    onChange={(e) => setTitles({ ...titles, [item.id]: e.target.value })}
                    placeholder="Event title"
                  />
                </label>
                <button
                  type="button"
                  style={{ marginTop: 8 }}
                  disabled={busy || !(titles[item.id] ?? item.email.hackathon_name ?? "").trim()}
                  onClick={() => resolve(item, "create")}
                >
                  Create New Event
                </button>
              </div>
            </div>

            <div className="row" style={{ justifyContent: "flex-end", marginTop: 8 }}>
              <button
                type="button"
                disabled={busy}
                onClick={() => resolve(item, "ignore")}
                className="danger"
              >
                Ignore Email
              </button>
            </div>
          </section>
        ))
      )}

      <p>
        <Link href="/">← Back to dashboard</Link>
      </p>
    </div>
  );
}
