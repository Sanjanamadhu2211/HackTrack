import hashlib
import hmac
import secrets


def new_token():
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def hash_token(raw: str) -> bytes:
    return hashlib.sha256(raw.encode()).digest()


def constant_time_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
