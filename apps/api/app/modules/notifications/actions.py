from app import reminders
from app.errors import NotFound
from app.rules import record_transition


class Ack:
    def __init__(self, kind, snoozed_until=None):
        self.kind = kind
        self.snoozed_until = snoozed_until


def ack_delivery(connect_factory, user_id, delivery_id, action, choice, now):
    """Apply a reminder button action for its owning user."""
    with connect_factory() as conn:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM notification_deliveries WHERE id = %s AND user_id = %s FOR UPDATE",
                    (delivery_id, user_id),
                )
                delivery = cur.fetchone()
                if delivery is None:
                    raise NotFound()
                if delivery["acknowledged_at"] is not None:
                    return Ack("repeated")
                cur.execute("SELECT milestone_id FROM notification_jobs WHERE id = %s", (delivery["job_id"],))
                job = cur.fetchone()
                if job is None:
                    raise NotFound()
                milestone_id = str(job["milestone_id"])
                cur.execute("SELECT * FROM milestones WHERE id = %s FOR UPDATE", (milestone_id,))
                milestone = cur.fetchone()
                if milestone is None:
                    raise NotFound()
                cur.execute(
                    "UPDATE notification_deliveries SET acknowledged_at = %s, action = %s, updated_at = now() WHERE id = %s",
                    (now, action, str(delivery["id"])),
                )
                if action == "DONE":
                    if milestone["status"] in ("TODO", "MISSED"):
                        cur.execute(
                            "UPDATE milestones SET status = 'DONE', completed_at = %s, updated_at = now() WHERE id = %s",
                            (now, milestone_id),
                        )
                        record_transition(conn, user_id, "milestone", milestone_id, "status", milestone["status"], "DONE", "USER")
                        reminders.cancel_pending(conn, milestone_id)
                    return Ack("done")
                if milestone["status"] != "TODO":
                    return Ack("snooze_no_time")
                return Ack("snoozed", reminders.snooze(conn, user_id, milestone_id, str(delivery["id"]), choice, now))
