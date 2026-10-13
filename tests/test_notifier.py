import logging

import pytest
import requests

from pwca_monitor.models import Notification
from pwca_monitor.notifier import NotifyError, TelegramNotifier, _truncate

SECRET = "123456:SECRET-TOKEN-xyz"


class Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def post(self, url, json, timeout):
        self.calls += 1
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def make(responses):
    s = FakeSession(responses)
    return TelegramNotifier(SECRET, "42", max_retries=2, session=s, sleep=lambda _: None), s


N = Notification("test", "テスト", "hello")


def test_success():
    t, s = make([Resp(200, {"ok": True})])
    t.send(N)
    assert s.calls == 1


def test_invalid_token_no_retry_and_no_leak(caplog):
    t, s = make([Resp(401, {"ok": False, "description": "Unauthorized"})])
    with caplog.at_level(logging.DEBUG), pytest.raises(NotifyError) as e:
        t.send(N)
    assert s.calls == 1 and "Unauthorized" in str(e.value)
    assert SECRET not in str(e.value) and SECRET not in caplog.text
    assert SECRET not in repr(t)


def test_bad_chat_id():
    t, _ = make([Resp(400, {"ok": False, "description": "Bad Request: chat not found"})])
    with pytest.raises(NotifyError, match="chat not found"):
        t.send(N)


def test_network_error_retries_then_fails_without_leak(caplog):
    err = requests.exceptions.ConnectionError(f"https://api.telegram.org/bot{SECRET}/sendMessage failed")
    t, s = make([err, err, err])
    with caplog.at_level(logging.DEBUG), pytest.raises(NotifyError) as e:
        t.send(N)
    assert s.calls == 3
    assert SECRET not in str(e.value) and SECRET not in caplog.text


def test_429_then_success():
    t, s = make([Resp(429, {"parameters": {"retry_after": 1}}), Resp(200, {"ok": True})])
    t.send(N)
    assert s.calls == 2


def test_missing_secrets():
    with pytest.raises(NotifyError):
        TelegramNotifier("", "")


def test_truncate():
    out = _truncate("x" * 5000, 3800)
    assert len(out) <= 3800 and "省略" in out
