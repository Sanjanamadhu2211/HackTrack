from urllib.parse import urlparse

from app import db, fetching
from app.ai import extract_with_ai
from app.draft import Draft, extract, page_text
from app.errors import Invalid
from app.normalize import is_short_host, normalize_url, original_url


def _trim(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _is_blocked_host(normalized):
    try:
        host = (urlparse(normalized).hostname or "").lower()
    except Exception:
        return False
    if host.startswith("www."):
        host = host[4:]
    return host in ("linkedin.com", "instagram.com") or host.endswith((".linkedin.com", ".instagram.com"))


def _consume_ai_call(user_id):
    with db.connect() as conn:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO capture_ai_usage (user_id, day, calls) "
                    "VALUES (%s, CURRENT_DATE, 1) "
                    "ON CONFLICT (user_id, day) DO UPDATE SET calls = capture_ai_usage.calls + 1 "
                    "WHERE capture_ai_usage.calls < 10 RETURNING calls",
                    (user_id,),
                )
                if not cur.fetchone():
                    raise Invalid("daily AI capture limit reached")


def capture_data(user_id, url, text="", use_ai=False):
    url = _trim(url)
    text = _trim(text)
    normalized = ""
    if url:
        try:
            normalized = normalize_url(url)
            if is_short_host(normalized):
                target = fetching.resolve_redirect(normalized)
                if not target:
                    raise ValueError("redirect missing")
                normalized = normalize_url(target.strip())
        except Exception:
            raise Invalid("invalid or unreachable URL")

        with db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, normalized_url, source_url FROM hackathons "
                    "WHERE user_id = %s AND (normalized_url = %s OR (normalized_url IS NULL AND source_url <> '')) "
                    "ORDER BY created_at ASC",
                    (user_id, normalized),
                )
                rows = cur.fetchall()
        for row in rows:
            if row.get("normalized_url") == normalized:
                return {"duplicate_id": str(row["id"])}
            source = row.get("source_url") or ""
            if source:
                try:
                    if normalize_url(source) == normalized:
                        return {"duplicate_id": str(row["id"])}
                except Exception:
                    continue

    if url and not text and _is_blocked_host(normalized):
        raise Invalid("paste the post text for this site")
    draft = Draft()
    if not text and url:
        try:
            final_url, _media, body = fetching.fetch(original_url(url))
        except Exception:
            raise Invalid("could not fetch this page; paste its text instead")
        draft = extract(body, final_url)
        if use_ai and not draft.has_dates():
            text = page_text(body)
    if len(text) > 12000:
        raise Invalid("text is too long")
    if not url and not text:
        raise Invalid("provide a URL or text")
    if use_ai and not draft.has_dates() and text:
        _consume_ai_call(user_id)
        ai_draft = extract_with_ai(text)
        if not draft.title:
            draft = ai_draft
        else:
            for field in ("organizer", "mode", "location", "timezone"):
                if not getattr(draft, field) and getattr(ai_draft, field):
                    setattr(draft, field, getattr(ai_draft, field))
                    draft.field_sources[field] = ai_draft.field_sources.get(field, "ai")
            for field in ("starts_at", "ends_at"):
                if getattr(draft, field) is None:
                    if getattr(ai_draft, field) is not None:
                        setattr(draft, field, getattr(ai_draft, field))
                        draft.field_sources[field] = ai_draft.field_sources.get(field, "ai")
                    date_field = field + "_date"
                    if getattr(ai_draft, date_field) and not getattr(draft, date_field):
                        setattr(draft, date_field, getattr(ai_draft, date_field))
            for milestone in ai_draft.milestones:
                draft.add_milestone(
                    milestone.get("type", "OTHER"),
                    milestone.get("title", ""),
                    milestone.get("due_at"),
                    milestone.get("due_date", ""),
                    milestone.get("due_precision", "UNKNOWN"),
                    ai_draft.field_sources.get("milestones", "ai"),
                    milestone.get("source_quote", ""),
                )
    return {"draft": draft.to_json(), "normalized_url": normalized if url else ""}
