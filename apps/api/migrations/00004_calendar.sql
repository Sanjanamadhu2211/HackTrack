-- +goose Up
CREATE TABLE calendar_connections (
  user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  refresh_token bytea NOT NULL,
  calendar_id text NOT NULL,
  last_error text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE calendar_events (
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  milestone_id uuid NOT NULL,
  content_hash text NOT NULL,
  PRIMARY KEY (user_id, milestone_id)
);
-- +goose Down
DROP TABLE calendar_events;
DROP TABLE calendar_connections;
