"""Compatibility facade for app.modules.identity."""
from app.modules.identity import (
    SESSION_COOKIE,
    OAUTH_COOKIE,
    SESSION_DAYS,
    clear_session_cookie,
    require_user,
    require_user_dependency,
    router,
    set_session_cookie,
)
from app.modules.identity.repository import (
    create_session,
    get_user_profile as me_payload,
)
from app.modules.identity.service import (
    validate_hhmm as _valid_hhmm,
    validate_timezone as _valid_timezone,
)
from app.core.config import load_config as cfg

__all__ = [
    "SESSION_COOKIE",
    "OAUTH_COOKIE",
    "SESSION_DAYS",
    "cfg",
    "clear_session_cookie",
    "create_session",
    "me_payload",
    "require_user",
    "require_user_dependency",
    "router",
    "set_session_cookie",
    "_valid_hhmm",
    "_valid_timezone",
]
