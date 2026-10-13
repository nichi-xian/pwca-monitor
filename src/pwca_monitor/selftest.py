"""Built-in self-test (no network, no Telegram). Used by the self-test / test modes."""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path
from typing import Callable

from .config import Config
from .models import FetchResult, Notification, Target
from .monitor import run_once
from .notifier import ConsoleNotifier, NotifyError
from .state import State, StateError, load_state

URL = "https://pwca.example/events/selftest"

TEMPLATE = """<html><head><title>Self Test Event</title></head><body>
<nav><a href="/menu">Menu</a></nav>
<div data-page-events-show>
  <h3>Self Test Event 2027</h3>
  <div data-buttons><span>{countdown}</span><span>days</span></div>
  <div>Registration deadline: 07.11.2026</div>
  <div>Launch: Table Land</div>
  {body}
  <a href="https://pwca.example/account/competitions">Register</a>
  {links}
  <div data-news-latest>News {countdown}</div>
</div><footer>footer</footer></body></html>"""


def make_html(body: str = "", links: str = "", countdown: str = "23") -> str:
    return TEMPLATE.format(body=body, links=links, countdown=countdown)


class FakeFetcher:
    def __init__(self):
        self.response: FetchResult | None = None
        self.pages: dict[str, FetchResult] = {}   # per-URL responses (subpages)
        self.calls: list[str] = []
        self.main_url = URL
        self.clock = "2026-10-13T00:00:00Z"

    def set_page(self, url: str, html: str | None, status: int = 200) -> None:
        if html is None:
            self.pages[url] = FetchResult(url, False, status=status, final_url=url,
                                          fetched_at=self.clock, error_kind=f"http_{status}",
                                          error_message=f"HTTP {status}")
        else:
            self.pages[url] = FetchResult(url, True, status=200, final_url=url,
                                          content_type="text/html", html=html, fetched_at=self.clock)

    def set_html(self, html: str) -> None:
        self.response = FetchResult(URL, True, status=200, final_url=URL,
                                    content_type="text/html", html=html, fetched_at=self.clock)

    def set_error(self, kind: str, msg: str, status: int | None = None) -> None:
        self.response = FetchResult(URL, False, status=status, fetched_at=self.clock,
                                    error_kind=kind, error_message=msg)

    def fetch(self, url: str) -> FetchResult:
        self.calls.append(url)
        if url in self.pages:
            return self.pages[url]
        if url != self.main_url:
            return FetchResult(url, False, status=404, final_url=url, fetched_at=self.clock,
                               error_kind="http_404", error_message="HTTP 404")
        assert self.response is not None
        return self.response


class RecordingNotifier:
    name = "recording"

    def __init__(self, fail: bool = False):
        self.sent: list[Notification] = []
        self.fail = fail

    def send(self, n: Notification) -> None:
        if self.fail:
            raise NotifyError("simulated notification failure")
        self.sent.append(n)


def selftest_config(keywords: list[str] | None = None) -> Config:
    return Config(
        base_url="https://pwca.example", events_path="/events/",
        targets=[Target("selftest", "Self Test Event", URL, scope="selftest")],
        request={"min_text_length": 20},
        parser={"content_selector": "[data-page-events-show]",
                "exclude_selectors": ["[data-buttons]", "[data-news-latest]"],
                "attachment_extensions": [".pdf"], "strip_query_params": ["utm_source"]},
        keywords=keywords if keywords is not None else
        ["accommodation", "hotel", "booking", "check-in", "first come", "宿泊", "予約"],
        notify={"max_message_chars": 3800, "max_excerpt_lines": 15, "error_renotify_hours": 6},
        subpages={"enabled": True, "max_per_target": 3,
                  "exclude_url_patterns": [r"^https?://scoring\.pwca\.example/", r"/results?(/|$)"],
                  "parser": {"content_selector": ["main", "body"]}},
    )


def _run(cfg, state, fetcher, notifier):
    # Suppress console output and observe only the result
    with redirect_stdout(io.StringIO()):
        return run_once(cfg, state, fetcher, notifier, now=lambda: fetcher.clock, sleep=lambda _: None)


def _baseline(body="", links=""):
    cfg, state, f, n = selftest_config(), State(), FakeFetcher(), RecordingNotifier()
    f.set_html(make_html(body, links))
    r = _run(cfg, state, f, n)
    assert r.baselines == ["selftest"] and not n.sent, "first run must not notify"
    n.sent.clear()
    return cfg, state, f, n


def t_first_run_no_notify():
    _baseline()


def t_no_change_no_notify():
    cfg, state, f, n = _baseline()
    r = _run(cfg, state, f, n)
    assert r.unchanged == ["selftest"] and not n.sent


def t_noise_only_no_notify():
    cfg, state, f, n = _baseline()
    noisy = make_html(countdown="22").replace("<div>Launch: Table Land</div>",
                                              '<div   class="x" id="r9">  Launch:\n\t Table   Land </div>')
    f.set_html(noisy.replace('competitions"', 'competitions?utm_source=x"'))
    _run(cfg, state, f, n)
    assert not n.sent, f"noise-only change must not notify: {[x.text for x in n.sent]}"


def t_general_add():
    cfg, state, f, n = _baseline()
    f.set_html(make_html(body="<div>Task briefing at 9:00 in the HQ</div>"))
    _run(cfg, state, f, n)
    assert len(n.sent) == 1 and n.sent[0].priority == "通常更新", n.sent
    assert "Task briefing" in n.sent[0].text


def t_change_and_delete():
    cfg, state, f, n = _baseline(body="<div>HQ Location: To be published</div>")
    f.set_html(make_html(body="<div>HQ Location: Panchgani Club</div>"))
    _run(cfg, state, f, n)
    assert len(n.sent) == 1 and "Panchgani Club" in n.sent[0].text and "To be published" in n.sent[0].text
    n.sent.clear()
    f.set_html(make_html())
    _run(cfg, state, f, n)
    assert len(n.sent) == 1 and "削除" in n.sent[0].text


def t_lodging_priority():
    for added in ("<div>Accommodation</div>", "<div>Hotel Booking is now open</div>",
                  "<div>宿泊の予約受付を開始します</div>", "<div>Check in from 14:00</div>"):
        cfg, state, f, n = _baseline()
        f.set_html(make_html(body=added))
        _run(cfg, state, f, n)
        assert len(n.sent) == 1 and n.sent[0].priority == "宿泊関連の可能性", (added, n.sent)
        assert "内容を確認してください" in n.sent[0].text


def t_existing_keyword_not_priority():
    cfg, state, f, n = _baseline(body="<div>Accommodation: see below</div>")
    f.set_html(make_html(body="<div>Accommodation: see below</div><div>Briefing at 9:00</div>"))
    _run(cfg, state, f, n)
    assert len(n.sent) == 1 and n.sent[0].priority == "通常更新", n.sent


def t_new_link_and_pdf():
    cfg, state, f, n = _baseline()
    f.set_html(make_html(links='<a href="/storage/1/Bulletin.pdf">Bulletin</a>'
                               '<a href="https://example.org/stay">Stay info</a>'))
    _run(cfg, state, f, n)
    assert len(n.sent) == 1
    t = n.sent[0].text
    assert "[PDF/添付] Bulletin" in t and "https://pwca.example/storage/1/Bulletin.pdf" in t
    assert "https://example.org/stay" in t


def t_link_url_change():
    cfg, state, f, n = _baseline(links='<a href="https://a.example/form">Entry form</a>')
    f.set_html(make_html(links='<a href="https://b.example/form">Entry form</a>'))
    _run(cfg, state, f, n)
    assert len(n.sent) == 1 and "https://b.example/form" in n.sent[0].text


def t_duplicate_suppressed():
    cfg, state, f, n = _baseline()
    before = state.data["pages"]["selftest"]
    f.set_html(make_html(body="<div>New info</div>"))
    _run(cfg, state, f, n)
    assert len(n.sent) == 1
    # Roll back to the old snapshot and reprocess the same diff (simulates a failed state push)
    state.data["pages"]["selftest"] = before
    r = _run(cfg, state, f, n)
    assert len(n.sent) == 1 and r.suppressed == ["selftest"]


def t_fetch_error_keeps_state():
    cfg, state, f, n = _baseline()
    good = dict(state.data["pages"]["selftest"])
    f.set_error("http_403", "HTTP 403", 403)
    r = _run(cfg, state, f, n)
    assert state.data["pages"]["selftest"] == good, "must not overwrite good state on fetch failure"
    assert r.fetch_errors == ["selftest"] and len(n.sent) == 1 and n.sent[0].kind == "error"
    # Persisting error: no re-notification within 6 hours
    f.clock = "2026-10-13T01:00:00Z"
    _run(cfg, state, f, n)
    assert len(n.sent) == 1
    # Re-notify after 6 hours
    f.clock = "2026-10-13T06:05:00Z"
    _run(cfg, state, f, n)
    assert len(n.sent) == 2 and "継続中" in n.sent[1].text
    # Recovery notice
    f.clock = "2026-10-13T07:00:00Z"
    f.set_html(make_html())
    _run(cfg, state, f, n)
    assert n.sent[-1].kind == "recovery" and state.get_error("selftest") is None


def t_short_and_structure_error():
    cfg, state, f, n = _baseline()
    f.set_html("<html><body><h1>Login</h1><form>password</form></body></html>")
    r = _run(cfg, state, f, n)
    assert r.fetch_errors and n.sent[-1].kind == "error" and "parse" in n.sent[-1].text


def t_notify_failure_retries_next_run():
    cfg, state, f, _ = _baseline()
    before = state.data["pages"]["selftest"]
    failing = RecordingNotifier(fail=True)
    f.set_html(make_html(body="<div>Hotel list published</div>"))
    r = _run(cfg, state, f, failing)
    assert r.exit_code == 1 and state.data["pages"]["selftest"] == before
    ok = RecordingNotifier()
    _run(cfg, state, f, ok)
    assert len(ok.sent) == 1, "should notify on the next run after a failure"


def t_invalid_state_json():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "state.json"
        p.write_text("{broken", encoding="utf-8")
        try:
            load_state(p)
        except StateError:
            return
        raise AssertionError("invalid JSON must raise StateError")


SUB_HOTEL = URL + "/accommodation"
SUB_INFO = URL + "/local-info"
SUB_HTML = "<html><body><nav>menu</nav><main><h1>{title}</h1><p>{body}</p></main></body></html>"
LINK = '<a href="{url}">{text}</a>'


def t_subpage_baseline_silent():
    # Fresh state: page and subpage both become the baseline
    cfg, state, f, n = selftest_config(), State(), FakeFetcher(), RecordingNotifier()
    f.set_page(SUB_INFO, SUB_HTML.format(title="Local info", body="Airport transfer details will follow soon."))
    f.set_html(make_html(links=LINK.format(url=SUB_INFO, text="Local info")))
    r = _run(cfg, state, f, n)
    assert not n.sent and len(r.baselines) == 2, (r, n.sent)


def t_subpage_new_page_notified():
    cfg, state, f, n = _baseline()
    f.set_page(SUB_HOTEL, SUB_HTML.format(title="Accommodation", body="Hotel booking opens on 01.12.2026. First come first served."))
    f.set_html(make_html(links=LINK.format(url=SUB_HOTEL, text="Accommodation")))
    r = _run(cfg, state, f, n)
    kinds = [x.text.splitlines()[0] for x in n.sent]
    assert r.new_pages and len(n.sent) == 2, kinds   # parent "new link" + subpage "new page"
    sub = n.sent[1]
    assert "新規ページ" in sub.text and sub.priority == "宿泊関連の可能性" and "Hotel booking opens" in sub.text
    assert "Self Test Event › Accommodation" in sub.text and SUB_HOTEL in sub.text
    n.sent.clear()
    r = _run(cfg, state, f, n)
    assert not n.sent, "no repeat notification"


def t_subpage_change_notified():
    cfg, state, f, n = _baseline()
    f.set_page(SUB_INFO, SUB_HTML.format(title="Local info", body="Details to be announced"))
    f.set_html(make_html(links=LINK.format(url=SUB_INFO, text="Local info")))
    _run(cfg, state, f, n)
    n.sent.clear()
    f.set_page(SUB_INFO, SUB_HTML.format(title="Local info", body="Hotel shuttle from Pune station available"))
    _run(cfg, state, f, n)
    assert len(n.sent) == 1 and "Hotel shuttle from Pune" in n.sent[0].text and n.sent[0].priority == "宿泊関連の可能性"


def t_subpage_404_pending_then_published():
    cfg, state, f, n = _baseline()
    f.set_page(SUB_HOTEL, None, status=404)
    f.set_html(make_html(links=LINK.format(url=SUB_HOTEL, text="Accommodation")))
    r = _run(cfg, state, f, n)
    assert r.pending and not r.fetch_errors and all(x.kind != "error" for x in n.sent)
    n.sent.clear()
    r = _run(cfg, state, f, n)
    assert not n.sent, "still 404: no notification"
    f.set_page(SUB_HOTEL, SUB_HTML.format(title="Accommodation", body="Rooms are now available for booking."))
    r = _run(cfg, state, f, n)
    assert r.new_pages and len(n.sent) == 1 and "新規ページ" in n.sent[0].text


def t_subpage_excludes_results_and_other_events():
    cfg, state, f, n = _baseline()
    links = "".join(LINK.format(url=u, text=t) for u, t in [
        ("https://scoring.pwca.example/selftest/ranking0.html", "Overall"),
        (URL + "/results", "Results"),
        ("https://pwca.example/events/other-event", "Other event"),
        ("https://pwca.example/account/competitions", "Register"),
        (URL + "#pilot-information", "Pilot Information"),
        (URL + "/bulletin.pdf", "Bulletin"),
    ])
    f.set_html(make_html(links=links))
    f.calls.clear()
    _run(cfg, state, f, n)
    assert f.calls == [URL], f.calls


def t_subpage_limit():
    cfg, state, f, n = _baseline()
    links = "".join(LINK.format(url=f"{URL}/p{i}", text=f"P{i}") for i in range(6))
    f.set_html(make_html(links=links))
    f.calls.clear()
    _run(cfg, state, f, n)
    assert len(f.calls) == 1 + 3, f.calls   # max_per_target = 3


def t_subpage_unlinked_is_pruned():
    cfg, state, f, n = _baseline()
    f.set_page(SUB_INFO, SUB_HTML.format(title="Local info", body="TBA for now, details later"))
    f.set_html(make_html(links=LINK.format(url=SUB_INFO, text="Local info")))
    _run(cfg, state, f, n)
    assert len(state.data["pages"]) == 2
    f.set_html(make_html())
    f.calls.clear()
    _run(cfg, state, f, n)
    assert f.calls == [URL] and len(state.data["pages"]) == 1


NEWS_URL = "https://pwca.example/news"
NEWS_LIST = ("<html><body><nav>menu</nav><main><h1>News</h1>{items}"
             "<p>All the latest stories from the Paragliding World Cup tour.</p></main></body></html>")
NEWS_ITEM = '<article><h2>{title}</h2><a href="https://pwca.example/news/{n}">Continue reading...</a></article>'
ARTICLE = "<html><body><main><a href='/news'>back to all news</a><h1>{title}</h1><p>{body}</p></main></body></html>"


def _news_setup():
    cfg = selftest_config(keywords=["accommodation", "hotel", "booking"])
    cfg.targets = [Target("news", "PWCAニュース", NEWS_URL, scope="pwca.example/news/",
                          parser={"content_selector": ["main"]}, notify_page_changes=False,
                          max_subpages=9, subpage_fetch_once=True,
                          relevance_words=["Panchgani", "India", "hotel", "accommodation"],
                          new_page_note="PWCAニュースに関連する新しい記事が掲載されました。")]
    f = FakeFetcher()
    f.main_url = NEWS_URL
    state, n = State(), RecordingNotifier()
    f.set_page("https://pwca.example/news/1", ARTICLE.format(title="Race report Brazil", body="Task 1 was won by someone in Baixo Guandu."))
    f.set_html(NEWS_LIST.format(items=NEWS_ITEM.format(title="Race report Brazil", n=1)))
    f.response.url = f.response.final_url = NEWS_URL
    r = _run(cfg, state, f, n)
    assert not n.sent and "news" in r.baselines, (r, n.sent)
    return cfg, state, f, n


def _news_add(f, items):
    f.set_html(NEWS_LIST.format(items="".join(NEWS_ITEM.format(title=t, n=i) for i, t in items)))
    f.response.url = f.response.final_url = NEWS_URL


def t_news_relevant_article_notified():
    cfg, state, f, n = _news_setup()
    f.set_page("https://pwca.example/news/2", ARTICLE.format(
        title="Panchgani 2027: accommodation info", body="The official hotel list for Panchgani is now available."))
    _news_add(f, [(2, "Panchgani 2027: accommodation info"), (1, "Race report Brazil")])
    r = _run(cfg, state, f, n)
    assert len(n.sent) == 1 and r.new_pages, (r, [x.text for x in n.sent])
    t = n.sent[0].text
    assert "PWCAニュース › Panchgani 2027: accommodation info" in t and n.sent[0].priority == "宿泊関連の可能性"
    assert "PWCAニュースに関連する新しい記事" in t and "関連語を検出" in t


def t_news_irrelevant_article_silent():
    cfg, state, f, n = _news_setup()
    f.set_page("https://pwca.example/news/2", ARTICLE.format(title="Task 2 in Brazil", body="A long race decided at the final glide."))
    _news_add(f, [(2, "Task 2 in Brazil"), (1, "Race report Brazil")])
    r = _run(cfg, state, f, n)
    assert not n.sent and "news" in r.silent, (r, [x.text for x in n.sent])


def t_news_articles_fetched_once():
    cfg, state, f, n = _news_setup()
    f.calls.clear()
    _run(cfg, state, f, n)
    assert f.calls == [NEWS_URL], f.calls


def t_console_notifier():
    n = ConsoleNotifier()
    with redirect_stdout(io.StringIO()) as buf:
        n.send(Notification("test", "テスト", "hello"))
    assert "hello" in buf.getvalue()


SCENARIOS: list[tuple[str, Callable[[], None]]] = [
    ("first run creates baseline only (no notification)", t_first_run_no_notify),
    ("no change → no notification", t_no_change_no_notify),
    ("whitespace/attribute/countdown/tracking-param changes → no notification", t_noise_only_no_notify),
    ("general text added → normal notification", t_general_add),
    ("text changed/removed → notification", t_change_and_delete),
    ("accommodation keyword added → priority notification", t_lodging_priority),
    ("pre-existing keyword only → not priority", t_existing_keyword_not_priority),
    ("new link / new PDF detected", t_new_link_and_pdf),
    ("link URL change detected", t_link_url_change),
    ("same diff → duplicate notification suppressed", t_duplicate_suppressed),
    ("fetch failure: state kept, first/periodic/recovery notices", t_fetch_error_keeps_state),
    ("login page / structure change → error", t_short_and_structure_error),
    ("notification failure → exit 1, retried next run", t_notify_failure_retries_next_run),
    ("invalid state JSON → StateError", t_invalid_state_json),
    ("subpages: existing pages on first discovery become baseline", t_subpage_baseline_silent),
    ("subpages: newly linked page → new-page notification (accommodation priority)", t_subpage_new_page_notified),
    ("subpages: content change → notification", t_subpage_change_notified),
    ("subpages: 404 is pending (no error), notified when published", t_subpage_404_pending_then_published),
    ("subpages: results / other events / PDFs / anchors not fetched", t_subpage_excludes_results_and_other_events),
    ("subpages: per-event cap", t_subpage_limit),
    ("subpages: unlinked pages stop being monitored", t_subpage_unlinked_is_pruned),
    ("news: relevant new article → notification (named by article heading)", t_news_relevant_article_notified),
    ("news: unrelated article / list change → no notification", t_news_irrelevant_article_silent),
    ("news: known articles are not re-fetched", t_news_articles_fetched_once),
    ("console notifier output", t_console_notifier),
]


def run_selftest() -> int:
    import logging
    logging.disable(logging.CRITICAL)  # keep only the PASS/FAIL lines
    failed = 0
    for name, fn in SCENARIOS:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ERROR {name}: {type(e).__name__}: {e}")
    logging.disable(logging.NOTSET)
    print(f"\nself-test: {len(SCENARIOS) - failed}/{len(SCENARIOS)} passed")
    return 1 if failed else 0
