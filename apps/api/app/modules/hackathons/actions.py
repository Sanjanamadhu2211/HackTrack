from app import reminders, rules
from app.errors import NotFound

from app.modules.hackathons.router import _ensure_confirm_dates, transition_for_user


def register_hackathon(user_id, hackathon_id):
    return transition_for_user(
        user_id, hackathon_id, "registration_state", "REGISTERED", {}, idempotent_registration=True
    )


def confirm_dates(connect_factory, user_id, hackathon_id, now):
    with connect_factory() as conn:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM hackathons WHERE id = %s AND user_id = %s FOR UPDATE", (hackathon_id, user_id))
                if not cur.fetchone():
                    raise NotFound()
                cur.execute(
                    "SELECT id FROM milestones WHERE hackathon_id = %s AND user_id = %s "
                    "AND status = 'TODO' AND due_precision <> 'UNKNOWN' AND NOT date_confirmed FOR UPDATE",
                    (hackathon_id, user_id),
                )
                ids = [str(row["id"]) for row in cur.fetchall()]
                for milestone_id in ids:
                    cur.execute(
                        "UPDATE milestones SET date_confirmed = TRUE, version = version + 1, updated_at = now() "
                        "WHERE id = %s AND user_id = %s",
                        (milestone_id, user_id),
                    )
                    rules.record_transition(conn, user_id, "milestone", milestone_id, "date_confirmed", "false", "true", "USER")
                    reminders.regenerate(conn, milestone_id, now)
                _ensure_confirm_dates(conn, user_id, hackathon_id, now)
                return len(ids)
