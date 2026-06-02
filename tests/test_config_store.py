"""Tests for config_store.load_config transient-retry behaviour."""
import pytest

import config_store


class _FakeQuery:
    def __init__(self, behavior):
        self._behavior = behavior

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def maybe_single(self):
        return self

    def execute(self):
        return self._behavior()


class _FakeClient:
    def __init__(self, behavior):
        self._behavior = behavior

    def table(self, *a, **k):
        return _FakeQuery(self._behavior)


class _Resp:
    def __init__(self, config):
        self.data = {"config": config}


def test_load_config_retries_transient_then_succeeds(monkeypatch):
    monkeypatch.setattr(config_store.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    class RemoteProtocolError(Exception):
        pass

    def behavior():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RemoteProtocolError("Server disconnected without sending a response.")
        return _Resp({"ticker": "X", "company": "Acme"})

    cfg = config_store.load_config(_FakeClient(behavior), "x")
    assert cfg["company"] == "Acme"
    assert calls["n"] == 2  # one retry


def test_load_config_pgrst116_returns_none_without_retry():
    calls = {"n": 0}

    def behavior():
        calls["n"] += 1
        raise Exception("PGRST116: 0 rows returned")

    assert config_store.load_config(_FakeClient(behavior), "x") is None
    assert calls["n"] == 1  # no retry for a legitimate 0-rows


def test_load_config_persistent_transient_raises_after_retries(monkeypatch):
    monkeypatch.setattr(config_store.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def behavior():
        calls["n"] += 1
        raise Exception("RemoteProtocolError: Server disconnected")

    with pytest.raises(Exception, match="RemoteProtocolError"):
        config_store.load_config(_FakeClient(behavior), "x")
    assert calls["n"] == 3  # 3 attempts total


def test_load_config_non_transient_raises_immediately():
    calls = {"n": 0}

    def behavior():
        calls["n"] += 1
        raise ValueError("schema mismatch")

    with pytest.raises(ValueError):
        config_store.load_config(_FakeClient(behavior), "x")
    assert calls["n"] == 1  # no retry for a non-transient error
