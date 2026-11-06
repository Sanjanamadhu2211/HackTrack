"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";

type Candidate = { id: string; email: { subject: string; from: string; kind: string; hackathon_name?: string; deadline?: string; deadline_date?: string; deadline_precision?: string; deadline_quote?: string } };
export default function InboxPage() {
  const router = useRouter();
  const [items,setItems] = useState<Candidate[]>([]);
  const [events,setEvents] = useState<{id:string;title:string}[]>([]);
  const [address,setAddress] = useState("");
  const [choices,setChoices] = useState<Record<string,string>>({});
  const [titles,setTitles] = useState<Record<string,string>>({});
  const [error,setError] = useState("");
  const [busy,setBusy] = useState(false);
  useEffect(() => {
    Promise.all([api<{items:Candidate[]}>("/inbox"), api<{hackathons:{id:string;title:string}[]}>("/hackathons?summary=1")])
      .then(([inbox, hackathons]) => { setItems(inbox.items); setEvents(hackathons.hackathons); })
      .catch(err => setError((err as Error).message));
  }, []);
  async function addressAction(method: "POST" | "DELETE") {
    setBusy(true); setError("");
    try { if (method === "POST") setAddress((await api<{address:string}>("/integrations/email", {method})).address); else { await api("/integrations/email", {method}); setAddress(""); } }
    catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  async function resolve(item: Candidate, action: "match" | "create" | "ignore") {
    setBusy(true); setError("");
    try {
      const result = await api<{hackathon_id:string}>(`/inbox/${item.id}/resolve`, {method:"POST", body:{action, hackathon_id:choices[item.id] || "", title:titles[item.id] || item.email.hackathon_name || ""}});
      setItems(items.filter(i => i.id !== item.id));
      if (result.hackathon_id) router.push(`/hackathons/${result.hackathon_id}`);
    } catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  return <><h1>Email inbox</h1>
    <div className="panel stack"><h2>Forwarding address</h2>
      <p>Generate an address and forward hackathon emails to it. Generating a new address invalidates the previous one.</p>
      <button type="button" disabled={busy} onClick={() => addressAction("POST")}>Generate forwarding address</button>
      {address && <p><strong>{address}</strong> — copy this address now.</p>}
      <button type="button" disabled={busy} onClick={() => addressAction("DELETE")}>Disconnect forwarding</button>
    </div>
    {error && <p className="error" role="alert">{error}</p>}
    {items.length === 0 && <p>No emails to review.</p>}
    {items.map(item => <section className="panel stack" key={item.id}>
      <h2>{item.email.subject || "Untitled email"}</h2><p>From {item.email.from}; detected {item.email.kind.toLowerCase()}.</p>
      {item.email.deadline && <p>Proposed deadline: {item.email.deadline_precision === "DATE" && item.email.deadline_date ? item.email.deadline_date : item.email.deadline} {item.email.deadline_quote && <q>{item.email.deadline_quote}</q>}</p>}
      <label>Match an event<select value={choices[item.id] || ""} onChange={e => setChoices({...choices,[item.id]:e.target.value})}><option value="">Choose an event</option>{events.map(event => <option key={event.id} value={event.id}>{event.title}</option>)}</select></label>
      <button type="button" disabled={busy || !choices[item.id]} onClick={() => resolve(item,"match")}>Confirm match</button>
      <label>New event title<input value={titles[item.id] ?? item.email.hackathon_name ?? ""} onChange={e => setTitles({...titles,[item.id]:e.target.value})} /></label>
      <button type="button" disabled={busy || !(titles[item.id] ?? item.email.hackathon_name ?? "").trim()} onClick={() => resolve(item,"create")}>Create event</button>
      <button type="button" disabled={busy} onClick={() => resolve(item,"ignore")}>Ignore</button>
      <p className="muted">Confirming may mark the event registered or accepted. Proposed deadlines remain unconfirmed.</p>
    </section>)}
    <p><Link href="/">Back to dashboard</Link></p>
  </>;
}
