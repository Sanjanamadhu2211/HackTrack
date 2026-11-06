-- +goose Up
ALTER TABLE hackathons ADD COLUMN starts_date DATE, ADD COLUMN ends_date DATE;
ALTER TABLE milestones ADD COLUMN due_date DATE;
UPDATE milestones SET due_date = (due_at AT TIME ZONE (SELECT event_timezone FROM hackathons WHERE id = milestones.hackathon_id))::date
  WHERE due_precision = 'DATE' AND due_at IS NOT NULL AND due_date IS NULL;

-- +goose Down
ALTER TABLE milestones DROP COLUMN due_date;
ALTER TABLE hackathons DROP COLUMN starts_date, DROP COLUMN ends_date;
