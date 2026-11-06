from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app import db, tokens
from app.auth import require_user_dependency
from app.config import load_config
from app.errors import Invalid

router = APIRouter()


@router.post("/integrations/telegram/pair")
def pair(user_id: str = Depends(require_user_dependency)):
    if not isinstance(user_id, str):
        return user_id
    cfg = load_config()
    if not cfg.telegram_bot_token or not cfg.telegram_bot_username:
        return JSONResponse({"error": "telegram not configured"}, status_code=503)
    raw, token_hash = tokens.new_token()
    from datetime import datetime, timedelta, timezone

    expires = datetime.now(timezone.utc) + timedelta(minutes=15)
    with db.connect() as conn:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO telegram_links (user_id, pairing_token_hash, pairing_expires_at) VALUES (%s, %s, %s) "
                    "ON CONFLICT (user_id) DO UPDATE SET pairing_token_hash=EXCLUDED.pairing_token_hash, "
                    "pairing_expires_at=EXCLUDED.pairing_expires_at, updated_at=now()",
                    (user_id, token_hash, expires),
                )
    return {"url": f"https://t.me/{cfg.telegram_bot_username}?start={raw}"}


@router.delete("/integrations/telegram")
def unlink(user_id: str = Depends(require_user_dependency)):
    if not isinstance(user_id, str):
        return user_id
    with db.connect() as conn:
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM telegram_links WHERE user_id=%s", (user_id,))
    return PlainTextResponse("", status_code=204)


@router.post("/integrations/telegram/webhook")
async def webhook(request: Request):
    cfg = load_config()
    secret = cfg.telegram_webhook_secret or ""
    if not secret:
        return PlainTextResponse("", status_code=401)
    header = request.headers.get("x-telegram-bot-api-secret-token", "")
    if not tokens.constant_time_equal(header, secret):
        return PlainTextResponse("", status_code=401)
    try:
        update = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    try:
        handle_update(update)
    except Exception:
        return PlainTextResponse("", status_code=500)
    return JSONResponse({})


def send_text(chat_id, text):
    from app import sender

    cfg = load_config()
    sender._post(cfg.telegram_bot_token, "sendMessage", {"chat_id": chat_id, "text": text})


def answer_callback(callback_id, text):
    from app import sender

    cfg = load_config()
    try:
        sender._post(cfg.telegram_bot_token, "answerCallbackQuery", {"callback_query_id": callback_id, "text": text})
    except Exception:
        pass


def handle_update(update):
    message = update.get("message") or {}
    callback = update.get("callback_query") or {}
    chat = message.get("chat", {})
    if message.get("text", "").startswith("/start") and chat.get("type") == "private":
        token = message["text"][len("/start"):].strip()
        chat_id = chat["id"]
        if not token:
            send_text(chat_id, "Open HackTrack settings and tap Connect Telegram.")
            return
        token_hash = tokens.hash_token(token)
        with db.connect() as conn:
            with conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE telegram_links SET chat_id=NULL, updated_at=now() WHERE chat_id=%s", (chat_id,))
                    cur.execute(
                        "UPDATE telegram_links SET chat_id=%s, linked_at=now(), pairing_token_hash=NULL, pairing_expires_at=NULL, updated_at=now() "
                        "WHERE pairing_token_hash=%s AND pairing_expires_at>now() RETURNING user_id",
                        (chat_id, token_hash),
                    )
                    row = cur.fetchone()
        send_text(
            chat_id,
            "Linked. HackTrack reminders will arrive here."
            if row
            else "That link has expired or was already used. Generate a new one in HackTrack settings.",
        )
        return
    data = callback.get("data", "")
    if callback:
        from_id = (callback.get("from") or {}).get("id")
        callback_id = callback.get("id")
        if data.startswith("done:"):
            action, choice, delivery_id = "DONE", None, data[len("done:"):]
        elif data.startswith("snz:"):
            parts = data.split(":", 2)
            if len(parts) != 3 or parts[1] not in ("1h", "3h", "tomorrow"):
                answer_callback(callback_id, "Unknown action")
                return
            action, choice, delivery_id = "SNOOZE", parts[1], parts[2]
        else:
            answer_callback(callback_id, "Unknown action")
            return
        user_id = None
        with db.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id FROM telegram_links WHERE chat_id=%s", (from_id,))
                row = cur.fetchone()
                if row:
                    user_id = str(row["user_id"])
        if not user_id:
            answer_callback(callback_id, "This chat is not linked to HackTrack")
            return
        try:
            result = ack_delivery(user_id, delivery_id, action, choice)
        except Invalid:
            answer_callback(callback_id, "Something went wrong, try again")
            raise
        except Exception:
            answer_callback(callback_id, "Something went wrong, try again")
            raise
        if result.get("not_found"):
            answer_callback(callback_id, "Reminder not found")
            return
        message = callback.get("message") or {}
        if message.get("chat"):
            try:
                from app import sender

                sender._post(load_config().telegram_bot_token, "editMessageReplyMarkup", {"chat_id": message["chat"]["id"], "message_id": message.get("message_id"), "reply_markup": {"inline_keyboard": []}})
            except Exception:
                pass
        if result.get("repeated"):
            answer_callback(callback_id, "Already handled")
        elif result.get("snoozed_until"):
            answer_callback(callback_id, f"Snoozed until {result['snoozed_until']}")
        elif action == "SNOOZE":
            answer_callback(callback_id, "This reminder can no longer be snoozed")
        else:
            answer_callback(callback_id, "Marked done")
        return


def ack_delivery(user_id, delivery_id, action, choice=None):
    from datetime import datetime, timezone

    from app import reminders
    from app.rules import record_transition

    now = datetime.now(timezone.utc)
    with db.connect() as conn:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT d.id, d.acknowledged_at, d.action, d.job_id, j.milestone_id, m.status, m.version, m.type "
                    "FROM notification_deliveries d JOIN notification_jobs j ON j.id=d.job_id JOIN milestones m ON m.id=j.milestone_id "
                    "WHERE d.id=%s AND d.user_id=%s FOR UPDATE OF d",
                    (delivery_id, user_id),
                )
                row = cur.fetchone()
                if not row:
                    return {"not_found": True}
                if row["acknowledged_at"] is not None:
                    return {"action": row["action"], "repeated": True}
                cur.execute(
                    "UPDATE notification_deliveries SET acknowledged_at=now(), action=%s, updated_at=now() WHERE id=%s",
                    (action, delivery_id),
                )
                if row["status"] == "MISSED" and action == "DONE":
                    cur.execute("UPDATE milestones SET status='DONE', completed_at=now(), version=version+1, updated_at=now() WHERE id=%s", (row["milestone_id"],))
                    record_transition(conn, user_id, "milestone", str(row["milestone_id"]), "status", "MISSED", "DONE", "USER")
                    reminders.regenerate(conn, str(row["milestone_id"]), now)
                    return {"action": action}
                if row["status"] != "TODO":
                    return {"action": action}
                if action == "SNOOZE":
                    at = reminders.snooze(conn, user_id, str(row["milestone_id"]), str(row["id"]), choice, now)
                    try:
                        from zoneinfo import ZoneInfo

                        prefs = reminders.load_prefs(conn, user_id)
                        when = at.astimezone(prefs.location).strftime("%a %H:%M")
                    except Exception:
                        when = at.strftime("%a %H:%M")
                    return {"action": action, "snoozed_until": when}
                cur.execute(
                    "UPDATE milestones SET status='DONE', completed_at=now(), version=version+1, updated_at=now() WHERE id=%s",
                    (row["milestone_id"],),
                )
                record_transition(conn, user_id, "milestone", str(row["milestone_id"]), "status", "TODO", "DONE", "USER")
                reminders.regenerate(conn, str(row["milestone_id"]), now)
                return {"action": action}
