import httpx


class DiscordError(Exception):
    pass


class Bot:
    def __init__(self, token, base="https://discord.com/api/v10"):
        self._token = token
        self._base = base.rstrip("/")
        self._client = httpx.Client(timeout=10.0)

    def call(self, method, path, payload):
        try:
            response = self._client.request(
                method,
                f"{self._base}{path}",
                headers={"Authorization": f"Bot {self._token}"},
                json=payload,
            )
        except Exception as error:
            raise DiscordError("Discord request failed") from error
        if not response.is_success:
            raise DiscordError(f"Discord returned HTTP {response.status_code}")
        try:
            return response.json()
        except Exception as error:
            raise DiscordError("Discord returned an invalid response") from error


class Sender:
    def __init__(self, token=None, bot=None):
        if bot is None:
            from app.core.config import load_config

            token = token or load_config().discord_bot_token
        self._bot = bot or Bot(token)

    def send_message(self, channel_id, content, components=None, nonce=None):
        payload = {"content": content[:2000], "allowed_mentions": {"parse": []}}
        if components:
            payload["components"] = components
        if nonce is not None:
            payload.update({"nonce": nonce, "enforce_nonce": True})
        result = self._bot.call("POST", f"/channels/{channel_id}/messages", payload)
        return str(result["id"])

    def send_reminder(self, channel_id, text, delivery_id, open_url):
        buttons = [
            {"type": 2, "style": 3, "label": "Done", "custom_id": f"notify:done:{delivery_id}"},
            {"type": 2, "style": 2, "label": "Snooze 1h", "custom_id": f"notify:1h:{delivery_id}"},
            {"type": 2, "style": 2, "label": "Snooze 3h", "custom_id": f"notify:3h:{delivery_id}"},
            {"type": 2, "style": 2, "label": "Tomorrow", "custom_id": f"notify:tomorrow:{delivery_id}"},
        ]
        if open_url.startswith(("https://", "http://")):
            buttons.append({"type": 2, "style": 5, "label": "Open", "url": open_url})
        return self.send_message(channel_id, text, [{"type": 1, "components": buttons}])
