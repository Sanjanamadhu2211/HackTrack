import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app import db, reminders, sender
from app.config import load_config
from app.errors import Invalid

router = APIRouter()

LEASE = timedelta(minutes=2)
MAX_ATTEMPTS = 5


def _sender():
    cfg = load_config()
    if not cfg.telegram_bot_token:
        return None
    return sender


def claim(conn, now, limit=20):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE notification_jobs SET state='CLAIMED', lease_until=%s, attempt_count=attempt_count+1, updated_at=now() "
            "WHERE id IN (SELECT id FROM notification_jobs WHERE (state='PENDING' AND scheduled_at<=%s) OR (state='CLAIMED' AND lease_until<%s) "
            "ORDER BY scheduled_at FOR UPDATE SKIP LOCKED LIMIT %s) RETURNING id, attempt_count",
            (now + LEASE, now, now, limit),
        )
        return cur.fetchall()


def housekeep(conn, now):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT m.id, m.user_id FROM milestones m WHERE m.status='TODO' AND m.date_confirmed AND m.due_at IS NOT NULL AND m.due_at<%s AND m.type<>'CONFIRM_DATES'",
            (now,),
        )
        for row in cur.fetchall():
            cur.execute("UPDATE milestones SET status='MISSED', updated_at=now() WHERE id=%s", (row["id"],))
            cur.execute(
                "INSERT INTO state_transitions (user_id, entity, entity_id, field, from_state, to_state, actor) "
                "VALUES (%s,'milestone',%s,'status','TODO','MISSED','SYSTEM')",
                (row["user_id"], row["id"]),
            )
            from app.reminders import cancel_pending

            cancel_pending(conn, str(row["id"]))
    with conn.cursor() as cur:
        cur.execute(
            "SELECT h.id FROM hackathons h WHERE h.outcome IS NOT NULL AND h.archived_at IS NULL "
            "AND EXISTS (SELECT 1 FROM state_transitions s WHERE s.entity='hackathon' AND s.entity_id=h.id AND s.field='outcome' AND s.at<%s)",
            (now - timedelta(days=14),),
        )
        for row in cur.fetchall():
            cur.execute("UPDATE hackathons SET archived_at=now(), updated_at=now() WHERE id=%s", (row["id"],))


def tick(now=None):
    now = now or datetime.now(timezone.utc)
    claimed = sent = missed = 0
    with db.connect() as conn:
        with conn:
            housekeep(conn, now)
    active_sender = _sender()
    for _ in range(200):
        with db.connect() as conn:
            with conn:
                jobs = claim(conn, now, limit=1)
        if not jobs:
            break
        claimed += 1
        job = jobs[0]
        try:
            kind = process_job(str(job["id"]), job["attempt_count"], now, active_sender)
            if kind == "SENT":
                sent += 1
        except Exception:
            pass
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS c FROM notification_jobs WHERE state='EXPIRED' AND updated_at>=%s", (now - timedelta(seconds=5),))
            missed = int(cur.fetchone()["c"] or 0)
    return {"claimed": claimed, "sent": sent, "missed": missed}


def process_job(job_id, fence, now, active_sender):
    with db.connect() as conn:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT j.*, m.type, m.title, m.due_at, m.due_precision, m.required, m.status, m.date_confirmed, m.version, "
                    "h.title AS hack_title, h.archived_at, h.registration_state, h.outcome, h.event_timezone, "
                    "t.chat_id, u.timezone AS user_tz "
                    "FROM notification_jobs j JOIN milestones m ON m.id=j.milestone_id JOIN hackathons h ON h.id=m.hackathon_id "
                    "JOIN users u ON u.id=j.user_id LEFT JOIN telegram_links t ON t.user_id=j.user_id "
                    "WHERE j.id=%s AND j.state='CLAIMED' AND j.attempt_count=%s FOR UPDATE OF j, m",
                    (job_id, fence),
                )
                row = cur.fetchone()
                if not row:
                    return "LOST"
                milestone_id = str(row["milestone_id"])
                live = (
                    row["status"] == "TODO"
                    and row["date_confirmed"]
                    and row["archived_at"] is None
                    and row["registration_state"] not in ("SKIPPED", "REJECTED")
                    and (row["outcome"] is None or (row["outcome"] == "SUBMITTED" and row["type"] != "SUBMIT"))
                )
                if not live:
                    finish(cur, job_id, fence, "CANCELLED", "")
                    return "CANCELLED"
                due = row["due_at"]
                due_limit = due + timedelta(minutes=30) if row["offset_label"] == "T-0" else due
                if due is None or now > due_limit or now - row["scheduled_at"] > reminders.staleness_window(row["offset_label"]):
                    finish(cur, job_id, fence, "EXPIRED", "")
                    return "EXPIRED"
                chat_id = row["chat_id"]
                if chat_id is None:
                    finish(cur, job_id, fence, "FAILED", "telegram not linked")
                    return "FAILED"
                prefs = reminders.load_prefs(conn, str(row["user_id"]))
                cur.execute("SELECT pg_advisory_xact_lock(hashtextextended('reminder-cap:' || %s::text, 0))", (str(row["user_id"]),))
                user_tz = _tz(row["user_tz"])
                day_start = datetime(now.year, now.month, now.day, tzinfo=user_tz).astimezone(timezone.utc)
                if over_cap(cur, row, str(row["user_id"]), job_id, now, day_start, prefs):
                    finish(cur, job_id, fence, "CANCELLED", "daily cap reached")
                    return "CANCELLED"
                cur.execute(
                    "INSERT INTO notification_deliveries (user_id, job_id) VALUES (%s, %s) RETURNING id",
                    (row["user_id"], job_id),
                )
                delivery_id = str(cur.fetchone()["id"])
                version = row["version"]
                text = reminder_text(row, now)
                open_url = f"{load_config().app_base_url}/hackathons/{row['hackathon_id']}"
    if active_sender is None:
        with db.connect() as conn:
            with conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE notification_jobs SET state='PENDING', lease_until=NULL, updated_at=now() WHERE id=%s AND attempt_count=%s", (job_id, fence))
        return "PENDING"
    try:
        message_id = sender.send_reminder(chat_id, text, delivery_id, open_url)
    except Exception as e:
        attempts = fence
        if attempts >= MAX_ATTEMPTS:
            with db.connect() as conn:
                with conn:
                    with conn.cursor() as cur:
                        finish(cur, job_id, fence, "FAILED", str(e)[:500])
            return "FAILED"
        backoff = now + timedelta(minutes=2**attempts)
        with db.connect() as conn:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE notification_jobs SET state='PENDING', lease_until=NULL, scheduled_at=%s, last_error=%s, updated_at=now() "
                        "WHERE id=%s AND state='CLAIMED' AND attempt_count=%s",
                        (backoff, str(e)[:500], job_id, fence),
                    )
        return "RETRY"
    with db.connect() as conn:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE notification_deliveries SET provider_message_id=%s, sent_at=now(), updated_at=now() WHERE id=%s",
                    (message_id, delivery_id),
                )
                finish(cur, job_id, fence, "SENT", "")
    return "SENT"


def finish(cur, job_id, fence, state, error):
    cur.execute(
        "UPDATE notification_jobs SET state=%s, lease_until=NULL, last_error=%s, updated_at=now() "
        "WHERE id=%s AND state='CLAIMED' AND attempt_count=%s",
        (state, error, job_id, fence),
    )


def over_cap(cur, row, user_id, current_job_id, now, day_start, prefs):
    if row["final"] and row["required"]:
        return False
    cur.execute(
        "SELECT count(*) AS sent FROM notification_deliveries d JOIN notification_jobs j ON j.id=d.job_id "
        "WHERE d.user_id=%s AND ((d.sent_at>=%s AND d.sent_at<%s) OR "
        "(d.sent_at IS NULL AND j.state='CLAIMED' AND j.id<>%s AND d.created_at>=j.updated_at AND d.created_at<%s))",
        (user_id, day_start, day_start + timedelta(days=1), current_job_id, day_start + timedelta(days=1)),
    )
    sent_count = int(cur.fetchone()["sent"] or 0)
    cur.execute(
        "SELECT count(*) AS finals FROM notification_jobs j JOIN milestones m ON m.id=j.milestone_id "
        "WHERE j.user_id=%s AND j.id<>%s AND j.state IN ('PENDING','CLAIMED') AND j.scheduled_at>=%s AND j.scheduled_at<%s "
        "AND m.status='TODO' AND m.required AND j.final",
        (user_id, current_job_id, day_start, day_start + timedelta(days=1)),
    )
    finals = int(cur.fetchone()["finals"] or 0)
    budget = prefs.cap_per_day - finals
    if not row["required"]:
        cur.execute(
            "SELECT count(*) AS req FROM notification_jobs j JOIN milestones m ON m.id=j.milestone_id "
            "WHERE j.user_id=%s AND j.id<>%s AND j.state IN ('PENDING','CLAIMED') AND j.scheduled_at>=%s AND j.scheduled_at<%s "
            "AND m.status='TODO' AND m.required AND NOT j.final",
            (user_id, current_job_id, day_start, day_start + timedelta(days=1)),
        )
        budget -= int(cur.fetchone()["req"] or 0)
    return sent_count >= budget


def _tz(name):
    try:
        return ZoneInfo(name)
    except Exception:
        return timezone.utc


def reminder_text(row, now):
    if row["offset_label"] == "leave-by":
        head = "Time to leave"
    elif row["final"]:
        head = "Final reminder"
    else:
        head = "Reminder"
    due = row["due_at"]
    if row["due_precision"] == "DATE" and due:
        when = due.astimezone(_tz(row["event_timezone"])).strftime("%a %d %b") + ", by end of day (assumed)"
    else:
        when = due.strftime("%a %d %b %H:%M %Z") if due else "unknown"
    delta = due - now if due else timedelta(0)
    total_minutes = int(delta.total_seconds() // 60)
    if total_minutes < 0:
        until = "now"
    elif total_minutes >= 48 * 60:
        until = f"in {total_minutes // (24 * 60)} days"
    elif total_minutes >= 60:
        until = f"in {total_minutes // 60} h"
    else:
        until = f"in {total_minutes} min"
    return f"{head}: {row['title']}\n{row['hack_title']}\nDue {when} ({until})"


def _cron_auth(request: Request):
    secret = os.getenv("CRON_SECRET", "")
    header = request.headers.get("authorization", "")
    if not secret or header != f"Bearer {secret}":
        return False
    import hashlib

    return hashlib.sha256(header.encode()).digest() == hashlib.sha256(f"Bearer {secret}".encode()).digest()


@router.api_route("/cron/reminders", methods=["GET", "POST"])
def cron_reminders(request: Request):
    if not _cron_auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        return tick()
    except Exception:
        return JSONResponse({"error": "internal error"}, status_code=500)


@router.api_route("/cron/lag", methods=["GET"])
def cron_lag(request: Request):
    if not _cron_auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        with db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) AS c, COALESCE(EXTRACT(EPOCH FROM now()-min(scheduled_at)),0) AS lag "
                    "FROM notification_jobs WHERE state='PENDING' AND scheduled_at<now()"
                )
                row = cur.fetchone()
    except Exception:
        return JSONResponse({"error": "internal error"}, status_code=500)
    return {"overdue_jobs": int(row["c"] or 0), "oldest_lag_seconds": float(row["lag"] or 0)}
