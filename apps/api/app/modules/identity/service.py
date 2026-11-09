import base64
import json as json_mod
import logging
import time
from datetime import datetime, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from app.core import security
from app.core.config import load_config
from app.core.errors import Invalid, NotFound
from app.modules.identity import repository
from app.modules.identity.domain import OAUTH_COOKIE, SESSION_COOKIE

logger = logging.getLogger(__name__)


def set_session_cookie(response: Response, token: str, expiry: datetime) -> None:
    cfg = load_config()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        path="/",
        expires=expiry,
        httponly=True,
        secure=cfg.secure,
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    cfg = load_config()
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        httponly=True,
        secure=cfg.secure,
        samesite="lax",
    )


def require_user(request: Request) -> str:
    # Middleware validates once per request; dependencies reuse that result.
    user_id = getattr(request.state, "authenticated_user_id", None)
    if user_id:
        return user_id
    raw = request.cookies.get(SESSION_COOKIE, "")
    if not raw:
        raise NotFound()
    try:
        user_id = repository.find_user_id_by_session(raw)
    except Exception as exc:
        logger.error("require_user database lookup failed: %s", exc)
        raise RuntimeError("database unavailable")
    if not user_id:
        raise NotFound()
    request.state.authenticated_user_id = user_id
    return user_id


def require_user_dependency(request: Request):
    try:
        return require_user(request)
    except NotFound:
        return JSONResponse({"error": "not signed in"}, status_code=401)


def initiate_google_oauth():
    cfg = load_config()
    if not cfg.google_client_id:
        return JSONResponse(
            {"error": "Google sign-in not configured", "detail": "GOOGLE_CLIENT_ID environment variable is missing"},
            status_code=503,
        )
    state, _ = security.new_token()
    nonce, _ = security.new_token()
    verifier, _ = security.new_token()
    challenge = (
        base64.urlsafe_b64encode(security.hash_token(verifier))
        .rstrip(b"=")
        .decode()
    )

    params = {
        "client_id": cfg.google_client_id,
        "redirect_uri": cfg.app_base_url + "/api/auth/google/callback",
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
        path="/api/auth/google",
        max_age=600,
        httponly=True,
        secure=cfg.secure,
        samesite="lax",
    )
    return response


async def complete_google_oauth(request: Request, code: str, state: str):
    try:
        cfg = load_config()
    except Exception as exc:
        logger.exception("google_callback: config load failed: %s", exc)
        return JSONResponse({"error": "server config error", "detail": str(exc)}, status_code=500)

    cookie = request.cookies.get(OAUTH_COOKIE, "")
    parts = cookie.split(".")
    if len(parts) != 3 or not security.constant_time_equal(parts[0], state):
        return JSONResponse(
            {"error": "invalid OAuth state", "detail": "OAuth state cookie is missing or does not match query state"},
            status_code=400,
        )
    verifier = parts[2]
    response = RedirectResponse(cfg.app_base_url + "/", status_code=302)
    response.delete_cookie(OAUTH_COOKIE, path="/api/auth/google", httponly=True, secure=cfg.secure, samesite="lax")

    import httpx
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            res = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": cfg.google_client_id,
                    "client_secret": cfg.google_client_secret,
                    "redirect_uri": cfg.app_base_url + "/api/auth/google/callback",
                    "grant_type": "authorization_code",
                    "code_verifier": verifier,
                },
            )
            res.raise_for_status()
            token_data = res.json()
            id_token = token_data.get("id_token", "")
    except Exception as exc:
        logger.exception("google_callback: code exchange failed: %s", exc)
        detail = str(exc)
        if "res" in locals() and hasattr(res, "text"):
            detail = f"{exc}: {res.text}"
        return JSONResponse({"error": "code exchange failed", "detail": detail}, status_code=400)

    if not id_token:
        return JSONResponse({"error": "missing id_token", "detail": "Google token response did not contain an id_token"}, status_code=400)
    segments = id_token.split(".")
    if len(segments) != 3:
        return JSONResponse({"error": "malformed id_token", "detail": "id_token does not have 3 JWT segments"}, status_code=400)
    try:
        payload = segments[1] + "=" * (-len(segments[1]) % 4)
        claims = json_mod.loads(base64.urlsafe_b64decode(payload).decode())
    except Exception as exc:
        return JSONResponse({"error": "malformed id_token", "detail": str(exc)}, status_code=400)

    if claims.get("iss") not in ("https://accounts.google.com", "accounts.google.com"):
        return JSONResponse({"error": "bad issuer", "detail": f"Issuer {claims.get('iss')} is not Google"}, status_code=400)
    if claims.get("aud") != cfg.google_client_id:
        return JSONResponse({"error": "bad audience", "detail": "Audience does not match configured google_client_id"}, status_code=400)
    if not claims.get("sub"):
        return JSONResponse({"error": "missing subject", "detail": "id_token claims lack sub"}, status_code=400)
    if time.time() > int(claims.get("exp", 0)):
        return JSONResponse({"error": "id_token expired", "detail": "Token exp is in the past"}, status_code=400)
    if not security.constant_time_equal(claims.get("nonce", ""), parts[1]):
        return JSONResponse({"error": "bad nonce", "detail": "Nonce does not match state nonce"}, status_code=400)
    if not claims.get("email_verified") or not claims.get("email"):
        return JSONResponse({"error": "email not verified", "detail": "Google email is missing or not marked verified"}, status_code=400)

    try:
        user_id, token, expiry = repository.upsert_google_user(claims)
    except Exception as exc:
        logger.exception("google_callback: db upsert/session failed: %s", exc)
        return JSONResponse({"error": "database error", "detail": str(exc)}, status_code=500)

    set_session_cookie(response, token, expiry)
    return response


def validate_timezone(value: str) -> bool:
    try:
        ZoneInfo(value)
        return True
    except Exception:
        return False


def validate_hhmm(value: str) -> bool:
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        return False
    try:
        hour, minute = int(value[:2]), int(value[3:])
        return 0 <= hour <= 23 and 0 <= minute <= 59
    except ValueError:
        return False
