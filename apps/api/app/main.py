import os

import uvicorn
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from starlette.middleware.errors import ServerErrorMiddleware

from app import db
from app.config import load_config

app = FastAPI()


@app.get("/healthz")
def healthz():
    return PlainTextResponse("ok")


@app.get("/readyz")
def readyz():
    if db.ready():
        return PlainTextResponse("ok")
    return PlainTextResponse("database unavailable", status_code=503)


if __name__ == "__main__":
    try:
        load_config()
    except RuntimeError as e:
        print(f"startup failed: {e}")
        raise SystemExit(1)
    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
