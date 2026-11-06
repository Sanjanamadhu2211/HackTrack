import base64
import hashlib
import secrets


def new_token():
    raw = secrets.token_bytes(32)
    token = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    return token, hash_token(token)


def hash_token(raw):
    return hashlib.sha256(raw.encode()).digest()


def constant_time_equal(a, b):
    try:
        return hashlib.sha256(a.encode()).digest() == hashlib.sha256(b.encode()).digest()
    except Exception:
        return False
