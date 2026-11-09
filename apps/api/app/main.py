import os
import uvicorn
from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, PlainTextResponse

from app.core import database as db
from app.core.config import load_config
from app.core.errors import NotFound, register_error_handlers


def healthz():
    return PlainTextResponse("ok")


def readyz():
    if db.ready():
        return PlainTextResponse("ok")
    return PlainTextResponse("database unavailable", status_code=503)


def _mount():
    from app.modules.identity import router as auth_router, require_user
    from app.modules.hackathons import router as hackathons_router
    from app.modules.capture import router as capture_router
    from app.modules.notifications import router as notifications_router
    from app.modules.integrations.telegram import router as telegram_router
    from app.modules.integrations.calendar import router as calendar_router
    from app.modules.integrations.email import router as email_router

    api = FastAPI(title="HackTrack API")

    @api.middleware("http")
    async def same_origin_middleware(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            cfg = load_config()
            origin = request.headers.get("origin", "")
            if origin != cfg.app_base_url and not (
                origin == "" and request.headers.get("sec-fetch-site", "") == "same-origin"
            ):
                return JSONResponse({"error": "cross-origin request rejected"}, status_code=403)
        # Session gate for everything except machine routes.
        machine = request.url.path.endswith(
            (
                "/healthz",
                "/readyz",
                "/integrations/telegram/webhook",
                "/integrations/email/webhook/mime",
                "/cron/reminders",
                "/cron/lag",
                "/cron/calendar",
                "/auth/google/start",
                "/auth/google/callback",
                "/auth/logout",
                "/auth/dev-login",
            )
        )
        if not machine:
            try:
                # Sync DB lookup must not block the event loop.
                await run_in_threadpool(require_user, request)
            except NotFound:
                return JSONResponse({"error": "not signed in"}, status_code=401)
            except RuntimeError:
                return JSONResponse({"error": "database unavailable"}, status_code=503)
        return await call_next(request)

    api.include_router(auth_router, prefix="/api")
    api.include_router(hackathons_router, prefix="/api")
    api.include_router(capture_router, prefix="/api")
    api.include_router(telegram_router, prefix="/api")
    api.include_router(email_router, prefix="/api")
    api.include_router(calendar_router, prefix="/api")
    api.include_router(notifications_router, prefix="/api")
    register_error_handlers(api)
    return api


api_app = _mount()
api_app.add_api_route("/healthz", healthz, methods=["GET"])
api_app.add_api_route("/readyz", readyz, methods=["GET"])
api_app.add_api_route("/api/healthz", healthz, methods=["GET"])
api_app.add_api_route("/api/readyz", readyz, methods=["GET"])

# Serve the API app directly; the Next.js frontend proxies /api to it.
app = api_app


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "migrate":
        url = os.getenv("DATABASE_URL", "")
        if not url:
            print("DATABASE_URL is required")
            raise SystemExit(1)
        db.migrate(url, sys.argv[2] if len(sys.argv) > 2 else "up")
        raise SystemExit(0)
    try:
        load_config()
    except RuntimeError as e:
        print(f"startup failed: {e}")
        raise SystemExit(1)
    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
