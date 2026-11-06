-- +goose Up
CREATE TABLE capture_ai_usage (
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  day date NOT NULL,
  calls integer NOT NULL,
  PRIMARY KEY (user_id, day)
);
-- +goose Down
DROP TABLE capture_ai_usage;
