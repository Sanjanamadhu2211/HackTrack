-- +goose Up
CREATE TABLE users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  google_sub text UNIQUE,
  email text NOT NULL UNIQUE,
  name text NOT NULL DEFAULT '',
  timezone text NOT NULL DEFAULT 'UTC',
  quiet_hours_start time NOT NULL DEFAULT '23:00',
  quiet_hours_end time NOT NULL DEFAULT '08:00',
  reminder_cap_per_day int NOT NULL DEFAULT 6,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sessions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash bytea NOT NULL UNIQUE,
  expires_at timestamptz NOT NULL,
  revoked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE telegram_links (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
  chat_id bigint UNIQUE,
  pairing_token_hash bytea UNIQUE,
  pairing_expires_at timestamptz,
  linked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE hackathons (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title text NOT NULL,
  organizer text NOT NULL DEFAULT '',
  source_url text NOT NULL DEFAULT '',
  normalized_url text,
  platform text NOT NULL DEFAULT '',
  mode text NOT NULL DEFAULT 'ONLINE' CHECK (mode IN ('ONLINE','OFFLINE','HYBRID')),
  venue text NOT NULL DEFAULT '',
  location text NOT NULL DEFAULT '',
  event_timezone text NOT NULL DEFAULT 'UTC',
  starts_at timestamptz,
  ends_at timestamptz,
  description text NOT NULL DEFAULT '',
  registration_state text NOT NULL DEFAULT 'PLANNING' CHECK (registration_state IN
    ('DISCOVERED','PLANNING','REGISTERED','ACCEPTED','WAITLISTED','REJECTED','SKIPPED')),
  participation_state text NOT NULL DEFAULT 'NOT_STARTED' CHECK (participation_state IN
    ('NOT_STARTED','PREPARING','ACTIVE','ENDED')),
  outcome text CHECK (outcome IN
    ('SUBMITTED','ATTENDED_NO_SUBMISSION','WITHDRAWN','MISSED','EVENT_CANCELLED')),
  archived_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, normalized_url)
);

CREATE TABLE registration_evidence (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  hackathon_id uuid NOT NULL REFERENCES hackathons(id) ON DELETE CASCADE,
  type text NOT NULL CHECK (type IN ('MANUAL','EMAIL','LINK')),
  reference text NOT NULL DEFAULT '',
  note text NOT NULL DEFAULT '',
  confirmed_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE milestones (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  hackathon_id uuid NOT NULL REFERENCES hackathons(id) ON DELETE CASCADE,
  type text NOT NULL CHECK (type IN
    ('REGISTER','RSVP','TEAM','CHECK_IN','START','SUBMIT','PRESENT','CONFIRM_DATES','OTHER')),
  title text NOT NULL,
  due_at timestamptz,
  due_precision text NOT NULL DEFAULT 'DATETIME' CHECK (due_precision IN ('DATETIME','DATE','UNKNOWN')),
  date_confirmed boolean NOT NULL DEFAULT false,
  required boolean NOT NULL DEFAULT true,
  status text NOT NULL DEFAULT 'TODO' CHECK (status IN ('TODO','DONE','SKIPPED','MISSED')),
  completed_at timestamptz,
  source text NOT NULL DEFAULT 'USER' CHECK (source IN ('USER','EXTRACTED','EMAIL')),
  extraction_run_id uuid,
  depart_at timestamptz, -- user-set "leave by" time for offline CHECK_IN / START
  version int NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX milestones_hackathon_idx ON milestones (hackathon_id);
CREATE INDEX milestones_due_idx ON milestones (due_at) WHERE status = 'TODO';
CREATE INDEX hackathons_user_idx ON hackathons (user_id);

CREATE TABLE notification_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  milestone_id uuid NOT NULL REFERENCES milestones(id) ON DELETE CASCADE,
  channel text NOT NULL DEFAULT 'TELEGRAM',
  offset_label text NOT NULL,
  final boolean NOT NULL DEFAULT false, -- last reminder for this milestone version; never dropped by the cap
  scheduled_at timestamptz NOT NULL,
  state text NOT NULL DEFAULT 'PENDING' CHECK (state IN
    ('PENDING','CLAIMED','SENT','FAILED','CANCELLED','EXPIRED')),
  attempt_count int NOT NULL DEFAULT 0,
  lease_until timestamptz,
  idempotency_key text NOT NULL UNIQUE,
  last_error text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX notification_jobs_due_idx ON notification_jobs (scheduled_at) WHERE state IN ('PENDING','CLAIMED');

CREATE TABLE notification_deliveries (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  job_id uuid NOT NULL REFERENCES notification_jobs(id) ON DELETE CASCADE,
  provider_message_id text NOT NULL DEFAULT '',
  sent_at timestamptz,
  acknowledged_at timestamptz,
  action text CHECK (action IN ('DONE','SNOOZE','OPEN')),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX notification_deliveries_job_idx ON notification_deliveries (job_id);
CREATE INDEX notification_deliveries_user_sent_idx ON notification_deliveries (user_id, sent_at);

CREATE TABLE state_transitions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  entity text NOT NULL CHECK (entity IN ('hackathon','milestone')),
  entity_id uuid NOT NULL,
  field text NOT NULL,
  from_state text,
  to_state text NOT NULL,
  actor text NOT NULL CHECK (actor IN ('USER','SYSTEM','EMAIL')),
  at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX state_transitions_entity_idx ON state_transitions (entity, entity_id);

-- +goose Down
DROP TABLE state_transitions;
DROP TABLE notification_deliveries;
DROP TABLE notification_jobs;
DROP TABLE milestones;
DROP TABLE registration_evidence;
DROP TABLE hackathons;
DROP TABLE telegram_links;
DROP TABLE sessions;
DROP TABLE users;
