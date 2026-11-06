import { test } from "node:test";
import assert from "node:assert/strict";
import {
  __clearPendingCreateMemoryForTest,
  canonicalCreateJson,
  clearPendingCreate,
  fingerprintCreatePayload,
  mintIdempotencyKey,
  pendingCreateKey,
} from "./pendingCreate.ts";

class FakeStorage implements Storage {
  private map = new Map<string, string>();
  get length() { return this.map.size; }
  clear() { this.map.clear(); }
  getItem(key: string) { return this.map.get(key) ?? null; }
  key(index: number) { return [...this.map.keys()][index] ?? null; }
  removeItem(key: string) { this.map.delete(key); }
  setItem(key: string, value: string) { this.map.set(key, String(value)); }
}

function installStorage(): FakeStorage {
  const fake = new FakeStorage();
  Object.defineProperty(globalThis, "sessionStorage", { value: fake, configurable: true });
  return fake;
}

test("canonical JSON ignores key order and omitted-vs-explicit defaults", () => {
  const omitted = { title: "Hack", milestones: [] };
  const explicit = {
    milestones: [],
    title: "Hack",
    organizer: "",
    mode: "ONLINE",
    event_timezone: "UTC",
    venue: "",
    location: "",
    description: "",
    registration_state: "PLANNING",
  };
  assert.equal(canonicalCreateJson(omitted), canonicalCreateJson(explicit));
});

test("a meaningful change changes the fingerprint", async () => {
  const a = await fingerprintCreatePayload({ title: "Hack" });
  const b = await fingerprintCreatePayload({ title: "Hack", location: "Hall A" });
  assert.notEqual(a, b);
});

test("identical retry reuses the persisted key; changed draft gets a new one", async () => {
  const store = installStorage();
  const payload = { title: "No URL event", mode: "ONLINE" };
  const first = await pendingCreateKey(payload);
  // Simulated reload: only storage survives, so the key must come back.
  __clearPendingCreateMemoryForTest();
  assert.equal(await pendingCreateKey(payload), first);
  const changed = await pendingCreateKey({ title: "Different event", mode: "ONLINE" });
  assert.notEqual(changed, first);
  // Only keys/fingerprints are persisted, never event content.
  for (const value of storeEntries(store)) {
    assert.ok(!value.includes("No URL event"), `stored ${value}`);
  }
});

test("clears only after confirmed success: next retry mints a fresh key", async () => {
  const store = installStorage();
  const payload = { title: "Again" };
  const first = await pendingCreateKey(payload);
  await clearPendingCreate(payload);
  assert.equal(store.getItem(`hacktrack.pendingCreate.v1.${await fingerprintCreatePayload(payload)}`), null);
  assert.notEqual(await pendingCreateKey(payload), first);
});

test("without storage it still mints ephemeral keys (SSR-safe)", async () => {
  Object.defineProperty(globalThis, "sessionStorage", { value: undefined, configurable: true });
  const key = await pendingCreateKey({ title: "No storage" });
  assert.ok(typeof key === "string" && key.length > 0);
});

test("minted keys are UUID-shaped", () => {
  assert.match(mintIdempotencyKey(), /^[0-9a-f-]{36}$/);
});

function storeEntries(store: FakeStorage): string[] {
  const out: string[] = [];
  for (let i = 0; i < store.length; i++) {
    const v = store.getItem(store.key(i)!);
    if (v) out.push(v);
  }
  return out;
}
