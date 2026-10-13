import json

import pytest

from pwca_monitor.models import Link, PageSnapshot
from pwca_monitor.state import State, StateError, load_state, save_state


def make_snap():
    return PageSnapshot("https://x", "https://x", "T", ["H"], ["a", "b"], [Link("L", "https://x/l")],
                        [], "sha256:1", "2026-10-13T00:00:00Z")


def test_roundtrip(tmp_path):
    p = tmp_path / "s" / "state.json"
    s = State()
    s.set_snapshot("p", make_snap())
    s.mark_notified("p", "h1")
    save_state(s, p)
    s2 = load_state(p)
    assert s2.get_snapshot("p") == make_snap()
    assert s2.was_notified("p", "h1")
    assert json.loads(p.read_text())["schema_version"] == 1


def test_missing_file_is_empty_state(tmp_path):
    assert load_state(tmp_path / "none.json").get_snapshot("p") is None


@pytest.mark.parametrize("content", ["{bad", "[]", '{"schema_version": 99}'])
def test_invalid_state(tmp_path, content):
    p = tmp_path / "state.json"
    p.write_text(content)
    with pytest.raises(StateError):
        load_state(p)


def test_heartbeat_once_per_day():
    s = State()
    s.heartbeat("2026-10-13T00:00:00Z")
    s.dirty = False
    s.heartbeat("2026-10-13T05:00:00Z")
    assert not s.dirty
    s.heartbeat("2026-10-14T00:00:00Z")
    assert s.dirty
