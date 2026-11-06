import socket
from urllib.parse import urlparse

import httpx

BLOCKED_PREFIXES_V4 = [
    (0x00000000, 8, "0.0.0.0/8"),
    (0x64400000 // 256, 10, "100.64.0.0/10"),
    (0, 0, ""),
]
BLOCKED_NETS = [
    ("0.0.0.0", 8), ("100.64.0.0", 10), ("192.0.0.0", 24), ("192.0.2.0", 24),
    ("198.18.0.0", 15), ("198.51.100.0", 24), ("203.0.113.0", 24), ("240.0.0.0", 4),
]
BLOCKED_NETS_V6 = [
    ("64:ff9b::", 96), ("64:ff9b:1::", 48), ("2001:db8::", 32), ("fd00:ec2::", 32),
]


def _ip_to_int(ip):
    parts = ip.split(".")
    if len(parts) != 4:
        return None
    try:
        return (int(parts[0]) << 24) + (int(parts[1]) << 16) + (int(parts[2]) << 8) + int(parts[3])
    except ValueError:
        return None


def _in_net(value, base, prefix):
    if value is None:
        return False
    bits = 32 - prefix
    return (value >> bits) == (base >> bits)


def is_public_ip(ip):
    if ip is None:
        return False
    if ":" in ip:
        if ip in ("::1", "::", "fe80::"):
            return False
        if ip.startswith("fe80:") or ip.startswith("ff"):
            return False
        lower = ip.lower()
        if lower.startswith("64:ff9b") or lower.startswith("2001:db8") or lower.startswith("fd00:ec2"):
            return False
        if lower.startswith("::ffff:"):
            mapped = ip.split(":")[-1]
            return is_public_ip(mapped)
        return True
    value = _ip_to_int(ip)
    if value is None:
        return False
    if value == 0x7F000001:
        return False
    if (value >> 24) == 127 or (value >> 24) == 0:
        return False
    if (value >> 24) == 224 or (value >> 28) == 15:
        return False
    if (value >> 16) in (0xA09E,):  # 10.158/16 sample check fallback below
        pass
    octets = [(value >> 24) & 0xFF, (value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF]
    if octets[0] == 10 or (octets[0] == 172 and 16 <= octets[1] <= 31) or (octets[0] == 192 and octets[1] == 168):
        return False
    if octets[0] == 169 and octets[1] == 254:
        return False
    for base_str, prefix in BLOCKED_NETS:
        base = _ip_to_int(base_str)
        if _in_net(value, base, prefix):
            return False
    return True


def _check_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("only http and https urls are supported")
    if parsed.username or parsed.password:
        raise ValueError("credentials in url are not allowed")
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port not in (80, 443):
        raise ValueError("only ports 80 and 443 are allowed")
    return host


def _resolve(host):
    if ":" in host or host.replace(".", "").isdigit():
        if is_public_ip(host):
            return host
        raise ValueError("host is not public")
    infos = socket.getaddrinfo(host, None)
    ips = []
    for info in infos:
        sockaddr = info[4]
        if sockaddr:
            ips.append(sockaddr[0])
    if not ips:
        raise ValueError("host did not resolve")
    for ip in ips:
        if not is_public_ip(ip):
            raise ValueError("host does not resolve to a public address")
    v4 = [ip for ip in ips if ":" not in ip] + [ip for ip in ips if ip.lower().startswith("::ffff:")]
    return v4[0] if v4 else ips[0]


def _robots_allowed(url):
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    _check_url(robots_url)
    host = parsed.hostname or ""
    try:
        with httpx.Client(timeout=5.0, follow_redirects=False, proxy=None, headers={"User-Agent": "HackTrackBot/0.1"}) as client:
            response = client.get(robots_url)
            for _ in range(3):
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        break
                    response = client.get(location, follow_redirects=False)
                else:
                    break
            if response.status_code in (429,) or response.status_code >= 500:
                raise ValueError(f"robots.txt unavailable: {response.status_code}")
            body = response.content[: 2 << 20]
            if len(response.content) > (2 << 20):
                raise ValueError("robots.txt too large")
            text = body.decode("utf-8", errors="ignore")
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"robots.txt unavailable: {e}")
    return _parse_robots(text, parsed.path + (("?" + parsed.query) if parsed.query else ""))


def _parse_robots(text, path):
    groups = []
    current = None
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = [part.strip() for part in line.split(":", 1)]
        key = key.lower()
        if key == "user-agent":
            if current and current["rules"]:
                groups.append(current)
            current = {"agents": [value], "rules": []}
            if groups and current["agents"] == groups[-1]["agents"]:
                pass
        elif key in ("allow", "disallow") and current is not None:
            current["rules"].append((key, value))
    if current:
        groups.append(current)
    path = path or "/"
    allowed = True
    matched_len = -1
    for group in groups:
        if not any(agent == "*" or agent.lower() == "hacktrackbot" for agent in group["agents"]):
            continue
        for kind, rule in group["rules"]:
            if not rule:
                if kind == "disallow":
                    return True
                continue
            if path.startswith(rule) and len(rule) >= matched_len:
                matched_len = len(rule)
                allowed = kind == "allow"
    return allowed


def fetch(url):
    current = url
    for hop in range(4):
        host = _check_url(current)
        if host.lower() in ("linkedin.com", "instagram.com") or host.lower().endswith((".linkedin.com", ".instagram.com")):
            raise ValueError("paste the post text for this site")
        if hop == 0 and not _robots_allowed(current):
            raise ValueError("blocked by robots.txt")
        parsed = urlparse(current)
        ip = _resolve(parsed.hostname or "")
        headers = {
            "User-Agent": "HackTrackBot/0.1 (+https://hacktrack.app)",
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.8",
        }
        url_to_fetch = current if parsed.hostname == ip else _url_at_ip(current, ip)
        try:
            with httpx.Client(timeout=5.0, follow_redirects=False, proxy=None, headers=headers) as client:
                response = client.get(url_to_fetch, headers={"Host": parsed.netloc} if parsed.hostname == ip else None)
        except Exception as e:
            raise ValueError(f"fetch failed: {e}")
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("location")
            if not location or hop == 3:
                raise ValueError("too many redirects")
            from urllib.parse import urljoin

            current = urljoin(current, location)
            continue
        if not (200 <= response.status_code < 300):
            raise ValueError(f"unexpected status {response.status_code}")
        media = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if media not in ("text/html", "application/xhtml+xml", "text/plain"):
            raise ValueError("unsupported content type")
        body = response.content[: 2 << 20]
        return current, media, body
    raise ValueError("too many redirects")


def resolve_redirect(url):
    host = _check_url(url)
    if not _robots_allowed(url):
        raise ValueError("blocked by robots.txt")
    parsed = urlparse(url)
    _resolve(parsed.hostname or "")
    try:
        with httpx.Client(timeout=5.0, follow_redirects=False, proxy=None, headers={"User-Agent": "HackTrackBot/0.1"}) as client:
            response = client.get(url)
    except Exception as e:
        raise ValueError(f"fetch failed: {e}")
    if response.status_code in (301, 302, 303, 307, 308):
        return response.headers.get("location", "")
    return ""


def _url_at_ip(url, ip):
    parsed = urlparse(url)
    host = f"[{ip}]" if ":" in ip else ip
    port = parsed.port
    netloc = host + (f":{port}" if port else "")
    return urlparse(url)._replace(netloc=netloc).geturl()
