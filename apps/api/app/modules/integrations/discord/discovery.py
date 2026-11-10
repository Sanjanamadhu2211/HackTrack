import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from ipaddress import ip_address
from urllib.parse import parse_qs, urljoin, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from app import fetching
from app.normalize import normalize_url

log = logging.getLogger(__name__)
AGGREGATOR_URL = "https://hackathon-api-sigma.vercel.app/api/v1/hackathons"
MAX_BRIEF_ITEMS = 5
SOURCES = {
    "Devpost": "https://devpost.com/hackathons",
    "Unstop": "https://api.unstop.com/hackathons/",
    "MLH": "https://www.mlh.com/events",
}


def _source_date(value):
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def parse_aggregator(body, today=None):
    data = json.loads(body)
    if not isinstance(data, dict) or data.get("status") != "success" or not isinstance(data.get("data"), list):
        raise ValueError("invalid aggregator response")
    today = today or datetime.now(timezone.utc).date()
    events = {}
    for item in data["data"]:
        if not isinstance(item, dict) or not isinstance(item.get("title"), str) or not isinstance(item.get("url"), str):
            continue
        title = " ".join(item["title"].split())[:200]
        url = item["url"].strip()
        try:
            parsed = urlparse(url)
            host = parsed.hostname or ""
            if not title or len(url) > 2048 or any(ch.isspace() or ch in "<>" for ch in url) or parsed.scheme not in ("http", "https") or parsed.username or parsed.password or "." not in host or parsed.port not in (None, 80, 443):
                continue
            try:
                ip_address(host)
            except ValueError:
                pass
            else:
                continue
            normalized = normalize_url(url)
        except ValueError:
            continue
        start = _source_date(item.get("start_date"))
        end = _source_date(item.get("end_date"))
        registration_end = _source_date(item.get("registration_end"))
        if (end and end < today) or (registration_end and registration_end < today):
            continue
        platform = item.get("platform") if isinstance(item.get("platform"), str) else ""
        mode = item.get("mode", "")
        mode = mode.lower() if isinstance(mode, str) and mode.lower() in ("online", "offline", "hybrid") else ""
        events.setdefault(normalized, {
            "title": title, "source": " ".join(platform.split())[:40].capitalize() or "Aggregator", "mode": mode,
            "source_url": url, "normalized_url": normalized, "start_date": start, "end_date": end,
            "registration_end": registration_end,
        })
    return list(events.values())


def parse_listing(source, page_url, body):
    parsed_page = urlparse(page_url)
    out = {}
    for anchor in BeautifulSoup(body, "html.parser").select("a[href]"):
        url = urljoin(page_url, anchor.get("href", ""))
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        path = parsed.path.lower()
        if parsed.scheme not in ("http", "https") or not host or parsed.username or parsed.password:
            continue
        heading = None
        if source == "Devpost":
            allowed = host.endswith(".devpost.com") and host != "devpost.com" and path in ("", "/")
        elif source == "Unstop":
            heading = anchor.select_one("h2")
            allowed = host in ("unstop.com", "www.unstop.com", "api.unstop.com") and path.startswith("/hackathons/") and heading is not None
            if allowed:
                url = parsed._replace(netloc="unstop.com").geturl()
        elif source == "MLH":
            heading = anchor.select_one("h4")
            allowed = heading is not None and "events" in parse_qs(parsed.query).get("utm_campaign", [])
        else:
            allowed = False
        if not allowed or (host == parsed_page.hostname and path == parsed_page.path.rstrip("/").lower()):
            continue
        try:
            normalized = normalize_url(url)
        except Exception:
            continue
        title = " ".join(((heading or anchor).get_text(" ", strip=True) or anchor.get("title", "")).split())
        if not title or title.lower() in {"learn more", "view event", "details"}:
            continue
        out.setdefault(normalized, {"source": source, "source_url": normalized if source == "MLH" else url, "normalized_url": normalized, "title": title[:200]})
        if len(out) >= 40:
            break
    return list(out.values())


def discover_events():
    try:
        _url, _media, body = fetching.fetch(AGGREGATOR_URL, accept_json=True, timeout=20.0)
        events = parse_aggregator(body)
        if not events:
            raise ValueError("aggregator returned no usable events")
        sources = {"MLH": SOURCES["MLH"]} if not any(event["source"].lower() == "mlh" for event in events) else {}
    except Exception as error:
        log.warning("Hackathon aggregator unavailable: %s", error)
        events = []
        sources = SOURCES
    def fetch_listing(item):
        source, url = item
        try:
            final_url, _media, body = fetching.fetch(url)
            return parse_listing(source, final_url, body)
        except Exception as error:
            log.info("Discovery source %s unavailable: %s", source, error)
            return []

    if sources:
        with ThreadPoolExecutor(max_workers=len(sources)) as pool:
            for listing in pool.map(fetch_listing, sources.items()):
                events.extend(listing)
    return events


def _digest_content(events):
    content = "Good morning! New hackathons to check out:\n"
    included = []
    buttons = []
    titles = set()
    for event in events:
        title_key = " ".join(event["title"].lower().split())
        if len(title_key) >= 12 and title_key in titles:
            continue
        if len(included) >= MAX_BRIEF_ITEMS:
            break
        index = len(included) + 1
        line = f"{index}. {event['title'][:100]} — <{event['source_url']}>\n"
        details = [str(event[key]) for key in ("source", "mode") if event.get(key)]
        if event.get("start_date"):
            details.append(f"Starts {event['start_date']}")
        if event.get("registration_end"):
            details.append(f"Register by {event['registration_end']}")
        if details:
            line += "   " + " · ".join(details) + "\n"
        if len(content) + len(line) > 1850:
            break
        included.append(event)
        titles.add(title_key)
        content += line
        buttons.append({"type": 2, "style": 3, "label": f"Track {index}", "custom_id": f"discover:{event['id']}"})
        buttons.append({"type": 2, "style": 2, "label": f"Registered {index}", "custom_id": f"registered:{event['id']}"})
    components = [
        {"type": 1, "components": buttons[index:index + 5]}
        for index in range(0, len(buttons), 5)
    ]
    return content.rstrip(), components, included


def _local_time(now, timezone_name):
    try:
        return now.astimezone(ZoneInfo(timezone_name))
    except Exception:
        return now.astimezone(timezone.utc)


def _is_morning(now, timezone_name):
    return _local_time(now, timezone_name).hour == 9


def run_discovery(connect_factory, now=None):
    now = now or datetime.now(timezone.utc)
    with connect_factory() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT l.user_id, u.timezone FROM discord_links l JOIN users u ON u.id = l.user_id WHERE l.channel_id IS NOT NULL")
            links = []
            for link in cur.fetchall():
                if not _is_morning(now, link["timezone"]):
                    continue
                try:
                    digest_date = now.astimezone(ZoneInfo(link["timezone"])).date()
                except Exception:
                    digest_date = now.date()
                cur.execute("SELECT 1 FROM discord_digest_runs WHERE user_id = %s AND digest_date = %s", (link["user_id"], digest_date))
                if not cur.fetchone():
                    links.append((str(link["user_id"]), digest_date))
    if not links:
        return {"sources_found": 0, "digests_queued": 0}
    fresh = discover_events()
    if not fresh:
        return {"sources_found": 0, "digests_queued": 0}
    with connect_factory() as conn:
        with conn:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO discovery_events (source_url, normalized_url, title, source, mode, start_date, end_date, registration_end) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (normalized_url) DO UPDATE SET "
                    "title = EXCLUDED.title, source_url = EXCLUDED.source_url, source = EXCLUDED.source, mode = EXCLUDED.mode, "
                    "start_date = EXCLUDED.start_date, end_date = EXCLUDED.end_date, registration_end = EXCLUDED.registration_end, updated_at = now()",
                    [(event["source_url"], event["normalized_url"], event["title"], event["source"], event.get("mode", ""),
                      event.get("start_date"), event.get("end_date"), event.get("registration_end")) for event in fresh],
                )
    queued = 0
    for user_id, digest_date in links:
        with connect_factory() as conn:
            with conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT channel_id FROM discord_links WHERE user_id = %s FOR UPDATE", (user_id,))
                    current = cur.fetchone()
                    if not current or not current["channel_id"]:
                        continue
                    cur.execute(
                        "INSERT INTO discord_digest_runs (user_id, digest_date) VALUES (%s, %s) "
                        "ON CONFLICT DO NOTHING RETURNING user_id",
                        (user_id, digest_date),
                    )
                    if not cur.fetchone():
                        continue
                    cur.execute(
                        "SELECT e.id, e.title, e.source_url, e.source, e.mode, e.start_date, e.registration_end FROM discovery_events e "
                        "WHERE e.normalized_url = ANY(%s) AND (e.end_date IS NULL OR e.end_date >= %s) "
                        "AND (e.registration_end IS NULL OR e.registration_end >= %s) "
                        "AND NOT EXISTS (SELECT 1 FROM discovery_deliveries d JOIN discovery_events sent ON sent.id = d.event_id "
                        "WHERE d.user_id = %s AND (d.event_id = e.id OR (length(e.title) >= 12 AND lower(sent.title) = lower(e.title)))) "
                        "AND NOT EXISTS (SELECT 1 FROM hackathons h WHERE h.user_id = %s AND "
                        "(h.normalized_url = e.normalized_url OR (length(e.title) >= 12 AND lower(h.title) = lower(e.title)))) "
                        "ORDER BY e.first_seen_at DESC LIMIT 100",
                        ([event["normalized_url"] for event in fresh], digest_date, digest_date, user_id, user_id),
                    )
                    events = [{**row, "id": str(row["id"])} for row in cur.fetchall()]
                    _content, _components, included = _digest_content(events)
                    if not included:
                        cur.execute("DELETE FROM discord_digest_runs WHERE user_id = %s AND digest_date = %s", (user_id, digest_date))
                        continue
                    for event in included:
                        cur.execute(
                            "INSERT INTO discovery_deliveries (user_id, event_id, digest_date) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                            (user_id, event["id"], digest_date),
                        )
                    queued += 1
    return {"sources_found": len(fresh), "digests_queued": queued}


def deliver_pending_digests(connect_factory, sender, now=None):
    now = now or datetime.now(timezone.utc)
    sent = 0
    attempted = 0
    while attempted < 10:
        with connect_factory() as conn:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE discord_digest_runs SET state = 'CLAIMED', lease_until = %s + interval '2 minutes', "
                        "attempt_count = attempt_count + 1 WHERE (user_id, digest_date) = ("
                        "SELECT user_id, digest_date FROM discord_digest_runs WHERE "
                        "(state = 'PENDING' AND next_attempt_at <= %s) OR (state = 'CLAIMED' AND lease_until < %s) "
                        "ORDER BY next_attempt_at FOR UPDATE SKIP LOCKED LIMIT 1) "
                        "RETURNING user_id, digest_date, attempt_count",
                        (now, now, now),
                    )
                    run = cur.fetchone()
        if not run:
            break
        attempted += 1
        user_id = str(run["user_id"])
        try:
            with connect_factory() as conn:
                with conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT l.channel_id, u.timezone FROM discord_links l JOIN users u ON u.id = l.user_id "
                            "WHERE l.user_id = %s FOR UPDATE OF l",
                            (user_id,),
                        )
                        link = cur.fetchone()
                        if not link or not link["channel_id"]:
                            raise RuntimeError("Discord channel is no longer available")
                        cur.execute(
                            "SELECT state, attempt_count FROM discord_digest_runs WHERE user_id = %s AND digest_date = %s FOR UPDATE",
                            (user_id, run["digest_date"]),
                        )
                        current = cur.fetchone()
                        if not current or current["state"] != "CLAIMED" or current["attempt_count"] != run["attempt_count"]:
                            continue
                        local = _local_time(now, link["timezone"])
                        day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
                        day_end = day_start + timedelta(days=1)
                        cur.execute(
                            "SELECT count(*) AS n FROM discovery_deliveries WHERE user_id = %s AND state = 'SENT' "
                            "AND delivered_at >= %s AND delivered_at < %s",
                            (user_id, day_start, day_end),
                        )
                        already_sent = cur.fetchone()["n"] > 0
                        if local.hour != 9 or already_sent:
                            next_morning = day_start.replace(hour=9)
                            if local.hour >= 9 or already_sent:
                                next_morning += timedelta(days=1)
                            cur.execute(
                                "UPDATE discord_digest_runs SET state = 'PENDING', attempt_count = attempt_count - 1, "
                                "next_attempt_at = %s, lease_until = NULL WHERE user_id = %s AND digest_date = %s",
                                (next_morning, user_id, run["digest_date"]),
                            )
                            continue
                        cur.execute(
                            "SELECT e.id, e.title, e.source_url, e.source, e.mode, e.start_date, e.registration_end FROM discovery_events e "
                            "JOIN discovery_deliveries d ON d.event_id = e.id AND d.user_id = %s "
                            "WHERE d.state = 'PENDING' AND d.digest_date = %s "
                            "AND (e.end_date IS NULL OR e.end_date >= %s) AND (e.registration_end IS NULL OR e.registration_end >= %s) "
                            "ORDER BY e.first_seen_at DESC LIMIT 10",
                            (user_id, run["digest_date"], local.date(), local.date()),
                        )
                        events = [{**row, "id": str(row["id"])} for row in cur.fetchall()]
                        content, components, included = _digest_content(events)
                        if not included:
                            cur.execute(
                                "DELETE FROM discovery_deliveries WHERE user_id = %s AND digest_date = %s AND state = 'PENDING'",
                                (user_id, run["digest_date"]),
                            )
                            cur.execute(
                                "UPDATE discord_digest_runs SET state = 'FAILED', lease_until = NULL WHERE user_id = %s AND digest_date = %s",
                                (user_id, run["digest_date"]),
                            )
                            continue
                        nonce = hashlib.sha256(f"{user_id}:{run['digest_date']}".encode()).hexdigest()[:24]
                        message_id = sender.send_message(link["channel_id"], content, components, nonce=nonce)
                        cur.execute(
                            "UPDATE discovery_deliveries SET state = 'SENT', delivered_at = %s "
                            "WHERE user_id = %s AND state = 'PENDING' AND event_id = ANY(%s)",
                            (now, user_id, [event["id"] for event in included]),
                        )
                        cur.execute(
                            "DELETE FROM discovery_deliveries WHERE user_id = %s AND digest_date = %s AND state = 'PENDING'",
                            (user_id, run["digest_date"]),
                        )
                        cur.execute(
                            "UPDATE discord_digest_runs SET state = 'SENT', message_id = %s, lease_until = NULL "
                            "WHERE user_id = %s AND digest_date = %s",
                            (message_id, user_id, run["digest_date"]),
                        )
        except Exception:
            with connect_factory() as conn:
                with conn:
                    with conn.cursor() as cur:
                        if run["attempt_count"] >= 5:
                            cur.execute(
                                "UPDATE discord_digest_runs SET state = 'FAILED', lease_until = NULL "
                                "WHERE user_id = %s AND digest_date = %s AND state = 'CLAIMED' AND attempt_count = %s RETURNING user_id",
                                (user_id, run["digest_date"], run["attempt_count"]),
                            )
                            if cur.fetchone():
                                cur.execute(
                                    "DELETE FROM discovery_deliveries WHERE user_id = %s AND digest_date = %s AND state = 'PENDING'",
                                    (user_id, run["digest_date"]),
                                )
                        else:
                            retry_at = now + timedelta(minutes=2 ** run["attempt_count"])
                            cur.execute(
                                "UPDATE discord_digest_runs SET state = 'PENDING', next_attempt_at = %s, lease_until = NULL "
                                "WHERE user_id = %s AND digest_date = %s AND state = 'CLAIMED' AND attempt_count = %s",
                                (retry_at, user_id, run["digest_date"], run["attempt_count"]),
                            )
            log.exception("Discord digest delivery failed for user %s", user_id)
            continue
        sent += 1
    return {"digest_attempts": attempted, "digests_sent": sent}
