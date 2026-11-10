import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from fastapi.responses import JSONResponse

from app import db
from app.core.config import load_config
from app.errors import Invalid
from app.modules.capture.service import capture_data
from app.modules.hackathons.actions import register_hackathon
from app.modules.hackathons.router import create_hackathon_for_user
from app.normalize import normalize_url

log = logging.getLogger(__name__)
LEASE = timedelta(minutes=2)
MAX_ATTEMPTS = 5
MAX_PER_TICK = 2


def capture_payload(url, draft, job_id):
    try:
        ZoneInfo(draft.get("timezone") or "UTC")
        event_timezone = draft.get("timezone") or "UTC"
    except Exception:
        event_timezone = "UTC"
    milestones = []
    for item in draft.get("milestones", []):
        precision = item.get("due_precision", "UNKNOWN")
        milestone = {
            "type": item.get("type", "OTHER"),
            "title": item.get("title", ""),
            "due_precision": precision,
            "date_confirmed": False,
        }
        if precision == "DATE":
            milestone["due_date"] = item.get("due_date") or (item.get("due_at") or "")[:10]
        elif precision == "DATETIME":
            milestone["due_at"] = item.get("due_at")
        milestones.append(milestone)
    title = (draft.get("title") or "").strip() or (urlparse(url).hostname or "Hackathon")
    return {
        "title": title[:200],
        "organizer": (draft.get("organizer") or "")[:200],
        "source_url": url,
        "mode": draft.get("mode") if draft.get("mode") in ("ONLINE", "OFFLINE", "HYBRID") else "ONLINE",
        "location": (draft.get("location") or "")[:300],
        "event_timezone": event_timezone,
        "starts_at": draft.get("starts_at") or None,
        "starts_date": draft.get("starts_date") or None,
        "ends_at": draft.get("ends_at") or None,
        "ends_date": draft.get("ends_date") or None,
        "registration_state": "PLANNING",
        "milestones": milestones,
        "idempotency_key": f"discord-capture:{job_id}",
    }


def claim(connect_factory, now):
    with connect_factory() as conn:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE discord_capture_jobs SET state = 'CLAIMED', lease_until = %s + %s, "
                    "attempt_count = attempt_count + 1, updated_at = now() "
                    "WHERE id = (SELECT id FROM discord_capture_jobs WHERE "
                    "(state = 'PENDING' AND next_attempt_at <= %s) OR (state = 'CLAIMED' AND lease_until < %s) "
                    "ORDER BY next_attempt_at, created_at FOR UPDATE SKIP LOCKED LIMIT 1) "
                    "RETURNING id, user_id, channel_id, url, attempt_count, registration_requested, discovery_event_id",
                    (now, LEASE, now, now),
                )
                return cur.fetchone()


def _finish(connect_factory, job, state, error="", retry=False):
    delay = timedelta(minutes=2 ** job["attempt_count"])
    next_attempt = datetime.now(timezone.utc) + delay if retry else None
    with connect_factory() as conn:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE discord_capture_jobs SET state = %s, last_error = %s, next_attempt_at = COALESCE(%s, next_attempt_at), "
                    "lease_until = NULL, updated_at = now() WHERE id = %s AND state = 'CLAIMED' AND attempt_count = %s",
                    (state, error[:200], next_attempt, job["id"], job["attempt_count"]),
                )


def _existing_event(connect_factory, user_id, normalized_url):
    with connect_factory() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM hackathons WHERE user_id = %s AND normalized_url = %s",
                (user_id, normalized_url),
            )
            row = cur.fetchone()
    return str(row["id"]) if row else ""


def _capture_message(connect_factory, job):
    user_id = str(job["user_id"])
    registration_requested = job.get("registration_requested", False)
    missing_details = False
    try:
        result = capture_data(user_id, job["url"])
    except Invalid:
        if not registration_requested or not job.get("discovery_event_id"):
            raise
        with connect_factory() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT title FROM discovery_events WHERE id = %s", (job["discovery_event_id"],))
                event = cur.fetchone()
        if not event:
            raise
        result = {"draft": {"title": event["title"], "milestones": []}, "normalized_url": normalize_url(job["url"])}
        missing_details = True

    hackathon_id = result.get("duplicate_id")
    payload = None
    if not hackathon_id:
        payload = capture_payload(job["url"], result["draft"], str(job["id"]))
        created = create_hackathon_for_user(user_id, payload, milestone_source="EXTRACTED")
        if isinstance(created, JSONResponse) and created.status_code == 409:
            hackathon_id = _existing_event(connect_factory, user_id, result.get("normalized_url"))
            if not hackathon_id:
                raise Invalid("that event is already tracked")
        elif isinstance(created, dict) and created.get("id"):
            hackathon_id = created["id"]
        else:
            raise RuntimeError("event could not be saved")

    detail = None
    if registration_requested:
        detail = register_hackathon(user_id, hackathon_id)
        state = detail["registration_state"]
        content = "Registration recorded in HackTrack." if state == "REGISTERED" else f"Registration already recorded; current status: {state}."
    else:
        content = f"Added to HackTrack: {payload['title']}" if payload else "Already tracking that link."
    app_url = f"{load_config().app_base_url}/hackathons/{hackathon_id}"
    milestones = detail.get("milestones", []) if detail else (payload["milestones"] if payload else [])
    dates = [m for m in milestones if m.get("status", "TODO") == "TODO" and not m.get("date_confirmed") and (m.get("due_at") or m.get("due_date"))]
    content += f"\n{app_url}"
    if dates and len(dates) <= 5:
        content += "\nExtracted dates to confirm:"
        for milestone in dates:
            content += f"\n• {milestone.get('title', 'Deadline')[:100]}: {str(milestone.get('due_date') or milestone.get('due_at'))[:40]}"
    elif dates or (payload is None and detail is None):
        content += "\nReview the event's dates in HackTrack."
    elif missing_details or (registration_requested and not any(m.get("due_at") or m.get("due_date") for m in milestones if m.get("type") != "CONFIRM_DATES")):
        content += "\nAdd the event's deadlines in HackTrack so follow-up reminders can be scheduled."
    components = [{"type": 1, "components": [
        {"type": 2, "style": 5, "label": "Open in HackTrack", "url": app_url},
        *([{"type": 2, "style": 3, "label": "Confirm dates", "custom_id": f"confirm:{hackathon_id}"}] if dates and len(dates) <= 5 else []),
        *([] if registration_requested else [{"type": 2, "style": 2, "label": "I registered", "custom_id": f"register:{hackathon_id}"}]),
    ]}]
    return content, components


def _send_if_linked(connect_factory, sender, job, content, components=None):
    with connect_factory() as conn:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT channel_id FROM discord_links WHERE user_id = %s FOR SHARE", (job["user_id"],))
                link = cur.fetchone()
            if not link or link["channel_id"] != job["channel_id"]:
                return False
            sender.send_message(job["channel_id"], content, components)
    return True


def process_captures(connect_factory, sender):
    processed = 0
    failed = 0
    while processed < MAX_PER_TICK:
        job = claim(connect_factory, datetime.now(timezone.utc))
        if job is None:
            break
        processed += 1
        try:
            content, components = _capture_message(connect_factory, job)
            if not _send_if_linked(connect_factory, sender, job, content, components):
                _finish(connect_factory, job, "FAILED", "Discord disconnected")
                continue
            _finish(connect_factory, job, "DONE")
        except Invalid as error:
            try:
                if _send_if_linked(connect_factory, sender, job, f"Couldn't track that link: {error.message}"):
                    _finish(connect_factory, job, "FAILED", error.message)
                else:
                    _finish(connect_factory, job, "FAILED", "Discord disconnected")
                failed += 1
            except Exception:
                log.exception("Could not report Discord capture error")
                _finish(connect_factory, job, "PENDING", "Discord delivery failed", retry=job["attempt_count"] < MAX_ATTEMPTS)
        except Exception:
            log.exception("Discord capture failed for job %s", job["id"])
            if job["attempt_count"] >= MAX_ATTEMPTS:
                _finish(connect_factory, job, "FAILED", "capture failed after retries")
                failed += 1
            else:
                _finish(connect_factory, job, "PENDING", "temporary capture failure", retry=True)
    return {"processed": processed, "failed": failed}
