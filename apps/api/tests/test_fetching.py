from types import SimpleNamespace

import pytest

from app import fetching


def test_https_fetch_pins_public_ip_but_uses_original_host_for_http_and_tls(monkeypatch):
    calls = []

    class Client:
        def __init__(self, **kwargs):
            calls.append(("client", kwargs))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return SimpleNamespace(status_code=200, headers={"content-type": "text/html"}, content=b"event")

    monkeypatch.setattr(fetching, "_robots_allowed", lambda _url: True)
    monkeypatch.setattr(fetching, "_resolve", lambda _host: "93.184.216.34")
    monkeypatch.setattr(fetching.httpx, "Client", Client)

    assert fetching.fetch("https://example.com/event") == ("https://example.com/event", "text/html", b"event")
    assert calls[0][1]["trust_env"] is False
    assert calls[1] == (
        "https://93.184.216.34/event",
        {"headers": {"Host": "example.com"}, "extensions": {"sni_hostname": "example.com"}},
    )


def test_json_responses_are_only_accepted_when_requested(monkeypatch):
    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, *_args, **_kwargs):
            return SimpleNamespace(status_code=200, headers={"content-type": "application/json"}, content=b'{}')

    monkeypatch.setattr(fetching, "_robots_allowed", lambda _url: True)
    monkeypatch.setattr(fetching, "_resolve", lambda _host: "93.184.216.34")
    monkeypatch.setattr(fetching.httpx, "Client", Client)

    with pytest.raises(ValueError, match="unsupported content type"):
        fetching.fetch("https://example.com/api")
    assert fetching.fetch("https://example.com/api", accept_json=True, timeout=20)[1:] == ("application/json", b'{}')
