import os

import httpx

from app.config import load_config

BASE = os.getenv("TELEGRAM_API_BASE", "https://api.telegram.org")


def _post(token, method, payload):
    with httpx.Client(timeout=40.0) as client:
        response = client.post(f"{BASE}/bot{token}/{method}", json=payload)
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description", "telegram request failed"))
    return data.get("result")


def send_reminder(chat_id, text, delivery_id, open_url=""):
    cfg = load_config()
    if not cfg.telegram_bot_token:
        raise RuntimeError("telegram not configured")
    rows = [[{"text": "Done", "callback_data": f"done:{delivery_id}"}]]
    if open_url.startswith("https://"):
        rows[0].append({"text": "Open", "url": open_url})
    rows.append(
        [
            {"text": "Snooze 1h", "callback_data": f"snz:1h:{delivery_id}"},
            {"text": "Snooze 3h", "callback_data": f"snz:3h:{delivery_id}"},
            {"text": "Tomorrow 9:00", "callback_data": f"snz:tomorrow:{delivery_id}"},
        ]
    )
    result = _post(
        cfg.telegram_bot_token,
        "sendMessage",
        {"chat_id": chat_id, "text": text, "reply_markup": {"inline_keyboard": rows}},
    )
    return str(result.get("message_id", ""))
