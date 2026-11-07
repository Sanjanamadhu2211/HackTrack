from datetime import datetime, timezone
import logging
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app.core.errors import Invalid, NotFound, body_bytes
from app.modules.identity import repository, service
from app.modules.identity.service import require_user_dependency

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/auth/google/start")
def google_start():
    return service.initiate_google_oauth()


@router.get("/auth/google/callback")
async def google_callback(request: Request, code: str = "", state: str = ""):
    return await service.complete_google_oauth(request, code, state)


@router.post("/auth/dev-login")
async def dev_login(request: Request):
    data = await body_bytes()(request)
    email = (data.get("email") or "").strip().lower()
    if "@" not in email:
        raise Invalid("email required")
    try:
        user_id, token, expiry = repository.upsert_dev_user(email)
    except Exception as exc:
        logger.exception("dev_login failed: %s", exc)
        return JSONResponse({"error": "internal error"}, status_code=500)
    response = JSONResponse({"id": str(user_id)})
    service.set_session_cookie(response, token, expiry)
    return response


@router.post("/auth/logout")
def logout(request: Request):
    raw = request.cookies.get(service.SESSION_COOKIE, "")
    if raw:
        try:
            repository.revoke_session(raw)
        except Exception as exc:
            logger.exception("logout failed: %s", exc)
            return JSONResponse({"error": "internal error"}, status_code=500)
    response = PlainTextResponse("", status_code=204)
    service.clear_session_cookie(response)
    return response


@router.get("/me")
def get_me(user_id: str = Depends(require_user_dependency)):
    if not isinstance(user_id, str):
        return user_id
    try:
        return repository.get_user_profile(user_id)
    except Exception as exc:
        logger.exception("get_me failed: %s", exc)
        return JSONResponse({"error": "internal error"}, status_code=500)


@router.patch("/me")
async def patch_me(request: Request, user_id: str = Depends(require_user_dependency)):
    if not isinstance(user_id, str):
        return user_id
    data = await body_bytes()(request)
    timezone_val = data.get("timezone")
    if timezone_val is not None:
        if timezone_val == "" or not service.validate_timezone(timezone_val):
            raise Invalid("unknown timezone")
    for key in ("quiet_hours_start", "quiet_hours_end"):
        value = data.get(key)
        if value is not None and not service.validate_hhmm(value):
            raise Invalid("quiet hours must be HH:MM")
    cap = data.get("reminder_cap_per_day")
    if cap is not None and (not isinstance(cap, int) or isinstance(cap, bool) or not 1 <= cap <= 50):
        raise Invalid("reminder_cap_per_day must be between 1 and 50")
    try:
        repository.update_user_settings(
            user_id,
            timezone_val,
            data.get("quiet_hours_start"),
            data.get("quiet_hours_end"),
            cap,
        )
        # Import reminders replanner lazily to prevent circular imports
        from app.modules.notifications import planner
        with repository.database.connect() as conn:
            planner.replan_user(conn, user_id, datetime.now(timezone.utc))
    except Exception as exc:
        logger.exception("patch_me failed: %s", exc)
        return JSONResponse({"error": "internal error"}, status_code=500)
    return repository.get_user_profile(user_id)


@router.get("/me/export")
def export_me(user_id: str = Depends(require_user_dependency)):
    if not isinstance(user_id, str):
        return user_id
    try:
        value = repository.export_user_data(user_id)
    except Exception as exc:
        logger.exception("export_me failed: %s", exc)
        return JSONResponse({"error": "internal error"}, status_code=500)
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
        from app.modules.integrations.calendar import sync as cal_sync
        with repository.database.connect() as conn:
            with conn:
                cal_sync.revoke_locked(conn, user_id)
        repository.delete_user_account(user_id)
    except NotFound:
        return JSONResponse({"error": "not found"}, status_code=404)
    except Exception as exc:
        logger.exception("delete_me failed: %s", exc)
        return JSONResponse({"error": "internal error"}, status_code=500)
    response = PlainTextResponse("", status_code=204)
    service.clear_session_cookie(response)
    return response
