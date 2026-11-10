-- +goose Up
ALTER TABLE discord_capture_jobs
  ADD COLUMN registration_requested boolean NOT NULL DEFAULT false,
  ADD COLUMN discovery_event_id uuid REFERENCES discovery_events(id) ON DELETE SET NULL;

-- +goose Down
ALTER TABLE discord_capture_jobs
  DROP COLUMN discovery_event_id,
  DROP COLUMN registration_requested;
