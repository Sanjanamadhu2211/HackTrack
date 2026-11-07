import json

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from psycopg import errors as pg_errors


class Invalid(Exception):
    def __init__(self, message):
        self.message = message


class NotFound(Exception):
    pass


DUPLICATE_URL_MESSAGE = "event URL already saved"


def register_error_handlers(app: FastAPI):
    @app.exception_handler(Invalid)
    async def invalid_handler(request, exc: Invalid):
        return JSONResponse({"error": exc.message}, status_code=422)

    @app.exception_handler(NotFound)
    async def not_found_handler(request, exc: NotFound):
        return JSONResponse({"error": "not found"}, status_code=404)

    @app.exception_handler(Exception)
    async def global_exception_handler(request, exc: Exception):
        import logging
        logging.getLogger("app").exception("Unhandled server error: %s", exc)
        return JSONResponse({"error": f"Internal server error: {exc}"}, status_code=500)


def is_duplicate_url_error(exc):
    if isinstance(exc, pg_errors.UniqueViolation):
        return exc.sqlstate == "23505" and getattr(exc.diag, "constraint_name", "") == "hackathons_user_id_normalized_url_key"
    return False


def is_bad_uuid_error(exc):
    return isinstance(exc, pg_errors.InvalidTextRepresentation) and exc.sqlstate == "22P02"


def body_bytes(limit=1 << 20):
    async def read_body(request):
        total = 0
        chunks = []
        async for chunk in request.stream():
            total += len(chunk)
            if total > limit:
                raise Invalid("invalid JSON body: body too large")
            chunks.append(chunk)
        raw = b"".join(chunks)
        if not raw:
            return {}
        try:
            value = json.loads(raw.decode())
        except Exception as e:
            raise Invalid(f"invalid JSON body: {e}")
        if not isinstance(value, dict):
            raise Invalid("invalid JSON body: expected object")
        return value

    return read_body
