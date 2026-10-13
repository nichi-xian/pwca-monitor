"""Command-line entry point.

  python -m pwca_monitor self-test [--send-telegram]
  python -m pwca_monitor test [--send-telegram]
  python -m pwca_monitor check [--dry-run] [--show-text]
  python -m pwca_monitor notify-test
  python -m pwca_monitor monitor --state PATH [--dry-run] [--report-dir DIR]
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from .config import ConfigError, load_config
from .fetcher import HttpFetcher, utc_now_iso
from .models import Notification
from .monitor import run_once, write_reports
from .notifier import ConsoleNotifier, NotifyError, TelegramNotifier, to_jst
from .parser import ParseError, parse_page
from .state import State, StateError, load_state, save_state

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "targets.yaml"
DEFAULT_FIXTURES = ROOT / "tests" / "fixtures"
log = logging.getLogger("pwca_monitor")


def _telegram(cfg_notify: dict | None = None) -> TelegramNotifier:
    return TelegramNotifier.from_env(int((cfg_notify or {}).get("max_retries", 3)))


def cmd_self_test(args) -> int:
    from .selftest import run_selftest
    print("== self-test (no network / no Telegram)")
    rc = run_selftest()
    if args.send_telegram:
        rc |= _send_test_message("self-test")
    return rc


def cmd_test(args) -> int:
    """Run the fixtures before → before → after → after flow and print the notifications."""
    from .selftest import FakeFetcher
    cfg = load_config(args.config)
    target = cfg.enabled_targets[0]
    fixtures = Path(args.fixtures)
    before = (fixtures / "page_before.html").read_text(encoding="utf-8")
    after = (fixtures / "page_after.html").read_text(encoding="utf-8")
    cfg.targets = [target]
    state, fetcher, console = State(), FakeFetcher(), ConsoleNotifier()
    fetcher.main_url = target.url

    def step(label: str, html: str):
        fetcher.set_html(html)
        fetcher.response.url = fetcher.response.final_url = target.url
        r = run_once(cfg, state, fetcher, console, now=lambda: fetcher.clock, sleep=lambda _: None)
        print(f"-- {label}: baseline={r.baselines} unchanged={r.unchanged} "
              f"updates={r.updates} suppressed={r.suppressed}")
        return r

    step("1) first run (before)", before)
    step("2) no change (before)", before)
    r = step("3) updated (after)", after)
    step("4) reprocess same content (after)", after)
    ok = (len(console.sent) == 1 and r.updates == [target.id])
    print(f"\ntest: {'OK' if ok else 'NG'} (notifications: {len(console.sent)}, expected: 1)")
    if r.reports:
        print("\n== full diff (equivalent to the Actions log)\n" + r.reports[0][1])
    rc = 0 if ok else 1
    if args.send_telegram and console.sent:
        n = console.sent[0]
        try:
            _telegram(cfg.notify).send(Notification(n.kind, n.priority, "[テスト送信・fixture]\n" + n.text))
            print("Sent the test notification to Telegram")
        except NotifyError as e:
            print(f"Telegram send failed: {e}", file=sys.stderr)
            rc = 1
    return rc


def cmd_check(args) -> int:
    """Fetch the live site once and show the extraction result. Never modifies the state."""
    cfg = load_config(args.config)
    fetcher = HttpFetcher(cfg.request)
    min_len = int(cfg.request.get("min_text_length", 80))
    prev_state = None
    if args.state and Path(args.state).exists():
        prev_state = load_state(args.state)
    rc = 0
    print(f"site: base_url={cfg.base_url} events_path={cfg.events_path}  (state is not modified)")
    for t in cfg.enabled_targets:
        print("\n" + "=" * 70 + f"\n[{t.id}] {t.name}\nURL: {t.url}")
        fr = fetcher.fetch(t.url)
        print(f"HTTP: {fr.status}  final URL: {fr.final_url}  Content-Type: {fr.content_type}")
        print(f"fetched at: {fr.fetched_at} ({to_jst(fr.fetched_at)})")
        if not fr.ok:
            print(f"!! fetch failed: {fr.error_kind}: {fr.error_message}")
            rc = 1
            continue
        try:
            snap = parse_page(fr.html, t.url, fr.final_url, fr.fetched_at, cfg.parser)
        except ParseError as e:
            print(f"!! extraction failed: {e}")
            rc = 1
            continue
        n = len(snap.normalized_text)
        print(f"title: {snap.title}\nheadings: {snap.headings}")
        print(f"text: {n} chars / {len(snap.lines)} lines"
              + ("" if n >= min_len else f"  !! below threshold {min_len} (error)"))
        print(f"links: {len(snap.links)}  attachments: {len(snap.attachments)}  hash: {snap.content_hash[:23]}…")
        if n < min_len:
            rc = 1
        for l in snap.links:
            print(f"  - {l.text or '(no text)'} -> {l.url}")
        if args.show_text:
            print("--- extracted text ---\n" + snap.normalized_text + "\n----------------")
        rc |= _check_subpages(cfg, t, snap, fetcher, min_len)
        if prev_state:
            prev = prev_state.get_snapshot(t.id)
            if prev:
                from .diff import diff_snapshots
                d = diff_snapshots(prev, snap)
                print("diff vs stored state: " + ("yes\n" + d.unified_diff if d.has_changes else "none"))
    return rc


def _check_subpages(cfg, target, snap, fetcher, min_len: int) -> int:
    from .subpages import discover_subpages
    if not cfg.subpages.get("enabled"):
        print("subpages: disabled")
        return 0
    exts = tuple(e.lower() for e in cfg.parser.get("attachment_extensions") or [".pdf"])
    subs = discover_subpages(target, snap, cfg.subpages, exts)
    print(f"subpages: {len(subs)} (scope='{target.scope}', excluded: {cfg.subpages.get('exclude_url_patterns') or []})")
    sub_parser = {**cfg.parser, **(cfg.subpages.get("parser") or {})}
    rc = 0
    for s in subs:
        time.sleep(float(cfg.request.get("delay_between_requests", 1)))
        fr = fetcher.fetch(s.url)
        if fr.status == 404:
            print(f"  - {s.name}: 404 (not published yet; will notify when it appears) {s.url}")
            continue
        if not fr.ok:
            print(f"  - {s.name}: !! {fr.error_kind}: {fr.error_message} {s.url}")
            rc = 1
            continue
        try:
            sp = parse_page(fr.html, s.url, fr.final_url, fr.fetched_at, sub_parser)
            flag = "" if len(sp.normalized_text) >= min_len else "  !! too short"
            print(f"  - {s.name}: HTTP {fr.status}, {len(sp.normalized_text)} chars{flag} {s.url}")
        except ParseError as e:
            print(f"  - {s.name}: !! extraction failed: {e}")
            rc = 1
    return rc


def _send_test_message(source: str) -> int:
    try:
        n = _telegram()
        n.send(Notification("test", "テスト",
                            f"✅ PWCA監視 テスト通知（{source}）\n送信日時: {to_jst(utc_now_iso())}\n"
                            "これはテストです。更新を意味しません。"))
    except NotifyError as e:
        print(f"Telegram test send failed: {e}", file=sys.stderr)
        return 1
    print("Sent 1 Telegram test message")
    return 0


def cmd_notify_test(args) -> int:
    return _send_test_message("notify-test")


def cmd_monitor(args) -> int:
    cfg = load_config(args.config)
    state_path = Path(args.state)
    if args.dry_run or args.console:
        notifier = ConsoleNotifier()
    else:
        try:
            notifier = _telegram(cfg.notify)
        except NotifyError as e:
            print(f"::error::{e}")
            return 1
    try:
        state = load_state(state_path)
    except StateError as e:
        # Do not overwrite the broken state. Try to notify only.
        print(f"::error::{e}")
        try:
            notifier.send(Notification("error", "監視エラー", f"⚠️ 【監視エラー】状態ファイルを読み込めません\n{e}\n監視を停止しています。"))
        except NotifyError:
            pass
        return 1

    result = run_once(cfg, state, HttpFetcher(cfg.request), notifier)
    for tid in result.fetch_errors:
        print(f"::warning::fetch error: {tid} (see the Telegram notification / log)")
    for tid, text in result.reports:
        print(f"::group::full diff: {tid}\n{text}\n::endgroup::")
    if args.report_dir:
        write_reports(result, args.report_dir)

    rc = result.exit_code
    if args.dry_run:
        print("dry-run: state file not written")
    elif state.dirty:
        try:
            save_state(state, state_path)
            print(f"state updated: {state_path}")
        except StateError as e:
            print(f"::error::{e}")
            try:
                notifier.send(Notification("error", "監視エラー", f"⚠️ 【監視エラー】状態ファイルを書き込めません\n{e}"))
            except NotifyError:
                pass
            rc = 1
    print(f"summary: baseline={result.baselines} unchanged={result.unchanged} updates={result.updates} "
          f"new_pages={result.new_pages} pending_404={result.pending} "
          f"suppressed={result.suppressed} fetch_errors={result.fetch_errors} notify_failures={result.notify_failures}")
    return rc


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pwca_monitor", description="PWCA event page update monitor")
    p.add_argument("--config", default=str(DEFAULT_CONFIG), help="config file (YAML)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="mode", required=True)

    s = sub.add_parser("self-test", help="built-in self-test (no network)")
    s.add_argument("--send-telegram", action="store_true", help="also send one Telegram test message")
    s.set_defaults(func=cmd_self_test)

    s = sub.add_parser("test", help="update-detection test with fixture HTML (console output)")
    s.add_argument("--fixtures", default=str(DEFAULT_FIXTURES))
    s.add_argument("--send-telegram", action="store_true", help="send the generated notification to Telegram")
    s.set_defaults(func=cmd_test)

    s = sub.add_parser("check", help="fetch the live site once and show the result (no state changes)")
    s.add_argument("--dry-run", action="store_true", help="explicitly do not modify state (always true for check)")
    s.add_argument("--state", help="compare against this state file (read-only)")
    s.add_argument("--show-text", action="store_true", help="print the extracted text")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("notify-test", help="send exactly one Telegram test message")
    s.set_defaults(func=cmd_notify_test)

    s = sub.add_parser("monitor", help="run the production monitor once")
    s.add_argument("--state", default=str(ROOT / "state" / "state.json"))
    s.add_argument("--dry-run", action="store_true", help="do not write state, print notifications to console")
    s.add_argument("--console", action="store_true", help="print notifications to console instead of Telegram (state is written)")
    s.add_argument("--report-dir", help="directory to write the full diff to")
    s.set_defaults(func=cmd_monitor)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    # urllib3 debug logs can contain the request URL (= Telegram token), so keep them at WARNING or above
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    try:
        return args.func(args)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
