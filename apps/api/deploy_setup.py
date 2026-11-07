"""Run database migrations as part of a Vercel deployment.

Repeat-safe: applied migrations are tracked in goose_db_version, so re-running is
a no-op once they are applied. Fails (non-zero exit) if DATABASE_URL is missing,
points at a workspace/local database, or a migration fails -- so the deployment
fails too. Never runs against the local workspace database.
"""

import os
from urllib.parse import urlparse

from app import db


def main() -> int:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        print("DATABASE_URL is required")
        return 1
    host = (urlparse(url).hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        print("Refusing to run migrations against a workspace/local database")
        return 1
    try:
        db.migrate(url, "up")
    except Exception as exc:
        print(f"migration failed: {exc}")
        return 1
    print("migrations up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
