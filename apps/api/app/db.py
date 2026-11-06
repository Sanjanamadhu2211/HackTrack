import os
from pathlib import Path

import psycopg


def connect(url=None):
    return psycopg.connect(url or os.getenv("DATABASE_URL", ""), row_factory=psycopg.rows.dict_row)


def ready(url=None):
    try:
        with connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return True
    except Exception:
        return False


def _sections(path):
    text = Path(path).read_text()
    up, _, rest = text.partition("-- +goose Down")
    up = up.replace("-- +goose Up", "")
    return up.strip(), rest.strip()


def migrate(url=None, direction="up"):
    migrations = sorted(Path(__file__).parent.parent.glob("migrations/*.sql"))
    if not migrations and not Path("migrations").exists():
        migrations = sorted(Path("migrations").glob("*.sql"))
    with connect(url) as conn:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "CREATE TABLE IF NOT EXISTS goose_db_version ("
                    "id serial PRIMARY KEY, version_id bigint NOT NULL, is_applied boolean NOT NULL, tstamp timestamptz NOT NULL DEFAULT now())"
                )
                cur.execute(
                    "SELECT version_id FROM goose_db_version WHERE is_applied ORDER BY version_id"
                )
                applied = {r["version_id"] for r in cur.fetchall()}
                versions = []
                for m in migrations:
                    versions.append((int(m.name.split("_")[0]), m))
                versions.sort()
                if direction == "up":
                    for version, path in versions:
                        if version not in applied:
                            up, _ = _sections(path)
                            if up:
                                cur.execute(up)
                            cur.execute(
                                "INSERT INTO goose_db_version (version_id, is_applied) VALUES (%s, true)", (version,)
                            )
                else:
                    for version, path in reversed(versions):
                        if version in applied:
                            _, down = _sections(path)
                            if down:
                                cur.execute(down)
                            cur.execute(
                                "INSERT INTO goose_db_version (version_id, is_applied) VALUES (%s, false)", (version,)
                            )
                            break
