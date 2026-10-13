"""Core monitoring logic (one run). Fetching and notification are injected so it can be tested."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol

from .classifier import KeywordClassifier
from .config import Config
from .diff import diff_snapshots
from .fetcher import utc_now_iso
from .models import FetchResult, Notification, PageSnapshot, Target
from .notifier import (NotifyError, Notifier, build_error_message,
                       build_recovery_message, build_update_message)
from .parser import ParseError, parse_page
from .state import State
from .subpages import discover_subpages

log = logging.getLogger(__name__)


class Fetcher(Protocol):
    def fetch(self, url: str) -> FetchResult: ...


@dataclass
class RunResult:
    baselines: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    updates: list[str] = field(default_factory=list)
    new_pages: list[str] = field(default_factory=list)    # subpages that newly appeared
    pending: list[str] = field(default_factory=list)      # subpages not published yet (404)
    suppressed: list[str] = field(default_factory=list)   # duplicate diffs
    fetch_errors: list[str] = field(default_factory=list)
    notify_failures: list[str] = field(default_factory=list)
    reports: list[tuple[str, str]] = field(default_factory=list)  # (target_id, full diff)

    @property
    def exit_code(self) -> int:
        # Notification failures fail the job. Fetch errors are reported via Telegram,
        # so they do not fail the job (avoids a red X every 5 minutes).
        return 1 if self.notify_failures else 0


def _hours_between(a: str, b: str) -> float:
    pa = datetime.fromisoformat(a.replace("Z", "+00:00"))
    pb = datetime.fromisoformat(b.replace("Z", "+00:00"))
    return (pb - pa).total_seconds() / 3600


def _empty_snapshot(target: Target) -> PageSnapshot:
    return PageSnapshot(target.url, target.url, "", [], [], [], [], "", "")


class _Run:
    def __init__(self, config: Config, state: State, fetcher: Fetcher, notifier: Notifier,
                 now: Callable[[], str], sleep: Callable[[float], None]):
        self.config, self.state, self.fetcher, self.notifier = config, state, fetcher, notifier
        self.now, self.sleep = now, sleep
        self.result = RunResult()
        self.classifier = KeywordClassifier(config.keywords)
        self.min_len = int(config.request.get("min_text_length", 80))
        self.delay = float(config.request.get("delay_between_requests", 1))
        self.renotify_hours = float(config.notify.get("error_renotify_hours", 6))
        self.sub_cfg = config.subpages or {}
        self.sub_parser = {**config.parser, **(self.sub_cfg.get("parser") or {})}
        self.att_exts = tuple(e.lower() for e in config.parser.get("attachment_extensions") or [".pdf"])
        self.requests_made = 0

    def send(self, n: Notification, target_id: str) -> bool:
        try:
            self.notifier.send(n)
            return True
        except NotifyError as e:
            log.error("[%s] notification failed: %s", target_id, e)
            self.result.notify_failures.append(target_id)
            return False

    def fetch(self, url: str) -> FetchResult:
        if self.requests_made and self.delay > 0:
            self.sleep(self.delay)  # be gentle with the site
        self.requests_made += 1
        return self.fetcher.fetch(url)

    def run(self) -> RunResult:
        for target in self.config.enabled_targets:
            snap = self.process(target, self.config.parser, notify_new=False)
            if snap is not None and self.sub_cfg.get("enabled"):
                self.process_subpages(target, snap)
        self.state.heartbeat(self.now())
        return self.result

    def process_subpages(self, target: Target, snap: PageSnapshot) -> None:
        subs = discover_subpages(target, snap, self.sub_cfg, self.att_exts)
        # On the first discovery for this event, existing subpages become a baseline (no notification)
        initialized = self.state.subpages_initialized(target.id)
        for sub in subs:
            self.process(sub, self.sub_parser, notify_new=initialized, allow_404=True)
        self.state.set_subpages(target.id, {s.url: s.id for s in subs})

    def process(self, target: Target, parser_cfg: dict, notify_new: bool,
                allow_404: bool = False) -> PageSnapshot | None:
        """Fetch, compare and notify for one page. Returns the current snapshot (None on failure)."""
        st, res = self.state, self.result
        fr = self.fetch(target.url)
        if allow_404 and fr.status == 404 and st.get_snapshot(target.id) is None:
            # Linked but not published yet. Not an error; notify when it appears.
            log.info("[%s] not published yet (404): %s", target.id, target.url)
            res.pending.append(target.id)
            return None

        snap = None
        err_kind, err_msg = fr.error_kind, fr.error_message
        if fr.ok:
            try:
                snap = parse_page(fr.html or "", target.url, fr.final_url or target.url,
                                  fr.fetched_at or self.now(), parser_cfg)
                if len(snap.normalized_text) < self.min_len:
                    err_kind = "short_text"
                    err_msg = f"Extracted text is too short ({len(snap.normalized_text)} chars). Possible error or login page"
                    snap = None
            except ParseError as e:
                err_kind, err_msg = "parse", str(e)
        if snap is None:
            self.handle_error(target, err_kind or "unknown", err_msg or "")
            return None

        log.info("[%s] fetched OK: HTTP %s, %d chars, %d links",
                 target.id, fr.status, len(snap.normalized_text), len(snap.links))
        prev_err = st.get_error(target.id)
        if prev_err and self.send(build_recovery_message(target, prev_err["kind"], prev_err["first_at"], self.now()), target.id):
            st.clear_error(target.id)

        prev = st.get_snapshot(target.id)
        is_new_page = False
        if prev is None:
            if not notify_new:
                log.info("[%s] first run: saving baseline (no notification)", target.id)
                st.set_snapshot(target.id, snap)
                res.baselines.append(target.id)
                return snap
            prev, is_new_page = _empty_snapshot(target), True
        elif prev.content_hash == snap.content_hash:
            res.unchanged.append(target.id)
            return snap

        diff = diff_snapshots(prev, snap)
        if not diff.has_changes:
            st.set_snapshot(target.id, snap)  # only ordering etc. changed
            res.unchanged.append(target.id)
            return snap
        if st.was_notified(target.id, diff.diff_hash):
            log.info("[%s] diff already notified; skipping", target.id)
            st.set_snapshot(target.id, snap)
            res.suppressed.append(target.id)
            return snap

        priority, hits = self.classifier.classify(diff)
        res.reports.append((target.id, diff.unified_diff))
        log.info("[%s] %s (%s) keywords=%s", target.id,
                 "new page" if is_new_page else "update detected", priority, hits)
        msg = build_update_message(target, snap, diff, priority, hits, self.config.notify,
                                   new_page=is_new_page)
        if self.send(msg, target.id):
            st.mark_notified(target.id, diff.diff_hash)
            st.set_snapshot(target.id, snap)
            (res.new_pages if is_new_page else res.updates).append(target.id)
        # On send failure the state is left unchanged, so the next run retries.
        return snap

    def handle_error(self, target: Target, kind: str, message: str) -> None:
        """Keep the existing good snapshot. Notify on first occurrence and periodically while it persists."""
        st, now_iso = self.state, self.now()
        log.warning("[%s] fetch error: %s %s", target.id, kind, message)
        self.result.fetch_errors.append(target.id)
        err = st.get_error(target.id)
        changed = err is None or err.get("kind") != kind
        continuing = not changed
        if changed:
            err = {"kind": kind, "message": message, "first_at": now_iso, "last_notified_at": None}
        due = (err["last_notified_at"] is None
               or _hours_between(err["last_notified_at"], now_iso) >= self.renotify_hours)
        if due and self.send(build_error_message(target, kind, message, err["first_at"], now_iso, continuing), target.id):
            err = {**err, "message": message, "last_notified_at": now_iso}
            changed = True
        if changed:
            # Only write when the content changes (no state commit every run while an error persists)
            st.set_error(target.id, err)


def run_once(config: Config, state: State, fetcher: Fetcher, notifier: Notifier,
             now: Callable[[], str] = utc_now_iso,
             sleep: Callable[[float], None] = time.sleep) -> RunResult:
    return _Run(config, state, fetcher, notifier, now, sleep).run()


def write_reports(result: RunResult, report_dir: str | Path) -> None:
    d = Path(report_dir)
    d.mkdir(parents=True, exist_ok=True)
    for tid, text in result.reports:
        (d / f"{tid}.diff").write_text(text + "\n", encoding="utf-8")
