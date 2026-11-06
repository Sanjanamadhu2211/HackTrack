import os

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
