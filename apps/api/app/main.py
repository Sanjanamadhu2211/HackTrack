import os

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app import db
from app.config import load_config
from app.errors import register_error_handlers


def healthz():
    return PlainTextResponse("ok")


def readyz():
    if db.ready():
        return PlainTextResponse("ok")
    return PlainTextResponse("database unavailable", status_code=503)


def _mount():
    from app import auth, calendar, capture, hackathons, inbound, telegram, worker

    api = FastAPI()

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
            from app.auth import require_user

            try:
                require_user(request)
            except Exception:
                return JSONResponse({"error": "not signed in"}, status_code=401)
        return await call_next(request)

    api.include_router(auth.router, prefix="/api")
    api.include_router(hackathons.router, prefix="/api")
    api.include_router(capture.router, prefix="/api")
    api.include_router(telegram.router, prefix="/api")
    api.include_router(inbound.router, prefix="/api")
    api.include_router(calendar.router, prefix="/api")
    api.include_router(worker.router, prefix="/api")
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
