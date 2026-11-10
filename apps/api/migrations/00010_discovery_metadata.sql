-- +goose Up
ALTER TABLE discovery_events
  ADD COLUMN mode text NOT NULL DEFAULT '',
  ADD COLUMN start_date date,
  ADD COLUMN end_date date,
  ADD COLUMN registration_end date;

-- +goose Down
ALTER TABLE discovery_events
  DROP COLUMN registration_end,
  DROP COLUMN end_date,
  DROP COLUMN start_date,
  DROP COLUMN mode;
