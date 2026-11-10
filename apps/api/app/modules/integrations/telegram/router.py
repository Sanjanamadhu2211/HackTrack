import os
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app import db, reminders, tokens
from app.auth import require_user_dependency
from app.config import load_config
from app.modules.notifications.actions import ack_delivery
from app.errors import NotFound

router = APIRouter()

LINK_EXPIRED_TEXT = "That link has expired or was already used. Generate a new one in HackTrack settings."
LINKED_TEXT = "Linked. HackTrack reminders will arrive here."
UNLINKED_START_TEXT = "Open HackTrack settings and tap Connect Telegram."


class TelegramError(Exception):
    pass


class Bot:
    def __init__(self, token, base=None):
        self._token = token
        self._base = (base or os.getenv("TELEGRAM_API_BASE", "https://api.telegram.org")).rstrip("/")
        self._client = httpx.Client(timeout=40.0)

    def call(self, method, payload=None):
        try:
            response = self._client.post(f"{self._base}/bot{self._token}/{method}", json=payload or {})
        except Exception as error:
            raise TelegramError(f"transport error calling {method}") from error
        try:
            data = response.json()
        except Exception as error:
            raise TelegramError(f"bad response from {method}") from error
        if not data.get("ok"):
            raise TelegramError(data.get("description") or f"telegram {method} failed")
        return data.get("result")


class Sender:
    def __init__(self, token=None, bot=None, base=None):
        self._bot = bot or Bot(token or load_config().telegram_bot_token, base)

    def send_reminder(self, chat_id, text, delivery_id, open_url):
        done = [{"text": "Done", "callback_data": f"done:{delivery_id}"}]
        if open_url and open_url.startswith("https://"):
            done.append({"text": "Open", "url": open_url})
        keyboard = {
            "inline_keyboard": [
                done,
                [
                    {"text": "Snooze 1h", "callback_data": f"snz:1h:{delivery_id}"},
                    {"text": "Snooze 3h", "callback_data": f"snz:3h:{delivery_id}"},
                    {"text": "Tomorrow 9:00", "callback_data": f"snz:tomorrow:{delivery_id}"},
                ],
            ]
        }
        result = self._bot.call("sendMessage", {"chat_id": chat_id, "text": text, "reply_markup": keyboard})
        return str(result["message_id"])

    def send_text(self, chat_id, text):
        result = self._bot.call("sendMessage", {"chat_id": chat_id, "text": text})
        return str(result["message_id"])

    def answer_callback(self, callback_id, text):
        self._bot.call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})

    def edit_keyboard(self, chat_id, message_id):
        try:
            self._bot.call(
                "editMessageReplyMarkup",
                {"chat_id": chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard": []}},
            )
        except Exception:
            pass

def link_chat(connect_factory, chat_id, raw, now):
    with connect_factory() as conn:
        with conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE telegram_links SET chat_id = NULL, updated_at = now() WHERE chat_id = %s", (chat_id,))
                cur.execute(
                    "UPDATE telegram_links SET chat_id = %s, linked_at = %s, pairing_token_hash = NULL, "
                    "pairing_expires_at = NULL, updated_at = now() "
                    "WHERE pairing_token_hash = %s AND pairing_expires_at > %s RETURNING user_id",
                    (chat_id, now, tokens.hash_token(raw), now),
                )
                row = cur.fetchone()
    return LINKED_TEXT if row else LINK_EXPIRED_TEXT


def _start_payload(text):
    rest = text[len("/start"):]
    if rest.startswith("@"):
        parts = rest.split(None, 1)
        return parts[1].strip() if len(parts) == 2 else ""
    return rest.strip()


def handle_update(sender, update, connect_factory=None):
    connect_factory = connect_factory or db.connect
    now = datetime.now(timezone.utc)
    callback = update.get("callback_query")
    if callback:
        _handle_callback(sender, callback, connect_factory, now)
        return
    message = update.get("message")
    if not message:
        return
    text = message.get("text") or ""
    if not text.startswith("/start"):
        return
    chat = message.get("chat") or {}
    if chat.get("type") != "private":
        return
    chat_id = chat.get("id")
    payload = _start_payload(text)
    if not payload:
        sender.send_text(chat_id, UNLINKED_START_TEXT)
        return
    reply = link_chat(connect_factory, chat_id, payload, now)
    sender.send_text(chat_id, reply)


def _handle_callback(sender, callback, connect_factory, now):
    data = callback.get("data") or ""
    action = None
    choice = None
    delivery_id = None
    if data.startswith("done:"):
        action = "DONE"
        delivery_id = data[len("done:"):]
    elif data.startswith("snz:"):
        parts = data.split(":")
        if len(parts) == 3 and parts[1] in ("1h", "3h", "tomorrow"):
            action = "SNOOZE"
            choice = parts[1]
            delivery_id = parts[2]
    if action is None or not delivery_id:
        sender.answer_callback(callback["id"], "Unknown action")
        return
    presser = (callback.get("from") or {}).get("id")
    with connect_factory() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, timezone FROM telegram_links t JOIN users u ON u.id = t.user_id WHERE t.chat_id = %s", (presser,))
            row = cur.fetchone()
    if row is None:
        sender.answer_callback(callback["id"], "This chat is not linked to HackTrack")
        return
    user_id = str(row["user_id"])
    try:
        ack = ack_delivery(connect_factory, user_id, delivery_id, action, choice, now)
    except NotFound:
        sender.answer_callback(callback["id"], "Reminder not found")
        _edit_keyboard(sender, callback)
        return
    except Exception:
        sender.answer_callback(callback["id"], "Something went wrong, try again")
        raise
    if ack.kind == "repeated":
        text = "Already handled"
    elif ack.kind == "snoozed":
        try:
            from zoneinfo import ZoneInfo

            tz = ZoneInfo(row["timezone"])
        except Exception:
            tz = timezone.utc
        text = f"Snoozed until {ack.snoozed_until.astimezone(tz).strftime('%a %H:%M')}"
    elif ack.kind == "snooze_no_time":
        text = "This reminder can no longer be snoozed"
    else:
        text = "Marked done"
    sender.answer_callback(callback["id"], text)
    _edit_keyboard(sender, callback)


def _edit_keyboard(sender, callback):
    message = callback.get("message")
    if not message:
        return
    chat = message.get("chat") or {}
    sender.edit_keyboard(chat.get("id"), message.get("message_id"))


@router.post("/integrations/telegram/pair")
def telegram_pair(user_id: str = Depends(require_user_dependency)):
    if not isinstance(user_id, str):
        return user_id
    cfg = load_config()
    if not cfg.telegram_bot_token or not cfg.telegram_bot_username:
        return JSONResponse({"error": "telegram not configured"}, status_code=503)
    raw, hashed = tokens.new_token()
    expires = datetime.now(timezone.utc) + timedelta(minutes=15)
    with db.connect() as conn:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO telegram_links (user_id, pairing_token_hash, pairing_expires_at) VALUES (%s, %s, %s) "
                    "ON CONFLICT (user_id) DO UPDATE SET pairing_token_hash = EXCLUDED.pairing_token_hash, "
                    "pairing_expires_at = EXCLUDED.pairing_expires_at, updated_at = now()",
                    (user_id, hashed, expires),
                )
    return {"url": f"https://t.me/{cfg.telegram_bot_username}?start={raw}"}


@router.delete("/integrations/telegram")
def telegram_delete(user_id: str = Depends(require_user_dependency)):
    if not isinstance(user_id, str):
        return user_id
    with db.connect() as conn:
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM telegram_links WHERE user_id = %s", (user_id,))
    return PlainTextResponse("", status_code=204)


@router.post("/integrations/telegram/webhook")
async def telegram_webhook(request: Request):
    cfg = load_config()
    secret = cfg.telegram_webhook_secret
    if not secret:
        return PlainTextResponse("", status_code=401)
    if not tokens.constant_time_equal(request.headers.get("x-telegram-bot-api-secret-token", ""), secret):
        return PlainTextResponse("", status_code=401)
    try:
        body = await request.json()
    except Exception:
        return PlainTextResponse("", status_code=400)
    try:
        handle_update(Sender(cfg.telegram_bot_token), body or {}, db.connect)
    except Exception:
        return PlainTextResponse("", status_code=500)
    return PlainTextResponse("", status_code=200)
