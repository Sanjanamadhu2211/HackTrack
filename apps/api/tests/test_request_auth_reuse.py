import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.main import api_app
from app.modules.identity import repository, service

SESSION_COOKIE = "ht_session"


@pytest.fixture
def client():
    return TestClient(api_app)


def patch_session_lookup(monkeypatch, result):
    calls = []

    def fake(token_raw):
        calls.append(token_raw)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(repository, "find_user_id_by_session", fake)
    return calls


def patch_profile(monkeypatch):
    monkeypatch.setattr(repository, "get_user_profile", lambda user_id: {"id": user_id})


def make_request(cookie: str) -> Request:
    return Request(
        {
            "type": "http",
            "headers": [(b"cookie", f"{SESSION_COOKIE}={cookie}".encode())],
        }
    )


def test_authenticated_request_looks_up_session_once(client, monkeypatch):
    calls = patch_session_lookup(monkeypatch, "user-1")
    patch_profile(monkeypatch)

    res = client.get("/api/me", headers={"Cookie": f"{SESSION_COOKIE}=token"})

    assert res.status_code == 200
    assert res.json() == {"id": "user-1"}
    assert calls == ["token"]


def test_session_lookup_is_not_cached_across_requests(client, monkeypatch):
    calls = []

    def fake(token_raw):
        calls.append(token_raw)
        return f"user-{token_raw}"

    monkeypatch.setattr(repository, "find_user_id_by_session", fake)
    patch_profile(monkeypatch)

    first = client.get("/api/me", headers={"Cookie": f"{SESSION_COOKIE}=a"})
    second = client.get("/api/me", headers={"Cookie": f"{SESSION_COOKIE}=b"})

    assert [first.json(), second.json()] == [{"id": "user-a"}, {"id": "user-b"}]
    assert calls == ["a", "b"]


def test_require_user_reuses_validated_user_within_one_request(monkeypatch):
    calls = patch_session_lookup(monkeypatch, "user-1")
    request = make_request("token")

    assert service.require_user(request) == "user-1"
    assert service.require_user(request) == "user-1"
    assert calls == ["token"]


def test_require_user_state_is_request_scoped(monkeypatch):
    calls = patch_session_lookup(monkeypatch, "user-1")

    assert service.require_user(make_request("a")) == "user-1"
    assert service.require_user(make_request("b")) == "user-1"
    assert calls == ["a", "b"]


def test_absent_session_is_rejected_without_lookup(client, monkeypatch):
    calls = patch_session_lookup(monkeypatch, "user-1")

    res = client.get("/api/me")

    assert res.status_code == 401
    assert calls == []


def test_revoked_or_unknown_session_is_rejected(client, monkeypatch):
    calls = patch_session_lookup(monkeypatch, None)

    res = client.get("/api/me", headers={"Cookie": f"{SESSION_COOKIE}=dead"})

    assert res.status_code == 401
    assert calls == ["dead"]


def test_database_failure_is_not_reported_as_signed_out(client, monkeypatch):
    patch_session_lookup(monkeypatch, RuntimeError("db down"))

    res = client.get("/api/me", headers={"Cookie": f"{SESSION_COOKIE}=token"})

    assert res.status_code == 503
    assert res.json() == {"error": "database unavailable"}
    assert "set-cookie" not in res.headers


def test_client_supplied_user_header_is_not_trusted(client, monkeypatch):
    calls = patch_session_lookup(monkeypatch, "user-1")

    res = client.get("/api/me", headers={"X-User-Id": "attacker"})

    assert res.status_code == 401
    assert calls == []
