from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from datetime import date, datetime, timezone
import asyncio
from importlib import import_module
import json
import sqlite3
from types import SimpleNamespace

from app.modules.integrations.discord.discovery import _digest_content, _is_morning, parse_listing
from app.modules.integrations.discord.router import _first_url, verify_signature
from app.modules.integrations.discord.worker import capture_payload
from app.modules.notifications.worker import _delivery_target


def test_discord_signature_accepts_fresh_signed_payload_and_rejects_stale_or_changed_body():
    now = 1_800_000_000
    timestamp = str(now)
    body = b'{"type":1}'
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    signature = private.sign(timestamp.encode() + body).hex()

    assert verify_signature(public, signature, timestamp, body, now=now)
    assert not verify_signature(public, signature, timestamp, body + b" ", now=now)
    assert not verify_signature(public, signature, timestamp, body, now=now + 301)


def test_track_link_command_extracts_a_clean_url_from_a_message():
    assert _first_url("Please check https://event.example.com/apply?utm_source=discord.") == "https://event.example.com/apply"
    assert _first_url("no hackathon link here") == ""


def test_discovery_parser_keeps_only_event_links_from_the_selected_source():
    html = b'''<a href="https://build.devpost.com/">Build Hackathon</a>
    <a href="https://example.com/event">Not Devpost</a>'''
    events = parse_listing("Devpost", "https://devpost.com/hackathons", html)

    assert [(event["title"], event["source_url"]) for event in events] == [
        ("Build Hackathon", "https://build.devpost.com/")
    ]


def test_unstop_listing_uses_event_headings_and_public_event_urls():
    html = b'''<a href="/hackathons/css-battle-1802090"><h2>CSS Battle</h2><span>66 Registered</span></a>
    <a href="/hackathons/">Browse Hackathons</a>'''
    events = parse_listing("Unstop", "https://api.unstop.com/hackathons/", html)

    assert [(event["title"], event["source_url"]) for event in events] == [
        ("CSS Battle", "https://unstop.com/hackathons/css-battle-1802090")
    ]


def test_mlh_listing_uses_event_cards_and_ignores_navigation():
    html = b'''<a href="/events/prizes">Prizes &amp; Freebies</a>
    <a href="https://event.example/?utm_campaign=events&amp;utm_source=mlh"><h4>Example Hackathon</h4><span>NOV 13</span></a>'''
    events = parse_listing("MLH", "https://www.mlh.com/seasons/2027/events/", html)

    assert [(event["title"], event["normalized_url"]) for event in events] == [
        ("Example Hackathon", "https://event.example")
    ]


def test_aggregator_data_keeps_categories_and_dates_and_discards_invalid_or_expired_items():
    from app.modules.integrations.discord import discovery

    body = json.dumps({"status": "success", "data": [
        {"title": "Example Hackathon", "platform": "unstop", "mode": "online", "url": "https://event.example/?utm_source=feed",
         "start_date": "2026-12-02T08:00:00Z", "end_date": "2026-12-04", "registration_end": "2026-12-01"},
        {"title": "Expired Hackathon", "url": "https://old.example/", "end_date": "2026-11-01"},
        {"title": "Private Address", "url": "http://127.0.0.1/event"},
        {"title": "Malformed Link", "url": "https://event.example/>unexpected"},
        {"title": "Missing URL"},
    ]}).encode()

    events = discovery.parse_aggregator(body, today=date(2026, 11, 10))

    assert events == [{
        "title": "Example Hackathon", "source": "Unstop", "mode": "online",
        "source_url": "https://event.example/?utm_source=feed", "normalized_url": "https://event.example",
        "start_date": date(2026, 12, 2), "end_date": date(2026, 12, 4), "registration_end": date(2026, 12, 1),
    }]


def test_brief_shows_platform_mode_and_registration_date():
    event = {"id": "event-1", "title": "Example Hackathon", "source_url": "https://event.example/",
             "source": "Unstop", "mode": "online", "registration_end": date(2026, 12, 1)}

    content, _components, _included = _digest_content([event])

    assert "Unstop" in content and "online" in content and "2026-12-01" in content


def test_discovery_uses_aggregator_and_supplements_missing_mlh(monkeypatch):
    from app.modules.integrations.discord import discovery

    called = []
    response = json.dumps({"status": "success", "data": [
        {"title": "Example Hackathon", "platform": "unstop", "url": "https://event.example/"},
    ]}).encode()

    def fetch(url, **_kwargs):
        called.append(url)
        return url, "application/json" if url == discovery.AGGREGATOR_URL else "text/html", response

    monkeypatch.setattr(discovery.fetching, "fetch", fetch)
    monkeypatch.setattr(discovery, "parse_listing", lambda source, _url, _body: [{"source": source}])

    events = discovery.discover_events()

    assert [event["source"] for event in events] == ["Unstop", "MLH"]
    assert called == [discovery.AGGREGATOR_URL, discovery.SOURCES["MLH"]]


def test_discovery_falls_back_to_direct_listings_when_aggregator_fails(monkeypatch):
    from app.modules.integrations.discord import discovery

    def fetch(url, **_kwargs):
        if url == discovery.AGGREGATOR_URL:
            raise ValueError("unavailable")
        return url, "text/html", b"listing"

    monkeypatch.setattr(discovery.fetching, "fetch", fetch)
    monkeypatch.setattr(discovery, "parse_listing", lambda source, _url, _body: [{"source": source}])

    assert [event["source"] for event in discovery.discover_events()] == ["Devpost", "Unstop", "MLH"]


def test_digest_buttons_fit_discord_action_row_limits():
    events = [
        {"id": f"event-{index}", "title": f"Hackathon {index}", "source_url": f"https://event{index}.example.com"}
        for index in range(10)
    ]

    _content, components, included = _digest_content(events)

    assert len(included) == 5
    assert [len(row["components"]) for row in components] == [5, 5]
    buttons = [button for row in components for button in row["components"]]
    assert {button["custom_id"] for button in buttons} == {
        f"{action}:event-{index}" for index in range(5) for action in ("discover", "registered")
    }


def test_oversized_queued_brief_sends_five_and_releases_the_rest():
    from app.modules.integrations.discord import discovery

    class Connection:
        def __init__(self, already_sent=False):
            self.state = "PENDING"
            self.pending = {str(index) for index in range(10)}
            self.sent = set()
            self.one = None
            self.already_sent = already_sent
            self.deferred = None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return self

        def execute(self, query, params):
            if query.startswith("UPDATE discord_digest_runs SET state = 'CLAIMED'"):
                self.one = {"user_id": "user-1", "digest_date": date(2026, 11, 10), "attempt_count": 1} if self.state == "PENDING" and self.deferred is None else None
                if self.one:
                    self.state = "CLAIMED"
            elif query.startswith("SELECT l.channel_id"):
                self.one = {"channel_id": "channel-1", "timezone": "Asia/Kolkata"}
            elif query.startswith("SELECT state, attempt_count"):
                self.one = {"state": self.state, "attempt_count": 1}
            elif query.startswith("SELECT count(*) AS n FROM discovery_deliveries"):
                self.one = {"n": int(self.already_sent)}
            elif query.startswith("UPDATE discovery_deliveries SET state = 'SENT'"):
                self.sent.update(params[2])
            elif query.startswith("DELETE FROM discovery_deliveries"):
                self.pending.intersection_update(self.sent)
            elif query.startswith("UPDATE discord_digest_runs SET state = 'SENT'"):
                self.state = "SENT"
            elif query.startswith("UPDATE discord_digest_runs SET state = 'PENDING'"):
                self.state = "PENDING"
                self.deferred = params[0]

        def fetchone(self):
            return self.one

        def fetchall(self):
            return [{"id": str(index), "title": f"Hackathon {index}", "source_url": f"https://event{index}.example/"} for index in range(10)]

    class Sender:
        def __init__(self):
            self.messages = []

        def send_message(self, channel, content, components, nonce=None):
            self.messages.append((channel, content, components))
            return "message-1"

    conn, sender = Connection(), Sender()
    result = discovery.deliver_pending_digests(lambda: conn, sender, datetime(2026, 11, 10, 3, 30, tzinfo=timezone.utc))

    assert result == {"digest_attempts": 1, "digests_sent": 1}
    assert len(sender.messages) == 1
    assert len(conn.sent) == 5
    assert conn.pending == conn.sent

    deferred, second_sender = Connection(already_sent=True), Sender()
    result = discovery.deliver_pending_digests(lambda: deferred, second_sender, datetime(2026, 11, 10, 3, 30, tzinfo=timezone.utc))
    assert result == {"digest_attempts": 1, "digests_sent": 0}
    assert not second_sender.messages
    assert deferred.deferred.isoformat() == "2026-11-11T09:00:00+05:30"


def test_two_queued_briefs_send_at_most_five_in_one_morning():
    from app.modules.integrations.discord import discovery

    database = sqlite3.connect(":memory:")
    database.row_factory = sqlite3.Row
    database.executescript("""
        CREATE TABLE users (id text PRIMARY KEY, timezone text);
        CREATE TABLE discord_links (user_id text, channel_id text);
        CREATE TABLE discord_digest_runs (
            user_id text, digest_date date, state text, attempt_count integer, next_attempt_at text,
            lease_until text, message_id text DEFAULT '', PRIMARY KEY(user_id, digest_date)
        );
        CREATE TABLE discovery_events (
            id integer PRIMARY KEY, title text, source_url text, source text, mode text,
            start_date date, end_date date, registration_end date, first_seen_at text
        );
        CREATE TABLE discovery_deliveries (user_id text, event_id integer, digest_date date, state text, delivered_at text);
        INSERT INTO users VALUES ('user-1', 'Asia/Kolkata');
        INSERT INTO discord_links VALUES ('user-1', 'channel-1');
        INSERT INTO discord_digest_runs VALUES ('user-1', '2026-11-09', 'PENDING', 0, '2026-11-10T03:00:00+00:00', NULL, '');
        INSERT INTO discord_digest_runs VALUES ('user-1', '2026-11-10', 'PENDING', 0, '2026-11-10T03:01:00+00:00', NULL, '');
    """)
    for index in range(13):
        day = "2026-11-09" if index < 10 else "2026-11-10"
        database.execute("INSERT INTO discovery_events VALUES (?, ?, ?, 'Test', '', NULL, NULL, NULL, ?)",
                         (index, f"Hackathon {index}", f"https://event{index}.example/", f"2026-11-10T00:00:{index:02}"))
        database.execute("INSERT INTO discovery_deliveries VALUES ('user-1', ?, ?, 'PENDING', NULL)", (index, day))

    class Cursor:
        def __enter__(self):
            self.cursor = database.cursor()
            return self

        def __exit__(self, *_args):
            self.cursor.close()

        def execute(self, query, params=()):
            if "event_id = ANY(%s)" in query:
                query = query.replace("event_id = ANY(%s)", "event_id IN (" + ",".join("?" for _ in params[-1]) + ")")
                params = (*params[:-1], *params[-1])
            sql = (query.replace(" + interval '2 minutes'", "").replace(" FOR UPDATE SKIP LOCKED", "")
                   .replace(" FOR UPDATE OF l", "").replace(" FOR UPDATE", "").replace("%s", "?"))
            self.cursor.execute(sql, tuple(p.isoformat() if isinstance(p, date) else p for p in params))

        def fetchone(self):
            return self.cursor.fetchone()

        def fetchall(self):
            return self.cursor.fetchall()

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return Cursor()

    class Sender:
        def __init__(self):
            self.messages = []

        def send_message(self, channel, content, components, nonce=None):
            self.messages.append((channel, content, components))
            return "message-1"

    sender = Sender()
    result = discovery.deliver_pending_digests(Connection, sender, datetime(2026, 11, 10, 3, 30, tzinfo=timezone.utc))

    assert result == {"digest_attempts": 2, "digests_sent": 1}
    assert len(sender.messages) == 1
    assert database.execute("SELECT count(*) FROM discovery_deliveries WHERE state = 'SENT'").fetchone()[0] == 5
    assert database.execute("SELECT count(*) FROM discovery_deliveries WHERE state = 'PENDING'").fetchone()[0] == 3
    next_run = database.execute("SELECT state, next_attempt_at FROM discord_digest_runs WHERE digest_date = '2026-11-10'").fetchone()
    assert (next_run["state"], next_run["next_attempt_at"]) == ("PENDING", "2026-11-11T09:00:00+05:30")


def test_digest_uses_the_users_morning_including_half_hour_timezones():
    assert _is_morning(datetime(2026, 11, 9, 3, 30, tzinfo=timezone.utc), "Asia/Kolkata")
    assert _is_morning(datetime(2026, 11, 9, 4, 59, tzinfo=timezone.utc), "Asia/Kolkata")
    assert not _is_morning(datetime(2026, 11, 9, 5, 30, tzinfo=timezone.utc), "Asia/Kolkata")
    assert not _is_morning(datetime(2026, 11, 9, 2, 30, tzinfo=timezone.utc), "Asia/Kolkata")
    assert _is_morning(datetime(2026, 11, 9, 9, 0, tzinfo=timezone.utc), "UTC")


def test_discovery_cron_delivers_the_queued_brief(monkeypatch):
    discovery = import_module("app.modules.integrations.discord.discovery")
    router = import_module("app.modules.integrations.discord.router")
    calls = []
    monkeypatch.setattr(router, "cron_authorized", lambda _request: True)
    monkeypatch.setattr(discovery, "run_discovery", lambda _connect: calls.append("discover") or {"digests_queued": 1})
    monkeypatch.setattr(discovery, "deliver_pending_digests", lambda _connect, _sender: calls.append("deliver") or {"digests_sent": 1})
    monkeypatch.setattr(router, "Sender", lambda: object())

    response = asyncio.run(router.discovery_cron(object()))

    assert response.status_code == 200
    assert json.loads(response.body) == {"digests_queued": 1, "digests_sent": 1}
    assert calls == ["discover", "deliver"]


def test_digest_omits_matching_titles_from_different_sources():
    events = [
        {"id": "one", "title": "Global Innovation Hackathon 2026", "source_url": "https://one.example/event"},
        {"id": "two", "title": "global innovation hackathon 2026", "source_url": "https://two.example/event"},
        {"id": "three", "title": "Another Unique Hackathon", "source_url": "https://three.example/event"},
    ]

    _content, _components, included = _digest_content(events)

    assert [event["id"] for event in included] == ["one", "three"]


def test_discovery_does_not_fetch_again_after_the_daily_brief_is_queued(monkeypatch):
    from app.modules.integrations.discord import discovery

    database = sqlite3.connect(":memory:")
    database.row_factory = sqlite3.Row
    database.executescript("""
        CREATE TABLE users (id text PRIMARY KEY, timezone text);
        CREATE TABLE discord_links (user_id text, channel_id text);
        CREATE TABLE discord_digest_runs (user_id text, digest_date date);
        INSERT INTO users VALUES ('user-1', 'Asia/Kolkata');
        INSERT INTO discord_links VALUES ('user-1', 'channel-1');
        INSERT INTO discord_digest_runs VALUES ('user-1', '2026-11-09');
    """)

    class Cursor:
        def __enter__(self):
            self.cursor = database.cursor()
            return self

        def __exit__(self, *_args):
            self.cursor.close()

        def execute(self, query, params=()):
            self.cursor.execute(query.replace("%s", "?"), tuple(p.isoformat() if isinstance(p, date) else p for p in params))

        def fetchall(self):
            return self.cursor.fetchall()

        def fetchone(self):
            return self.cursor.fetchone()

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return Cursor()

    def unexpected_fetch():
        raise AssertionError("already queued brief should not fetch sources again")

    monkeypatch.setattr(discovery, "discover_events", unexpected_fetch)
    result = discovery.run_discovery(Connection, datetime(2026, 11, 9, 3, 45, tzinfo=timezone.utc))

    assert result == {"sources_found": 0, "digests_queued": 0}


def test_daily_briefs_do_not_repeat_sent_or_tracked_events(monkeypatch):
    from app.modules.integrations.discord import discovery

    database = sqlite3.connect(":memory:")
    database.row_factory = sqlite3.Row
    database.create_function("now", 0, lambda: "2026-11-09 03:30:00")
    database.executescript("""
        CREATE TABLE users (id text PRIMARY KEY, timezone text);
        CREATE TABLE discord_links (user_id text, channel_id text);
        CREATE TABLE hackathons (user_id text, normalized_url text, title text);
        CREATE TABLE discovery_events (
            id integer PRIMARY KEY, source_url text, normalized_url text UNIQUE, title text, source text,
            mode text NOT NULL DEFAULT '', start_date date, end_date date, registration_end date,
            first_seen_at timestamp DEFAULT CURRENT_TIMESTAMP, updated_at timestamp DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE discovery_deliveries (user_id text, event_id integer, digest_date date, PRIMARY KEY (user_id, event_id));
        CREATE TABLE discord_digest_runs (user_id text, digest_date date, PRIMARY KEY (user_id, digest_date));
        INSERT INTO users VALUES ('user-1', 'Asia/Kolkata');
        INSERT INTO discord_links VALUES ('user-1', 'channel-1');
        INSERT INTO hackathons VALUES ('user-1', 'https://tracked.example/', 'Already Tracked Hackathon');
        INSERT INTO discovery_events (source_url, normalized_url, title, source) VALUES
            ('https://stale.example/', 'https://stale.example/', 'No Longer Listed Hackathon', 'Test');
    """)

    class Cursor:
        def __enter__(self):
            self.cursor = database.cursor()
            return self

        def __exit__(self, *_args):
            self.cursor.close()

        def execute(self, query, params=()):
            if "e.normalized_url = ANY(%s)" in query:
                query = query.replace("e.normalized_url = ANY(%s)", "e.normalized_url IN (" + ",".join("?" for _ in params[0]) + ")")
                params = (*params[0], *params[1:])
            sql = query.replace("%s", "?").replace(" FOR UPDATE", "")
            self.cursor.execute(sql, tuple(p.isoformat() if isinstance(p, date) else p for p in params))

        def executemany(self, query, params):
            sql = query.replace("%s", "?")
            rows = [tuple(p.isoformat() if isinstance(p, date) else p for p in row) for row in params]
            self.cursor.executemany(sql, rows)

        def fetchall(self):
            return self.cursor.fetchall()

        def fetchone(self):
            return self.cursor.fetchone()

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return Cursor()

    def event(title, url):
        return {"title": title, "source_url": url, "normalized_url": url, "source": "Test"}

    found = [
        event("Global Innovation Hackathon 2026", "https://first.example/"),
        event("Global Innovation Hackathon 2026", "https://second.example/"),
        event("Already Tracked Hackathon", "https://tracked.example/"),
        event("already tracked hackathon", "https://other-source.example/"),
    ]
    monkeypatch.setattr(discovery, "discover_events", lambda: found)

    first = discovery.run_discovery(Connection, datetime(2026, 11, 9, 3, 30, tzinfo=timezone.utc))
    found.append({**event("A New Hackathon Tomorrow", "https://new.example/"), "mode": "online", "registration_end": date(2026, 11, 15)})
    found.append({**event("Expired Hackathon", "https://expired.example/"), "end_date": date(2026, 11, 9)})
    second = discovery.run_discovery(Connection, datetime(2026, 11, 10, 3, 30, tzinfo=timezone.utc))
    found.clear()
    third = discovery.run_discovery(Connection, datetime(2026, 11, 11, 3, 30, tzinfo=timezone.utc))
    sent = database.execute(
        "SELECT d.digest_date, e.title FROM discovery_deliveries d JOIN discovery_events e ON e.id = d.event_id ORDER BY d.digest_date"
    ).fetchall()

    assert first["digests_queued"] == second["digests_queued"] == 1
    assert third["digests_queued"] == 0
    assert [(row["digest_date"], row["title"]) for row in sent] == [
        ("2026-11-09", "Global Innovation Hackathon 2026"),
        ("2026-11-10", "A New Hackathon Tomorrow"),
    ]
    metadata = database.execute("SELECT mode, registration_end FROM discovery_events WHERE normalized_url = ?", ("https://new.example/",)).fetchone()
    assert (metadata["mode"], metadata["registration_end"]) == ("online", "2026-11-15")


def test_capture_payload_keeps_extracted_dates_unconfirmed_until_user_confirms_them():
    payload = capture_payload(
        "https://event.example.com/",
        {
            "title": "Build Hackathon",
            "timezone": "UTC",
            "milestones": [{"type": "SUBMIT", "title": "Submit project", "due_at": "2026-12-01T23:59:00Z", "due_precision": "DATETIME"}],
        },
        "job-1",
    )

    assert payload["milestones"] == [
        {"type": "SUBMIT", "title": "Submit project", "due_precision": "DATETIME", "date_confirmed": False, "due_at": "2026-12-01T23:59:00Z"}
    ]
    assert payload["registration_state"] == "PLANNING"
    assert payload["idempotency_key"] == "discord-capture:job-1"


def test_reminder_cutover_falls_back_to_telegram_until_discord_was_linked():
    class Cursor:
        def __init__(self, *rows):
            self.rows = iter(rows)

        def execute(self, *_args):
            pass

        def fetchone(self):
            return next(self.rows)

    assert _delivery_target(Cursor(
        {"channel_id": None, "linked_at": None}, {"chat_id": "telegram-chat"}
    ), "user") == ("TELEGRAM", "telegram-chat")
    assert _delivery_target(Cursor(
        {"channel_id": "discord-channel", "linked_at": object()}
    ), "user") == ("DISCORD", "discord-channel")
    assert _delivery_target(Cursor(
        {"channel_id": None, "linked_at": object()}
    ), "user") == (None, None)


def test_registered_capture_uses_existing_event_and_offers_unconfirmed_dates(monkeypatch):
    from app.modules.integrations.discord import worker

    recorded = []
    monkeypatch.setattr(worker, "capture_data", lambda *_args: {"duplicate_id": "event-1"})
    monkeypatch.setattr(worker, "register_hackathon", lambda user, event: recorded.append((user, event)) or {
        "registration_state": "REGISTERED",
        "milestones": [{"type": "SUBMIT", "title": "Submit project", "due_at": "2026-12-01T12:00:00Z", "date_confirmed": False, "status": "TODO"}],
    })
    monkeypatch.setattr(worker, "load_config", lambda: SimpleNamespace(app_base_url="https://hacktrack.example"))

    content, rows = worker._capture_message(None, {"user_id": "user-1", "url": "https://event.example", "registration_requested": True})

    assert recorded == [("user-1", "event-1")]
    assert "Registration recorded" in content
    assert "Submit project: 2026-12-01T12:00:00Z" in content
    assert any(button.get("custom_id") == "confirm:event-1" for button in rows[0]["components"])


def test_registration_is_saved_when_the_event_page_cannot_be_fetched(monkeypatch):
    from app.errors import Invalid
    from app.modules.integrations.discord import worker

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return self

        def execute(self, *_args):
            pass

        def fetchone(self):
            return {"title": "Example Hackathon"}

    def blocked(*_args):
        raise Invalid("could not fetch this page; paste its text instead")

    saved = []
    monkeypatch.setattr(worker, "capture_data", blocked)
    monkeypatch.setattr(worker, "create_hackathon_for_user", lambda user, payload, **_kwargs: saved.append((user, payload)) or {"id": "event-1"})
    monkeypatch.setattr(worker, "register_hackathon", lambda *_args: {"registration_state": "REGISTERED", "milestones": []})
    monkeypatch.setattr(worker, "load_config", lambda: SimpleNamespace(app_base_url="https://hacktrack.example"))

    content, _rows = worker._capture_message(Connection, {
        "id": "job-1", "user_id": "user-1", "url": "https://event.example/",
        "registration_requested": True, "discovery_event_id": "discovery-1",
    })

    assert saved[0][1]["title"] == "Example Hackathon"
    assert "Registration recorded" in content
    assert "Add the event's deadlines" in content


def test_registering_from_digest_queues_a_registration(monkeypatch):
    router = import_module("app.modules.integrations.discord.router")

    queued = []
    monkeypatch.setattr(router, "_linked_user", lambda _discord_id: ("user-1", "channel-1"))
    monkeypatch.setattr(router, "_queue_discovery_event", lambda user, event, interaction, registration_requested=False: queued.append((user, event, registration_requested)) or router._ephemeral("Queued"))

    router._component({"user": {"id": "discord-user"}}, "registered:event-1")

    assert queued == [("user-1", "event-1", True)]


def test_registration_can_be_queued_after_the_event_was_already_tracked(monkeypatch):
    router = import_module("app.modules.integrations.discord.router")

    class Connection:
        def __init__(self):
            self.rows = iter([
                {"source_url": "https://event.example", "channel_id": "channel-1", "tracked_at": object()},
                {"n": 0},
                {"id": "job-1"},
            ])
            self.inserted = None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return self

        def execute(self, query, params):
            if query.startswith("INSERT INTO discord_capture_jobs"):
                self.inserted = params

        def fetchone(self):
            return next(self.rows)

    conn = Connection()
    monkeypatch.setattr(router.db, "connect", lambda: conn)
    response = router._queue_discovery_event("user-1", "event-1", {}, registration_requested=True)

    assert response.status_code == 200
    assert conn.inserted[-2:] == (True, "event-1")

    retry = Connection()
    monkeypatch.setattr(router.db, "connect", lambda: retry)
    response = router._queue_discovery_event("user-1", "event-1", {})
    assert response.status_code == 200
    assert retry.inserted[-2:] == (False, "event-1")


def test_registration_transition_is_idempotent_and_finishes_registration_milestones(monkeypatch):
    router = import_module("app.modules.hackathons.router")

    event_id = "00000000-0000-0000-0000-000000000001"

    class Connection:
        state = "PLANNING"
        last_query = ""

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return self

        def execute(self, query, params):
            self.last_query = query
            if query.startswith("UPDATE hackathons SET registration_state"):
                self.state = params[0]

        def fetchone(self):
            return {"id": event_id, "registration_state": self.state}

        def fetchall(self):
            return [{"id": "registration-milestone"}]

    conn = Connection()
    completed, transitions, confirmations = [], [], []
    monkeypatch.setattr(router.db, "connect", lambda: conn)
    monkeypatch.setattr(router.rules, "record_transition", lambda *args: transitions.append(args))
    monkeypatch.setattr(router, "_update_milestone_status", lambda *args: completed.append(args))
    monkeypatch.setattr(router, "_ensure_confirm_dates", lambda *args: confirmations.append(args))
    monkeypatch.setattr(router, "_detail_for_row", lambda _conn, row, _now: {"registration_state": row["registration_state"]})

    first = router.transition_for_user("user-1", event_id, "registration_state", "REGISTERED", {}, idempotent_registration=True)
    second = router.transition_for_user("user-1", event_id, "registration_state", "REGISTERED", {}, idempotent_registration=True)

    assert first == second == {"registration_state": "REGISTERED"}
    assert len(transitions) == len(completed) == len(confirmations) == 1
    assert completed[0][2:4] == ("registration-milestone", "DONE")


def test_unconfirmed_deadlines_get_a_confirmation_reminder(monkeypatch):
    router = import_module("app.modules.hackathons.router")
    now = datetime(2026, 11, 9, 9, 0, tzinfo=timezone.utc)

    class Connection:
        query = ""
        inserted = None

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return self

        def execute(self, query, params):
            self.query = query
            if query.startswith("INSERT INTO milestones"):
                self.inserted = params

        def fetchone(self):
            if self.query.startswith("SELECT * FROM hackathons"):
                return {"registration_state": "REGISTERED", "outcome": None, "archived_at": None}
            if self.query.startswith("INSERT INTO milestones"):
                return {"id": "confirmation"}
            if "date_confirmed AND status IN ('TODO','DONE')" in self.query:
                return {"id": "completed-registration"}
            if "status = 'TODO' AND required AND due_at IS NOT NULL" in self.query:
                return {"id": "unconfirmed-deadline"}
            return None

        def fetchall(self):
            return []

    conn = Connection()
    planned = []
    monkeypatch.setattr(router.rules, "record_transition", lambda *_args: None)
    monkeypatch.setattr(router.reminders, "regenerate", lambda _conn, milestone, _now: planned.append(milestone))

    router._ensure_confirm_dates(conn, "user-1", "event-1", now)

    assert conn.inserted[2] == datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)
    assert planned == ["confirmation"]


def test_digest_sender_uses_discord_nonce_to_reduce_retry_duplicates():
    from app.modules.integrations.discord.client import Sender

    class Bot:
        def call(self, method, path, payload):
            self.payload = payload
            return {"id": "message-1"}

    bot = Bot()
    assert Sender(bot=bot).send_message("channel-1", "Morning brief", nonce="brief-1") == "message-1"
    assert bot.payload["nonce"] == "brief-1"
    assert bot.payload["enforce_nonce"] is True
