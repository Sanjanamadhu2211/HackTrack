import logging
import re
import time
from datetime import datetime, timedelta, timezone

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app import db, tokens
from app.core.config import load_config
from app.errors import Invalid, NotFound
from app.modules.hackathons.actions import confirm_dates, register_hackathon
from app.modules.identity.service import require_user_dependency
from app.modules.integrations.discord.client import Sender
from app.modules.notifications.actions import ack_delivery
from app.normalize import normalize_url

router = APIRouter()
log = logging.getLogger(__name__)
MAX_INTERACTION_BYTES = 256 * 1024
_URL = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)


def verify_signature(public_key, signature, timestamp, body, now=None):
    try:
        issued_at = int(timestamp)
        if abs((int(time.time()) if now is None else now) - issued_at) > 300:
            return False
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key)).verify(
            bytes.fromhex(signature), timestamp.encode("ascii") + body
        )
        return True
    except (ValueError, UnicodeError, InvalidSignature):
        return False


def _response(kind, content="", **data):
    body = {"content": content, "allowed_mentions": {"parse": []}, **data}
    return JSONResponse({"type": kind, "data": body})


def _ephemeral(content):
    return _response(4, content, flags=64)


def _interaction_user(interaction):
    return str((interaction.get("member", {}).get("user") or interaction.get("user") or {}).get("id") or "")


def _options(data):
    return {item.get("name"): item.get("value") for item in data.get("options", []) if isinstance(item, dict)}


def _first_url(text):
    for match in _URL.finditer(text or ""):
        candidate = match.group(0).rstrip(".,!?)]}")
        try:
            return normalize_url(candidate)
        except Exception:
            continue
    return ""


def _linked_user(discord_user_id):
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, channel_id FROM discord_links WHERE discord_user_id = %s", (discord_user_id,))
            row = cur.fetchone()
    return (str(row["user_id"]), row["channel_id"]) if row else (None, None)


def _pair_discord_user(discord_user_id, channel_id, raw_token, now):
    with db.connect() as conn:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id FROM discord_links WHERE discord_user_id = %s FOR UPDATE", (discord_user_id,))
                existing = cur.fetchone()
                cur.execute(
                    "SELECT user_id FROM discord_links WHERE pairing_token_hash = %s AND pairing_expires_at > %s FOR UPDATE",
                    (tokens.hash_token(raw_token), now),
                )
                target = cur.fetchone()
                if not target or (existing and str(existing["user_id"]) != str(target["user_id"])):
                    return False
                cur.execute(
                    "UPDATE discord_links SET discord_user_id = %s, channel_id = %s, linked_at = %s, "
                    "pairing_token_hash = NULL, pairing_expires_at = NULL, updated_at = now() "
                    "WHERE pairing_token_hash = %s AND pairing_expires_at > %s RETURNING user_id",
                    (discord_user_id, channel_id, now, tokens.hash_token(raw_token), now),
                )
                return cur.fetchone() is not None


def _queue_capture(user_id, channel_id, url, interaction_id):
    with db.connect() as conn:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM discord_capture_jobs WHERE user_id = %s AND interaction_id = %s",
                    (user_id, interaction_id),
                )
                if cur.fetchone():
                    return True
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"discord-capture:{user_id}",))
                cur.execute(
                    "SELECT count(*) AS n FROM discord_capture_jobs WHERE user_id = %s AND state IN ('PENDING','CLAIMED')",
                    (user_id,),
                )
                if cur.fetchone()["n"] >= 5:
                    return False
                cur.execute(
                    "INSERT INTO discord_capture_jobs (user_id, channel_id, url, interaction_id) "
                    "VALUES (%s, %s, %s, %s) ON CONFLICT (interaction_id) DO NOTHING RETURNING id",
                    (user_id, channel_id, url, interaction_id),
                )
                if cur.fetchone():
                    return True
                cur.execute("SELECT id FROM discord_capture_jobs WHERE user_id = %s AND interaction_id = %s", (user_id, interaction_id))
                return cur.fetchone() is not None


def _link_command(interaction, data):
    discord_user_id = _interaction_user(interaction)
    token_value = _options(data).get("token")
    channel_id = interaction.get("channel_id")
    if not discord_user_id or not channel_id or not isinstance(token_value, str):
        return _ephemeral("Run this in the HackTrack channel with the code from Settings.")
    if not _pair_discord_user(discord_user_id, str(channel_id), token_value.strip(), datetime.now(timezone.utc)):
        return _ephemeral("That code expired or is already linked. Generate a new one in HackTrack Settings.")
    return _ephemeral("Connected. HackTrack reminders and daily hackathon links will arrive in this channel.")


def _track_command(interaction, url):
    if not url:
        return _ephemeral("I couldn't find a web link in that message.")
    user_id, channel_id = _linked_user(_interaction_user(interaction))
    if not user_id:
        return _ephemeral("Connect Discord in HackTrack Settings first.")
    if not channel_id:
        return _ephemeral("Reconnect Discord from HackTrack Settings first.")
    try:
        url = normalize_url(url)
    except Exception:
        return _ephemeral("That doesn't look like a valid web link.")
    if len(url) > 2048:
        return _ephemeral("That link is too long to track.")
    if not _queue_capture(user_id, channel_id, url, str(interaction.get("id", ""))):
        return _ephemeral("There are already several links waiting, or this request was already queued. Try again shortly.")
    return _ephemeral("Queued. I’ll fetch the event details and post them in your HackTrack channel.")


def _context_message_url(interaction, data):
    target_id = str(data.get("target_id", ""))
    resolved = data.get("resolved", {}).get("messages", {})
    message = resolved.get(target_id, {})
    url = _first_url(message.get("content", ""))
    if url:
        return url
    return next((
        candidate for embed in message.get("embeds", [])
        if (candidate := _first_url(embed.get("url", "")))
    ), "")


def _component(interaction, custom_id):
    discord_user_id = _interaction_user(interaction)
    user_id, _channel_id = _linked_user(discord_user_id)
    if not user_id:
        return _ephemeral("Connect Discord in HackTrack Settings first.")
    now = datetime.now(timezone.utc)
    parts = custom_id.split(":")
    try:
        if parts[0] == "notify" and len(parts) == 3:
            choice = {"1h": "1h", "3h": "3h", "tomorrow": "tomorrow"}.get(parts[1])
            action = "DONE" if parts[1] == "done" else "SNOOZE"
            if parts[1] != "done" and choice is None:
                return _ephemeral("Unknown reminder action.")
            ack = ack_delivery(db.connect, user_id, parts[2], action, choice, now)
            if ack.kind == "repeated":
                status = "This reminder was already handled."
            elif ack.kind == "snoozed":
                status = f"Snoozed until {ack.snoozed_until.astimezone(timezone.utc).strftime('%a %H:%M UTC')}."
            elif ack.kind == "snooze_no_time":
                status = "This reminder can no longer be snoozed."
            else:
                status = "Marked done."
            message = interaction.get("message", {})
            content = (message.get("content") or "")[:1850] + "\n\n" + status
            return JSONResponse({"type": 7, "data": {"content": content, "components": [], "allowed_mentions": {"parse": []}}})
        if parts[0] == "confirm" and len(parts) == 2:
            count = confirm_dates(db.connect, user_id, parts[1], now)
            return _ephemeral(f"Confirmed {count} deadline(s); reminders are enabled.")
        if parts[0] == "register" and len(parts) == 2:
            register_hackathon(user_id, parts[1])
            return _ephemeral("Registration recorded. Check the event's dates to enable deadline reminders.")
        if parts[0] == "discover" and len(parts) == 2:
            return _queue_discovery_event(user_id, parts[1], interaction)
        if parts[0] == "registered" and len(parts) == 2:
            return _queue_discovery_event(user_id, parts[1], interaction, registration_requested=True)
    except NotFound:
        return _ephemeral("That event or reminder is no longer available.")
    except Invalid as error:
        return _ephemeral(error.message)
    except Exception:
        log.exception("Discord button action failed")
        return _ephemeral("Couldn't update HackTrack just now. Please try again.")
    return _ephemeral("Unknown HackTrack action.")


def _queue_discovery_event(user_id, event_id, interaction, registration_requested=False):
    with db.connect() as conn:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT e.source_url, l.channel_id FROM discovery_events e "
                    "JOIN discovery_deliveries d ON d.event_id = e.id AND d.user_id = %s "
                    "JOIN discord_links l ON l.user_id = d.user_id "
                    "WHERE e.id = %s FOR UPDATE OF d",
                    (user_id, event_id),
                )
                row = cur.fetchone()
                if not row:
                    raise NotFound()
                if not row["channel_id"]:
                    return _ephemeral("Reconnect Discord from HackTrack Settings first.")
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"discord-capture:{user_id}",))
                cur.execute(
                    "SELECT count(*) AS n FROM discord_capture_jobs WHERE user_id = %s AND state IN ('PENDING','CLAIMED')",
                    (user_id,),
                )
                if cur.fetchone()["n"] >= 5:
                    return _ephemeral("There are already several links waiting. Try again shortly.")
                cur.execute(
                    "INSERT INTO discord_capture_jobs (user_id, channel_id, url, interaction_id, registration_requested, discovery_event_id) "
                    "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (interaction_id) DO UPDATE SET "
                    "state = 'PENDING', channel_id = EXCLUDED.channel_id, attempt_count = 0, next_attempt_at = now(), lease_until = NULL, last_error = '', updated_at = now() "
                    "WHERE discord_capture_jobs.state = 'FAILED' RETURNING id",
                    (user_id, row["channel_id"], row["source_url"],
                     f"digest:{'registered:' if registration_requested else ''}{user_id}:{event_id}", registration_requested, event_id),
                )
                if not cur.fetchone():
                    return _ephemeral("That request is already queued or completed.")
                cur.execute(
                    "UPDATE discovery_deliveries SET tracked_at = now() WHERE user_id = %s AND event_id = %s",
                    (user_id, event_id),
                )
    if registration_requested:
        return _ephemeral("Queued. I’ll record your registration and send the event details here.")
    return _ephemeral("Queued. I’ll add it to HackTrack and send the details here.")


def _handle_interaction(interaction):
    kind = interaction.get("type")
    data = interaction.get("data") or {}
    if kind == 1:
        return JSONResponse({"type": 1})
    if kind == 2:
        command_type = data.get("type", 1)
        name = data.get("name")
        if name == "link":
            return _link_command(interaction, data)
        if name == "track" and command_type == 1:
            return _track_command(interaction, _options(data).get("url", ""))
        if name == "Track link" and command_type == 3:
            return _track_command(interaction, _context_message_url(interaction, data))
    if kind == 3:
        return _component(interaction, str(data.get("custom_id", "")))
    return _ephemeral("This HackTrack action is not supported.")


@router.post("/integrations/discord/pair")
def discord_pair(user_id: str = Depends(require_user_dependency)):
    cfg = load_config()
    if not cfg.discord_application_id or not cfg.discord_bot_token or not cfg.discord_public_key:
        return JSONResponse({"error": "Discord is not configured"}, status_code=503)
    raw, hashed = tokens.new_token()
    expires = datetime.now(timezone.utc) + timedelta(minutes=15)
    with db.connect() as conn:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO discord_links (user_id, pairing_token_hash, pairing_expires_at) VALUES (%s, %s, %s) "
                    "ON CONFLICT (user_id) DO UPDATE SET pairing_token_hash = EXCLUDED.pairing_token_hash, "
                    "pairing_expires_at = EXCLUDED.pairing_expires_at, updated_at = now()",
                    (user_id, hashed, expires),
                )
    return {"code": raw}


@router.delete("/integrations/discord")
def discord_delete(user_id: str = Depends(require_user_dependency)):
    with db.connect() as conn:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id FROM discord_links WHERE user_id = %s FOR UPDATE", (user_id,))
                cur.execute(
                    "UPDATE discord_capture_jobs SET state = 'FAILED', last_error = 'Discord disconnected', "
                    "lease_until = NULL, updated_at = now() WHERE user_id = %s AND state IN ('PENDING','CLAIMED')",
                    (user_id,),
                )
                cur.execute(
                    "UPDATE discord_digest_runs SET state = 'FAILED', lease_until = NULL "
                    "WHERE user_id = %s AND state IN ('PENDING','CLAIMED')",
                    (user_id,),
                )
                cur.execute("DELETE FROM discovery_deliveries WHERE user_id = %s AND state = 'PENDING'", (user_id,))
                cur.execute(
                    "UPDATE discord_links SET discord_user_id = NULL, channel_id = NULL, pairing_token_hash = NULL, "
                    "pairing_expires_at = NULL, updated_at = now() WHERE user_id = %s",
                    (user_id,),
                )
    return PlainTextResponse("", status_code=204)


@router.post("/integrations/discord/interactions")
async def discord_interactions(request: Request):
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > MAX_INTERACTION_BYTES:
        return PlainTextResponse("payload too large", status_code=413)
    body = await request.body()
    if len(body) > MAX_INTERACTION_BYTES:
        return PlainTextResponse("payload too large", status_code=413)
    cfg = load_config()
    if not cfg.discord_public_key:
        return PlainTextResponse("Discord is not configured", status_code=503)
    if not verify_signature(
        cfg.discord_public_key,
        request.headers.get("x-signature-ed25519", ""),
        request.headers.get("x-signature-timestamp", ""),
        body,
    ):
        return PlainTextResponse("invalid request signature", status_code=401)
    try:
        interaction = await request.json()
        if not isinstance(interaction, dict):
            raise ValueError("invalid interaction")
    except Exception:
        log.exception("Invalid Discord interaction")
        return PlainTextResponse("invalid interaction", status_code=400)
    try:
        return _handle_interaction(interaction)
    except Exception:
        log.exception("Discord interaction handling failed")
        return _ephemeral("HackTrack couldn't process that just now. Please try again.")


def cron_authorized(request):
    try:
        secret = load_config().cron_secret
    except RuntimeError:
        return False
    return bool(secret) and tokens.constant_time_equal(request.headers.get("authorization", ""), f"Bearer {secret}")


@router.api_route("/cron/discord", methods=["GET", "POST"])
async def discord_cron(request: Request):
    if not cron_authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        from app.modules.integrations.discord.discovery import deliver_pending_digests
        from app.modules.integrations.discord.worker import process_captures

        sender = Sender()
        result = process_captures(db.connect, sender)
        result.update(deliver_pending_digests(db.connect, sender))
        return JSONResponse(result, status_code=200)
    except Exception:
        log.exception("Discord capture worker failed")
        return JSONResponse({"error": "internal error"}, status_code=500)


@router.api_route("/cron/discovery", methods=["GET", "POST"])
async def discovery_cron(request: Request):
    if not cron_authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        from app.modules.integrations.discord.discovery import deliver_pending_digests, run_discovery

        result = run_discovery(db.connect)
        result.update(deliver_pending_digests(db.connect, Sender()))
        return JSONResponse(result, status_code=200)
    except Exception:
        log.exception("Discord discovery run failed")
        return JSONResponse({"error": "internal error"}, status_code=500)
