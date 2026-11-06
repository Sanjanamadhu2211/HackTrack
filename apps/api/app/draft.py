import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def load_loc(name):
    try:
        return ZoneInfo(name) if name else timezone.utc
    except Exception:
        return timezone.utc


def parse_date(value, tz_name="UTC", out=None):
    """Returns (instant_or_None, precision). Civil date is written into out."""
    if not value:
        return None, "UNKNOWN"
    value = value.strip()
    loc = load_loc(tz_name)
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            kind = "DATETIME"
            raw = value
            if fmt.endswith("%z") and raw.endswith(("Z", "+00:00")) is False and ("+" in raw or raw.count("-") > 2):
                pass
            parsed = datetime.strptime(value, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=loc)
            return parsed.astimezone(timezone.utc), kind
        except ValueError:
            continue
    try:
        from datetime import date

        parsed_date = date.fromisoformat(value)
        if out is not None:
            out["civil"] = parsed_date.isoformat()
        return None, "DATE"
    except ValueError:
        pass
    return None, "UNKNOWN"


def civil_date_of(instant, tz_name):
    if not instant:
        return ""
    return instant.astimezone(load_loc(tz_name)).strftime("%Y-%m-%d")


def end_of_day(date_str, tz_name):
    try:
        from datetime import date

        y, m, d = (int(part) for part in date_str.split("-"))
        local = datetime(y, m, d, 23, 59, 0, tzinfo=load_loc(tz_name))
        return local.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def tz_midnight(date_str, tz_name):
    try:
        y, m, d = (int(part) for part in date_str.split("-"))
        return datetime(y, m, d, 0, 0, tzinfo=load_loc(tz_name)).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def normalize_mode(text):
    if not text:
        return ""
    lower = text.lower()
    if "mixed" in lower or "hybrid" in lower:
        return "HYBRID"
    if "online" in lower or "virtual" in lower or "remote" in lower:
        return "ONLINE"
    if "offline" in lower or "in-person" in lower or "in person" in lower:
        return "OFFLINE"
    return ""


def type_from_label(label):
    lower = (label or "").lower()
    if "rsvp" in lower:
        return "RSVP"
    if "regist" in lower or "application" in lower:
        return "REGISTER"
    if "team" in lower:
        return "TEAM"
    if "submi" in lower or "prototype" in lower or "idea" in lower:
        return "SUBMIT"
    if "check-in" in lower or "check in" in lower:
        return "CHECK_IN"
    if "present" in lower or "demo" in lower or "pitch" in lower or "finale" in lower:
        return "PRESENT"
    return "OTHER"


class Draft:
    def __init__(self):
        self.platform = ""
        self.title = ""
        self.organizer = ""
        self.mode = ""
        self.location = ""
        self.starts_at = None
        self.ends_at = None
        self.starts_date = ""
        self.ends_date = ""
        self.timezone = ""
        self.milestones = []
        self.field_sources = {}

    def set_string(self, field, value, source):
        value = (value or "").strip()
        if value and not getattr(self, field):
            setattr(self, field, value)
            self.field_sources[field] = source

    def set_time(self, field, instant, precision, source, civil=""):
        if instant is None and not civil:
            return
        if getattr(self, field) is not None:
            return
        setattr(self, field, instant)
        if civil:
            setattr(self, field + "_date", civil)
        self.field_sources[field] = source

    def add_milestone(self, milestone_type, title, due_at, due_date, precision, source, source_quote=""):
        for existing in self.milestones:
            if existing["type"] == milestone_type and existing["title"] == title:
                return
        item = {"type": milestone_type, "title": title, "due_precision": precision}
        if due_at:
            item["due_at"] = due_at
        if due_date:
            item["due_date"] = due_date
        if source_quote:
            item["source_quote"] = source_quote
        self.milestones.append(item)
        self.field_sources["milestones"] = source

    def has_dates(self):
        if self.starts_at or self.ends_at:
            return True
        return any(m.get("due_at") for m in self.milestones)

    def to_json(self):
        result = {}
        if self.platform:
            result["platform"] = self.platform
        if self.title:
            result["title"] = self.title
        if self.organizer:
            result["organizer"] = self.organizer
        if self.mode:
            result["mode"] = self.mode
        if self.location:
            result["location"] = self.location
        if self.starts_at:
            result["starts_at"] = self.starts_at.isoformat().replace("+00:00", "Z")
        if self.starts_date:
            result["starts_date"] = self.starts_date
        if self.ends_at:
            result["ends_at"] = self.ends_at.isoformat().replace("+00:00", "Z")
        if self.ends_date:
            result["ends_date"] = self.ends_date
        if self.timezone:
            result["timezone"] = self.timezone
        result["milestones"] = [
            {
                "type": m["type"],
                "title": m["title"],
                **({"due_at": m["due_at"].isoformat().replace("+00:00", "Z")} if m.get("due_at") else {}),
                **({"due_date": m["due_date"]} if m.get("due_date") else {}),
                "due_precision": m["due_precision"],
                **({"source_quote": m["source_quote"]} if m.get("source_quote") else {}),
            }
            for m in self.milestones
        ]
        result["field_sources"] = self.field_sources or {}
        return result


def extract(body, final_url):
    from app.platforms import try_platform_then_metadata

    return try_platform_then_metadata(body, final_url)


def page_text(body):
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(body, "html.parser")
    target = soup.body or soup
    for tag in target(["script", "style"]):
        tag.decompose()
    text = " ".join(target.get_text(" ").split())
    return text[:200000]
