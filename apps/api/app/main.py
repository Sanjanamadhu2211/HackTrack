import os

import uvicorn
from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from app import db
from app.config import load_config
from app.errors import register_error_handlers

app = FastAPI()


@app.api_route("/healthz", methods=["GET"])
def healthz():
    return PlainTextResponse("ok")


@app.api_route("/readyz", methods=["GET"])
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
        machine = (
            request.url.path.endswith("/healthz")
            or request.url.path.endswith("/readyz")
            or request.url.path.endswith("/integrations/telegram/webhook")
            or request.url.path.endswith("/integrations/email/webhook/mime")
            or request.url.path.endswith("/cron/reminders")
            or request.url.path.endswith("/cron/lag")
            or request.url.path.endswith("/cron/calendar")
            or request.url.path.endswith("/auth/google/start")
            or request.url.path.endswith("/auth/google/callback")
            or request.url.path.endswith("/auth/logout")
            or request.url.path.endswith("/auth/dev-login")
        )
        if not machine:
            from app.auth import require_user

            try:
                require_user(request)
            except Exception:
                return JSONResponse({"error": "not signed in"}, status_code=401)
        return await call_next(request)

    api.include_router(auth.router, prefix="/api/v1")
    api.include_router(hackathons.router, prefix="/api/v1")
    api.include_router(capture.router, prefix="/api/v1")
    api.include_router(telegram.router, prefix="/api/v1")
    api.include_router(inbound.router, prefix="/api/v1")
    api.include_router(calendar.router, prefix="/api/v1")
    api.include_router(worker.router, prefix="/api/v1")
    register_error_handlers(api)
    return api


api_app = _mount()
api_app.add_api_route("/healthz", healthz, methods=["GET"])
api_app.add_api_route("/readyz", readyz, methods=["GET"])

# Serve the API app directly; the Next.js frontend proxies /api to it.
app = api_app


@app.api_route("/{rest:path}", methods=["GET", "POST", "PATCH", "DELETE", "PUT", "OPTIONS"])
async def proxy(request: Request):
    response = await _forward(request)
    return response


async def _forward(request: Request):
    from starlette.responses import Response as StarletteResponse

    path = request.url.path
    qs = f"?{request.url.query}" if request.url.query else ""
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host",)}
    body = await request.body()
    import httpx

    origin = os.getenv("API_ORIGIN_INTERNAL", os.getenv("APP_BASE_URL", "http://localhost:8080"))
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.request(
            request.method,
            f"{origin}{path}{qs}",
            headers=headers,
            content=body if body else None,
            cookies=dict(request.cookies),
        )
    return StarletteResponse(resp.content, status_code=resp.status_code, headers=dict(resp.headers))


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
