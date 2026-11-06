import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse

from app import db, tokens
from app.config import load_config
from app.errors import Invalid, NotFound, body_bytes
from app.errors import is_bad_uuid_error, is_duplicate_url_error

router = APIRouter()

SESSION_COOKIE = "ht_session"
OAUTH_COOKIE = "ht_oauth"
SESSION_DAYS = 30


def cfg():
    return load_config()


def set_session_cookie(response: Response, token: str, expiry: datetime):
    response.set_cookie(
        SESSION_COOKIE,
        token,
        path="/",
        expires=expiry,
        httponly=True,
        secure=cfg().secure,
        samesite="lax",
    )


def clear_session_cookie(response: Response):
    response.delete_cookie(
        SESSION_COOKIE, path="/", httponly=True, secure=cfg().secure, samesite="lax"
    )


def create_session(conn, user_id):
    token, token_hash = tokens.new_token()
    expiry = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (%s, %s, %s)",
            (user_id, token_hash, expiry),
        )
    return token, expiry


def require_user(request: Request):
    raw = request.cookies.get(SESSION_COOKIE, "")
    if not raw:
        raise NotFound()
    user_id = None
    try:
        with db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT user_id FROM sessions WHERE token_hash = %s AND revoked_at IS NULL AND expires_at > now()",
                    (tokens.hash_token(raw),),
                )
                row = cur.fetchone()
                if row:
                    user_id = row["user_id"]
    except Exception:
        raise RuntimeError("database unavailable")
    if not user_id:
        raise NotFound()
    return str(user_id)


def require_user_dependency(request: Request):
    try:
        return require_user(request)
    except NotFound:
        return JSONResponse({"error": "not signed in"}, status_code=401)


# Same-origin and session checks live in main.py middleware.


def me_payload(user_id):
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT u.id, u.email, u.name, u.timezone, "
                "to_char(u.quiet_hours_start,'HH24:MI') AS qs, "
                "to_char(u.quiet_hours_end,'HH24:MI') AS qe, u.reminder_cap_per_day, "
                "EXISTS(SELECT 1 FROM telegram_links t WHERE t.user_id = u.id AND t.chat_id IS NOT NULL) AS linked "
                "FROM users u WHERE u.id = %s",
                (user_id,),
            )
            row = cur.fetchone()
    if not row:
        raise NotFound()
    return {
        "id": str(row["id"]),
        "email": row["email"],
        "name": row["name"],
        "timezone": row["timezone"],
        "quiet_hours_start": row["qs"],
        "quiet_hours_end": row["qe"],
        "reminder_cap_per_day": row["reminder_cap_per_day"],
        "telegram_linked": bool(row["linked"]),
    }


@router.get("/auth/google/start")
def google_start():
    c = cfg()
    if not c.google_client_id:
        return JSONResponse({"error": "Google sign-in not configured"}, status_code=503)
    state, _ = tokens.new_token()
    nonce, _ = tokens.new_token()
    verifier, _ = tokens.new_token()
    challenge = (
        __import__("base64").urlsafe_b64encode(__import__("hashlib").sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    from urllib.parse import urlencode

    params = {
        "client_id": c.google_client_id,
        "redirect_uri": c.app_base_url + "/api/v1/auth/google/callback",
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "nonce": nonce,
        "prompt": "select_account",
    }
    response = RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params), status_code=302)
    response.set_cookie(
        OAUTH_COOKIE,
        f"{state}.{nonce}.{verifier}",
        path="/api/v1/auth/google",
        max_age=600,
        httponly=True,
        secure=c.secure,
        samesite="lax",
    )
    return response


@router.get("/auth/google/callback")
async def google_callback(request: Request, code: str = "", state: str = ""):
    c = cfg()
    cookie = request.cookies.get(OAUTH_COOKIE, "")
    parts = cookie.split(".")
    if len(parts) != 3 or not tokens.constant_time_equal(parts[0], state):
        return JSONResponse({"error": "invalid OAuth state"}, status_code=400)
    verifier = parts[2]
    response = RedirectResponse(c.app_base_url + "/", status_code=302)
    response.delete_cookie(OAUTH_COOKIE, path="/api/v1/auth/google", max_age=-1)
    import httpx

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            res = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": c.google_client_id,
                    "client_secret": c.google_client_secret,
                    "redirect_uri": c.app_base_url + "/api/v1/auth/google/callback",
                    "grant_type": "authorization_code",
                    "code_verifier": verifier,
                },
            )
            res.raise_for_status()
            token_data = res.json()
            id_token = token_data.get("id_token", "")
    except Exception:
        return JSONResponse({"error": "code exchange failed"}, status_code=400)
    if not id_token:
        return JSONResponse({"error": "missing id_token"}, status_code=400)
    segments = id_token.split(".")
    if len(segments) != 3:
        return JSONResponse({"error": "malformed id_token"}, status_code=400)
    try:
        import base64
        import json as json_mod

        payload = segments[1] + "=" * (-len(segments[1]) % 4)
        claims = json_mod.loads(base64.urlsafe_b64decode(payload).decode())
    except Exception:
        return JSONResponse({"error": "malformed id_token"}, status_code=400)
    if claims.get("iss") not in ("https://accounts.google.com", "accounts.google.com"):
        return JSONResponse({"error": "bad issuer"}, status_code=400)
    if claims.get("aud") != c.google_client_id:
        return JSONResponse({"error": "bad audience"}, status_code=400)
    if not claims.get("sub"):
        return JSONResponse({"error": "missing subject"}, status_code=400)
    if time.time() > int(claims.get("exp", 0)):
        return JSONResponse({"error": "id_token expired"}, status_code=400)
    if not tokens.constant_time_equal(claims.get("nonce", ""), parts[1]):
        return JSONResponse({"error": "bad nonce"}, status_code=400)
    if not claims.get("email_verified") or not claims.get("email"):
        return JSONResponse({"error": "email not verified"}, status_code=400)
    try:
        with db.connect() as conn:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO users (google_sub, email, name) VALUES (%s, %s, %s) "
                        "ON CONFLICT (google_sub) DO UPDATE SET email = EXCLUDED.email, name = EXCLUDED.name, updated_at = now() RETURNING id",
                        (claims["sub"], claims["email"].lower(), claims.get("name", "")),
                    )
                    user_id = cur.fetchone()["id"]
                    token, expiry = create_session(conn, str(user_id))
    except Exception:
        return JSONResponse({"error": "internal error"}, status_code=500)
    set_session_cookie(response, token, expiry)
    return response


@router.post("/auth/dev-login")
async def dev_login(request: Request):
    data = await body_bytes()(request)
    email = (data.get("email") or "").strip().lower()
    if "@" not in email:
        raise Invalid("email required")
    try:
        with db.connect() as conn:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO users (email, name) VALUES (%s, %s) "
                        "ON CONFLICT (email) DO UPDATE SET updated_at = now() RETURNING id",
                        (email, email),
                    )
                    user_id = cur.fetchone()["id"]
                    token, expiry = create_session(conn, str(user_id))
    except Exception:
        return JSONResponse({"error": "internal error"}, status_code=500)
    response = JSONResponse({"id": str(user_id)})
    set_session_cookie(response, token, expiry)
    return response


@router.post("/auth/logout")
def logout(request: Request):
    raw = request.cookies.get(SESSION_COOKIE, "")
    if raw:
        try:
            with db.connect() as conn:
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE sessions SET revoked_at = now(), updated_at = now() "
                            "WHERE token_hash = %s AND revoked_at IS NULL",
                            (tokens.hash_token(raw),),
                        )
        except Exception:
            return JSONResponse({"error": "internal error"}, status_code=500)
    response = PlainTextResponse("", status_code=204)
    clear_session_cookie(response)
    return response


@router.get("/me")
def get_me(user_id: str = Depends(require_user_dependency)):
    if not isinstance(user_id, str):
        return user_id
    try:
        return me_payload(user_id)
    except Exception:
        return JSONResponse({"error": "internal error"}, status_code=500)


@router.patch("/me")
async def patch_me(request: Request, user_id: str = Depends(require_user_dependency)):
    if not isinstance(user_id, str):
        return user_id
    data = await body_bytes()(request)
    timezone = data.get("timezone")
    if timezone is not None:
        if timezone == "" or not _valid_timezone(timezone):
            raise Invalid("unknown timezone")
    for key in ("quiet_hours_start", "quiet_hours_end"):
        value = data.get(key)
        if value is not None and not _valid_hhmm(value):
            raise Invalid("quiet hours must be HH:MM")
    cap = data.get("reminder_cap_per_day")
    if cap is not None and (not isinstance(cap, int) or isinstance(cap, bool) or not 1 <= cap <= 50):
        raise Invalid("reminder_cap_per_day must be between 1 and 50")
    try:
        with db.connect() as conn:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE users SET timezone = COALESCE(%s, timezone), "
                        "quiet_hours_start = COALESCE(%s::time, quiet_hours_start), "
                        "quiet_hours_end = COALESCE(%s::time, quiet_hours_end), "
                        "reminder_cap_per_day = COALESCE(%s, reminder_cap_per_day), updated_at = now() WHERE id = %s",
                        (timezone, data.get("quiet_hours_start"), data.get("quiet_hours_end"), cap, user_id),
                    )
                    from app import reminders

                    reminders.replan_user(conn, user_id, datetime.now(timezone.utc))
    except Exception:
        return JSONResponse({"error": "internal error"}, status_code=500)
    return me_payload(user_id)


def _valid_timezone(value):
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(value)
        return True
    except Exception:
        return False


def _valid_hhmm(value):
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        return False
    try:
        hour, minute = int(value[:2]), int(value[3:])
        return 0 <= hour <= 23 and 0 <= minute <= 59
    except ValueError:
        return False


@router.get("/me/export")
def export_me(user_id: str = Depends(require_user_dependency)):
    if not isinstance(user_id, str):
        return user_id
    try:
        with db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT jsonb_build_object("
                    "'profile', (to_jsonb(u) - 'google_sub'), "
                    "'hackathons', COALESCE(jsonb_agg(to_jsonb(h)) FILTER (WHERE h.id IS NOT NULL), '[]'::jsonb), "
                    "'milestones', COALESCE(jsonb_agg(to_jsonb(m)) FILTER (WHERE m.id IS NOT NULL), '[]'::jsonb), "
                    "'registration_evidence', COALESCE(jsonb_agg(to_jsonb(e)) FILTER (WHERE e.id IS NOT NULL), '[]'::jsonb), "
                    "'state_transitions', COALESCE(jsonb_agg(to_jsonb(s)) FILTER (WHERE s.id IS NOT NULL), '[]'::jsonb), "
                    "'inbound_emails', COALESCE(jsonb_agg(to_jsonb(i)) FILTER (WHERE i.id IS NOT NULL), '[]'::jsonb), "
                    "'notification_jobs', COALESCE(jsonb_agg(to_jsonb(j)) FILTER (WHERE j.id IS NOT NULL), '[]'::jsonb), "
                    "'notification_deliveries', COALESCE(jsonb_agg(to_jsonb(d)) FILTER (WHERE d.id IS NOT NULL), '[]'::jsonb)) "
                    "FROM users u "
                    "LEFT JOIN hackathons h ON h.user_id = u.id "
                    "LEFT JOIN milestones m ON m.user_id = u.id "
                    "LEFT JOIN registration_evidence e ON e.user_id = u.id "
                    "LEFT JOIN state_transitions s ON s.user_id = u.id "
                    "LEFT JOIN inbound_emails i ON i.user_id = u.id "
                    "LEFT JOIN notification_jobs j ON j.user_id = u.id "
                    "LEFT JOIN notification_deliveries d ON d.user_id = u.id "
                    "WHERE u.id = %s",
                    (user_id,),
                )
                row = cur.fetchone()
    except Exception:
        return JSONResponse({"error": "internal error"}, status_code=500)
    value = row["jsonb_build_object"]
    headers = {
        "Content-Disposition": 'attachment; filename="hacktrack-export.json"',
        "Cache-Control": "no-store",
    }
    return JSONResponse(value, headers=headers)


@router.delete("/me")
def delete_me(user_id: str = Depends(require_user_dependency)):
    if not isinstance(user_id, str):
        return user_id
    try:
        with db.connect() as conn:
            with conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT id FROM users WHERE id = %s FOR UPDATE", (user_id,))
                    if not cur.fetchone():
                        raise NotFound()
                    from app import calendar

                    calendar.revoke_locked(conn, user_id)
                    cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
    except NotFound:
        return JSONResponse({"error": "not found"}, status_code=404)
    except Exception:
        return JSONResponse({"error": "internal error"}, status_code=500)
    response = PlainTextResponse("", status_code=204)
    clear_session_cookie(response)
    return response
