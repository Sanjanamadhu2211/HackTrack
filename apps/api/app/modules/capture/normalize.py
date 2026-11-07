from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

TRACKING = {"ref", "fbclid", "igshid"}
SHORT_HOSTS = {"bit.ly", "lnkd.in", "t.co", "tinyurl.com", "goo.gl", "rb.gy", "dub.sh"}


def normalize_url(raw):
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("capture: empty url")
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError("capture: only http and https urls are supported")
    host = parsed.hostname.lower() if parsed.hostname else ""
    if host.startswith("www."):
        host = host[4:]
    if not host:
        raise ValueError("capture: url has no host")
    port = parsed.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    query = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k.lower() not in TRACKING and not k.lower().startswith("utm_")
    ]
    query.sort()
    path = parsed.path.rstrip("/")
    normalized = urlunparse((scheme, host, path, "", urlencode(query), ""))
    return normalized


def is_short_host(normalized):
    try:
        return (urlparse(normalized).hostname or "") in SHORT_HOSTS
    except Exception:
        return False


def original_url(raw):
    raw = (raw or "").strip()
    if "://" not in raw:
        return "https://" + raw
    return raw
