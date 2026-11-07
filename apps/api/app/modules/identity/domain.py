from dataclasses import dataclass
from datetime import datetime
from typing import Optional

SESSION_COOKIE = "ht_session"
OAUTH_COOKIE = "ht_oauth"
SESSION_DAYS = 30


@dataclass
class User:
    id: str
    email: str
    name: str = ""
    timezone: str = "UTC"
    quiet_hours_start: str = "23:00"
    quiet_hours_end: str = "08:00"
    reminder_cap_per_day: int = 6
    telegram_linked: bool = False
    google_sub: Optional[str] = None


@dataclass
class Session:
    id: str
    user_id: str
    token_hash: bytes
    expires_at: datetime
    revoked_at: Optional[datetime] = None
