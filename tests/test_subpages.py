from conftest import FIXTURES, ROOT

from pwca_monitor.config import load_config
from pwca_monitor.parser import parse_page
from pwca_monitor.subpages import discover_subpages

CFG = load_config(ROOT / "config" / "targets.yaml")
EXTS = tuple(CFG.parser["attachment_extensions"])


def test_real_page_results_excluded():
    """Real HTML: scoring.pwca.org (results) and the #pilot-information anchor are not subpages."""
    t = CFG.targets[0]
    html = (FIXTURES / "pwca_event_real.html").read_text(encoding="utf-8")
    snap = parse_page(html, t.url, t.url, "", CFG.parser)
    assert any("scoring.pwca.org" in l.url for l in snap.links)
    assert discover_subpages(t, snap, CFG.subpages, EXTS) == []


def test_results_included_when_exclusion_removed():
    t = CFG.targets[0]
    html = (FIXTURES / "pwca_event_real.html").read_text(encoding="utf-8")
    snap = parse_page(html, t.url, t.url, "", CFG.parser)
    cfg = {**CFG.subpages, "exclude_url_patterns": []}
    urls = [s.url for s in discover_subpages(t, snap, cfg, EXTS)]
    assert len(urls) == 3 and all("scoring.pwca.org" in u for u in urls)


def test_events_are_separate():
    """Each event only picks up pages containing its own slug."""
    at, wc = CFG.targets[:2]
    links = (f'<a href="{at.url}/hotel">AT hotel</a>'
             f'<a href="{wc.url}/hotel">WC hotel</a>')
    html = f"<html><body><div data-page-events-show>{links}</div></body></html>"
    for t, other in ((at, wc), (wc, at)):
        snap = parse_page(html, t.url, t.url, "", CFG.parser)
        subs = discover_subpages(t, snap, CFG.subpages, EXTS)
        assert [s.url for s in subs] == [t.url + "/hotel"]
        assert subs[0].parent_id == t.id and subs[0].id.startswith(t.id + "~")


def test_disabled():
    t = CFG.targets[0]
    snap = parse_page(f'<div data-page-events-show><a href="{t.url}/x">x</a></div>', t.url, t.url, "", CFG.parser)
    assert discover_subpages(t, snap, {**CFG.subpages, "enabled": False}, EXTS) == []


def test_news_target_config():
    news = next(t for t in CFG.targets if t.id == "pwca_news")
    assert news.url == "https://pwca.org/news" and news.scope == "pwca.org/news/"
    assert news.notify_page_changes is False and news.subpage_fetch_once is True
    assert "Panchgani" in news.relevance_words
