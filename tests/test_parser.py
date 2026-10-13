from conftest import FIXTURES, ROOT

from pwca_monitor.config import build_target_url, load_config
from pwca_monitor.parser import ParseError, parse_page

import pytest

CFG = load_config(ROOT / "config" / "targets.yaml")
URL = "https://pwca.org/events/x"


def parse(name):
    html = (FIXTURES / name).read_text(encoding="utf-8")
    return parse_page(html, URL, URL, "2026-10-13T00:00:00Z", CFG.parser)


def test_fixture_excludes_noise():
    s = parse("page_before.html")
    text = s.normalized_text
    assert "Registration deadline: 07.11.2026" in text
    assert "To be published" in text
    for noise in ("months", "Racing Under Blue Skies", "1 day ago", "All rights reserved", "Events", "var x"):
        assert noise not in text, noise


def test_tracking_params_removed():
    s = parse("page_before.html")
    assert any(l.url == "https://pwca.org/account/competitions" for l in s.links)


def test_before_after_noise_only_parts_equal_except_accommodation():
    b, a = parse("page_before.html"), parse("page_after.html")
    assert b.content_hash != a.content_hash
    assert [l for l in a.lines if l not in b.lines] == [
        "Accommodation",
        "Official hotel booking opens on 01.12.2026. Rooms are limited, first come first served.",
        "Hotel booking form Local Regulations",
    ]
    assert [x.url for x in a.attachments] == ["https://pwca.org/storage/9999/Panchgani-Local-Regulations.pdf"]


def test_real_page_snapshot():
    """Real HTML saved on 2026-10-13. Detects regressions in extraction rules."""
    s = parse("pwca_event_real.html")
    assert "PWCA Asian Tour India Panchgani 2027 - Season 2026" in s.headings
    assert "Registration deadline: 07.11.2026" in s.lines
    assert "Pilot Information" in s.lines and "HQ Location" in s.lines
    text = s.normalized_text
    for noise in ("months", "Read more", "Stay Up", "day ago", "PARTNERS", "Allow cookies", "Selection List\nAll"):
        assert noise not in text, noise
    assert len(text) > 80


def test_missing_root_is_parse_error():
    with pytest.raises(ParseError):
        parse_page("<html><body>Login</body></html>", URL, URL, "", CFG.parser)


def test_url_building_from_variables():
    assert build_target_url("https://pwca.org", "/events/", "abc") == "https://pwca.org/events/abc"
    assert build_target_url("https://pwca.org/", "events", "/abc") == "https://pwca.org/events/abc"
    assert build_target_url("https://new.example", "/competitions/2027/", "abc") == "https://new.example/competitions/2027/abc"
    assert CFG.targets[0].url == "https://pwca.org/events/2026-pwca-asian-tour-india-panchgani-2027-season-2026"


def test_url_env_override(monkeypatch):
    monkeypatch.setenv("PWCA_BASE_URL", "https://mirror.example")
    monkeypatch.setenv("PWCA_EVENTS_PATH", "/ev/")
    cfg = load_config(ROOT / "config" / "targets.yaml")
    assert cfg.targets[1].url == "https://mirror.example/ev/2026-paragliding-world-cup-india-panchgani-2027-season-2026"
