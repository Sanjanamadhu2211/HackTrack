from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request

from app import db, fetching
from app.ai import extract_with_ai
from app.auth import require_user_dependency
from app.draft import Draft, extract, page_text
from app.errors import Invalid, body_bytes
from app.normalize import is_short_host, normalize_url, original_url

router = APIRouter()


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
    return host in ("linkedin.com", "instagram.com") or host.endswith(
        (".linkedin.com", ".instagram.com")
    )


def _consume_ai_call(user_id):
    with db.connect() as conn:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO capture_ai_usage (user_id, day, calls) "
                    "VALUES (%s, CURRENT_DATE, 1) "
                    "ON CONFLICT (user_id, day) DO UPDATE SET calls = capture_ai_usage.calls + 1 "
                    "WHERE capture_ai_usage.calls < 10 "
                    "RETURNING calls",
                    (user_id,),
                )
                row = cur.fetchone()
                if not row:
                    raise Invalid("daily AI capture limit reached")


@router.post("/capture")
async def capture(request: Request, user_id: str = Depends(require_user_dependency)):
    if not isinstance(user_id, str):
        return user_id

    data = await body_bytes()(request)
    url = _trim(data.get("url"))
    text = _trim(data.get("text"))
    use_ai = bool(data.get("use_ai"))

    normalized = ""
    if url:
        try:
            normalized = normalize_url(url)
        except Exception:
            raise Invalid("invalid or unreachable URL")
        if is_short_host(normalized):
            try:
                target = fetching.resolve_redirect(normalized)
            except Exception:
                raise Invalid("invalid or unreachable URL")
            if not target:
                raise Invalid("invalid or unreachable URL")
            try:
                normalized = normalize_url(target.strip())
            except Exception:
                raise Invalid("invalid or unreachable URL")

    if url:
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
            if not draft.organizer and ai_draft.organizer:
                draft.organizer = ai_draft.organizer
                draft.field_sources["organizer"] = ai_draft.field_sources.get("organizer", "ai")
            if not draft.mode and ai_draft.mode:
                draft.mode = ai_draft.mode
                draft.field_sources["mode"] = ai_draft.field_sources.get("mode", "ai")
            if not draft.location and ai_draft.location:
                draft.location = ai_draft.location
                draft.field_sources["location"] = ai_draft.field_sources.get("location", "ai")
            if not draft.timezone and ai_draft.timezone:
                draft.timezone = ai_draft.timezone
                draft.field_sources["timezone"] = ai_draft.field_sources.get("timezone", "ai")
            if draft.starts_at is None:
                if ai_draft.starts_at is not None:
                    draft.starts_at = ai_draft.starts_at
                    draft.field_sources["starts_at"] = ai_draft.field_sources.get("starts_at", "ai")
                if ai_draft.starts_date and not draft.starts_date:
                    draft.starts_date = ai_draft.starts_date
            if draft.ends_at is None:
                if ai_draft.ends_at is not None:
                    draft.ends_at = ai_draft.ends_at
                    draft.field_sources["ends_at"] = ai_draft.field_sources.get("ends_at", "ai")
                if ai_draft.ends_date and not draft.ends_date:
                    draft.ends_date = ai_draft.ends_date
            for milestone in ai_draft.milestones:
                due_at = milestone.get("due_at")
                due_date = milestone.get("due_date", "")
                precision = milestone.get("due_precision", "UNKNOWN")
                quote = milestone.get("source_quote", "")
                source = ai_draft.field_sources.get("milestones", "ai")
                draft.add_milestone(
                    milestone.get("type", "OTHER"),
                    milestone.get("title", ""),
                    due_at,
                    due_date,
                    precision,
                    source,
                    quote,
                )

    return {"draft": draft.to_json(), "normalized_url": normalized if url else ""}
