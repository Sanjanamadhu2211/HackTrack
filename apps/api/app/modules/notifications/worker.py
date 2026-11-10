import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app import db, reminders, tokens
from app.rules import record_transition

router = APIRouter()

LEASE = timedelta(minutes=2)
MAX_ATTEMPTS = 5
BATCH_SIZE = 20
MAX_BATCHES = 10

log = logging.getLogger(__name__)


def housekeep(conn, now):
    """Mark overdue TODO milestones MISSED and archive old hackathons (in tx)."""
    missed = 0
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, user_id FROM milestones WHERE status = 'TODO' AND date_confirmed "
                "AND due_at < %s AND type <> 'CONFIRM_DATES'",
                (now,),
            )
            rows = cur.fetchall()
            for row in rows:
                cur.execute("UPDATE milestones SET status = 'MISSED', updated_at = now() WHERE id = %s", (row["id"],))
                record_transition(conn, str(row["user_id"]), "milestone", str(row["id"]), "status", "TODO", "MISSED", "SYSTEM")
                reminders.cancel_pending(conn, str(row["id"]))
                missed += 1
            cur.execute(
                "UPDATE hackathons h SET archived_at = now(), updated_at = now() "
                "WHERE h.archived_at IS NULL AND EXISTS ("
                "SELECT 1 FROM state_transitions st WHERE st.entity = 'hackathon' "
                "AND st.entity_id = h.id AND st.field = 'outcome' AND st.at < %s)",
                (now - timedelta(days=14),),
            )
    return missed


def claim(connect_factory, now):
    """Claim one due job; returns {id, attempt_count} or None. attempt_count is the fencing token."""
    with connect_factory() as conn:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE notification_jobs SET state = 'CLAIMED', lease_until = %s + %s, "
                    "attempt_count = attempt_count + 1, updated_at = now() "
                    "WHERE id IN (SELECT id FROM notification_jobs "
                    "WHERE (state = 'PENDING' AND scheduled_at <= %s) "
                    "OR (state = 'CLAIMED' AND lease_until < %s) "
                    "ORDER BY scheduled_at FOR UPDATE SKIP LOCKED LIMIT 1) "
                    "RETURNING id, attempt_count",
                    (now, LEASE, now, now),
                )
                return cur.fetchone()


def _finish(connect_factory, job_id, fence, state, last_error="", scheduled_at=None):
    """Fenced terminal/transition write; True iff this claimant still owns the job."""
    with connect_factory() as conn:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE notification_jobs SET state = %s, last_error = %s, scheduled_at = COALESCE(%s, scheduled_at), "
                    "lease_until = NULL, updated_at = now() "
                    "WHERE id = %s AND state = 'CLAIMED' AND attempt_count = %s",
                    (state, last_error, scheduled_at, job_id, fence),
                )
                return cur.rowcount == 1


def _is_live(hackathon, milestone):
    if hackathon["archived_at"] is not None:
        return False
    if hackathon["registration_state"] in ("SKIPPED", "REJECTED"):
        return False
    outcome = hackathon["outcome"]
    if outcome is None:
        return True
    return outcome == "SUBMITTED" and milestone["type"] != "SUBMIT"


def _invalid_state(job, milestone, hackathon, channel_id, now):
    """None when deliverable, else (state, last_error) terminal outcome."""
    if milestone is None or milestone["status"] != "TODO" or not milestone["date_confirmed"] or not _is_live(hackathon, milestone):
        return "CANCELLED", ""
    due = milestone["due_at"]
    due_limit = due + timedelta(minutes=30) if due and job["offset_label"] == "T-0" else due
    if due is None or now > due_limit or now - job["scheduled_at"] > reminders.staleness_window(job["offset_label"]):
        return "EXPIRED", ""
    if channel_id is None:
        return "FAILED", "Discord not linked"
    return None


def _delivery_target(cur, user_id):
    cur.execute("SELECT channel_id, linked_at FROM discord_links WHERE user_id = %s", (user_id,))
    link = cur.fetchone()
    if link:
        if link["channel_id"]:
            return "DISCORD", link["channel_id"]
        if link["linked_at"] is not None:
            return None, None
    cur.execute("SELECT chat_id FROM telegram_links WHERE user_id = %s", (user_id,))
    link = cur.fetchone()
    if link and link["chat_id"] is not None:
        return "TELEGRAM", link["chat_id"]
    return None, None


def _until(delta):
    if delta < timedelta(0):
        return "now"
    hours = delta.total_seconds() / 3600
    if delta >= timedelta(hours=48):
        return f"in {int(hours // 24)} days"
    if delta >= timedelta(hours=1):
        return f"in {int(hours)} h"
    return f"in {int(delta.total_seconds() // 60)} min"


def reminder_text(job, milestone, hackathon, now):
    if job["offset_label"] == "leave-by":
        head = "Time to leave"
    elif job["final"]:
        head = "Final reminder"
    else:
        head = "Reminder"
    due = milestone["due_at"].astimezone(timezone.utc)
    if milestone["due_precision"] == "DATE":
        due_text = f"{due.strftime('%a')} {due.day} {due.strftime('%b')}, by end of day (assumed)"
    else:
        due_text = f"{due.strftime('%a')} {due.day} {due.strftime('%b')} {due.strftime('%H:%M')} {due.strftime('%Z')} ({_until(milestone['due_at'] - now)})"
    return f"{head}: {milestone['title']}\n{hackathon['title']}\nDue {due_text}"


def _over_cap(cur, user_id, job_id, required, final, prefs, now):
    if final and required:
        return False
    local_now = now.astimezone(prefs.location)
    day_start = datetime(local_now.year, local_now.month, local_now.day, tzinfo=prefs.location)
    day_end = day_start + timedelta(days=1)
    cur.execute(
        "SELECT count(*) AS n FROM notification_deliveries d JOIN notification_jobs j ON j.id = d.job_id "
        "WHERE d.user_id = %s AND d.sent_at >= %s AND d.sent_at < %s",
        (user_id, day_start, day_end),
    )
    sent = cur.fetchone()["n"]
    cur.execute(
        "SELECT count(*) AS n FROM notification_deliveries d JOIN notification_jobs j ON j.id = d.job_id "
        "WHERE d.user_id = %s AND d.sent_at IS NULL AND j.state = 'CLAIMED' AND j.id <> %s "
        "AND d.created_at >= j.updated_at AND d.created_at < %s",
        (user_id, job_id, day_end),
    )
    sent += cur.fetchone()["n"]
    cur.execute(
        "SELECT count(*) AS n FROM notification_jobs j JOIN milestones m ON m.id = j.milestone_id "
        "WHERE j.user_id = %s AND j.id <> %s AND j.state IN ('PENDING', 'CLAIMED') "
        "AND j.scheduled_at >= %s AND j.scheduled_at < %s AND m.status = 'TODO' AND m.required AND j.final",
        (user_id, job_id, day_start, day_end),
    )
    finals = cur.fetchone()["n"]
    budget = prefs.cap_per_day - finals
    if not required:
        cur.execute(
            "SELECT count(*) AS n FROM notification_jobs j JOIN milestones m ON m.id = j.milestone_id "
            "WHERE j.user_id = %s AND j.id <> %s AND j.state IN ('PENDING', 'CLAIMED') "
            "AND j.scheduled_at >= %s AND j.scheduled_at < %s AND m.status = 'TODO' AND m.required AND NOT j.final",
            (user_id, job_id, day_start, day_end),
        )
        budget -= cur.fetchone()["n"]
    return sent >= budget


def process(connect_factory, job_id, fence, sender):
    """Process one claimed job; True iff a reminder was sent."""
    now = datetime.now(timezone.utc)
    job_id = str(job_id)
    # Phase 1: load under fence.
    with connect_factory() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM notification_jobs WHERE id = %s", (job_id,))
            job = cur.fetchone()
            if job is None or job["state"] != "CLAIMED" or job["attempt_count"] != fence:
                return False
            cur.execute("SELECT * FROM milestones WHERE id = %s", (job["milestone_id"],))
            milestone = cur.fetchone()
            if milestone is None:
                return False
            cur.execute("SELECT * FROM hackathons WHERE id = %s", (milestone["hackathon_id"],))
            hackathon = cur.fetchone()
            user_id = str(job["user_id"])
            platform, channel_id = _delivery_target(cur, user_id)
            prefs = reminders.load_prefs(conn, user_id)
    invalid = _invalid_state(job, milestone, hackathon, channel_id, now)
    if invalid:
        _finish(connect_factory, job_id, fence, invalid[0], invalid[1])
        return False
    text = reminder_text(job, milestone, hackathon, now)
    open_url = hackathon["source_url"]
    milestone_id = str(milestone["id"])
    milestone_version = milestone["version"]
    required = bool(milestone["required"])
    final = bool(job["final"])

    # Phase 2: reservation tx; insert delivery as the reservation.
    delivery_id = None
    with connect_factory() as conn:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM milestones WHERE id = %s FOR UPDATE", (milestone_id,))
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"reminder-cap:{user_id}",))
                cur.execute("SELECT * FROM notification_jobs WHERE id = %s FOR UPDATE", (job_id,))
                current = cur.fetchone()
                if current is None or current["state"] != "CLAIMED" or current["attempt_count"] != fence:
                    return False
                cur.execute("SELECT version, status, date_confirmed, due_at, type, hackathon_id, required, title, due_precision FROM milestones WHERE id = %s", (milestone_id,))
                mrow = cur.fetchone()
                if mrow is None or mrow["version"] != milestone_version:
                    return False
                cur.execute("SELECT * FROM hackathons WHERE id = %s", (mrow["hackathon_id"],))
                hackathon2 = cur.fetchone()
                platform2, channel2 = _delivery_target(cur, user_id)
                if _invalid_state(current, mrow, hackathon2, channel2, now) is not None:
                    return False
                if _over_cap(cur, user_id, job_id, required, final, prefs, now):
                    cur.execute(
                        "UPDATE notification_jobs SET state = 'CANCELLED', last_error = 'daily cap reached', lease_until = NULL, updated_at = now() "
                        "WHERE id = %s AND state = 'CLAIMED' AND attempt_count = %s",
                        (job_id, fence),
                    )
                    return False
                cur.execute(
                    "INSERT INTO notification_deliveries (user_id, job_id) VALUES (%s, %s) RETURNING id",
                    (user_id, job_id),
                )
                delivery_id = str(cur.fetchone()["id"])

    # Phase 3: reload after reservation.
    changed = False
    with connect_factory() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM notification_jobs WHERE id = %s", (job_id,))
            job2 = cur.fetchone()
            if job2 is None or job2["state"] != "CLAIMED" or job2["attempt_count"] != fence:
                return False
            cur.execute("SELECT * FROM milestones WHERE id = %s", (milestone_id,))
            milestone2 = cur.fetchone()
            platform2, channel2 = _delivery_target(cur, user_id)
            if (
                milestone2 is None
                or milestone2["version"] != milestone_version
                or _invalid_state(job2, milestone2, hackathon, channel2, now) is not None
                or channel2 != channel_id
                or platform2 != platform
            ):
                changed = True
    if changed:
        with connect_factory() as conn:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE notification_jobs SET state = 'CANCELLED', last_error = 'reminder changed before delivery', "
                        "lease_until = NULL, updated_at = now() WHERE id = %s AND state = 'CLAIMED' AND attempt_count = %s",
                        (job_id, fence),
                    )
                    if cur.rowcount == 1:
                        cur.execute("DELETE FROM notification_deliveries WHERE id = %s", (delivery_id,))
        return False

    try:
        message_id = sender[platform].send_reminder(channel_id, text, delivery_id, open_url)
    except Exception as error:
        if fence >= MAX_ATTEMPTS:
            _finish(connect_factory, job_id, fence, "FAILED", str(error))
        else:
            _finish(connect_factory, job_id, fence, "PENDING", "", now + timedelta(minutes=2**fence))
        return False
    with connect_factory() as conn:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE notification_jobs SET state = 'SENT', lease_until = NULL, updated_at = now() "
                    "WHERE id = %s AND state = 'CLAIMED' AND attempt_count = %s",
                    (job_id, fence),
                )
                if cur.rowcount != 1:
                    return False
                cur.execute(
                    "UPDATE notification_deliveries SET provider_message_id = %s, sent_at = %s WHERE id = %s",
                    (str(message_id), now, delivery_id),
                )
    return True


def tick(connect_factory, sender):
    """conn-less tick: housekeep first, then claim and process one job at a time."""
    now = datetime.now(timezone.utc)
    with connect_factory() as conn:
        missed = housekeep(conn, now)
    if sender is None:
        return {"claimed": 0, "sent": 0, "missed": missed}
    claimed = 0
    sent = 0
    while claimed < BATCH_SIZE * MAX_BATCHES:
        job = claim(connect_factory, datetime.now(timezone.utc))
        if job is None:
            break
        claimed += 1
        try:
            if process(connect_factory, job["id"], job["attempt_count"], sender):
                sent += 1
        except Exception as error:
            log.exception("reminder processing failed for job %s: %s", job["id"], error)
            continue
    return {"claimed": claimed, "sent": sent, "missed": missed}


def _cron_authorized(request):
    secret = _cron_secret()
    if not secret:
        return False
    return tokens.constant_time_equal(request.headers.get("authorization", ""), f"Bearer {secret}")


def _cron_secret():
    from app.config import load_config

    try:
        return load_config().cron_secret
    except RuntimeError:
        return ""


def _default_sender():
    from app.config import load_config

    cfg = load_config()
    senders = {}
    if cfg.discord_bot_token:
        from app.modules.integrations.discord.client import Sender

        senders["DISCORD"] = Sender(cfg.discord_bot_token)
    if cfg.telegram_bot_token:
        from app.telegram import Sender

        senders["TELEGRAM"] = Sender(cfg.telegram_bot_token)
    return senders or None


@router.api_route("/cron/reminders", methods=["GET", "POST"])
async def cron_handler(request: Request):
    if not _cron_authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        result = tick(db.connect, _default_sender())
    except Exception:
        log.exception("cron tick failed")
        return JSONResponse({"error": "internal error"}, status_code=500)
    return JSONResponse(result, status_code=200)


@router.get("/cron/lag")
async def lag_handler(request: Request):
    if not _cron_authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        with db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) AS n, COALESCE(EXTRACT(EPOCH FROM now() - min(scheduled_at)), 0) AS lag "
                    "FROM notification_jobs WHERE state = 'PENDING' AND scheduled_at < now()"
                )
                row = cur.fetchone()
    except Exception:
        log.exception("lag check failed")
        return JSONResponse({"error": "internal error"}, status_code=500)
    return JSONResponse({"overdue_jobs": int(row["n"]), "oldest_lag_seconds": float(row["lag"])}, status_code=200)
