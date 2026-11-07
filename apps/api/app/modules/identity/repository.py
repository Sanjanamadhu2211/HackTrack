from datetime import datetime, timedelta, timezone
from app.core import database, security
from app.core.errors import NotFound
from app.modules.identity.domain import SESSION_DAYS


def create_session(conn, user_id: str):
    token, token_hash = security.new_token()
    expiry = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (%s, %s, %s)",
            (user_id, token_hash, expiry),
        )
    return token, expiry


def find_user_id_by_session(token_raw: str) -> str | None:
    token_hash = security.hash_token(token_raw)
    with database.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id FROM sessions WHERE token_hash = %s AND revoked_at IS NULL AND expires_at > now()",
                (token_hash,),
            )
            row = cur.fetchone()
            return str(row["user_id"]) if row else None


def revoke_session(token_raw: str) -> None:
    token_hash = security.hash_token(token_raw)
    with database.connect() as conn:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE sessions SET revoked_at = now(), updated_at = now() "
                    "WHERE token_hash = %s AND revoked_at IS NULL",
                    (token_hash,),
                )


def get_user_profile(user_id: str) -> dict:
    with database.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT u.id, u.email, u.name, u.timezone, "
                "to_char(u.quiet_hours_start,'HH24:MI') AS qs, "
                "to_char(u.quiet_hours_end,'HH24:MI') AS qe, u.reminder_cap_per_day, "
                "EXISTS(SELECT 1 FROM telegram_links t WHERE t.user_id = u.id AND t.chat_id IS NOT NULL) AS linked "
                "FROM users u WHERE u.id = %s",
                (user_id,),
            )
            row = cur.fetchone()
    if not row:
        raise NotFound()
    return {
        "id": str(row["id"]),
        "email": row["email"],
        "name": row["name"],
        "timezone": row["timezone"],
        "quiet_hours_start": row["qs"],
        "quiet_hours_end": row["qe"],
        "reminder_cap_per_day": row["reminder_cap_per_day"],
        "telegram_linked": bool(row["linked"]),
    }


def upsert_google_user(claims: dict) -> tuple[str, str, datetime]:
    with database.connect() as conn:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM users WHERE google_sub = %s", (claims["sub"],))
                row = cur.fetchone()
                if not row:
                    cur.execute("SELECT id FROM users WHERE email = %s", (claims["email"].lower(),))
                    row = cur.fetchone()

                if row:
                    user_id = row["id"]
                    cur.execute(
                        "UPDATE users SET google_sub = %s, email = %s, name = COALESCE(NULLIF(%s, ''), name), updated_at = now() WHERE id = %s",
                        (claims["sub"], claims["email"].lower(), claims.get("name", ""), user_id),
                    )
                else:
                    cur.execute(
                        "INSERT INTO users (google_sub, email, name) VALUES (%s, %s, %s) RETURNING id",
                        (claims["sub"], claims["email"].lower(), claims.get("name", "")),
                    )
                    user_id = cur.fetchone()["id"]
                token, expiry = create_session(conn, str(user_id))
                return str(user_id), token, expiry


def upsert_dev_user(email: str) -> tuple[str, str, datetime]:
    with database.connect() as conn:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (email, name) VALUES (%s, %s) "
                    "ON CONFLICT (email) DO UPDATE SET updated_at = now() RETURNING id",
                    (email, email),
                )
                user_id = cur.fetchone()["id"]
                token, expiry = create_session(conn, str(user_id))
                return str(user_id), token, expiry


def update_user_settings(user_id: str, timezone_val: str | None, qs: str | None, qe: str | None, cap: int | None) -> None:
    with database.connect() as conn:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET timezone = COALESCE(%s, timezone), "
                    "quiet_hours_start = COALESCE(%s::time, quiet_hours_start), "
                    "quiet_hours_end = COALESCE(%s::time, quiet_hours_end), "
                    "reminder_cap_per_day = COALESCE(%s, reminder_cap_per_day), updated_at = now() WHERE id = %s",
                    (timezone_val, qs, qe, cap, user_id),
                )


def export_user_data(user_id: str) -> dict:
    with database.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT jsonb_build_object("
                "'profile', (to_jsonb(u) - 'google_sub'), "
                "'hackathons', COALESCE(jsonb_agg(to_jsonb(h)) FILTER (WHERE h.id IS NOT NULL), '[]'::jsonb), "
                "'milestones', COALESCE(jsonb_agg(to_jsonb(m)) FILTER (WHERE m.id IS NOT NULL), '[]'::jsonb), "
                "'registration_evidence', COALESCE(jsonb_agg(to_jsonb(e)) FILTER (WHERE e.id IS NOT NULL), '[]'::jsonb), "
                "'state_transitions', COALESCE(jsonb_agg(to_jsonb(s)) FILTER (WHERE s.id IS NOT NULL), '[]'::jsonb), "
                "'inbound_emails', COALESCE(jsonb_agg(to_jsonb(i)) FILTER (WHERE i.id IS NOT NULL), '[]'::jsonb), "
                "'notification_jobs', COALESCE(jsonb_agg(to_jsonb(j)) FILTER (WHERE j.id IS NOT NULL), '[]'::jsonb), "
                "'notification_deliveries', COALESCE(jsonb_agg(to_jsonb(d)) FILTER (WHERE d.id IS NOT NULL), '[]'::jsonb)) "
                "FROM users u "
                "LEFT JOIN hackathons h ON h.user_id = u.id "
                "LEFT JOIN milestones m ON m.user_id = u.id "
                "LEFT JOIN registration_evidence e ON e.user_id = u.id "
                "LEFT JOIN state_transitions s ON s.user_id = u.id "
                "LEFT JOIN inbound_emails i ON i.user_id = u.id "
                "LEFT JOIN notification_jobs j ON j.user_id = u.id "
                "LEFT JOIN notification_deliveries d ON d.user_id = u.id "
                "WHERE u.id = %s",
                (user_id,),
            )
            row = cur.fetchone()
    if not row:
        raise NotFound()
    return row["jsonb_build_object"]


def delete_user_account(user_id: str) -> None:
    with database.connect() as conn:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM users WHERE id = %s FOR UPDATE", (user_id,))
                if not cur.fetchone():
                    raise NotFound()
                cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
