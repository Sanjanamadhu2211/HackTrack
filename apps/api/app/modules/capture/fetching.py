"""Compatibility exports for the shared capture fetcher."""

from app.fetching import fetch, is_public_ip, resolve_redirect

__all__ = ["fetch", "is_public_ip", "resolve_redirect"]
