-- +goose Up
CREATE TABLE inbound_addresses (user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, token_hash bytea NOT NULL UNIQUE);
CREATE TABLE inbound_emails (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 message_id text NOT NULL, parsed_json jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 resolved_at timestamptz, UNIQUE (user_id, message_id)
);
CREATE TABLE inbound_webhook_tokens (token_hash bytea PRIMARY KEY, seen_at timestamptz NOT NULL DEFAULT now());
-- +goose Down
DROP TABLE inbound_webhook_tokens;
DROP TABLE inbound_emails;
DROP TABLE inbound_addresses;
