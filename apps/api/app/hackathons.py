import hashlib
import json
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app import db, reminders, rules
from app.auth import require_user_dependency
from app.errors import Invalid, NotFound, body_bytes
from app.errors import is_bad_uuid_error, is_duplicate_url_error
from app.normalize import normalize_url

router = APIRouter()


# ---------- helpers ----------

def _valid_uuid(value):
    try:
        uuid.UUID(value)
        return True
    except Exception:
        return False


def _parse_rfc3339(value, msg):
    if not isinstance(value, str) or not value.strip():
        raise Invalid(msg)
    v = value.strip()
    # strict RFC3339 shape
    if not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$", v):
        raise Invalid(msg)
    try:
        raw = v
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        raise Invalid(msg)
    if dt.tzinfo is None:
        raise Invalid(msg)
    return dt


def _parse_date_str(value):
    if not isinstance(value, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", value.strip()):
        raise Invalid("date must be YYYY-MM-DD")
    v = value.strip()
    try:
        parsed = date.fromisoformat(v)
    except ValueError:
        raise Invalid("date must be YYYY-MM-DD")
    if parsed.isoformat() != v:
        raise Invalid("date must be YYYY-MM-DD")
    return parsed, v


def _load_tz(name):
    # strict: must be valid IANA name, caller validates separately.
    return ZoneInfo(name)


def _utc_str(dt):
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _iso(dt):
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _date_str(v):
    if v is None or v == "":
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).date().isoformat() if v.tzinfo else v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    try:
        return v.isoformat()
    except Exception:
        return str(v)


def _civil_of(instant, tz_name):
    return instant.astimezone(_load_tz(tz_name)).date()


def _serialize_milestone(m):
    return {
        "id": str(m["id"]),
        "hackathon_id": str(m["hackathon_id"]) if m.get("hackathon_id") else None,
        "type": m["type"],
        "title": m["title"],
        "due_at": _iso(m.get("due_at")),
        "due_date": _date_str(m.get("due_date")),
        "due_precision": m["due_precision"],
        "date_confirmed": bool(m["date_confirmed"]),
        "required": bool(m["required"]),
        "status": m["status"],
        "completed_at": _iso(m.get("completed_at")),
        "source": m.get("source", "USER"),
        "depart_at": _iso(m.get("depart_at")),
        "version": m["version"],
        "created_at": _iso(m.get("created_at")),
        "updated_at": _iso(m.get("updated_at")),
    }


def _serialize_hackathon(row, milestones, now, unacked_final, blank_description=False, blank_milestones=False):
    next_action_raw = rules.select_next_action(row, milestones, now)
    bucket_str = rules.bucket(row, milestones, next_action_raw, unacked_final, now)
    next_action_out = None
    if next_action_raw is not None:
        next_action_out = dict(next_action_raw)
        if next_action_out.get("due_at") is not None:
            next_action_out["due_at"] = _utc_str(next_action_out["due_at"])
        # milestone_id already str or None
    milestones_out = [] if blank_milestones else [_serialize_milestone(m) for m in milestones]
    return {
        "id": str(row["id"]),
        "title": row["title"],
        "organizer": row["organizer"],
        "source_url": row["source_url"],
        "normalized_url": row.get("normalized_url"),
        "platform": row.get("platform", ""),
        "mode": row["mode"],
        "venue": row["venue"],
        "location": row["location"],
        "event_timezone": row["event_timezone"],
        "starts_at": _iso(row.get("starts_at")),
        "ends_at": _iso(row.get("ends_at")),
        "starts_date": _date_str(row.get("starts_date")),
        "ends_date": _date_str(row.get("ends_date")),
        "description": "" if blank_description else row["description"],
        "registration_state": row["registration_state"],
        "participation_state": row["participation_state"],
        "outcome": row.get("outcome"),
        "archived_at": _iso(row.get("archived_at")),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
        "milestones": milestones_out,
        "next_action": next_action_out,
        "bucket": bucket_str,
    }


def _fetch_milestones(conn, hackathon_id):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM milestones WHERE hackathon_id = %s ORDER BY due_at ASC NULLS LAST, created_at ASC",
            (hackathon_id,),
        )
        return cur.fetchall()


def _unacked_final(conn, hackathon_id):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS(SELECT 1 FROM notification_deliveries d "
            "JOIN notification_jobs j ON j.id = d.job_id "
            "JOIN milestones m ON m.id = j.milestone_id "
            "WHERE m.hackathon_id = %s AND j.final AND d.sent_at IS NOT NULL AND d.acknowledged_at IS NULL)",
            (hackathon_id,),
        )
        row = cur.fetchone()
        if row is None:
            # EXISTS returns single-col keyed by 'exists'
            return False
        # psycopg dict row for EXISTS column name is 'exists'
        return bool(row.get("exists", False))


def _load_hackathon_row(conn, user_id, hackathon_id):
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM hackathons WHERE id = %s AND user_id = %s", (hackathon_id, user_id))
        return cur.fetchone()


def _detail_for_row(conn, row, now):
    milestones = _fetch_milestones(conn, str(row["id"]))
    unacked = _unacked_final(conn, str(row["id"]))
    return _serialize_hackathon(row, milestones, now, unacked)


def _resolve_milestone(raw, event_tz_name):
    if not isinstance(raw, dict):
        raise Invalid("invalid milestone")
    mtype = raw.get("type")
    if mtype not in rules.MILESTONE_TYPES:
        raise Invalid("type must be one of REGISTER, RSVP, TEAM, CHECK_IN, START, SUBMIT, PRESENT, CONFIRM_DATES, OTHER")
    title_raw = raw.get("title")
    title = title_raw.strip() if isinstance(title_raw, str) else ""
    if not title:
        title = mtype
    # due_at
    due_at_val = None
    if raw.get("due_at") is not None and raw.get("due_at") != "":
        due_at_val = _parse_rfc3339(raw.get("due_at"), "due_at must be an RFC 3339 time")
    # due_date
    due_date_str = None
    due_date_obj = None
    if raw.get("due_date") is not None and raw.get("due_date") != "":
        due_date_obj, due_date_str = _parse_date_str(raw.get("due_date"))
    # depart_at
    depart_val = None
    if raw.get("depart_at") is not None and raw.get("depart_at") != "":
        depart_val = _parse_rfc3339(raw.get("depart_at"), "depart_at must be an RFC 3339 time")
    # due_precision
    prec_provided = None
    if raw.get("due_precision") is not None and raw.get("due_precision") != "":
        prec_provided = raw.get("due_precision")
        if prec_provided not in ("DATETIME", "DATE", "UNKNOWN"):
            raise Invalid("due_precision must be DATETIME, DATE or UNKNOWN")
    # clear_due_at
    clear = raw.get("clear_due_at") is True
    if clear:
        due_at_val = None
        due_date_str = None
        due_date_obj = None
        # UNKNOWN forcing; if explicit precision also given and not UNKNOWN, clear wins as UNKNOWN
        prec_provided = "UNKNOWN"
    # UNKNOWN conflict
    if prec_provided == "UNKNOWN" and (due_at_val is not None or due_date_str is not None):
        raise Invalid("an unknown deadline cannot have a due date")
    # precision resolution
    precision = None
    if due_date_str is not None:
        precision = "DATE"
        # overwrite due_at with EndOfDay
        due_at_val = rules.end_of_day(due_date_obj, event_tz_name)
    elif prec_provided is not None:
        precision = prec_provided
        if precision == "DATE" and due_at_val is not None:
            civil = _civil_of(due_at_val, event_tz_name)
            due_date_str = civil.isoformat()
            due_date_obj = civil
            due_at_val = rules.end_of_day(civil, event_tz_name)
    else:
        if due_at_val is not None:
            precision = "DATETIME"
        else:
            precision = "UNKNOWN"
    if precision == "UNKNOWN":
        due_at_val = None
        due_date_str = None
        due_date_obj = None
    # date_confirmed
    if "date_confirmed" in raw and raw.get("date_confirmed") is not None:
        date_confirmed = bool(raw.get("date_confirmed"))
    else:
        date_confirmed = precision != "UNKNOWN"
    if precision == "UNKNOWN":
        date_confirmed = False
    # required
    if "required" in raw and raw.get("required") is not None:
        required = bool(raw.get("required"))
    else:
        required = True
    return {
        "type": mtype,
        "title": title,
        "due_at": due_at_val,
        "due_date": due_date_str,  # YYYY-MM-DD or None for DB DATE
        "due_date_obj": due_date_obj,
        "due_precision": precision,
        "date_confirmed": date_confirmed,
        "required": required,
        "depart_at": depart_val,
    }


def _fingerprint(canonical):
    raw = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _check_duplicate(conn, user_id, normalized_url):
    """Returns hackathon id if duplicate, else None. Assumes advisory lock held."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM hackathons WHERE user_id = %s AND normalized_url = %s",
            (user_id, normalized_url),
        )
        row = cur.fetchone()
        if row:
            return str(row["id"])
        # legacy NULL normalized_url rows
        cur.execute(
            "SELECT id, source_url FROM hackathons WHERE user_id = %s AND normalized_url IS NULL AND source_url <> ''",
            (user_id,),
        )
        for r in cur.fetchall():
            try:
                if normalize_url(r["source_url"] or "") == normalized_url:
                    return str(r["id"])
            except Exception:
                continue
    return None


def _update_milestone_status(conn, user_id, milestone_id, to_status, actor, now):
    """Bump version, completed_at, record transition, regenerate."""
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM milestones WHERE id = %s AND user_id = %s", (milestone_id, user_id))
        row = cur.fetchone()
        if not row:
            return False
        from_value = row["status"]
        completed = now if to_status == "DONE" else None
        cur.execute(
            "UPDATE milestones SET status = %s, completed_at = %s, updated_at = now(), version = version + 1 WHERE id = %s",
            (to_status, completed, milestone_id),
        )
    rules.record_transition(conn, user_id, "milestone", milestone_id, "status", from_value, to_status, actor)
    reminders.regenerate(conn, milestone_id, now)
    return True


def _ensure_confirm_dates(conn, user_id, hackathon_id, now):
    # fetch hackathon row
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM hackathons WHERE id = %s", (hackathon_id,))
        h = cur.fetchone()
    if not h:
        return
    # other required dated confirmed TODO|DONE (non-CONFIRM)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM milestones WHERE hackathon_id = %s AND type <> 'CONFIRM_DATES' "
            "AND required AND due_at IS NOT NULL AND date_confirmed AND status IN ('TODO','DONE')",
            (hackathon_id,),
        )
        has_other_confirmed = cur.fetchone() is not None
        cur.execute(
            "SELECT id FROM milestones WHERE hackathon_id = %s AND type = 'CONFIRM_DATES' AND status = 'TODO'",
            (hackathon_id,),
        )
        confirm_todos = [r["id"] for r in cur.fetchall()]
    if has_other_confirmed and confirm_todos:
        for mid in confirm_todos:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status FROM milestones WHERE id = %s",
                    (mid,),
                )
                r = cur.fetchone()
                from_value = r["status"] if r else "TODO"
                cur.execute(
                    "UPDATE milestones SET status = 'DONE', completed_at = now(), updated_at = now(), version = version + 1 WHERE id = %s",
                    (mid,),
                )
            rules.record_transition(conn, user_id, "milestone", str(mid), "status", from_value, "DONE", "SYSTEM")
            reminders.cancel_pending(conn, str(mid))
        return
    if has_other_confirmed:
        return
    # create condition
    if h["registration_state"] not in ("REGISTERED", "ACCEPTED"):
        return
    if h["outcome"] is not None:
        return
    if h["archived_at"] is not None:
        return
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM milestones WHERE hackathon_id = %s AND type = 'CONFIRM_DATES'",
            (hackathon_id,),
        )
        if cur.fetchone() is not None:
            return
        # no other TODO required dated milestone (due_at not null)
        cur.execute(
            "SELECT id FROM milestones WHERE hackathon_id = %s AND type <> 'CONFIRM_DATES' "
            "AND status = 'TODO' AND required AND due_at IS NOT NULL",
            (hackathon_id,),
        )
        if cur.fetchone() is not None:
            return
    created_at = h["created_at"] if h.get("created_at") else now
    due_at = created_at + timedelta(hours=24)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO milestones (user_id, hackathon_id, type, title, due_at, due_precision, date_confirmed, required, status, source, version) "
            "VALUES (%s, %s, 'CONFIRM_DATES', 'Confirm dates and requirements', %s, 'DATETIME', true, true, 'TODO', 'USER', 1) RETURNING id",
            (user_id, hackathon_id, due_at),
        )
        new_id = str(cur.fetchone()["id"])
    reminders.regenerate(conn, new_id, now)


# ---------- routes ----------

@router.get("/hackathons")
def list_hackathons(request: Request, user_id: str = Depends(require_user_dependency)):
    if not isinstance(user_id, str):
        return user_id
    summary = request.query_params.get("summary", "")
    is_summary = summary in ("1", "true", "True")
    try:
        with db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM hackathons WHERE user_id = %s", (user_id,))
                rows = cur.fetchall()
            if is_summary:
                rows_sorted = sorted(rows, key=lambda r: r["title"])
                return {"hackathons": [{"id": str(r["id"]), "title": r["title"]} for r in rows_sorted]}
            now = datetime.now(timezone.utc)
            # filters parsing
            bucket_filter = request.query_params.get("bucket")
            mode_filter = request.query_params.get("mode")
            from_q = request.query_params.get("from")
            to_q = request.query_params.get("to")
            from_dt = None
            to_dt = None
            if from_q:
                from_dt = _parse_rfc3339(from_q, "from must be an RFC 3339 time")
            if to_q:
                to_dt = _parse_rfc3339(to_q, "to must be an RFC 3339 time")
            items = []
            for row in rows:
                milestones = _fetch_milestones(conn, str(row["id"]))
                unacked = _unacked_final(conn, str(row["id"]))
                detail = _serialize_hackathon(row, milestones, now, unacked)
                # filters
                if bucket_filter and detail["bucket"] != bucket_filter:
                    continue
                if mode_filter and row["mode"] != mode_filter:
                    continue
                if from_dt is not None or to_dt is not None:
                    if row.get("starts_at") is None:
                        continue
                    if from_dt is not None and row["starts_at"] < from_dt:
                        continue
                    if to_dt is not None and row["starts_at"] > to_dt:
                        continue
                items.append((row, detail))
            # order starts_at NULLS LAST, created_at DESC
            def _sort_key(t):
                row = t[0]
                st = row.get("starts_at")
                # None last: (st is None, st, -created timestamp)
                # created_at descending: negate timestamp
                ca = row.get("created_at")
                ca_ts = ca.timestamp() if isinstance(ca, datetime) and ca.tzinfo else (ca.replace(tzinfo=timezone.utc).timestamp() if isinstance(ca, datetime) else 0)
                return (st is None, st if st is not None else datetime.max.replace(tzinfo=timezone.utc), -ca_ts)
            items.sort(key=_sort_key)
            return {"hackathons": [d for _, d in items]}
    except Invalid:
        raise
    except Exception as e:
        if is_bad_uuid_error(e):
            raise NotFound()
        raise


@router.get("/dashboard")
def dashboard(request: Request, user_id: str = Depends(require_user_dependency)):
    if not isinstance(user_id, str):
        return user_id
    try:
        with db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM hackathons WHERE user_id = %s", (user_id,))
                rows = cur.fetchall()
            now = datetime.now(timezone.utc)
            buckets = {"needs_action": [], "upcoming": [], "in_progress": [], "done": [], "archived": []}
            for row in rows:
                milestones = _fetch_milestones(conn, str(row["id"]))
                unacked = _unacked_final(conn, str(row["id"]))
                detail = _serialize_hackathon(row, milestones, now, unacked, blank_description=True, blank_milestones=True)
                buckets[detail["bucket"]].append(detail)
            # order bucket items? Keep starts_at NULLS LAST, created_at DESC like list for consistency
            def _sort_key_d(d):
                # d starts_at is iso string or None; use string comparison fallback
                st = d.get("starts_at")
                ca = d.get("created_at") or ""
                return (st is None, st or "", ca)
            # simpler: sort by starts_at string asc nulls last; created_at desc
            for k in buckets:
                buckets[k].sort(key=lambda d: (d.get("starts_at") is None, d.get("starts_at") or "", ), reverse=False)
                # created desc secondary: need stable sort by created desc first then starts asc
                buckets[k].sort(key=lambda d: d.get("created_at") or "", reverse=True)
                buckets[k].sort(key=lambda d: (d.get("starts_at") is None, d.get("starts_at") or ""))
            registered = sum(1 for r in rows if r["registration_state"] in ("REGISTERED", "ACCEPTED"))
            started = sum(1 for r in rows if r["participation_state"] in ("ACTIVE", "ENDED"))
            submitted = sum(1 for r in rows if r.get("outcome") == "SUBMITTED")
            missed = sum(1 for r in rows if r.get("outcome") == "MISSED")
            upcoming = len(buckets["upcoming"])
            return {
                "buckets": buckets,
                "stats": {
                    "registered": registered,
                    "started": started,
                    "submitted": submitted,
                    "missed": missed,
                    "upcoming": upcoming,
                },
            }
    except Invalid:
        raise
    except Exception as e:
        if is_bad_uuid_error(e):
            raise NotFound()
        raise


@router.get("/hackathons/{hackathon_id}")
def get_hackathon(hackathon_id: str, user_id: str = Depends(require_user_dependency)):
    if not isinstance(user_id, str):
        return user_id
    if not _valid_uuid(hackathon_id):
        raise NotFound()
    try:
        with db.connect() as conn:
            row = _load_hackathon_row(conn, user_id, hackathon_id)
            if not row:
                raise NotFound()
            now = datetime.now(timezone.utc)
            detail = _detail_for_row(conn, row, now)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT entity, entity_id, field, from_state, to_state, actor, at FROM state_transitions "
                    "WHERE user_id = %s AND entity = 'hackathon' AND entity_id = %s ORDER BY at ASC, created_at ASC",
                    (user_id, hackathon_id),
                )
                hist = cur.fetchall()
            history = [
                {
                    "entity": r["entity"],
                    "entity_id": str(r["entity_id"]),
                    "field": r["field"],
                    "from": r.get("from_state"),
                    "to": r["to_state"],
                    "actor": r["actor"],
                    "at": _iso(r.get("at")),
                }
                for r in hist
            ]
            detail["history"] = history
            return detail
    except NotFound:
        raise
    except Invalid:
        raise
    except Exception as e:
        if is_bad_uuid_error(e):
            raise NotFound()
        raise


@router.post("/hackathons", status_code=201)
async def create_hackathon(request: Request, user_id: str = Depends(require_user_dependency)):
    if not isinstance(user_id, str):
        return user_id
    data = await body_bytes()(request)
    # archived not allowed on create
    if "archived" in data and data.get("archived") is not None:
        raise Invalid("archived can only be set on an existing event")
    # mode
    mode_in = data.get("mode")
    if mode_in is None or mode_in == "":
        mode = "ONLINE"
    else:
        if mode_in not in ("ONLINE", "OFFLINE", "HYBRID"):
            raise Invalid("mode must be ONLINE, OFFLINE or HYBRID")
        mode = mode_in
    # event_timezone
    tz_in = data.get("event_timezone")
    if tz_in is None or tz_in == "":
        event_timezone = "UTC"
    else:
        if not isinstance(tz_in, str):
            raise Invalid("unknown event_timezone")
        try:
            ZoneInfo(tz_in)
        except Exception:
            raise Invalid("unknown event_timezone")
        event_timezone = tz_in
    # strings
    title = data.get("title") if isinstance(data.get("title"), str) else ("" if data.get("title") is None else str(data.get("title")))
    title = title.strip()
    organizer = data.get("organizer") if isinstance(data.get("organizer"), str) else ("" if data.get("organizer") is None else str(data.get("organizer")))
    source_url_raw = data.get("source_url") if isinstance(data.get("source_url"), str) else ("" if data.get("source_url") is None else str(data.get("source_url")))
    source_url = source_url_raw.strip()
    venue = data.get("venue") if isinstance(data.get("venue"), str) else ("" if data.get("venue") is None else str(data.get("venue")))
    location = data.get("location") if isinstance(data.get("location"), str) else ("" if data.get("location") is None else str(data.get("location")))
    description = data.get("description") if isinstance(data.get("description"), str) else ("" if data.get("description") is None else str(data.get("description")))
    # starts/ends instants + civil
    starts_at_input = None
    if data.get("starts_at") is not None and data.get("starts_at") != "":
        starts_at_input = _parse_rfc3339(data.get("starts_at"), "starts_at must be an RFC 3339 time")
    ends_at_input = None
    if data.get("ends_at") is not None and data.get("ends_at") != "":
        ends_at_input = _parse_rfc3339(data.get("ends_at"), "ends_at must be an RFC 3339 time")
    starts_date_obj = None
    starts_date_str = None
    if data.get("starts_date") is not None and data.get("starts_date") != "":
        starts_date_obj, starts_date_str = _parse_date_str(data.get("starts_date"))
    ends_date_obj = None
    ends_date_str = None
    if data.get("ends_date") is not None and data.get("ends_date") != "":
        ends_date_obj, ends_date_str = _parse_date_str(data.get("ends_date"))
    # resolve starts
    starts_at = starts_at_input
    ends_at = ends_at_input
    if starts_at_input is not None and starts_date_str is not None:
        if _civil_of(starts_at_input, event_timezone).isoformat() != starts_date_str:
            raise Invalid("starts_date does not match starts_at in event_timezone")
    elif starts_at_input is not None:
        starts_date_obj = _civil_of(starts_at_input, event_timezone)
        starts_date_str = starts_date_obj.isoformat()
    elif starts_date_str is not None:
        starts_at = rules.tz_midnight(starts_date_obj, event_timezone)
    if ends_at_input is not None and ends_date_str is not None:
        if _civil_of(ends_at_input, event_timezone).isoformat() != ends_date_str:
            raise Invalid("ends_date does not match ends_at in event_timezone")
    elif ends_at_input is not None:
        ends_date_obj = _civil_of(ends_at_input, event_timezone)
        ends_date_str = ends_date_obj.isoformat()
    elif ends_date_str is not None:
        ends_at = rules.tz_midnight(ends_date_obj, event_timezone)
    if starts_at is not None and ends_at is not None and ends_at < starts_at:
        raise Invalid("ends_at is before starts_at")
    if starts_date_obj is not None and ends_date_obj is not None and ends_date_obj < starts_date_obj:
        raise Invalid("ends_date is before starts_date")
    # registration_state
    reg_in = data.get("registration_state")
    if reg_in is None or reg_in == "":
        registration_state = "PLANNING"
    else:
        if reg_in not in ("PLANNING", "REGISTERED"):
            raise Invalid("registration_state must be PLANNING or REGISTERED")
        registration_state = reg_in
    # idempotency_key
    idem_key = None
    if "idempotency_key" in data and data.get("idempotency_key") is not None:
        raw_key = data.get("idempotency_key")
        if not isinstance(raw_key, str):
            raise Invalid("idempotency_key must be 1-200 characters")
        trimmed = raw_key.strip()
        if not (1 <= len(trimmed) <= 200):
            raise Invalid("idempotency_key must be 1-200 characters")
        idem_key = trimmed
    # source_url normalized
    normalized_url = None
    if source_url:
        try:
            normalized_url = normalize_url(source_url)
        except Exception:
            raise Invalid("invalid source URL")
    # milestones
    milestones_raw = data.get("milestones") if "milestones" in data else []
    if milestones_raw is None:
        milestones_raw = []
    if not isinstance(milestones_raw, list):
        raise Invalid("invalid milestones")
    resolved_milestones = [_resolve_milestone(m, event_timezone) for m in milestones_raw]
    # fingerprint canonical
    canonical = {
        "title": title,
        "organizer": organizer,
        "source_url": source_url,
        "mode": mode,
        "venue": venue,
        "location": location,
        "event_timezone": event_timezone,
        "starts_at": _utc_str(starts_at) if starts_at else None,
        "ends_at": _utc_str(ends_at) if ends_at else None,
        "starts_date": starts_date_str or "",
        "ends_date": ends_date_str or "",
        "description": description,
        "registration_state": registration_state,
        "milestones": [
            {
                "type": rm["type"],
                "title": rm["title"],
                "due_at": _utc_str(rm["due_at"]) if rm["due_at"] else None,
                "due_date": rm["due_date"] or "",
                "due_precision": rm["due_precision"],
                "date_confirmed": bool(rm["date_confirmed"]),
                "required": bool(rm["required"]),
                "depart_at": _utc_str(rm["depart_at"]) if rm["depart_at"] else None,
            }
            for rm in resolved_milestones
        ],
    }
    fingerprint = _fingerprint(canonical)
    now = datetime.now(timezone.utc)
    try:
        with db.connect() as conn:
            with conn:
                stored_id = None
                if idem_key:
                    with conn.cursor() as cur:
                        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)", (f"{user_id}:{idem_key}",))
                        cur.execute(
                            "SELECT fingerprint, hackathon_id FROM hackathon_create_requests WHERE user_id = %s AND idempotency_key = %s",
                            (user_id, idem_key),
                        )
                        existing = cur.fetchone()
                    if existing:
                        if existing["fingerprint"] != fingerprint:
                            raise Invalid("idempotency_key was already used for a different request")
                        stored_id = str(existing["hackathon_id"])
                        # return stored id; no new inserts
                        return {"id": stored_id}
                if normalized_url:
                    with conn.cursor() as cur:
                        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)", (f"{user_id}:{normalized_url}",))
                    dup_id = _check_duplicate(conn, user_id, normalized_url)
                    if dup_id:
                        return JSONResponse({"error": "event URL already saved"}, status_code=409)
                # insert hackathon
                with conn.cursor() as cur:
                    try:
                        cur.execute(
                            "INSERT INTO hackathons (user_id, title, organizer, source_url, normalized_url, platform, mode, venue, location, "
                            "event_timezone, starts_at, ends_at, starts_date, ends_date, description, registration_state, participation_state) "
                            "VALUES (%s, %s, %s, %s, %s, '', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'NOT_STARTED') RETURNING id",
                            (
                                user_id, title, organizer, source_url, normalized_url, mode, venue, location,
                                event_timezone, starts_at, ends_at, starts_date_str if starts_date_str else None,
                                ends_date_str if ends_date_str else None, description, registration_state,
                            ),
                        )
                        hackathon_id = str(cur.fetchone()["id"])
                    except Exception as e:
                        if is_duplicate_url_error(e):
                            return JSONResponse({"error": "event URL already saved"}, status_code=409)
                        raise
                if idem_key:
                    with conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO hackathon_create_requests (user_id, idempotency_key, fingerprint, hackathon_id) VALUES (%s, %s, %s, %s)",
                            (user_id, idem_key, fingerprint, hackathon_id),
                        )
                rules.record_transition(conn, user_id, "hackathon", hackathon_id, "registration_state", None, registration_state, "USER")
                created_milestone_ids = []
                for rm in resolved_milestones:
                    with conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO milestones (user_id, hackathon_id, type, title, due_at, due_precision, date_confirmed, required, status, due_date, depart_at, source, version) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'TODO', %s, %s, 'USER', 1) RETURNING id",
                            (
                                user_id, hackathon_id, rm["type"], rm["title"], rm["due_at"], rm["due_precision"],
                                rm["date_confirmed"], rm["required"], rm["due_date"] if rm["due_date"] else None, rm["depart_at"],
                            ),
                        )
                        mid = str(cur.fetchone()["id"])
                        created_milestone_ids.append(mid)
                for mid in created_milestone_ids:
                    reminders.regenerate(conn, mid, now)
                _ensure_confirm_dates(conn, user_id, hackathon_id, now)
                return {"id": hackathon_id}
    except Invalid:
        raise
    except Exception as e:
        if is_bad_uuid_error(e):
            raise NotFound()
        if is_duplicate_url_error(e):
            return JSONResponse({"error": "event URL already saved"}, status_code=409)
        raise


@router.patch("/hackathons/{hackathon_id}")
async def patch_hackathon(hackathon_id: str, request: Request, user_id: str = Depends(require_user_dependency)):
    if not isinstance(user_id, str):
        return user_id
    if not _valid_uuid(hackathon_id):
        raise NotFound()
    data = await body_bytes()(request)
    if "milestones" in data:
        raise Invalid("milestones can only be set when creating an event")
    if "registration_state" in data and data.get("registration_state") is not None:
        raise Invalid("use /transitions to change registration_state")
    if "title" in data:
        t = data.get("title")
        if not isinstance(t, str) or t.strip() == "":
            raise Invalid("title cannot be empty")
    # mode / tz validation
    mode_in = data.get("mode")
    if mode_in is not None and mode_in != "" and mode_in not in ("ONLINE", "OFFLINE", "HYBRID"):
        raise Invalid("mode must be ONLINE, OFFLINE or HYBRID")
    tz_in = data.get("event_timezone")
    if tz_in is not None and tz_in != "":
        try:
            ZoneInfo(tz_in)
        except Exception:
            raise Invalid("unknown event_timezone")
    # parse starts/ends inputs (present non-empty)
    starts_at_input = None
    has_starts_at = False
    if data.get("starts_at") is not None and data.get("starts_at") != "":
        starts_at_input = _parse_rfc3339(data.get("starts_at"), "starts_at must be an RFC 3339 time")
        has_starts_at = True
    ends_at_input = None
    has_ends_at = False
    if data.get("ends_at") is not None and data.get("ends_at") != "":
        ends_at_input = _parse_rfc3339(data.get("ends_at"), "ends_at must be an RFC 3339 time")
        has_ends_at = True
    starts_date_obj = None
    starts_date_str = None
    has_starts_date = False
    if data.get("starts_date") is not None and data.get("starts_date") != "":
        starts_date_obj, starts_date_str = _parse_date_str(data.get("starts_date"))
        has_starts_date = True
    ends_date_obj = None
    ends_date_str = None
    has_ends_date = False
    if data.get("ends_date") is not None and data.get("ends_date") != "":
        ends_date_obj, ends_date_str = _parse_date_str(data.get("ends_date"))
        has_ends_date = True
    # quick civil/instant consistency when both explicit (validate against new tz)
    # new_tz determined after fetch; but if both explicit, check against provided tz or after fetch
    new_tz_prelim = tz_in if (tz_in is not None and tz_in != "") else None
    if has_starts_at and has_starts_date and new_tz_prelim:
        if _civil_of(starts_at_input, new_tz_prelim).isoformat() != starts_date_str:
            raise Invalid("starts_date does not match starts_at in event_timezone")
    if has_ends_at and has_ends_date and new_tz_prelim:
        if _civil_of(ends_at_input, new_tz_prelim).isoformat() != ends_date_str:
            raise Invalid("ends_date does not match ends_at in event_timezone")
    # source_url validation if present
    source_url_provided = "source_url" in data and data.get("source_url") is not None
    new_source_url = None
    new_normalized = None
    if source_url_provided:
        new_source_url = data.get("source_url").strip() if isinstance(data.get("source_url"), str) else str(data.get("source_url")).strip()
        if new_source_url:
            try:
                new_normalized = normalize_url(new_source_url)
            except Exception:
                raise Invalid("invalid source URL")
    now = datetime.now(timezone.utc)
    try:
        with db.connect() as conn:
            with conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM hackathons WHERE id = %s AND user_id = %s FOR UPDATE", (hackathon_id, user_id))
                    old = cur.fetchone()
                if not old:
                    raise NotFound()
                old_tz = old["event_timezone"]
                new_tz = tz_in if (tz_in is not None and tz_in != "") else old_tz
                tz_changed = new_tz != old_tz
                # need civil/instant checks with new_tz if one explicit + stored? Validate explicit both already done; if only one explicit, no check.
                # For explicit starts_at without explicit starts_date but with old date irrelevant (civil follows instant).
                # For explicit starts_date without instant, instant derives.
                # Resolve new values
                new_title = data["title"].strip() if "title" in data and isinstance(data.get("title"), str) else old["title"]
                new_organizer = data.get("organizer") if "organizer" in data and data.get("organizer") is not None else old["organizer"]
                if not isinstance(new_organizer, str):
                    new_organizer = str(new_organizer) if new_organizer is not None else old["organizer"]
                new_venue = data.get("venue") if "venue" in data and data.get("venue") is not None else old["venue"]
                if not isinstance(new_venue, str):
                    new_venue = str(new_venue) if new_venue is not None else old["venue"]
                new_location = data.get("location") if "location" in data and data.get("location") is not None else old["location"]
                if not isinstance(new_location, str):
                    new_location = str(new_location) if new_location is not None else old["location"]
                new_description = data.get("description") if "description" in data and data.get("description") is not None else old["description"]
                if not isinstance(new_description, str):
                    new_description = str(new_description) if new_description is not None else old["description"]
                new_mode = mode_in if (mode_in is not None and mode_in != "") else old["mode"]
                new_src = new_source_url if source_url_provided else old["source_url"]
                new_norm = new_normalized if source_url_provided else old.get("normalized_url")
                if source_url_provided and not new_source_url:
                    new_norm = None
                # duplicate check for new normalized
                if source_url_provided and new_norm is not None:
                    # lock + check excluding self
                    with conn.cursor() as cur:
                        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)", (f"{user_id}:{new_norm}",))
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT id FROM hackathons WHERE user_id = %s AND normalized_url = %s AND id <> %s",
                            (user_id, new_norm, hackathon_id),
                        )
                        if cur.fetchone():
                            return JSONResponse({"error": "event URL already saved"}, status_code=409)
                        cur.execute(
                            "SELECT id, source_url FROM hackathons WHERE user_id = %s AND normalized_url IS NULL AND source_url <> '' AND id <> %s",
                            (user_id, hackathon_id),
                        )
                        for r in cur.fetchall():
                            try:
                                if normalize_url(r["source_url"] or "") == new_norm:
                                    return JSONResponse({"error": "event URL already saved"}, status_code=409)
                            except Exception:
                                continue
                # resolve starts/ends
                old_starts_at = old.get("starts_at")
                old_starts_date_obj = None
                old_starts_date_str = _date_str(old.get("starts_date")) if old.get("starts_date") is not None else ""
                if old_starts_date_str:
                    try:
                        old_starts_date_obj = date.fromisoformat(old_starts_date_str)
                    except Exception:
                        old_starts_date_obj = None
                old_ends_at = old.get("ends_at")
                old_ends_date_str = _date_str(old.get("ends_date")) if old.get("ends_date") is not None else ""
                old_ends_date_obj = None
                if old_ends_date_str:
                    try:
                        old_ends_date_obj = date.fromisoformat(old_ends_date_str)
                    except Exception:
                        old_ends_date_obj = None
                # starts resolution
                if has_starts_at and has_starts_date:
                    new_starts_at = starts_at_input
                    new_starts_date_obj = starts_date_obj
                    new_starts_date_str = starts_date_str
                    # already checked vs new_tz above if tz provided; if tz not provided, check vs new_tz (which is old)
                    if not new_tz_prelim and _civil_of(new_starts_at, new_tz).isoformat() != new_starts_date_str:
                        raise Invalid("starts_date does not match starts_at in event_timezone")
                elif has_starts_at:
                    new_starts_at = starts_at_input
                    new_starts_date_obj = _civil_of(new_starts_at, new_tz)
                    new_starts_date_str = new_starts_date_obj.isoformat()
                elif has_starts_date:
                    new_starts_date_obj = starts_date_obj
                    new_starts_date_str = starts_date_str
                    new_starts_at = rules.tz_midnight(starts_date_obj, new_tz)
                else:
                    # neither provided
                    if tz_changed:
                        # move only if exactly old-tz midnight
                        if old_starts_at is not None and old_starts_date_obj is not None:
                            old_mid = rules.tz_midnight(old_starts_date_obj, old_tz)
                            if old_starts_at == old_mid:
                                new_starts_at = rules.tz_midnight(old_starts_date_obj, new_tz)
                                new_starts_date_obj = old_starts_date_obj
                                new_starts_date_str = old_starts_date_str
                            else:
                                new_starts_at = old_starts_at
                                new_starts_date_obj = old_starts_date_obj
                                new_starts_date_str = old_starts_date_str
                        elif old_starts_at is not None:
                            new_starts_at = old_starts_at
                            # recompute civil in new tz? keep old civil if present else derive
                            if old_starts_date_obj is not None:
                                new_starts_date_obj = old_starts_date_obj
                                new_starts_date_str = old_starts_date_str
                            else:
                                new_starts_date_obj = _civil_of(old_starts_at, new_tz)
                                new_starts_date_str = new_starts_date_obj.isoformat()
                        else:
                            new_starts_at = None
                            new_starts_date_obj = old_starts_date_obj
                            new_starts_date_str = old_starts_date_str
                    else:
                        new_starts_at = old_starts_at
                        new_starts_date_obj = old_starts_date_obj
                        new_starts_date_str = old_starts_date_str
                if has_ends_at and has_ends_date:
                    new_ends_at = ends_at_input
                    new_ends_date_obj = ends_date_obj
                    new_ends_date_str = ends_date_str
                    if not new_tz_prelim and _civil_of(new_ends_at, new_tz).isoformat() != new_ends_date_str:
                        raise Invalid("ends_date does not match ends_at in event_timezone")
                elif has_ends_at:
                    new_ends_at = ends_at_input
                    new_ends_date_obj = _civil_of(new_ends_at, new_tz)
                    new_ends_date_str = new_ends_date_obj.isoformat()
                elif has_ends_date:
                    new_ends_date_obj = ends_date_obj
                    new_ends_date_str = ends_date_str
                    new_ends_at = rules.tz_midnight(ends_date_obj, new_tz)
                else:
                    if tz_changed:
                        if old_ends_at is not None and old_ends_date_obj is not None:
                            old_mid = rules.tz_midnight(old_ends_date_obj, old_tz)
                            if old_ends_at == old_mid:
                                new_ends_at = rules.tz_midnight(old_ends_date_obj, new_tz)
                                new_ends_date_obj = old_ends_date_obj
                                new_ends_date_str = old_ends_date_str
                            else:
                                new_ends_at = old_ends_at
                                new_ends_date_obj = old_ends_date_obj
                                new_ends_date_str = old_ends_date_str
                        elif old_ends_at is not None:
                            new_ends_at = old_ends_at
                            if old_ends_date_obj is not None:
                                new_ends_date_obj = old_ends_date_obj
                                new_ends_date_str = old_ends_date_str
                            else:
                                new_ends_date_obj = _civil_of(old_ends_at, new_tz)
                                new_ends_date_str = new_ends_date_obj.isoformat()
                        else:
                            new_ends_at = None
                            new_ends_date_obj = old_ends_date_obj
                            new_ends_date_str = old_ends_date_str
                    else:
                        new_ends_at = old_ends_at
                        new_ends_date_obj = old_ends_date_obj
                        new_ends_date_str = old_ends_date_str
                if new_starts_at is not None and new_ends_at is not None and new_ends_at < new_starts_at:
                    raise Invalid("ends_at is before starts_at")
                if new_starts_date_obj is not None and new_ends_date_obj is not None and new_ends_date_obj < new_starts_date_obj:
                    raise Invalid("ends_date is before starts_date")
                # archived
                new_archived_at = old.get("archived_at")
                archived_provided = "archived" in data and data.get("archived") is not None
                archived_bool = None
                if archived_provided:
                    archived_bool = bool(data.get("archived"))
                    new_archived_at = now if archived_bool else None
                # update hackathon
                with conn.cursor() as cur:
                    try:
                        cur.execute(
                            "UPDATE hackathons SET title = %s, organizer = %s, source_url = %s, normalized_url = %s, mode = %s, venue = %s, "
                            "location = %s, event_timezone = %s, starts_at = %s, ends_at = %s, starts_date = %s, ends_date = %s, "
                            "description = %s, archived_at = %s, updated_at = now() WHERE id = %s AND user_id = %s RETURNING id",
                            (
                                new_title, new_organizer, new_src, new_norm, new_mode, new_venue, new_location, new_tz,
                                new_starts_at, new_ends_at, new_starts_date_str if new_starts_date_str else None,
                                new_ends_date_str if new_ends_date_str else None, new_description, new_archived_at,
                                hackathon_id, user_id,
                            ),
                        )
                        updated = cur.fetchone()
                    except Exception as e:
                        if is_duplicate_url_error(e):
                            return JSONResponse({"error": "event URL already saved"}, status_code=409)
                        raise
                    if not updated:
                        raise NotFound()
                # tz milestone recalc
                if tz_changed:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT id, due_date FROM milestones WHERE hackathon_id = %s AND due_precision = 'DATE' AND due_date IS NOT NULL",
                            (hackathon_id,),
                        )
                        date_milestones = cur.fetchall()
                    for m in date_milestones:
                        due_date_val = m["due_date"]
                        # due_date_val could be date or string
                        if isinstance(due_date_val, date) and not isinstance(due_date_val, datetime):
                            dobj = due_date_val
                        elif isinstance(due_date_val, str):
                            dobj = date.fromisoformat(due_date_val)
                        else:
                            try:
                                dobj = date.fromisoformat(str(due_date_val))
                            except Exception:
                                continue
                        new_due = rules.end_of_day(dobj, new_tz)
                        with conn.cursor() as cur:
                            cur.execute(
                                "UPDATE milestones SET due_at = %s, updated_at = now(), version = version + 1 WHERE id = %s",
                                (new_due, str(m["id"])),
                            )
                        reminders.regenerate(conn, str(m["id"]), now)
                # archived side effects
                if archived_provided:
                    if archived_bool:
                        reminders.cancel_for_hackathon(conn, hackathon_id, "")
                    else:
                        with conn.cursor() as cur:
                            cur.execute(
                                "UPDATE milestones SET version = version + 1, updated_at = now() WHERE hackathon_id = %s AND status = 'TODO' RETURNING id",
                                (hackathon_id,),
                            )
                            todo_ids = [str(r["id"]) for r in cur.fetchall()]
                        for mid in todo_ids:
                            reminders.regenerate(conn, mid, now)
                # re-fetch detail
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM hackathons WHERE id = %s AND user_id = %s", (hackathon_id, user_id))
                    fresh = cur.fetchone()
                detail = _detail_for_row(conn, fresh, now)
                return detail
    except NotFound:
        raise
    except Invalid:
        raise
    except Exception as e:
        if is_bad_uuid_error(e):
            raise NotFound()
        if is_duplicate_url_error(e):
            return JSONResponse({"error": "event URL already saved"}, status_code=409)
        raise


@router.delete("/hackathons/{hackathon_id}", status_code=204)
def delete_hackathon(hackathon_id: str, user_id: str = Depends(require_user_dependency)):
    if not isinstance(user_id, str):
        return user_id
    if not _valid_uuid(hackathon_id):
        raise NotFound()
    try:
        with db.connect() as conn:
            with conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM hackathons WHERE id = %s AND user_id = %s", (hackathon_id, user_id))
                    if cur.rowcount == 0:
                        raise NotFound()
        return JSONResponse(status_code=204, content=b"") if False else _empty_204()
    except NotFound:
        raise
    except Exception as e:
        if is_bad_uuid_error(e):
            raise NotFound()
        raise


def _empty_204():
    from fastapi.responses import Response
    return Response(status_code=204)


@router.post("/hackathons/{hackathon_id}/transitions")
async def post_transition(hackathon_id: str, request: Request, user_id: str = Depends(require_user_dependency)):
    if not isinstance(user_id, str):
        return user_id
    if not _valid_uuid(hackathon_id):
        raise NotFound()
    data = await body_bytes()(request)
    field = data.get("field")
    to = data.get("to")
    if field not in ("registration_state", "participation_state", "outcome"):
        raise Invalid(f"unknown field {field}")
    now = datetime.now(timezone.utc)
    try:
        with db.connect() as conn:
            with conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM hackathons WHERE id = %s AND user_id = %s FOR UPDATE", (hackathon_id, user_id))
                    h = cur.fetchone()
                if not h:
                    raise NotFound()
                rules.check_transition(h, field, to)
                previous = h[field]
                # update
                with conn.cursor() as cur:
                    if field == "outcome" and to == "EVENT_CANCELLED":
                        cur.execute(
                            "UPDATE hackathons SET outcome = %s, archived_at = now(), updated_at = now() WHERE id = %s",
                            (to, hackathon_id),
                        )
                    else:
                        # field is validated safe
                        if field == "registration_state":
                            cur.execute("UPDATE hackathons SET registration_state = %s, updated_at = now() WHERE id = %s", (to, hackathon_id))
                        elif field == "participation_state":
                            cur.execute("UPDATE hackathons SET participation_state = %s, updated_at = now() WHERE id = %s", (to, hackathon_id))
                        else:
                            cur.execute("UPDATE hackathons SET outcome = %s, updated_at = now() WHERE id = %s", (to, hackathon_id))
                rules.record_transition(conn, user_id, "hackathon", hackathon_id, field, previous, to, "USER")
                # side effects
                if field == "registration_state":
                    if to in ("SKIPPED", "REJECTED"):
                        reminders.cancel_for_hackathon(conn, hackathon_id, "")
                    elif to == "PLANNING":
                        with conn.cursor() as cur:
                            cur.execute(
                                "UPDATE milestones SET version = version + 1, updated_at = now() WHERE hackathon_id = %s AND status = 'TODO' RETURNING id",
                                (hackathon_id,),
                            )
                            todo_ids = [str(r["id"]) for r in cur.fetchall()]
                        for mid in todo_ids:
                            reminders.regenerate(conn, mid, now)
                    elif to in ("REGISTERED", "ACCEPTED"):
                        evidence = data.get("evidence")
                        if evidence is not None:
                            if not isinstance(evidence, dict):
                                raise Invalid("invalid evidence type")
                            etype = evidence.get("type")
                            if etype not in ("MANUAL", "LINK"):
                                raise Invalid("invalid evidence type")
                            ref = evidence.get("reference") if isinstance(evidence.get("reference"), str) else ""
                            note = evidence.get("note") if isinstance(evidence.get("note"), str) else ""
                            with conn.cursor() as cur:
                                cur.execute(
                                    "INSERT INTO registration_evidence (user_id, hackathon_id, type, reference, note) VALUES (%s, %s, %s, %s, %s)",
                                    (user_id, hackathon_id, etype, ref, note),
                                )
                        with conn.cursor() as cur:
                            cur.execute(
                                "SELECT id FROM milestones WHERE hackathon_id = %s AND type = 'REGISTER' AND status = 'TODO'",
                                (hackathon_id,),
                            )
                            reg_ids = [str(r["id"]) for r in cur.fetchall()]
                        for mid in reg_ids:
                            _update_milestone_status(conn, user_id, mid, "DONE", "USER", now)
                        _ensure_confirm_dates(conn, user_id, hackathon_id, now)
                elif field == "outcome":
                    if to in ("SUBMITTED", "ATTENDED_NO_SUBMISSION"):
                        reminders.cancel_for_hackathon(conn, hackathon_id, "SUBMIT")
                    else:
                        reminders.cancel_for_hackathon(conn, hackathon_id, "")
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM hackathons WHERE id = %s AND user_id = %s", (hackathon_id, user_id))
                    fresh = cur.fetchone()
                detail = _detail_for_row(conn, fresh, now)
                return detail
    except NotFound:
        raise
    except Invalid:
        raise
    except Exception as e:
        if is_bad_uuid_error(e):
            raise NotFound()
        raise


@router.post("/hackathons/{hackathon_id}/milestones", status_code=201)
async def create_milestone(hackathon_id: str, request: Request, user_id: str = Depends(require_user_dependency)):
    if not isinstance(user_id, str):
        return user_id
    if not _valid_uuid(hackathon_id):
        raise NotFound()
    data = await body_bytes()(request)
    now = datetime.now(timezone.utc)
    try:
        with db.connect() as conn:
            with conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM hackathons WHERE id = %s AND user_id = %s", (hackathon_id, user_id))
                    h = cur.fetchone()
                if not h:
                    raise NotFound()
                event_tz = h["event_timezone"]
                # resolve (validates type, due_precision, UNKNOWN, dates)
                # need to apply same UNKNOWN conflict error for due_precision UNKNOWN + due
                rm = _resolve_milestone(data, event_tz)
                # insert via SELECT from hackathons WHERE id AND user_id (enforce same)
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO milestones (user_id, hackathon_id, type, title, due_at, due_precision, date_confirmed, required, status, due_date, depart_at, source, version) "
                        "SELECT %s, h.id, %s, %s, %s, %s, %s, %s, 'TODO', %s, %s, 'USER', 1 FROM hackathons h WHERE h.id = %s AND h.user_id = %s RETURNING id",
                        (
                            user_id, rm["type"], rm["title"], rm["due_at"], rm["due_precision"], rm["date_confirmed"],
                            rm["required"], rm["due_date"] if rm["due_date"] else None, rm["depart_at"], hackathon_id, user_id,
                        ),
                    )
                    inserted = cur.fetchone()
                    if not inserted:
                        raise NotFound()
                    mid = str(inserted["id"])
                reminders.regenerate(conn, mid, now)
                _ensure_confirm_dates(conn, user_id, hackathon_id, now)
                return {"id": mid}
    except NotFound:
        raise
    except Invalid:
        raise
    except Exception as e:
        if is_bad_uuid_error(e):
            raise NotFound()
        raise


@router.patch("/milestones/{milestone_id}")
async def patch_milestone(milestone_id: str, request: Request, user_id: str = Depends(require_user_dependency)):
    if not isinstance(user_id, str):
        return user_id
    if not _valid_uuid(milestone_id):
        raise NotFound()
    data = await body_bytes()(request)
    if "type" in data and data.get("type") is not None:
        raise Invalid("type cannot be changed")
    if "title" in data:
        t = data.get("title")
        if not isinstance(t, str) or t.strip() == "":
            raise Invalid("title cannot be empty")
    now = datetime.now(timezone.utc)
    try:
        with db.connect() as conn:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT m.*, h.event_timezone AS htz FROM milestones m JOIN hackathons h ON h.id = m.hackathon_id "
                        "WHERE m.id = %s AND m.user_id = %s FOR UPDATE OF m",
                        (milestone_id, user_id),
                    )
                    old = cur.fetchone()
                if not old:
                    raise NotFound()
                event_tz = old["htz"]
                hackathon_id = str(old["hackathon_id"])
                # presence flags
                has_due_at = "due_at" in data and data.get("due_at") is not None and data.get("due_at") != ""
                has_due_date = "due_date" in data and data.get("due_date") is not None and data.get("due_date") != ""
                has_precision = "due_precision" in data and data.get("due_precision") is not None and data.get("due_precision") != ""
                has_clear = data.get("clear_due_at") is True
                has_depart = "depart_at" in data and data.get("depart_at") is not None and data.get("depart_at") != ""
                has_title = "title" in data and isinstance(data.get("title"), str) and data.get("title").strip() != ""
                has_confirmed = "date_confirmed" in data and data.get("date_confirmed") is not None
                has_required = "required" in data and data.get("required") is not None
                has_status = "status" in data and data.get("status") is not None
                # parse inputs
                due_at_input = _parse_rfc3339(data["due_at"], "due_at must be an RFC 3339 time") if has_due_at else None
                due_date_obj = None
                due_date_str = None
                if has_due_date:
                    due_date_obj, due_date_str = _parse_date_str(data["due_date"])
                depart_input = _parse_rfc3339(data["depart_at"], "depart_at must be an RFC 3339 time") if has_depart else None
                precision_input = None
                if has_precision:
                    precision_input = data["due_precision"]
                    if precision_input not in ("DATETIME", "DATE", "UNKNOWN"):
                        raise Invalid("due_precision must be DATETIME, DATE or UNKNOWN")
                # UNKNOWN conflict when new due values with UNKNOWN precision
                if precision_input == "UNKNOWN" and (has_due_at or has_due_date) and not has_clear:
                    raise Invalid("an unknown deadline cannot have a due date")
                # status handling
                old_status = old["status"]
                new_status = data["status"] if has_status else old_status
                status_changed = has_status and new_status != old_status
                if status_changed:
                    rules.check_milestone_transition(old_status, new_status)
                # no-op check: neither status-change nor any field edit
                field_edit = has_title or has_due_at or has_due_date or has_precision or has_clear or has_depart or has_confirmed or has_required
                if not status_changed and not field_edit:
                    # also consider status provided but same value = not a change, no field edit -> no-op
                    return _empty_204()
                # resolve new values for due handling
                old_due_at = old.get("due_at")
                old_due_date_val = old.get("due_date")
                old_due_date_str_resolved = _date_str(old_due_date_val) if old_due_date_val is not None else ""
                old_precision = old["due_precision"]
                # defaults: no change
                new_due_at = old_due_at
                new_due_date = old_due_date_val  # keep as stored (date/str/None); will normalize to str/None for update
                # normalize new_due_date to str or None for SQL
                def _to_due_sql(v):
                    if v is None or v == "":
                        return None
                    return _date_str(v)
                new_due_date_sql = _to_due_sql(new_due_date)
                # healed value for legacy DATE missing civil
                # will be handled in else branch
                if has_clear:
                    new_due_at = None
                    new_due_date_sql = None
                    new_precision = "UNKNOWN"
                elif has_due_date:
                    # due_date follows DATE precision: force DATE, EndOfDay overwrites due_at
                    new_precision = "DATE"
                    new_due_date_sql = due_date_str
                    new_due_at = rules.end_of_day(due_date_obj, event_tz)
                elif precision_input == "DATE":
                    new_precision = "DATE"
                    if has_due_at:
                        civil = _civil_of(due_at_input, event_tz)
                        new_due_date_sql = civil.isoformat()
                        new_due_at = rules.end_of_day(civil, event_tz)
                    else:
                        # derive from stored/new instant
                        base = old_due_at
                        if base is not None:
                            civil = _civil_of(base, event_tz)
                            new_due_date_sql = civil.isoformat()
                            new_due_at = rules.end_of_day(civil, event_tz)
                        else:
                            # no instant: keep old due_date if any
                            new_due_at = old_due_at
                            # new_due_date_sql already old
                    # end
                elif precision_input == "DATETIME":
                    new_precision = "DATETIME"
                    new_due_date_sql = None  # switching away clears civil
                    if has_due_at:
                        new_due_at = due_at_input
                    else:
                        new_due_at = old_due_at
                elif precision_input == "UNKNOWN":
                    new_precision = "UNKNOWN"
                    new_due_at = None
                    new_due_date_sql = None
                else:
                    # no precision change
                    if has_due_at:
                        new_precision = "DATETIME"
                        new_due_at = due_at_input
                        new_due_date_sql = None
                    else:
                        new_precision = old_precision
                        # legacy heal: DATE rows missing civil healed from instant; UNKNOWN never backfilled
                        if old_precision == "DATE" and (old_due_date_val is None or old_due_date_val == "") and old_due_at is not None:
                            civil = _civil_of(old_due_at, event_tz)
                            new_due_date_sql = civil.isoformat()
                            # keep instant unchanged (heal date only)
                            new_due_at = old_due_at
                        else:
                            new_due_at = old_due_at
                            new_due_date_sql = _to_due_sql(old_due_date_val)
                # other fields
                new_title = data["title"].strip() if has_title else old["title"]
                new_confirmed = bool(data["date_confirmed"]) if has_confirmed else bool(old["date_confirmed"])
                if new_precision == "UNKNOWN":
                    # UNKNOWN forces date_confirmed false? Keep provided? For patch, spec says UNKNOWN sets date_confirmed false in POST context.
                    # For PATCH, if switching to UNKNOWN via clear or precision, force false? Let's force false for clear/UNKNOWN.
                    # Only force if has_clear or precision_input == UNKNOWN.
                    if has_clear or precision_input == "UNKNOWN":
                        # Do not override explicit true? POST forces false; apply same.
                        new_confirmed = False
                new_required = bool(data["required"]) if has_required else bool(old["required"])
                new_depart = depart_input if has_depart else old.get("depart_at")
                # completed_at
                if status_changed:
                    completed_at = now if new_status == "DONE" else None
                else:
                    completed_at = old.get("completed_at")
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE milestones SET title = %s, due_at = %s, due_date = %s, due_precision = %s, date_confirmed = %s, "
                        "required = %s, status = %s, depart_at = %s, completed_at = %s, updated_at = now(), version = version + 1 "
                        "WHERE id = %s AND user_id = %s",
                        (new_title, new_due_at, new_due_date_sql, new_precision, new_confirmed, new_required, new_status, new_depart, completed_at, milestone_id, user_id),
                    )
                if status_changed:
                    rules.record_transition(conn, user_id, "milestone", milestone_id, "status", old_status, new_status, "USER")
                reminders.regenerate(conn, milestone_id, now)
                _ensure_confirm_dates(conn, user_id, hackathon_id, now)
                return _empty_204()
    except NotFound:
        raise
    except Invalid:
        raise
    except Exception as e:
        if is_bad_uuid_error(e):
            raise NotFound()
        raise


@router.delete("/milestones/{milestone_id}", status_code=204)
def delete_milestone(milestone_id: str, user_id: str = Depends(require_user_dependency)):
    if not isinstance(user_id, str):
        return user_id
    if not _valid_uuid(milestone_id):
        raise NotFound()
    try:
        with db.connect() as conn:
            with conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM milestones WHERE id = %s AND user_id = %s", (milestone_id, user_id))
                    if cur.rowcount == 0:
                        raise NotFound()
        return _empty_204()
    except NotFound:
        raise
    except Exception as e:
        if is_bad_uuid_error(e):
            raise NotFound()
        raise


@router.post("/notifications/{delivery_id}/ack")
async def ack_notification(delivery_id: str, request: Request, user_id: str = Depends(require_user_dependency)):
    if not isinstance(user_id, str):
        return user_id
    if not _valid_uuid(delivery_id):
        raise NotFound()
    data = await body_bytes()(request)
    action = data.get("action")
    if action not in ("DONE", "SNOOZE"):
        raise Invalid("action must be DONE or SNOOZE")
    now = datetime.now(timezone.utc)
    try:
        with db.connect() as conn:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT d.*, m.id AS mid, m.status AS mstatus FROM notification_deliveries d "
                        "JOIN notification_jobs j ON j.id = d.job_id JOIN milestones m ON m.id = j.milestone_id "
                        "WHERE d.id = %s AND d.user_id = %s FOR UPDATE OF d",
                        (delivery_id, user_id),
                    )
                    row = cur.fetchone()
                if not row:
                    raise NotFound()
                if row.get("acknowledged_at") is not None:
                    stored = row.get("action") or action
                    return {"action": stored, "repeated": True}
                with conn.cursor() as cur:
                    cur.execute("UPDATE notification_deliveries SET acknowledged_at = now(), action = %s, updated_at = now() WHERE id = %s", (action, delivery_id))
                milestone_id = str(row["mid"])
                mstatus = row["mstatus"]
                # MISSED + DONE -> mark DONE
                if mstatus == "MISSED" and action == "DONE":
                    _update_milestone_status(conn, user_id, milestone_id, "DONE", "USER", now)
                    return {"action": action, "repeated": False}
                if mstatus != "TODO":
                    return {"action": action, "repeated": False}
                # TODO
                if action == "SNOOZE":
                    choice = data.get("snooze")
                    # choice must be validated by reminders.snooze; if missing, snooze will raise invalid
                    snoozed_until = reminders.snooze(conn, user_id, milestone_id, delivery_id, choice, now)
                    return {"action": action, "snoozed_until": _utc_str(snoozed_until), "repeated": False}
                else:  # DONE
                    _update_milestone_status(conn, user_id, milestone_id, "DONE", "USER", now)
                    return {"action": action, "repeated": False}
    except NotFound:
        raise
    except Invalid:
        raise
    except Exception as e:
        if is_bad_uuid_error(e):
            raise NotFound()
        raise
