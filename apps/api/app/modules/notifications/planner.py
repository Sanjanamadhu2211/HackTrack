from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

CHANNEL = "DISCORD"

POLICY = {
    "REGISTER": [("T-7d", timedelta(days=7), False), ("T-2d", timedelta(days=2), False), ("T-6h", timedelta(hours=6), False)],
    "RSVP": [("T-create", timedelta(0), True), ("T-24h", timedelta(days=1), False), ("T-3h", timedelta(hours=3), False)],
    "TEAM": [("T-create", timedelta(0), True), ("T-24h", timedelta(days=1), False), ("T-3h", timedelta(hours=3), False)],
    "SUBMIT": [("T-72h", timedelta(days=3), False), ("T-24h", timedelta(days=1), False), ("T-6h", timedelta(hours=6), False), ("T-1h", timedelta(hours=1), False)],
    "CHECK_IN": [("T-48h", timedelta(days=2), False), ("T-12h", timedelta(hours=12), False)],
    "START": [("T-48h", timedelta(days=2), False), ("T-12h", timedelta(hours=12), False)],
    "CONFIRM_DATES": [("T-0", timedelta(0), False)],
    "PRESENT": [("T-24h", timedelta(days=1), False), ("T-1h", timedelta(hours=1), False)],
    "OTHER": [("T-24h", timedelta(days=1), False), ("T-1h", timedelta(hours=1), False)],
}


class Prefs:
    def __init__(self, location, quiet_start, quiet_end, cap_per_day):
        self.location = location
        self.quiet_start = quiet_start
        self.quiet_end = quiet_end
        self.cap_per_day = cap_per_day

    def quiet_window(self, t):
        if self.quiet_start == self.quiet_end:
            return None
        local = t.astimezone(self.location)
        for off in (-1, 0):
            start = datetime(local.year, local.month, local.day + off, self.quiet_start // 60, self.quiet_start % 60, tzinfo=self.location)
            end_day = local.day + off + (1 if self.quiet_end < self.quiet_start else 0)
            end = datetime(local.year, local.month, end_day, self.quiet_end // 60, self.quiet_end % 60, tzinfo=self.location)
            if start <= t < end:
                return start, end
        return None

    def shift(self, at, due, now):
        window = self.quiet_window(at)
        if not window:
            return at
        start, end = window
        at = start if due < end else end
        return now if at < now else at


def plan(milestone_type, due, depart, first, reminded, now, prefs):
    out = []
    for label, before, on_create in POLICY.get(milestone_type, []):
        at = due - before
        if on_create:
            if not first:
                continue
            at = now
        if at < now:
            continue
        out.append({"label": label, "at": at, "final": False})
    if not out and due > now and not reminded:
        out.append({"label": "T-late", "at": now, "final": False})
    for item in out:
        item["at"] = prefs.shift(item["at"], due, now)
    if out:
        out[-1]["final"] = True
    if depart and milestone_type in ("CHECK_IN", "START") and depart > now and depart <= due:
        out.append({"label": "leave-by", "at": depart, "final": True})
    return out


def staleness_window(label):
    return timedelta(hours=2) if label == "T-7d" else timedelta(minutes=30)


def idempotency_key(milestone_id, version, label):
    return f"{milestone_id}:{version}:{label}:{CHANNEL}"


def load_prefs(conn, user_id):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT timezone, "
            "extract(hour FROM quiet_hours_start)::int * 60 + extract(minute FROM quiet_hours_start)::int, "
            "extract(hour FROM quiet_hours_end)::int * 60 + extract(minute FROM quiet_hours_end)::int, "
            "reminder_cap_per_day FROM users WHERE id = %s",
            (user_id,),
        )
        row = cur.fetchone()
    try:
        location = ZoneInfo(row["timezone"])
    except Exception:
        location = timezone.utc
    return Prefs(location, row["qs"], row["qe"], row["reminder_cap_per_day"])


def insert_job(conn, user_id, milestone_id, item, key):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO notification_jobs (user_id, milestone_id, channel, offset_label, final, scheduled_at, idempotency_key) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (idempotency_key) DO NOTHING",
            (user_id, milestone_id, CHANNEL, item["label"], item["final"], item["at"], key),
        )


def cancel_pending(conn, milestone_id):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE notification_jobs SET state = 'CANCELLED', lease_until = NULL, updated_at = now() "
            "WHERE milestone_id = %s AND state IN ('PENDING', 'CLAIMED')",
            (milestone_id,),
        )


def cancel_for_hackathon(conn, hackathon_id, only_type):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE notification_jobs j SET state = 'CANCELLED', lease_until = NULL, updated_at = now() "
            "FROM milestones m WHERE j.milestone_id = m.id AND m.hackathon_id = %s "
            "AND j.state IN ('PENDING', 'CLAIMED') AND (%s = '' OR m.type = %s)",
            (hackathon_id, only_type, only_type),
        )


def regenerate(conn, milestone_id, now):
    cancel_pending(conn, milestone_id)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT m.user_id, m.type, m.due_at, m.depart_at, m.version, "
            "m.status = 'TODO' AND m.date_confirmed AND m.due_at IS NOT NULL AND h.archived_at IS NULL "
            "AND h.registration_state NOT IN ('SKIPPED', 'REJECTED') "
            "AND (h.outcome IS NULL OR (h.outcome = 'SUBMITTED' AND m.type <> 'SUBMIT')) AS eligible, "
            "EXISTS (SELECT 1 FROM notification_jobs j WHERE j.milestone_id = m.id AND j.state = 'SENT') AS already "
            "FROM milestones m JOIN hackathons h ON h.id = m.hackathon_id WHERE m.id = %s",
            (milestone_id,),
        )
        row = cur.fetchone()
    if not row or not row["eligible"]:
        return
    prefs = load_prefs(conn, str(row["user_id"]))
    planned = plan(
        row["type"],
        row["due_at"],
        row["depart_at"],
        row["version"] == 1,
        bool(row["already"]),
        now,
        prefs,
    )
    for item in planned:
        insert_job(conn, str(row["user_id"]), milestone_id, item, idempotency_key(milestone_id, row["version"], item["label"]))


def replan_user(conn, user_id, now):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE milestones SET version = version + 1, updated_at = now() "
            "WHERE user_id = %s AND status = 'TODO' AND due_at > %s RETURNING id",
            (user_id, now),
        )
        ids = [str(r["id"]) for r in cur.fetchall()]
    for milestone_id in ids:
        regenerate(conn, milestone_id, now)


def snooze(conn, user_id, milestone_id, delivery_id, choice, now):
    prefs = load_prefs(conn, user_id)
    if choice == "1h":
        at = now + timedelta(hours=1)
    elif choice == "3h":
        at = now + timedelta(hours=3)
    elif choice == "tomorrow":
        local = now.astimezone(prefs.location)
        at = datetime(local.year, local.month, local.day + 1, 9, 0, tzinfo=prefs.location)
    else:
        from app.errors import Invalid

        raise Invalid("snooze must be 1h, 3h or tomorrow")
    with conn.cursor() as cur:
        cur.execute("SELECT due_at, version FROM milestones WHERE id = %s AND user_id = %s", (milestone_id, user_id))
        row = cur.fetchone()
        due, version = (row["due_at"], row["version"]) if row else (None, 1)
    if due:
        latest = due - timedelta(minutes=5)
        if at > latest:
            at = latest
    if at < now:
        at = now
    insert_job(
        conn,
        user_id,
        milestone_id,
        {"label": "snooze", "at": at, "final": True},
        idempotency_key(milestone_id, version, "snooze-" + delivery_id),
    )
    return at
