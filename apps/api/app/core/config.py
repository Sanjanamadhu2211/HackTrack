import os
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass
class Config:
    database_url: str = ""
    app_base_url: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    telegram_bot_token: str = ""
    telegram_bot_username: str = ""
    telegram_webhook_secret: str = ""
    discord_application_id: str = ""
    discord_public_key: str = ""
    discord_bot_token: str = ""
    cron_secret: str = ""
    dev_login: bool = False

    @property
    def secure(self):
        return self.app_base_url.startswith("https://")


def load_config():
    c = Config(
        database_url=os.getenv("DATABASE_URL", ""),
        app_base_url=os.getenv("APP_BASE_URL", "").rstrip("/"),
        google_client_id=os.getenv("GOOGLE_CLIENT_ID", ""),
        google_client_secret=os.getenv("GOOGLE_CLIENT_SECRET", ""),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_bot_username=os.getenv("TELEGRAM_BOT_USERNAME", ""),
        telegram_webhook_secret=os.getenv("TELEGRAM_WEBHOOK_SECRET", ""),
        discord_application_id=os.getenv("DISCORD_APPLICATION_ID", ""),
        discord_public_key=os.getenv("DISCORD_PUBLIC_KEY", ""),
        discord_bot_token=os.getenv("DISCORD_BOT_TOKEN", ""),
        cron_secret=os.getenv("CRON_SECRET", ""),
        dev_login=os.getenv("DEV_LOGIN") == "1",
    )
    missing = []

    def need(name, value):
        if not value:
            missing.append(name)

    need("DATABASE_URL", c.database_url)
    need("APP_BASE_URL", c.app_base_url)
    if c.secure:
        need("GOOGLE_CLIENT_ID", c.google_client_id)
        need("GOOGLE_CLIENT_SECRET", c.google_client_secret)
        discord = (c.discord_application_id, c.discord_public_key, c.discord_bot_token)
        if any(discord):
            need("DISCORD_APPLICATION_ID", c.discord_application_id)
            need("DISCORD_PUBLIC_KEY", c.discord_public_key)
            need("DISCORD_BOT_TOKEN", c.discord_bot_token)
        if c.telegram_bot_token:
            need("TELEGRAM_BOT_USERNAME", c.telegram_bot_username)
            need("TELEGRAM_WEBHOOK_SECRET", c.telegram_webhook_secret)
    if missing:
        raise RuntimeError(f"missing required env: {', '.join(missing)}")
    u = urlparse(c.app_base_url)
    if not u.netloc:
        raise RuntimeError("APP_BASE_URL must be an absolute URL")
    if c.dev_login and c.secure:
        raise RuntimeError("DEV_LOGIN must not be enabled on an https deployment")
    return c
