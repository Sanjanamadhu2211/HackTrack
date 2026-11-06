-- +goose Up
CREATE TABLE calendar_oauth_states (
  user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  state_hash bytea NOT NULL,
  expires_at timestamptz NOT NULL
);
-- +goose Down
DROP TABLE calendar_oauth_states;
