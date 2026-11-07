// Thin client for the Python FastAPI app. Every call goes to /api on this origin;
// next.config.ts proxies it to the API service.

export type Mode = "ONLINE" | "OFFLINE" | "HYBRID";

export type Milestone = {
  id: string;
  hackathon_id: string;
  type: string;
  title: string;
  due_at: string | null;
  due_date?: string;
  due_precision: "DATETIME" | "DATE" | "UNKNOWN";
  date_confirmed: boolean;
  required: boolean;
  status: "TODO" | "DONE" | "SKIPPED" | "MISSED";
  completed_at: string | null;
  source: string;
  depart_at: string | null;
  version: number;
};

export type NextAction = {
  milestone_id: string | null;
  type: string;
  title: string;
  due_at: string | null;
  due_date?: string;
  due_precision?: string;
  overdue: boolean;
  confidence: string;
};

export type Hackathon = {
  id: string;
  title: string;
  organizer: string;
  source_url: string;
  mode: Mode;
  venue: string;
  location: string;
  event_timezone: string;
  starts_at: string | null;
  starts_date?: string;
  ends_at: string | null;
  ends_date?: string;
  description: string;
  registration_state: string;
  participation_state: string;
  outcome: string | null;
  archived_at: string | null;
  created_at: string;
  milestones: Milestone[];
  next_action: NextAction | null;
  bucket: Bucket;
};

export type HistoryEntry = {
  entity: "hackathon" | "milestone";
  entity_id: string;
  field: string;
  from: string | null;
  to: string;
  actor: string;
  at: string;
};

export type Detail = Hackathon & { history: HistoryEntry[] };

export type Bucket = "needs_action" | "upcoming" | "in_progress" | "done" | "archived";

export type Dashboard = {
  buckets: Record<Bucket, Hackathon[]>;
  stats: Record<string, number>;
};

export type Me = {
  id: string;
  email: string;
  name: string;
  timezone: string;
  quiet_hours_start: string;
  quiet_hours_end: string;
  reminder_cap_per_day: number;
  telegram_linked: boolean;
};

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function api<T = unknown>(path: string, init?: { method?: string; body?: unknown }): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: init?.method ?? "GET",
    credentials: "same-origin",
    headers: init?.body === undefined ? undefined : { "Content-Type": "application/json" },
    body: init?.body === undefined ? undefined : JSON.stringify(init.body),
  });
  if (res.status === 401 && typeof window !== "undefined" && window.location.pathname !== "/login") {
    // Outside React there is no router; a full load also drops stale state.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.assign("/login");
  }
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new ApiError(res.status, data.error ?? `Request failed (${res.status})`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

/** Formats a civil date (YYYY-MM-DD) as written, without timezone conversion. */
export function formatCivil(civil?: string): string {
  if (!civil) return "";
  const d = new Date(civil + "T12:00:00Z");
  if (Number.isNaN(d.getTime())) return civil;
  return new Intl.DateTimeFormat(undefined, { timeZone: "UTC", weekday: "short", day: "numeric", month: "short" }).format(d);
}

/** Formats an instant in the user's timezone. Date-only values use the civil
 * date (event timezone) when supplied, so the day never shifts across zones. */
export function formatWhen(iso: string | null, tz: string, precision?: string, civil?: string): string {
  if (precision === "DATE" && civil) return formatCivil(civil) + ", end of day";
  if (!iso) return civil ? formatCivil(civil) : "Date unknown";
  const d = new Date(iso);
  if (precision === "DATE") {
    return new Intl.DateTimeFormat(undefined, { timeZone: tz, weekday: "short", day: "numeric", month: "short" }).format(d) + ", end of day";
  }
  return new Intl.DateTimeFormat(undefined, {
    timeZone: tz,
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(d);
}

/** Converts a datetime-local input value (browser time) to RFC 3339, or undefined. */
export function localToISO(v: string): string | undefined {
  return v ? new Date(v).toISOString() : undefined;
}

export const label = (s: string) => s.charAt(0) + s.slice(1).toLowerCase().replaceAll("_", " ");
