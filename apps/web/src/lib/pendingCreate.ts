// Per-tab idempotency identity for event creation.
//
// A component-local key dies with the page, so a response committed by the
// server but lost before it arrived becomes a duplicate when the same draft
// is re-entered (worst for no-URL drafts, which the server's duplicate-URL
// guard cannot catch). Instead, the key for a create payload is persisted
// in sessionStorage under a stable fingerprint of the canonical payload:
//
// - Same payload across a reload, a re-render or a re-preview reuses the
//   same key, so the server returns the original event instead of a second
//   one. A changed draft fingerprints differently and gets a fresh key.
// - Only the fingerprint and the key are stored — never event content —
//   keyed per payload, in sessionStorage only, so tabs stay separate
//   (sessionStorage is per-tab; localStorage would share keys across tabs)
//   and an abandoned draft leaves only an unguessable key behind, which the
//   server only ever maps back to an event for the same payload.
// - The entry is cleared only after the server confirms the create.
//   Clearing earlier (on error, on unmount) would drop a key whose event
//   may already have committed and re-open the duplicate. If storage is
//   unavailable (SSR, blocked storage), an in-memory map covers retries
//   within the current page load and each call mints an ephemeral key
//   otherwise, so the forms still work.
//
// The client canonicalization mirrors the server's canonical fingerprint
// (apps/api/internal/hackathons/store.go): omitted fields and explicit
// defaults hash to the same fingerprint, and anything that changes the
// stored event changes the fingerprint. Recovery without retyping is
// therefore limited to re-running the same input (e.g. re-previewing the
// same capture URL/text) — deliberately, since persisting drafts would
// store personal content in browser storage.

const STORAGE_PREFIX = "hacktrack.pendingCreate.v1.";
const DEFAULTS: Record<string, unknown> = {
  organizer: "",
  source_url: "",
  mode: "ONLINE",
  venue: "",
  location: "",
  event_timezone: "UTC",
  starts_date: "",
  ends_date: "",
  description: "",
  registration_state: "PLANNING",
};

// In-memory fallback for when sessionStorage is unavailable: retries within
// this page load behave exactly as if storage worked.
const memory = new Map<string, string>();

// Test-only: drop the in-memory map, as if the page had reloaded.
export function __clearPendingCreateMemoryForTest() {
  memory.clear();
}

// The payload is the exact JSON body of the create POST, minus the
// idempotency key itself.
export type PendingCreatePayload = Record<string, unknown>;

function canonicalize(value: unknown, key?: string): unknown {
  if (value === undefined) return undefined;
  if (value === null || typeof value !== "object") {
    if (key !== undefined && value === DEFAULTS[key]) return undefined;
    return value;
  }
  if (Array.isArray(value)) {
    return value.map((item) => canonicalize(item));
  }
  const out: Record<string, unknown> = {};
  for (const k of Object.keys(value).sort()) {
    const v = canonicalize((value as Record<string, unknown>)[k], k);
    if (v !== undefined) out[k] = v;
  }
  return out;
}

/** Canonical JSON of a create payload (defaults folded, keys sorted). */
export function canonicalCreateJson(payload: PendingCreatePayload): string {
  const canonical = canonicalize({ ...payload }) as Record<string, unknown>;
  delete canonical.idempotency_key;
  return JSON.stringify(canonical);
}

function toHex(bytes: ArrayBuffer): string {
  return [...new Uint8Array(bytes)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** Stable fingerprint of the canonical payload, independent of key order
 * and of omitted-vs-explicit defaults. */
export async function fingerprintCreatePayload(payload: PendingCreatePayload): Promise<string> {
  const json = canonicalCreateJson(payload);
  const subtle = globalThis.crypto?.subtle;
  if (subtle?.digest) {
    return "sha256-" + toHex(await subtle.digest("SHA-256", new TextEncoder().encode(json)));
  }
  // Deterministic fallback for environments without WebCrypto (e.g. plain
  // Node test runs): a simple stable hash is enough — a collision only
  // means the user's retry reuses an existing key, which the server then
  // rejects if the payloads actually differ.
  let h1 = 0x811c9dc5;
  let h2 = 0x01000193;
  for (let i = 0; i < json.length; i++) {
    const c = json.charCodeAt(i);
    h1 = Math.imul(h1 ^ c, 16777619) >>> 0;
    h2 = Math.imul(h2 ^ (c + i), 16777619) >>> 0;
  }
  return `fnv-${h1.toString(16)}${h2.toString(16)}`;
}

export function mintIdempotencyKey(): string {
  const cryptoObj = globalThis.crypto;
  if (cryptoObj?.randomUUID) return cryptoObj.randomUUID();
  const bytes = new Uint8Array(16);
  if (cryptoObj?.getRandomValues) cryptoObj.getRandomValues(bytes);
  else for (let i = 0; i < bytes.length; i++) bytes[i] = (Math.random() * 256) | 0;
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function storage(): Storage | null {
  try {
    return globalThis.sessionStorage ?? null;
  } catch {
    return null; // accessing sessionStorage can throw in some browsers
  }
}

/** Idempotency key for this create payload: reuses the persisted pending
 * key for the same payload, mints and persists one after a changed draft,
 * and returns a fresh ephemeral key when nothing can be persisted. */
export async function pendingCreateKey(payload: PendingCreatePayload): Promise<string> {
  const fingerprint = await fingerprintCreatePayload(payload);
  const inMemory = memory.get(fingerprint);
  if (inMemory) return inMemory;
  const store = storage();
  if (store) {
    try {
      const existing = store.getItem(STORAGE_PREFIX + fingerprint);
      if (existing) {
        memory.set(fingerprint, existing);
        return existing;
      }
    } catch {
      // fall through to minting with the in-memory map only
    }
  }
  const key = mintIdempotencyKey();
  memory.set(fingerprint, key);
  if (store) {
    try {
      store.setItem(STORAGE_PREFIX + fingerprint, key);
    } catch {
      // storage unavailable: the in-memory map still anchors retries
    }
  }
  return key;
}

/** Clear the pending identity for this payload. Call only after the server
 * has confirmed the create. */
export async function clearPendingCreate(payload: PendingCreatePayload): Promise<void> {
  const fingerprint = await fingerprintCreatePayload(payload);
  memory.delete(fingerprint);
  const store = storage();
  if (store) {
    try {
      store.removeItem(STORAGE_PREFIX + fingerprint);
    } catch {
      // nothing persisted, nothing to clear
    }
  }
}
