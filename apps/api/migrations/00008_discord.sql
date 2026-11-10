-- +goose Up
CREATE TABLE discord_links (
  user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  discord_user_id text UNIQUE,
  channel_id text UNIQUE,
  pairing_token_hash bytea UNIQUE,
  pairing_expires_at timestamptz,
  linked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE discord_capture_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  channel_id text NOT NULL,
  url text NOT NULL,
  interaction_id text NOT NULL UNIQUE,
  state text NOT NULL DEFAULT 'PENDING' CHECK (state IN ('PENDING','CLAIMED','DONE','FAILED')),
  attempt_count integer NOT NULL DEFAULT 0,
  lease_until timestamptz,
  next_attempt_at timestamptz NOT NULL DEFAULT now(),
  last_error text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX discord_capture_jobs_due_idx ON discord_capture_jobs (created_at) WHERE state IN ('PENDING','CLAIMED');

CREATE TABLE discovery_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_url text NOT NULL,
  normalized_url text NOT NULL UNIQUE,
  title text NOT NULL,
  source text NOT NULL,
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE discovery_deliveries (
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  event_id uuid NOT NULL REFERENCES discovery_events(id) ON DELETE CASCADE,
  digest_date date NOT NULL,
  state text NOT NULL DEFAULT 'PENDING' CHECK (state IN ('PENDING','SENT')),
  delivered_at timestamptz NOT NULL DEFAULT now(),
  tracked_at timestamptz,
  PRIMARY KEY (user_id, event_id)
);

CREATE TABLE discord_digest_runs (
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  digest_date date NOT NULL,
  message_id text NOT NULL DEFAULT '',
  state text NOT NULL DEFAULT 'PENDING' CHECK (state IN ('PENDING','CLAIMED','SENT','FAILED')),
  attempt_count integer NOT NULL DEFAULT 0,
  next_attempt_at timestamptz NOT NULL DEFAULT now(),
  lease_until timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, digest_date)
);
CREATE INDEX discord_digest_runs_due_idx ON discord_digest_runs (next_attempt_at) WHERE state IN ('PENDING','CLAIMED');

ALTER TABLE discord_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE discord_capture_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE discovery_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE discovery_deliveries ENABLE ROW LEVEL SECURITY;
ALTER TABLE discord_digest_runs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON discord_links, discord_capture_jobs, discovery_events, discovery_deliveries, discord_digest_runs FROM PUBLIC;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    REVOKE ALL ON discord_links, discord_capture_jobs, discovery_events, discovery_deliveries, discord_digest_runs FROM anon;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
    REVOKE ALL ON discord_links, discord_capture_jobs, discovery_events, discovery_deliveries, discord_digest_runs FROM authenticated;
  END IF;
END $$;

UPDATE notification_jobs SET channel = 'DISCORD' WHERE channel = 'TELEGRAM' AND state IN ('PENDING','CLAIMED');
ALTER TABLE notification_jobs ALTER COLUMN channel SET DEFAULT 'DISCORD';

-- +goose Down
ALTER TABLE notification_jobs ALTER COLUMN channel SET DEFAULT 'TELEGRAM';
UPDATE notification_jobs SET channel = 'TELEGRAM' WHERE channel = 'DISCORD' AND state IN ('PENDING','CLAIMED');
DROP TABLE discord_digest_runs;
DROP TABLE discovery_deliveries;
DROP TABLE discovery_events;
DROP TABLE discord_capture_jobs;
DROP TABLE discord_links;
