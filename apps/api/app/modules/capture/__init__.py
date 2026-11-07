from app.modules.capture.router import router
from app.modules.capture.normalize import normalize_url
from app.modules.capture.platforms import detect_platform
from app.modules.capture.fetching import fetch, resolve_redirect
from app.modules.capture.ai import extract_with_ai

__all__ = [
    "router",
    "normalize_url",
    "detect_platform",
    "fetch",
    "resolve_redirect",
    "extract_with_ai",
]
