-- +goose Up
-- Fence for calendar sync: every sync run claims a new generation; only the
-- latest generation for the captured calendar may record progress. A
-- disconnect/reconnect or a newer overlapping run makes older runs' commits
-- no-ops instead of overwriting newer state.
ALTER TABLE calendar_connections ADD COLUMN sync_generation bigint NOT NULL DEFAULT 0;

-- Per-user idempotency for atomic event creation (see hackathons.Create).
-- One row per (user, key): the first request creates the event, retries with
-- the same key return that event instead of creating a second one. The
-- fingerprint guards against reusing a key for a different payload. Rows
-- cascade away with the event so a deleted event does not pin its key.
CREATE TABLE hackathon_create_requests (
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  idempotency_key text NOT NULL,
  fingerprint text NOT NULL,
  hackathon_id uuid NOT NULL REFERENCES hackathons(id) ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, idempotency_key)
);

-- +goose Down
DROP TABLE hackathon_create_requests;
ALTER TABLE calendar_connections DROP COLUMN sync_generation;
