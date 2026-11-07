import base64
import hashlib
import hmac
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

from email.header import decode_header
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app import db
from app.auth import require_user_dependency
from app.errors import Invalid, NotFound, body_bytes, is_bad_uuid_error

try:
    from psycopg.types.json import Jsonb
except Exception:  # pragma: no cover
    Jsonb = None

router = APIRouter()

THREE_MIB = 3 * 1024 * 1024
ONE_MIB = 1024 * 1024

# Env names (config.py does not yet have these fields; read directly):
#   INBOUND_EMAIL_DOMAIN, MAILGUN_SIGNING_KEY
# CALENDAR_TOKEN_KEY is read by app.calendar via os.getenv too.

_REJECTION = [
    "waitlisted",
    "on the waitlist",
    "on waitlist",
    "on the wait list",
    "wait list",
    "not selected",
    "has not been selected",
    "have not been selected",
    "were not selected",
    "was not selected",
    "not been selected",
    "did not get selected",
    "didn't get selected",
    "unfortunately",
    "we regret",
    "regret to inform",
    "rejected",
    "rejection",
    "not accepted",
    "not able to accept",
    "unable to accept",
    "cannot accept",
    "can't accept",
    "could not accept",
    "couldn't accept",
    "not shortlisted",
    "not chosen",
    "not offered",
    "not moving forward",
    "will not be moving forward",
    "not been chosen",
    "did not qualify",
    "did not make it",
    "didn't make it",
    "not able to offer",
    "unable to offer",
    "we received many applications",
    "received many applications",
    "many applications",
    "tough choice",
    "difficult choice",
    "we will not be able to",
    "will not be able to offer",
    "please note that we will not",
    "we are unable to",
    "not proceeding",
    "not moving ahead",
    "we are not moving forward",
    "not a fit",
    "sadly",
]

_ACCEPTANCE = [
    "congratulations",
    "you're in!",
    "you're in",
    "youre in!",
    "youre in",
    "have been selected",
    "has been selected",
    "you have been selected",
    "you've been selected",
    "you are selected",
    "you're selected",
    "we have selected",
    "we selected you",
    "you have been accepted",
    "you've been accepted",
    "you are accepted",
    "you're accepted",
    "has been accepted",
    "have been accepted",
    "application accepted",
    "accepted your application",
    "accepted to",
    "we are happy to accept",
    "we're happy to accept",
    "happy to accept",
    "glad to accept",
    "pleased to accept",
    "you made the cut",
    "you made it",
    "you're welcome to join",
    "welcome aboard",
    "welcome to",
]

_RSVP = [
    "confirm your spot",
    "confirm your seat",
    "confirm your attendance",
    "confirm your participation",
    "confirm your presence",
    "confirm your place",
    "confirm your availability",
    "please confirm",
    "kindly confirm",
    "please respond",
    "respond by",
    "respond to this email",
    "reserve your spot",
    "reserve your seat",
    "reserve your place",
    "rsvp",
    "need to confirm",
    "you must confirm",
    "confirm now",
]

_REGISTRATION = [
    "registration confirmed",
    "successfully registered",
    "application received",
    "registration successful",
    "successfully submitted",
    "we have received your application",
    "your application has been received",
    "application has been received",
    "registration received",
    "registration complete",
    "registration has been confirmed",
    "you are registered",
    "you're registered",
    "you have registered",
    "we have received your registration",
    "we received your application",
    "application submitted",
    "registration submitted",
    "you have successfully registered",
]

_STOPWORDS = {
    "the", "a", "an", "of", "for", "and", "in", "on", "at", "to", "is", "are",
    "was", "were", "be", "been", "being", "by", "with", "from", "this", "that",
    "your", "you", "we", "our", "it", "its", "as", "via", "welcome", "hack",
    "hackathon", "hackathons", "hacker", "hackers", "eth", "please", "thank",
    "thanks", "congratulations", "re", "fw", "fwd", "subject", "date",
    "deadline", "rsvp", "registration", "register", "application",
}

_LINK_REGEX = re.compile(r"https?://[^\s\"<>]+")
_DEADLINE_KEYWORD = re.compile(
    r"\b(deadline|by|before|until|due|clos(?:e|es|ing)|last\s+date|no\s+later\s+than|rsvp)\b",
    re.IGNORECASE,
)
_MONTHS = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}
_MONTH_ALT = "|".join(sorted(_MONTHS.keys(), key=len, reverse=True))
_ISO_DATETIME_RE = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?"
)
_ISO_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_MONTH_FIRST_RE = re.compile(
    r"\b(" + _MONTH_ALT + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?\s*,?\s*(\d{4})?\b",
    re.IGNORECASE,
)
_DAY_FIRST_RE = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + _MONTH_ALT + r")\.?\s*,?\s*(\d{4})?\b",
    re.IGNORECASE,
)
_TIME_24_RE = re.compile(r"\b(\d{1,2}):(\d{2})(?::\d{2})?\b")
_TIME_12_RE = re.compile(r"\b(0?[1-9]|1[0-2])(?::([0-5]\d))?\s*(am|pm)\b", re.IGNORECASE)
# Numeric zone offsets must be standalone UTC offsets, not dashes inside an
# ISO date. Require a colon and leading whitespace (or line start) so that
# "2026-12-01T17:00:00Z" cannot be mistaken for an offset.
_NUMERIC_OFFSET_RE = re.compile(r"(?:^|\s)([+-])(\d{1,2}):(\d{2})\b")

TZ_MIN = {
    "UTC": 0, "GMT": 0, "UT": 0, "Z": 0,
    "EST": -300, "EDT": -240, "CST": -360, "CDT": -300,
    "MST": -420, "MDT": -360, "PST": -480, "PDT": -420,
    "AKST": -540, "AKDT": -480, "HST": -600, "AHST": -600,
    "AST": -240, "ADT": -180, "NST": -210, "NDT": -150,
    "CET": 60, "CEST": 120, "EET": 120, "EEST": 180,
    "WET": 0, "WEST": 60, "BST": 60,
    "MSK": 180, "GST": 240, "PKT": 300, "IST": 330,
    "JST": 540, "KST": 540, "SGT": 480, "HKT": 480,
    "AEST": 600, "AEDT": 660, "ACST": 570, "ACDT": 630,
    "AWST": 480, "NZST": 720, "NZDT": 780,
    "CHST": 600, "SST": 660, "WEST2": 60,
}


def _inbound_domain():
    return os.getenv("INBOUND_EMAIL_DOMAIN", "").strip()


def _mailgun_key():
    return os.getenv("MAILGUN_SIGNING_KEY", "")


def _raw_b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def hash_token_str(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def verify_mailgun_signature(timestamp, token, signature, signing_key=None):
    """Verify Mailgun webhook signature. Returns True if valid."""
    key = signing_key if signing_key is not None else _mailgun_key()
    ts = "" if timestamp is None else str(timestamp).strip()
    tok = "" if token is None else str(token).strip()
    sig = "" if signature is None else str(signature).strip()
    if not key or not tok or not sig:
        return False
    if not re.fullmatch(r"-?\d+", ts or ""):
        return False
    try:
        ts_int = int(ts)
    except ValueError:
        return False
    if abs(int(time.time()) - ts_int) > 300:
        return False
    try:
        bytes.fromhex(sig)
    except ValueError:
        return False
    expected = hmac.new(key.encode(), (ts + tok).encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.lower(), sig.lower())


def detect_kind(subject, text) -> str:
    haystack = f"{subject or ''}\n{text or ''}".lower().replace("’", "'")
    for phrase in _REJECTION:
        if phrase in haystack:
            return "OTHER"
    for phrase in _ACCEPTANCE:
        if phrase in haystack:
            return "ACCEPTANCE"
    for phrase in _RSVP:
        if phrase in haystack:
            return "RSVP"
    for phrase in _REGISTRATION:
        if phrase in haystack:
            return "REGISTRATION"
    return "OTHER"


classify_kind = detect_kind


def guess_hackathon_name(subject, text) -> str:
    candidates = []
    if subject:
        candidates.append(subject)
    if text:
        candidates.extend(text.splitlines()[:8])
    for candidate in candidates:
        name = _guess_name_from_line(candidate or "")
        if name:
            return name
    return ""


guess_name = guess_hackathon_name
hackathon_name_for = guess_hackathon_name


def _clean_token(raw: str) -> str:
    return (raw or "").strip(".,;:!?\"'()[]{}<>").strip()


def _cap_or_digit(clean: str) -> bool:
    return bool(clean) and (clean[0].isupper() or clean[0].isdigit())


def _guess_name_from_line(line: str) -> str:
    if not line or not line.strip():
        return ""
    tokens = line.strip().split()
    if not tokens:
        return ""
    for i, raw in enumerate(tokens):
        clean = _clean_token(raw)
        if not clean:
            continue
        lower = clean.lower()
        if lower in _STOPWORDS:
            continue
        if not _cap_or_digit(clean):
            continue
        is_anchor = "hack" in lower or bool(re.match(r"^eth[a-z]", lower))
        if not is_anchor:
            continue
        left = []
        for j in range(i - 1, max(-1, i - 5), -1):
            raw_j = tokens[j]
            if raw_j and raw_j[-1] in ".,;:!?":
                break
            clean_j = _clean_token(raw_j)
            if not clean_j:
                break
            low_j = clean_j.lower()
            if low_j in ("the", "of", "for"):
                left.append(clean_j)
                continue
            if _cap_or_digit(clean_j):
                left.append(clean_j)
                continue
            break
        left.reverse()
        right = []
        for j in range(i + 1, min(len(tokens), i + 4)):
            raw_j = tokens[j]
            if raw_j and raw_j[-1] in ".,;:!?":
                break
            clean_j = _clean_token(raw_j)
            if not clean_j:
                break
            if _cap_or_digit(clean_j):
                right.append(clean_j)
                continue
            break
        return " ".join(left + [clean] + right)
    return ""


def _decode_header(raw) -> str:
    if raw is None:
        return ""
    text = str(raw)
    try:
        parts = decode_header(text)
    except Exception:
        return text
    out = []
    for chunk, charset in parts:
        if isinstance(chunk, bytes):
            if charset:
                try:
                    out.append(chunk.decode(charset, errors="replace"))
                except Exception:
                    out.append(chunk.decode("utf-8", errors="replace"))
            else:
                out.append(chunk.decode("utf-8", errors="replace"))
        else:
            out.append(chunk)
    return "".join(out)


def _parse_utc(value: str):
    try:
        parsed = parsedate_to_datetime((value or "").strip())
    except Exception:
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _received_at(msg) -> datetime:
    try:
        received = msg.get_all("Received", [])
    except Exception:
        received = []
    if received:
        first = str(received[0])
        candidate = first.split(";")[-1].strip()
        parsed = _parse_utc(candidate)
        if parsed is not None:
            return parsed
    date_header = msg.get("Date")
    if date_header is not None:
        parsed = _parse_utc(str(date_header))
        if parsed is not None:
            return parsed
    return datetime.now(timezone.utc)


def _part_text(part) -> str:
    raw = part.get_payload(decode=True)
    if raw is None:
        payload = part.get_payload(decode=False)
        raw = str(payload or "").encode("utf-8", errors="replace")
    raw = raw[:ONE_MIB]
    charset = None
    try:
        charset = part.get_content_charset()
    except Exception:
        charset = None
    if not charset:
        charset = "utf-8"
    try:
        return raw.decode(charset, errors="replace")
    except Exception:
        return raw.decode("utf-8", errors="replace")


def _html_to_text(html_str: str):
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_str or "", "html.parser")
    if soup.head:
        soup.head.decompose()
    for tag in soup(["script", "style"]):
        tag.decompose()
    hrefs = []
    for a in soup.find_all("a", attrs={"href": True}):
        href = a.get("href")
        if href:
            hrefs.append(href)
    for node in soup.find_all(["p", "div", "li", "tr", "h1", "h2", "h3", "table"]):
        try:
            node.insert_before("\n")
            node.insert_after("\n")
        except Exception:
            pass
    for br in soup.find_all("br"):
        try:
            br.replace_with("\n")
        except Exception:
            pass
    raw_text = soup.get_text(separator=" ")
    lines = []
    for line in raw_text.splitlines():
        squashed = re.sub(r"\s+", " ", line).strip()
        if squashed:
            lines.append(squashed)
    return "\n".join(lines), hrefs


def extract_links(text: str, hrefs) -> list:
    seen = set()
    out = []
    for href in hrefs or []:
        url = (href or "").strip().rstrip(".,;:!?")
        if not url or url in seen:
            continue
        try:
            scheme = urlparse(url).scheme.lower()
        except Exception:
            continue
        if scheme not in ("http", "https"):
            continue
        seen.add(url)
        out.append(url)
        if len(out) >= 50:
            return out
    for hit in _LINK_REGEX.findall(text or ""):
        url = hit.strip().rstrip(".,;:!?")
        if not url or url in seen:
            continue
        try:
            scheme = urlparse(url).scheme.lower()
        except Exception:
            continue
        if scheme not in ("http", "https"):
            continue
        seen.add(url)
        out.append(url)
        if len(out) >= 50:
            break
    return out


def _zone_offset_minutes(line: str, after_pos=None):
    if not line:
        return 0
    if after_pos is not None:
        rest = line[after_pos : after_pos + 20]
        stripped = rest.lstrip()
        if stripped[:1].upper() == "Z" or stripped[:3].upper() in ("UTC", "GMT"):
            return 0
        tail = re.match(r"([+-])(\d{2})(?::(\d{2}))?", stripped)
        if tail:
            sign = 1 if tail.group(1) == "+" else -1
            hours = int(tail.group(2))
            minutes = int(tail.group(3) or 0)
            if hours <= 14 and minutes <= 59:
                return sign * (hours * 60 + minutes)
        abbrev = re.match(r"([A-Za-z]{1,4})\b", stripped)
        if abbrev and abbrev.group(1).upper() in TZ_MIN:
            return TZ_MIN[abbrev.group(1).upper()]
    match = _NUMERIC_OFFSET_RE.search(line)
    if match:
        sign = 1 if match.group(1) == "+" else -1
        hours = int(match.group(2))
        minutes = int(match.group(3) or 0)
        if hours > 14 or minutes > 59:
            return None
        return sign * (hours * 60 + minutes)
    words = re.findall(r"[A-Za-z]{1,4}", line)
    for word in words:
        upper = word.upper()
        if upper in TZ_MIN:
            return TZ_MIN[upper]
    return 0


def _find_time(line: str):
    match = _TIME_12_RE.search(line or "")
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        suffix = match.group(3).lower()
        if suffix == "am":
            if hour == 12:
                hour = 0
        else:
            if hour != 12:
                hour += 12
        return hour, minute
    match = _TIME_24_RE.search(line or "")
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    return None


def _try_make_instant(year, month, day, hour, minute, second, offset_min, received_at):
    try:
        tz = timezone(timedelta(minutes=offset_min))
        instant_local = datetime(year, month, day, hour, minute, second, tzinfo=tz)
    except ValueError:
        return None
    return instant_local.astimezone(timezone.utc)


def _deadline_from_line(line: str, received_at: datetime):
    cutoff = received_at - timedelta(hours=24)
    offset = _zone_offset_minutes(line)
    if offset is None:
        offset = 0
    iso_dt = _ISO_DATETIME_RE.search(line)
    if iso_dt:
        offset = _zone_offset_minutes(line, after_pos=iso_dt.end())
        if offset is None:
            offset = 0
        year, month, day = int(iso_dt.group(1)), int(iso_dt.group(2)), int(iso_dt.group(3))
        hour = int(iso_dt.group(4))
        minute = int(iso_dt.group(5))
        second = int(iso_dt.group(6) or 0)
        instant = _try_make_instant(year, month, day, hour, minute, second, offset, received_at)
        if instant is None:
            return None
        if instant < cutoff:
            return None
        return {"deadline": instant, "precision": "DATETIME", "civil": None}
    iso_date = _ISO_DATE_RE.search(line)
    if iso_date:
        year, month, day = int(iso_date.group(1)), int(iso_date.group(2)), int(iso_date.group(3))
        found_time = _find_time(line)
        if found_time is not None:
            instant = _try_make_instant(year, month, day, found_time[0], found_time[1], 0, offset, received_at)
            if instant is None or instant < cutoff:
                return None
            return {"deadline": instant, "precision": "DATETIME", "civil": None}
        instant = _try_make_instant(year, month, day, 23, 59, 0, offset, received_at)
        if instant is None or instant < cutoff:
            return None
        return {"deadline": instant, "precision": "DATE", "civil": f"{year:04d}-{month:02d}-{day:02d}"}
    month_match = _MONTH_FIRST_RE.search(line)
    day_match = _DAY_FIRST_RE.search(line)
    chosen = None
    if month_match and day_match:
        chosen = month_match if month_match.start() <= day_match.start() else day_match
    elif month_match:
        chosen = month_match
    elif day_match:
        chosen = day_match
    if chosen is None:
        return None
    groups = chosen.groups()
    if chosen is month_match:
        month = _MONTHS[groups[0].lower()]
        day = int(groups[1])
        year_raw = groups[2]
    else:
        day = int(groups[0])
        month = _MONTHS[groups[1].lower()]
        year_raw = groups[2]
    year_explicit = bool(year_raw)
    year = int(year_raw) if year_explicit else received_at.year
    found_time = _find_time(line)
    if found_time is not None:
        instant = _try_make_instant(year, month, day, found_time[0], found_time[1], 0, offset, received_at)
        if instant is None:
            return None
        if not year_explicit and instant < cutoff:
            instant = _try_make_instant(year + 1, month, day, found_time[0], found_time[1], 0, offset, received_at)
            if instant is None:
                return None
        if instant < cutoff:
            return None
        return {"deadline": instant, "precision": "DATETIME", "civil": None}
    instant = _try_make_instant(year, month, day, 23, 59, 0, offset, received_at)
    if instant is None:
        return None
    if not year_explicit and instant < cutoff:
        instant = _try_make_instant(year + 1, month, day, 23, 59, 0, offset, received_at)
        if instant is None:
            return None
    if instant < cutoff:
        return None
    civil = f"{instant.astimezone(timezone(timedelta(minutes=offset))).strftime('%Y-%m-%d')}"
    return {"deadline": instant, "precision": "DATE", "civil": civil}


def find_deadline(subject: str, text: str, received_at: datetime):
    lines = []
    if subject:
        lines.append(subject)
    if text:
        lines.extend(text.splitlines())
    for line in lines:
        stripped = (line or "").strip()
        if not stripped:
            continue
        if not _DEADLINE_KEYWORD.search(stripped):
            continue
        found = _deadline_from_line(stripped, received_at)
        if found is None:
            continue
        quote = stripped[:240]
        result = {
            "deadline": found["deadline"],
            "precision": found["precision"],
            "quote": quote,
            "civil": found.get("civil"),
        }
        return result
    return None


def parse_email(body_mime: str) -> dict:
    raw = (body_mime or "").encode("utf-8", errors="replace")
    msg = BytesParser(policy=default_compat()).parsebytes(raw)
    message_id = _decode_header(msg.get("Message-Id")).strip().strip("<>").strip()
    if not message_id:
        raise ValueError("invalid email")
    from_decoded = _decode_header(msg.get("From")).strip()
    from_addr = ""
    if from_decoded:
        try:
            _, parsed_addr = parseaddr(from_decoded)
        except Exception:
            parsed_addr = ""
        if parsed_addr and "@" in parsed_addr:
            from_addr = parsed_addr.lower()
        else:
            from_addr = from_decoded
    subject = " ".join(_decode_header(msg.get("Subject") or "").strip().split())
    received_at = _received_at(msg)
    plain = None
    html = None
    parts = []
    try:
        if msg.is_multipart():
            parts = list(msg.walk())
        else:
            parts = [msg]
    except Exception:
        parts = [msg]
    for part in parts:
        try:
            if part.is_multipart():
                continue
        except Exception:
            pass
        try:
            disp = str(part.get("Content-Disposition") or "")
            if "attachment" in disp.lower():
                continue
        except Exception:
            pass
        try:
            ctype = part.get_content_type()
        except Exception:
            ctype = "text/plain"
        if not ctype:
            ctype = "text/plain"
        ctype = ctype.lower()
        if ctype == "text/plain" and plain is None:
            plain = _part_text(part)
        elif ctype == "text/html" and html is None:
            html = _part_text(part)
    hrefs = []
    if plain is not None and plain != "":
        body_text = plain
        if html:
            _, hrefs = _html_to_text(html)
    elif html is not None:
        body_text, hrefs = _html_to_text(html)
    else:
        body_text = ""
    links = extract_links(body_text or "", hrefs)
    kind = detect_kind(subject, body_text or "")
    deadline_found = find_deadline(subject, body_text or "", received_at)
    hackathon_name = guess_hackathon_name(subject, body_text or "")
    parsed = {
        "message_id": message_id,
        "from": from_addr,
        "subject": subject,
        "received_at": received_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "text": body_text or "",
        "links": links,
        "kind": kind,
        "hackathon_name": hackathon_name,
    }
    if deadline_found is not None:
        parsed["deadline"] = deadline_found["deadline"].astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        parsed["deadline_precision"] = deadline_found["precision"]
        parsed["deadline_quote"] = deadline_found["quote"][:240]
        if deadline_found["precision"] == "DATE" and deadline_found.get("civil"):
            parsed["deadline_date"] = deadline_found["civil"]
    return parsed


def default_compat():
    import email.policy

    return email.policy.compat32


@router.post("/integrations/email")
def post_inbound_email(user_id: str = Depends(require_user_dependency)):
    if not isinstance(user_id, str):
        return user_id
    domain = _inbound_domain()
    if not domain:
        raise Invalid("email forwarding is not configured")
    token = _raw_b64url(secrets.token_bytes(32))
    token_hash = hash_token_str(token)
    try:
        with db.connect() as conn:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO inbound_addresses (user_id, token_hash) VALUES (%s, %s) "
                        "ON CONFLICT (user_id) DO UPDATE SET token_hash = EXCLUDED.token_hash",
                        (user_id, token_hash),
                    )
    except Exception:
        return JSONResponse({"error": "internal error"}, status_code=500)
    return {"address": f"u_{token}@{domain}"}


@router.delete("/integrations/email")
def delete_inbound_email(user_id: str = Depends(require_user_dependency)):
    if not isinstance(user_id, str):
        return user_id
    try:
        with db.connect() as conn:
            with conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM inbound_addresses WHERE user_id=%s", (user_id,))
    except Exception:
        return JSONResponse({"error": "internal error"}, status_code=500)
    return PlainTextResponse("", status_code=204)


def _form_str(form, key) -> str:
    value = form.get(key)
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


@router.post("/integrations/email/webhook/mime")
async def webhook_mime(request: Request):
    content_length = request.headers.get("content-length", "")
    if content_length.isdigit() and int(content_length) > THREE_MIB:
        return JSONResponse({"error": "invalid email"}, status_code=400)
    try:
        form = await request.form()
    except Exception:
        return JSONResponse({"error": "invalid email"}, status_code=400)
    recipient = _form_str(form, "recipient").strip()
    body_mime = _form_str(form, "body-mime")
    timestamp = _form_str(form, "timestamp").strip()
    mailgun_token = _form_str(form, "token").strip()
    signature = _form_str(form, "signature").strip()
    if len((body_mime or "").encode("utf-8", errors="replace")) > THREE_MIB:
        return JSONResponse({"error": "invalid email"}, status_code=400)
    domain = _inbound_domain()
    try:
        _, addr = parseaddr(recipient)
    except Exception:
        addr = ""
    if not addr or "@" not in addr:
        return JSONResponse({"error": "invalid recipient"}, status_code=400)
    localpart, _, addr_domain = addr.rpartition("@")
    if addr_domain.lower() != domain.lower():
        return JSONResponse({"error": "invalid recipient"}, status_code=400)
    if not localpart.startswith("u_"):
        return JSONResponse({"error": "invalid recipient"}, status_code=400)
    address_token = localpart[2:]
    if not address_token:
        return JSONResponse({"error": "invalid recipient"}, status_code=400)
    if not verify_mailgun_signature(timestamp, mailgun_token, signature):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    address_hash = hash_token_str(address_token)
    try:
        with db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id FROM inbound_addresses WHERE token_hash=%s", (address_hash,))
                row = cur.fetchone()
    except Exception:
        return JSONResponse({"error": "internal error"}, status_code=500)
    if not row:
        return JSONResponse({"error": "invalid recipient"}, status_code=400)
    user_id = str(row["user_id"])
    try:
        parsed = parse_email(body_mime)
    except ValueError:
        return JSONResponse({"error": "invalid email"}, status_code=400)
    except Exception:
        return JSONResponse({"error": "invalid email"}, status_code=400)
    mailgun_hash = hash_token_str(mailgun_token)
    try:
        with db.connect() as conn:
            with conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM inbound_webhook_tokens WHERE seen_at < now() - interval '10 minutes'")
                    cur.execute(
                        "INSERT INTO inbound_webhook_tokens (token_hash) VALUES (%s) ON CONFLICT (token_hash) DO NOTHING",
                        (mailgun_hash,),
                    )
                    if cur.rowcount == 0:
                        return PlainTextResponse("", status_code=204)
                    stored = dict(parsed)
                    stored["text"] = ""
                    stored["links"] = None
                    payload = Jsonb(stored) if Jsonb is not None else stored
                    cur.execute(
                        "INSERT INTO inbound_emails (user_id, message_id, parsed_json) VALUES (%s, %s, %s) "
                        "ON CONFLICT (user_id, message_id) DO NOTHING",
                        (user_id, parsed["message_id"], payload),
                    )
    except Exception:
        return JSONResponse({"error": "internal error"}, status_code=500)
    return PlainTextResponse("", status_code=204)


@router.get("/inbox")
def get_inbox(user_id: str = Depends(require_user_dependency)):
    if not isinstance(user_id, str):
        return user_id
    try:
        with db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, parsed_json FROM inbound_emails WHERE user_id=%s AND resolved_at IS NULL ORDER BY created_at DESC",
                    (user_id,),
                )
                rows = cur.fetchall()
    except Exception:
        return JSONResponse({"error": "internal error"}, status_code=500)
    items = [{"id": str(r["id"]), "email": r["parsed_json"]} for r in rows]
    return {"items": items}


def _apply_effects(cur, conn, user_id, hackathon_id, registration_state, parsed, now):
    kind = parsed.get("kind") or "OTHER"
    subject = parsed.get("subject") or ""
    message_id = parsed.get("message_id") or ""
    new_state = None
    if kind in ("REGISTRATION", "ACCEPTANCE") and registration_state in ("PLANNING", "DISCOVERED"):
        new_state = "REGISTERED"
    elif kind == "ACCEPTANCE" and registration_state in ("REGISTERED", "WAITLISTED"):
        new_state = "ACCEPTED"
    if new_state is not None:
        cur.execute(
            "UPDATE hackathons SET registration_state=%s, updated_at=now() WHERE id=%s",
            (new_state, hackathon_id),
        )
        cur.execute(
            "INSERT INTO registration_evidence (user_id, hackathon_id, type, reference, note) VALUES (%s, %s, 'EMAIL', %s, %s)",
            (user_id, hackathon_id, message_id, subject),
        )
        cur.execute(
            "INSERT INTO state_transitions (user_id, entity, entity_id, field, from_state, to_state, actor) VALUES (%s, 'hackathon', %s, 'registration_state', %s, %s, 'EMAIL')",
            (user_id, hackathon_id, registration_state, new_state),
        )
    milestone_id = None
    if kind in ("RSVP", "ACCEPTANCE") and parsed.get("deadline"):
        precision = parsed.get("deadline_precision") or "DATETIME"
        if precision == "DATE":
            due_at = None
            due_date = parsed.get("deadline_date") or None
        else:
            due_at = parsed.get("deadline")
            due_date = None
        cur.execute(
            "INSERT INTO milestones (user_id, hackathon_id, type, title, due_at, due_precision, date_confirmed, source, due_date) "
            "VALUES (%s, %s, 'RSVP', 'RSVP', %s, %s, false, 'EMAIL', %s) RETURNING id",
            (user_id, hackathon_id, due_at, precision, due_date),
        )
        row = cur.fetchone()
        if row:
            milestone_id = str(row["id"])
    return milestone_id, new_state


@router.post("/inbox/{id}/resolve")
async def resolve_inbox(id: str, request: Request, user_id: str = Depends(require_user_dependency)):
    if not isinstance(user_id, str):
        return user_id
    data = await body_bytes()(request)
    action = (data.get("action") or "").strip()
    if action not in ("match", "create", "ignore"):
        raise Invalid("action must be match, create or ignore")
    now = datetime.now(timezone.utc)
    try:
        with db.connect() as conn:
            with conn:
                with conn.cursor() as cur:
                    try:
                        cur.execute(
                            "SELECT id, user_id, message_id, parsed_json FROM inbound_emails "
                            "WHERE id=%s AND user_id=%s AND resolved_at IS NULL FOR UPDATE",
                            (id, user_id),
                        )
                    except Exception as exc:
                        if is_bad_uuid_error(exc):
                            raise NotFound()
                        raise
                    row = cur.fetchone()
                    if not row:
                        raise NotFound()
                    parsed = row["parsed_json"] or {}
                    if action == "ignore":
                        cur.execute("UPDATE inbound_emails SET resolved_at=now() WHERE id=%s", (row["id"],))
                        return {"hackathon_id": ""}
                    if action == "create":
                        title = (data.get("title") or "").strip()
                        if not title:
                            raise Invalid("title required")
                        cur.execute(
                            "INSERT INTO hackathons (user_id, title) VALUES (%s, %s) RETURNING id",
                            (user_id, title),
                        )
                        hackathon_id = str(cur.fetchone()["id"])
                        cur.execute(
                            "SELECT registration_state FROM hackathons WHERE id=%s AND user_id=%s FOR UPDATE",
                            (hackathon_id, user_id),
                        )
                        reg_row = cur.fetchone()
                        registration_state = reg_row["registration_state"] if reg_row else "PLANNING"
                        milestone_id, _ = _apply_effects(cur, conn, user_id, hackathon_id, registration_state, parsed, now)
                        if milestone_id:
                            try:
                                from app import reminders

                                reminders.regenerate(conn, milestone_id, now)
                            except Exception:
                                pass
                        cur.execute("UPDATE inbound_emails SET resolved_at=now() WHERE id=%s", (row["id"],))
                        return {"hackathon_id": hackathon_id}
                    hackathon_raw = (data.get("hackathon_id") or "").strip()
                    if not hackathon_raw:
                        raise NotFound()
                    try:
                        cur.execute(
                            "SELECT id, registration_state FROM hackathons WHERE id=%s AND user_id=%s FOR UPDATE",
                            (hackathon_raw, user_id),
                        )
                    except Exception as exc:
                        if is_bad_uuid_error(exc):
                            raise NotFound()
                        raise
                    hrow = cur.fetchone()
                    if not hrow:
                        raise NotFound()
                    hackathon_id = str(hrow["id"])
                    milestone_id, _ = _apply_effects(cur, conn, user_id, hackathon_id, hrow["registration_state"], parsed, now)
                    if milestone_id:
                        try:
                            from app import reminders

                            reminders.regenerate(conn, milestone_id, now)
                        except Exception:
                            pass
                    cur.execute("UPDATE inbound_emails SET resolved_at=now() WHERE id=%s", (row["id"],))
                    return {"hackathon_id": hackathon_id}
    except NotFound:
        raise
    except Invalid:
        raise
    except Exception:
        return JSONResponse({"error": "internal error"}, status_code=500)
