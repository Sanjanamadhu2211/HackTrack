from contextlib import contextmanager

import pytest
from psycopg_pool import PoolTimeout

from app.core import database


class FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakePool:
    """Minimal stand-in for psycopg_pool.ConnectionPool."""

    def __init__(self, conn=None, fail=None, exit_error=None):
        self.conn = conn or FakeConnection()
        self.fail = fail
        self.exit_error = exit_error
        self.connection_calls = 0

    def connection(self):
        self.connection_calls += 1
        return self._connection_cm()

    @contextmanager
    def _connection_cm(self):
        if self.fail is not None:
            raise self.fail
        yield self.conn
        if self.exit_error is not None:
            raise self.exit_error


def patch_direct_connect(monkeypatch):
    calls = []

    def fake_connect(conninfo, **kwargs):
        calls.append((conninfo, kwargs))
        return FakeConnection()

    monkeypatch.setattr(database.psycopg, "connect", fake_connect)
    return calls


def test_caller_error_is_propagated_without_pool_fallback(monkeypatch):
    pool = FakePool()
    monkeypatch.setattr(database, "get_pool", lambda url=None: pool)
    direct_calls = patch_direct_connect(monkeypatch)

    error = RuntimeError("transactional work failed")
    with pytest.raises(RuntimeError) as raised:
        with database.connect():
            raise error

    assert raised.value is error
    assert direct_calls == []


def test_pool_exit_error_is_not_retried(monkeypatch):
    pool = FakePool(exit_error=RuntimeError("commit failed"))
    monkeypatch.setattr(database, "get_pool", lambda url=None: pool)
    direct_calls = patch_direct_connect(monkeypatch)

    with pytest.raises(RuntimeError, match="commit failed"):
        with database.connect():
            pass

    assert direct_calls == []


def test_pool_acquisition_failure_still_falls_back(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://example/db")
    pool = FakePool(fail=PoolTimeout("no connection available"))
    monkeypatch.setattr(database, "get_pool", lambda url=None: pool)
    direct_calls = patch_direct_connect(monkeypatch)

    with database.connect() as conn:
        assert isinstance(conn, FakeConnection)

    assert len(direct_calls) == 1
