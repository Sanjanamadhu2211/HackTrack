import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse

from app import db
from app.auth import require_user_dependency

router = APIRouter()

OAUTH_COOKIE = "ht_calendar_oauth"
COOKIE_PATH = "/api/integrations/google/calendar"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GOOGLE_CALENDAR_CREATE_URL = "https://www.googleapis.com/calendar/v3/calendars"
CAL_SCOPE = "https://www.googleapis.com/auth/calendar.app.created"
ZERO_UUID = "00000000-0000-0000-0000-000000000000"
SYNC_FAILURE = "Sync failed; reconnect Calendar if this continues"

# Env names (config.py does not yet have these fields; read directly):
#   CALENDAR_TOKEN_KEY, APP_BASE_URL, GOOGLE_CLIENT_ID,
#   GOOGLE_CLIENT_SECRET, CRON_SECRET


def _app_base() -> str:
    return (os.getenv("APP_BASE_URL", "") or "").rstrip("/")


def _is_secure() -> bool:
    return _app_base().startswith("https://")


def _raw_b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _load_key() -> bytes:
    raw = (os.getenv("CALENDAR_TOKEN_KEY", "") or "").strip()
    if not raw:
        raise ValueError("CALENDAR_TOKEN_KEY is not configured")
    try:
        key = base64.b64decode(raw, validate=True)
    except Exception:
        raise ValueError("CALENDAR_TOKEN_KEY must be base64-encoded 32 bytes")
    if len(key) != 32:
        raise ValueError("CALENDAR_TOKEN_KEY must be base64-encoded 32 bytes")
    return key


def encrypt_token(user_id, plaintext_bytes: bytes) -> bytes:
    key = _load_key()
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext_bytes, str(user_id).encode())
    return nonce + ciphertext


def decrypt_token(user_id, blob: bytes) -> bytes:
    key = _load_key()
    blob = bytes(blob)
    if len(blob) < 13:
        raise ValueError("invalid token blob")
    nonce, ciphertext = blob[:12], blob[12:]
    return AESGCM(key).decrypt(nonce, ciphertext, str(user_id).encode())


def _mac_for(key: bytes, user_id: str, state: str, verifier: str) -> str:
    digest = hmac.new(key, f"{user_id}.{state}.{verifier}".encode(), hashlib.sha256).digest()
    return _raw_b64url(digest)


def _delete_oauth_cookie(response):
    response.delete_cookie(OAUTH_COOKIE, path=COOKIE_PATH, max_age=-1)
    return response


@router.get("/integrations/google/calendar/start")
def calendar_start(user_id: str = Depends(require_user_dependency)):
    if not isinstance(user_id, str):
        return user_id
    try:
        key = _load_key()
    except Exception:
        return JSONResponse({"error": "Calendar is not configured"}, status_code=503)
    client_id = (os.getenv("GOOGLE_CLIENT_ID", "") or "").strip()
    if not client_id:
        return JSONResponse({"error": "Calendar is not configured"}, status_code=503)
    try:
        with db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id FROM calendar_connections WHERE user_id=%s", (user_id,))
                if cur.fetchone():
                    return JSONResponse({"error": "Calendar already connected"}, status_code=409)
    except Exception:
        return JSONResponse({"error": "internal error"}, status_code=500)
    state = _raw_b64url(secrets.token_bytes(32))
    verifier = _raw_b64url(secrets.token_bytes(32))
    challenge = _raw_b64url(hashlib.sha256(verifier.encode()).digest())
    state_hash = hashlib.sha256(state.encode()).digest()
    expires = datetime.now(timezone.utc) + timedelta(minutes=10)
    try:
        with db.connect() as conn:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO calendar_oauth_states (user_id, state_hash, expires_at) VALUES (%s, %s, %s) "
                        "ON CONFLICT (user_id) DO UPDATE SET state_hash=EXCLUDED.state_hash, expires_at=EXCLUDED.expires_at",
                        (user_id, state_hash, expires),
                    )
    except Exception:
        return JSONResponse({"error": "internal error"}, status_code=500)
    mac = _mac_for(key, user_id, state, verifier)
    base = _app_base()
    params = {
        "client_id": client_id,
        "redirect_uri": base + "/api/integrations/google/calendar/callback",
        "response_type": "code",
        "scope": CAL_SCOPE,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
    }
    url = GOOGLE_AUTH_URL + "?" + urlencode(params)
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        OAUTH_COOKIE,
        f"{state}.{verifier}.{mac}",
        path=COOKIE_PATH,
        max_age=600,
        httponly=True,
        secure=_is_secure(),
        samesite="lax",
    )
    return response


@router.get("/integrations/google/calendar/callback")
def calendar_callback(
    request: Request,
    code: str = "",
    state: str = "",
    user_id: str = Depends(require_user_dependency),
):
    if not isinstance(user_id, str):
        return user_id
    cookie = request.cookies.get(OAUTH_COOKIE, "") or ""
    parts = cookie.split(".")
    if len(parts) != 3 or not hmac.compare_digest(parts[0], state or ""):
        return _delete_oauth_cookie(JSONResponse({"error": "invalid OAuth state"}, status_code=400))
    verifier = parts[1]
    mac = parts[2]
    try:
        key = _load_key()
    except Exception:
        return _delete_oauth_cookie(JSONResponse({"error": "Calendar is not configured"}, status_code=503))
    expected_mac = _mac_for(key, user_id, state, verifier)
    if not hmac.compare_digest(expected_mac, mac or ""):
        return _delete_oauth_cookie(JSONResponse({"error": "invalid OAuth state"}, status_code=400))
    base = _app_base()
    client_id = (os.getenv("GOOGLE_CLIENT_ID", "") or "").strip()
    client_secret = (os.getenv("GOOGLE_CLIENT_SECRET", "") or "").strip()
    refresh_token = ""
    access_token = ""
    try:
        with httpx.Client(timeout=20) as client:
            res = client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": base + "/api/integrations/google/calendar/callback",
                    "grant_type": "authorization_code",
                    "code_verifier": verifier,
                },
            )
            res.raise_for_status()
            token_data = res.json()
            refresh_token = token_data.get("refresh_token", "") or ""
            access_token = token_data.get("access_token", "") or ""
    except Exception:
        return _delete_oauth_cookie(
            JSONResponse({"error": "Calendar authorization failed; try connecting again"}, status_code=400)
        )
    if not refresh_token:
        return _delete_oauth_cookie(
            JSONResponse({"error": "Calendar authorization failed; try connecting again"}, status_code=400)
        )
    google_calendar_id = ""
    try:
        with httpx.Client(timeout=15) as client:
            res = client.post(
                GOOGLE_CALENDAR_CREATE_URL,
                headers={"Authorization": f"Bearer {access_token}"},
                json={"summary": "HackTrack"},
            )
            res.raise_for_status()
            google_calendar_id = (res.json().get("id", "") or "").strip()
    except Exception:
        return _delete_oauth_cookie(JSONResponse({"error": "Could not create calendar"}, status_code=502))
    if not google_calendar_id:
        return _delete_oauth_cookie(JSONResponse({"error": "Could not create calendar"}, status_code=502))
    state_hash = hashlib.sha256(state.encode()).digest()
    try:
        encrypted = encrypt_token(user_id, refresh_token.encode())
    except Exception:
        _delete_google_calendar(access_token, google_calendar_id)
        return _delete_oauth_cookie(JSONResponse({"error": "Calendar already connected or setup failed"}, status_code=409))
    try:
        with db.connect() as conn:
            with conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT id FROM users WHERE id=%s FOR UPDATE", (user_id,))
                    if not cur.fetchone():
                        raise RuntimeError("user not found")
                    cur.execute(
                        "DELETE FROM calendar_oauth_states WHERE user_id=%s AND state_hash=%s AND expires_at > now() RETURNING user_id",
                        (user_id, state_hash),
                    )
                    if not cur.fetchone():
                        raise RuntimeError("state expired or mismatch")
                    cur.execute(
                        "INSERT INTO calendar_connections (user_id, refresh_token, calendar_id) VALUES (%s, %s, %s)",
                        (user_id, encrypted, google_calendar_id),
                    )
    except Exception:
        _delete_google_calendar(access_token, google_calendar_id)
        return _delete_oauth_cookie(JSONResponse({"error": "Calendar already connected or setup failed"}, status_code=409))
    response = RedirectResponse(base + "/settings", status_code=302)
    return _delete_oauth_cookie(response)


def _delete_google_calendar(access_token: str, calendar_id: str) -> None:
    try:
        with httpx.Client(timeout=15) as client:
            client.request(
                "DELETE",
                f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}",
                headers={"Authorization": f"Bearer {access_token}"},
            )
    except Exception as exc:
        print(f"calendar cleanup failed for calendar {calendar_id}: {exc}")


@router.get("/integrations/google/calendar")
def calendar_status(user_id: str = Depends(require_user_dependency)):
    if not isinstance(user_id, str):
        return user_id
    try:
        with db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT last_error FROM calendar_connections WHERE user_id=%s", (user_id,))
                row = cur.fetchone()
    except Exception:
        return JSONResponse({"error": "internal error"}, status_code=500)
    if row:
        return {"connected": True, "last_error": row["last_error"] or ""}
    return {"connected": False, "last_error": ""}


def revoke_locked(conn, user_id):
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM users WHERE id=%s FOR UPDATE", (user_id,))
        cur.execute("DELETE FROM calendar_oauth_states WHERE user_id=%s", (user_id,))
        cur.execute("SELECT refresh_token FROM calendar_connections WHERE user_id=%s FOR UPDATE", (user_id,))
        row = cur.fetchone()
        if not row:
            cur.execute("DELETE FROM calendar_events WHERE user_id=%s", (user_id,))
            return
        blob = row["refresh_token"]
    plaintext = decrypt_token(user_id, blob).decode("utf-8", errors="replace")
    try:
        with httpx.Client(timeout=15) as client:
            res = client.post(GOOGLE_REVOKE_URL, data={"token": plaintext})
    except Exception as exc:
        raise RuntimeError(f"calendar revoke failed: {exc}")
    if res.status_code == 200:
        ok = True
    elif res.status_code == 400:
        try:
            ok = (res.json().get("error") or "") == "invalid_token"
        except Exception:
            ok = False
    else:
        ok = False
    if not ok:
        raise RuntimeError("calendar revoke failed")
    with conn.cursor() as cur:
        cur.execute("DELETE FROM calendar_connections WHERE user_id=%s", (user_id,))
        cur.execute("DELETE FROM calendar_events WHERE user_id=%s", (user_id,))


@router.delete("/integrations/google/calendar")
def calendar_delete(user_id: str = Depends(require_user_dependency)):
    if not isinstance(user_id, str):
        return user_id
    try:
        with db.connect() as conn:
            with conn:
                revoke_locked(conn, user_id)
    except Exception:
        return JSONResponse({"error": "internal error"}, status_code=500)
    return PlainTextResponse("", status_code=204)


def _remaining(deadline: float) -> float:
    return max(0.0, deadline - time.time())


def _refresh_access_token(refresh_token: str, deadline: float) -> str:
    remaining = _remaining(deadline)
    if remaining <= 0:
        raise RuntimeError("sync timeout")
    client_id = (os.getenv("GOOGLE_CLIENT_ID", "") or "").strip()
    client_secret = (os.getenv("GOOGLE_CLIENT_SECRET", "") or "").strip()
    with httpx.Client(timeout=min(15.0, remaining)) as client:
        res = client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        res.raise_for_status()
        access = (res.json().get("access_token", "") or "").strip()
    if not access:
        raise RuntimeError("refresh failed")
    return access


def _google_upsert(calendar_id: str, event_id: str, body: dict, access_token: str, deadline: float) -> None:
    remaining = _remaining(deadline)
    if remaining <= 0:
        raise RuntimeError("sync timeout")
    base_url = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"
    timeout = min(15.0, remaining)
    with httpx.Client(timeout=timeout) as client:
        res = client.request(
            "PUT",
            f"{base_url}/{event_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            json=body,
        )
        if 200 <= res.status_code < 300:
            return
        if res.status_code == 404:
            remaining = _remaining(deadline)
            if remaining <= 0:
                raise RuntimeError("sync timeout")
            create_payload = dict(body)
            create_payload["id"] = event_id
            with httpx.Client(timeout=min(15.0, remaining)) as create_client:
                created = create_client.request(
                    "POST",
                    base_url,
                    headers={"Authorization": f"Bearer {access_token}"},
                    json=create_payload,
                )
            if 200 <= created.status_code < 300:
                return
            if created.status_code == 409:
                remaining = _remaining(deadline)
                if remaining <= 0:
                    raise RuntimeError("sync timeout")
                with httpx.Client(timeout=min(15.0, remaining)) as retry_client:
                    retried = retry_client.request(
                        "PUT",
                        f"{base_url}/{event_id}",
                        headers={"Authorization": f"Bearer {access_token}"},
                        json=body,
                    )
                if 200 <= retried.status_code < 300:
                    return
                raise RuntimeError(f"google upsert failed: {retried.status_code}")
            raise RuntimeError(f"google create failed: {created.status_code}")
        raise RuntimeError(f"google upsert failed: {res.status_code}")


def _google_delete(calendar_id: str, event_id: str, access_token: str, deadline: float) -> None:
    remaining = _remaining(deadline)
    if remaining <= 0:
        raise RuntimeError("sync timeout")
    url = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/{event_id}"
    with httpx.Client(timeout=min(15.0, remaining)) as client:
        res = client.request("DELETE", url, headers={"Authorization": f"Bearer {access_token}"})
    if 200 <= res.status_code < 300 or res.status_code in (404, 410):
        return
    raise RuntimeError(f"google delete failed: {res.status_code}")


def _rfc3339(value) -> str:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _verify_fence(cur, user_id: str, calendar_id: str, generation: int) -> bool:
    cur.execute(
        "SELECT calendar_id, sync_generation FROM calendar_connections WHERE user_id=%s FOR UPDATE",
        (user_id,),
    )
    row = cur.fetchone()
    if not row:
        return False
    return row["calendar_id"] == calendar_id and row["sync_generation"] == generation


def _sync_user(user_id: str, deadline: float) -> None:
    with db.connect() as conn:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE calendar_connections SET sync_generation=sync_generation+1 WHERE user_id=%s RETURNING sync_generation, calendar_id, refresh_token",
                    (user_id,),
                )
                row = cur.fetchone()
                if not row:
                    return
                generation = row["sync_generation"]
                calendar_id = row["calendar_id"]
                blob = row["refresh_token"]
    refresh_token = decrypt_token(user_id, blob).decode("utf-8", errors="replace")
    access_token = _refresh_access_token(refresh_token, deadline)
    last_id = ZERO_UUID
    while True:
        if _remaining(deadline) <= 0:
            raise RuntimeError("sync timeout")
        try:
            with db.connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT m.id, m.title, m.due_at, h.title AS hackathon_title FROM milestones m "
                        "JOIN hackathons h ON h.id=m.hackathon_id "
                        "WHERE m.user_id=%s AND m.status='TODO' AND m.date_confirmed AND m.due_at IS NOT NULL "
                        "AND h.archived_at IS NULL AND m.id > %s ORDER BY m.id LIMIT 100",
                        (user_id, last_id),
                    )
                    rows = cur.fetchall()
        except Exception:
            raise
        if not rows:
            break
        for item in rows:
            milestone_id = str(item["id"])
            last_id = milestone_id
            due = item["due_at"]
            start_str = _rfc3339(due)
            end_str = _rfc3339((due if isinstance(due, datetime) else datetime.fromisoformat(str(due).replace("Z", "+00:00"))) + timedelta(minutes=15))
            body = {
                "summary": item["title"],
                "description": item["hackathon_title"],
                "start": {"dateTime": start_str},
                "end": {"dateTime": end_str},
            }
            content_hash = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
            event_id = "m" + milestone_id.replace("-", "")
            with db.connect() as conn:
                with conn:
                    with conn.cursor() as cur:
                        cur.execute("SET LOCAL lock_timeout='20s'")
                        cur.execute(
                            "SELECT pg_advisory_xact_lock(hashtextextended('ht:calendar:'||%s, 0))",
                            (user_id,),
                        )
                        if not _verify_fence(cur, user_id, calendar_id, generation):
                            continue
                        cur.execute(
                            "SELECT content_hash FROM calendar_events WHERE user_id=%s AND milestone_id=%s",
                            (user_id, milestone_id),
                        )
                        existing = cur.fetchone()
                        if existing and existing["content_hash"] == content_hash:
                            continue
                        _google_upsert(calendar_id, event_id, body, access_token, deadline)
                        cur.execute(
                            "INSERT INTO calendar_events (user_id, milestone_id, content_hash) VALUES (%s, %s, %s) "
                            "ON CONFLICT (user_id, milestone_id) DO UPDATE SET content_hash=EXCLUDED.content_hash",
                            (user_id, milestone_id, content_hash),
                        )
        if len(rows) < 100:
            break
    last_id = ZERO_UUID
    while True:
        if _remaining(deadline) <= 0:
            raise RuntimeError("sync timeout")
        with db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT ce.milestone_id FROM calendar_events ce "
                    "LEFT JOIN milestones m ON m.id=ce.milestone_id AND m.user_id=ce.user_id "
                    "AND m.status='TODO' AND m.date_confirmed AND m.due_at IS NOT NULL "
                    "LEFT JOIN hackathons h ON h.id=m.hackathon_id AND h.archived_at IS NULL "
                    "WHERE ce.user_id=%s AND (m.id IS NULL OR h.id IS NULL) AND ce.milestone_id > %s "
                    "ORDER BY ce.milestone_id LIMIT 100",
                    (user_id, last_id),
                )
                stale = cur.fetchall()
        if not stale:
            break
        for item in stale:
            milestone_id = str(item["milestone_id"])
            last_id = milestone_id
            event_id = "m" + milestone_id.replace("-", "")
            with db.connect() as conn:
                with conn:
                    with conn.cursor() as cur:
                        cur.execute("SET LOCAL lock_timeout='20s'")
                        cur.execute(
                            "SELECT pg_advisory_xact_lock(hashtextextended('ht:calendar:'||%s, 0))",
                            (user_id,),
                        )
                        if not _verify_fence(cur, user_id, calendar_id, generation):
                            continue
                        _google_delete(calendar_id, event_id, access_token, deadline)
                        cur.execute(
                            "DELETE FROM calendar_events WHERE user_id=%s AND milestone_id=%s",
                            (user_id, milestone_id),
                        )
        if len(stale) < 100:
            break
    try:
        with db.connect() as conn:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE calendar_connections SET last_error='' WHERE user_id=%s AND sync_generation=%s AND calendar_id=%s",
                        (user_id, generation, calendar_id),
                    )
    except Exception:
        pass


def _set_sync_error(user_id: str) -> None:
    try:
        with db.connect() as conn:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE calendar_connections SET last_error=%s WHERE user_id=%s",
                        (SYNC_FAILURE, user_id),
                    )
    except Exception:
        pass


@router.api_route("/cron/calendar", methods=["GET", "POST"])
def cron_calendar(request: Request):
    secret = (os.getenv("CRON_SECRET", "") or "").strip()
    authorization = request.headers.get("Authorization", "") or ""
    if not secret or not hmac.compare_digest(authorization, "Bearer " + secret):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    deadline = time.time() + 45.0
    try:
        with db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id FROM calendar_connections ORDER BY user_id")
                user_ids = [str(r["user_id"]) for r in cur.fetchall()]
    except Exception:
        return JSONResponse({"error": "internal error"}, status_code=500)
    errors = []
    for user_id in user_ids:
        try:
            _sync_user(user_id, deadline)
        except Exception as exc:
            errors.append(f"{user_id}: {exc}")
            _set_sync_error(user_id)
    if errors:
        return JSONResponse({"error": "internal error"}, status_code=500)
    return {"status": "ok"}


@router.api_route("/integrations/google/calendar/sync", methods=["POST"])
def sync_calendar(request: Request, user_id: str = Depends(require_user_dependency)):
    """User-triggered sync of the authenticated user's Google Calendar.

    Not a cron: runs only for the current user, on demand. Fenced against
    concurrent cron runs via sync_generation so an on-demand sync never
    overwrites newer cron state.
    """
    if not isinstance(user_id, str):
        return user_id
    deadline = time.time() + 45.0
    try:
        _sync_user(user_id, deadline)
    except Exception:
        _set_sync_error(user_id)
        return JSONResponse({"error": "sync failed"}, status_code=500)
    return {"status": "ok"}
