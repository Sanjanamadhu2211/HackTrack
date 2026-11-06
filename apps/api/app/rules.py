from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

REGISTRATION_NEXT = {
    "DISCOVERED": {"PLANNING", "REGISTERED", "SKIPPED"},
    "PLANNING": {"REGISTERED", "SKIPPED"},
    "REGISTERED": {"ACCEPTED", "WAITLISTED", "REJECTED"},
    "WAITLISTED": {"ACCEPTED", "REJECTED"},
    "SKIPPED": {"PLANNING"},
}
PARTICIPATION_NEXT = {
    "NOT_STARTED": {"PREPARING", "ACTIVE"},
    "PREPARING": {"ACTIVE"},
    "ACTIVE": {"ENDED"},
}
OUTCOMES = {"SUBMITTED", "ATTENDED_NO_SUBMISSION", "WITHDRAWN", "MISSED", "EVENT_CANCELLED"}
MILESTONE_NEXT = {"TODO": {"DONE", "SKIPPED", "MISSED"}, "MISSED": {"DONE"}}
MILESTONE_TYPES = {"REGISTER", "RSVP", "TEAM", "CHECK_IN", "START", "SUBMIT", "PRESENT", "CONFIRM_DATES", "OTHER"}


def confidence(milestone):
    if not milestone["due_at"] or milestone["due_precision"] == "UNKNOWN":
        return "Date unknown"
    if not milestone["date_confirmed"]:
        return "Deadline from page text, not confirmed"
    if milestone["type"] == "CONFIRM_DATES":
        return "Added by HackTrack"
    if milestone["due_precision"] == "DATE":
        return "By end of day (assumed), confirmed by you"
    return "Confirmed by you"


def is_active(h):
    return h["archived_at"] is None and h["outcome"] is None and h["registration_state"] not in ("SKIPPED", "REJECTED")


def is_ended(h, now):
    return h["participation_state"] == "ENDED" or (h["ends_at"] and h["ends_at"] < now)


def select_next_action(h, milestones, now):
    if not is_active(h):
        return None
    todo = [m for m in milestones if m["status"] == "TODO"]
    has_register = any(m["type"] == "REGISTER" for m in milestones)
    if h["registration_state"] in ("PLANNING", "DISCOVERED") and not has_register:
        return {"milestone_id": None, "type": "REGISTER", "title": "Register", "due_at": None, "overdue": False, "confidence": "Date unknown"}
    if not todo:
        if is_ended(h, now):
            return {"milestone_id": None, "type": "OUTCOME", "title": "Record the outcome", "due_at": None, "overdue": False, "confidence": ""}
        return None
    todo.sort(key=lambda m: (not m["required"], m["due_at"] is None, m["due_at"] or datetime.max.replace(tzinfo=timezone.utc)))
    m = todo[0]
    return {
        "milestone_id": str(m["id"]),
        "type": m["type"],
        "title": m["title"],
        "due_at": m["due_at"],
        "due_date": m["due_date"] or "",
        "due_precision": m["due_precision"],
        "overdue": bool(m["due_at"] and m["due_at"] < now),
        "confidence": confidence(m),
    }


def bucket(h, milestones, next_action, unacked_final, now):
    if h["archived_at"] is not None or h["registration_state"] in ("SKIPPED", "REJECTED"):
        return "archived"
    if h["outcome"] is not None:
        return "done"
    if unacked_final:
        return "needs_action"
    for m in milestones:
        if m["status"] == "TODO" and m["due_at"]:
            if not m["date_confirmed"] or m["due_at"] < now + timedelta(hours=48):
                return "needs_action"
    if next_action and (next_action["type"] == "OUTCOME" or (next_action["type"] == "REGISTER" and next_action["milestone_id"] is None)):
        return "needs_action"
    if h["participation_state"] == "ACTIVE":
        return "in_progress"
    return "upcoming"


def load_timezone(name):
    try:
        return ZoneInfo(name)
    except Exception:
        return timezone.utc


def end_of_day(date_value, tz_name):
    return datetime(date_value.year, date_value.month, date_value.day, 23, 59, 0, tzinfo=load_timezone(tz_name))


def tz_midnight(date_value, tz_name):
    return datetime(date_value.year, date_value.month, date_value.day, 0, 0, tzinfo=load_timezone(tz_name))


def record_transition(conn, user_id, entity, entity_id, field, from_value, to_value, actor):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO state_transitions (user_id, entity, entity_id, field, from_state, to_state, actor) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (user_id, entity, entity_id, field, from_value if from_value else None, to_value, actor),
        )


def check_transition(h, field, to):
    from app.errors import Invalid

    if field == "registration_state":
        if to not in REGISTRATION_NEXT.get(h["registration_state"], set()):
            raise Invalid(f"cannot move registration from {h['registration_state']} to {to}")
    elif field == "participation_state":
        if h["registration_state"] not in ("REGISTERED", "ACCEPTED"):
            raise Invalid("participation needs a REGISTERED or ACCEPTED event")
        if to not in PARTICIPATION_NEXT.get(h["participation_state"], set()):
            raise Invalid(f"cannot move participation from {h['participation_state']} to {to}")
    elif field == "outcome":
        if h["outcome"] is not None:
            raise Invalid("outcome is already set")
        if to not in OUTCOMES:
            raise Invalid(f"unknown outcome {to}")
    else:
        raise Invalid(f"unknown field {field}")


def check_milestone_transition(from_value, to):
    from app.errors import Invalid

    if to not in MILESTONE_NEXT.get(from_value, set()):
        raise Invalid(f"cannot move milestone from {from_value} to {to}")
