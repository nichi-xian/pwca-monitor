"""Notification body generation and delivery. To use another channel, implement the Notifier interface."""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Protocol

import requests

from .diff import is_attachment
from .models import DiffResult, Link, Notification, PageSnapshot, Target

log = logging.getLogger(__name__)
JST = timezone(timedelta(hours=9), "JST")


class NotifyError(Exception):
    pass


class Notifier(Protocol):
    name: str

    def send(self, n: Notification) -> None:
        """Raise NotifyError on failure."""


def to_jst(iso_utc: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    except ValueError:
        return iso_utc
    return dt.astimezone(JST).strftime("%Y-%m-%d %H:%M JST")


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    note = "\n…（省略。全文差分はGitHub Actionsのログ/成果物を確認）"
    return text[: limit - len(note)].rstrip() + note


def _excerpt(lines: list[str], max_lines: int, max_line_chars: int = 200) -> list[str]:
    out = [(l if len(l) <= max_line_chars else l[:max_line_chars] + "…") for l in lines[:max_lines]]
    if len(lines) > max_lines:
        out.append(f"…ほか{len(lines) - max_lines}行")
    return out


def _link_line(l: Link, attachments: list[Link]) -> str:
    tag = "[PDF/添付] " if is_attachment(l, attachments) else ""
    return f"・{tag}{l.text or '(テキストなし)'}\n  {l.url}"


def build_update_message(target: Target, snap: PageSnapshot, diff: DiffResult,
                         priority: str, hits: list[str], notify_cfg: dict,
                         new_page: bool = False,
                         relevance_hits: list[str] | None = None) -> Notification:
    max_lines = int(notify_cfg.get("max_excerpt_lines", 15))
    icon = "🏨" if priority != "通常更新" else ("📄" if new_page else "🔔")
    label = f"新規ページ・{priority}" if new_page else priority
    parts = [f"{icon} 【{label}】{target.name}",
             f"検知日時: {to_jst(snap.fetched_at)}"]
    if new_page:
        parts.append(target.new_page_note or "大会ページからリンクされた新しいページが公開されました。")
    if relevance_hits:
        parts.append(f"関連語を検出: {', '.join(relevance_hits)}")
    if hits:
        parts.append(f"関連キーワードを検出: {', '.join(hits)}\n"
                     "※予約受付開始とは限りません。内容を確認してください。")
    summary = []
    if diff.added_lines:
        summary.append(f"追加・変更 {len(diff.added_lines)}行")
    if diff.removed_lines:
        summary.append(f"削除 {len(diff.removed_lines)}行")
    if diff.added_links:
        summary.append(f"新規リンク {len(diff.added_links)}件")
    if diff.changed_links:
        summary.append(f"リンクURL変更 {len(diff.changed_links)}件")
    if diff.added_attachments:
        summary.append(f"PDF/添付追加 {len(diff.added_attachments)}件")
    if diff.title_change:
        summary.append("タイトル変更")
    if diff.heading_added or diff.heading_removed:
        summary.append("見出し変更")
    parts.append("変更の要約: " + ("、".join(summary) or "軽微な変更"))
    if diff.title_change:
        parts.append(f"タイトル: {diff.title_change[0]} → {diff.title_change[1]}")
    if diff.added_lines:
        parts.append(("▼ ページの内容（抜粋）\n" if new_page else "▼ 追加・変更されたテキスト\n") + "\n".join("+ " + l for l in _excerpt(diff.added_lines, max_lines)))
    if diff.removed_lines:
        parts.append("▼ 削除されたテキスト\n" + "\n".join("- " + l for l in _excerpt(diff.removed_lines, max(3, max_lines // 3))))
    new_links = diff.added_links + [n for _, n in diff.changed_links]
    if new_links:
        # Show PDFs first
        new_links.sort(key=lambda l: not is_attachment(l, snap.attachments))
        parts.append("▼ 新規・変更リンク（公式URLか確認してください）\n"
                     + "\n".join(_link_line(l, snap.attachments) for l in new_links[:10]))
    if target.parent_id:
        parts.append(f"ページ: {target.url}")
    else:
        parts.append(f"元ページ: {target.url}")
    text = _truncate("\n\n".join(parts), int(notify_cfg.get("max_message_chars", 3800)))
    return Notification(kind="update", priority=priority, text=text)


def build_error_message(target: Target, error_kind: str, message: str, first_at: str,
                        now: str, continuing: bool) -> Notification:
    head = "⚠️ 【監視エラー（継続中）】" if continuing else "⚠️ 【監視エラー】"
    text = (f"{head}{target.name}\n\n"
            f"種別: {error_kind}\n詳細: {message}\n"
            f"初回発生: {to_jst(first_at)}\n確認日時: {to_jst(now)}\n\n"
            "リトライは制限しており、アクセス制限の回避は行いません。"
            "手動でのページ確認と公式メールの確認を併用してください。\n"
            f"元ページ: {target.url}")
    return Notification(kind="error", priority="監視エラー", text=text)


def build_recovery_message(target: Target, error_kind: str, first_at: str, now: str) -> Notification:
    text = (f"✅ 【復旧】{target.name}\n\n"
            f"エラー {error_kind} が解消しました（{to_jst(first_at)} 〜 {to_jst(now)}）。\n"
            "停止中の更新は前回の正常状態と比較して検出します。\n"
            f"元ページ: {target.url}")
    return Notification(kind="recovery", priority="復旧", text=text)


class ConsoleNotifier:
    name = "console"

    def __init__(self):
        self.sent: list[Notification] = []

    def send(self, n: Notification) -> None:
        self.sent.append(n)
        print("=" * 60)
        print(f"[NOTIFY:{n.kind}] {n.priority}")
        print(n.text)
        print("=" * 60)


class TelegramNotifier:
    """Telegram Bot API. The token is used only in the URL and never written to logs or exception messages."""

    name = "telegram"
    API = "https://api.telegram.org/bot{token}/sendMessage"

    def __init__(self, token: str, chat_id: str, max_retries: int = 3,
                 session: requests.Session | None = None, sleep=time.sleep):
        if not token or not chat_id:
            raise NotifyError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are not set")
        self._token = token
        self._chat_id = chat_id
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self.sleep = sleep

    @classmethod
    def from_env(cls, max_retries: int = 3) -> "TelegramNotifier":
        return cls(os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(),
                   os.environ.get("TELEGRAM_CHAT_ID", "").strip(), max_retries)

    def __repr__(self) -> str:  # never show secrets
        return "TelegramNotifier(token=***, chat_id=***)"

    def send(self, n: Notification) -> None:
        url = self.API.format(token=self._token)
        payload = {"chat_id": self._chat_id, "text": n.text[:4096],
                   "disable_web_page_preview": True}
        last = "unknown"
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.session.post(url, json=payload, timeout=(10, 20))
            except requests.exceptions.RequestException as e:
                # Exception messages can contain the URL (= token), so log only the type name
                last = f"network error ({type(e).__name__})"
                retry_after = 2 * (attempt + 1)
            else:
                if resp.status_code == 200:
                    log.info("Telegram send succeeded")
                    return
                desc = _safe_description(resp)
                last = f"HTTP {resp.status_code}: {desc}"
                if resp.status_code == 429:
                    retry_after = _retry_after(resp)
                elif resp.status_code >= 500:
                    retry_after = 2 * (attempt + 1)
                else:
                    # 400 (bad chat_id), 401 (invalid token), 403 (bot blocked), etc. are not retried
                    raise NotifyError(f"Telegram send failed: {last}")
            if attempt < self.max_retries:
                log.warning("Telegram send failed (%s); retrying in %ss", last, retry_after)
                self.sleep(min(retry_after, 30))
        raise NotifyError(f"Telegram send failed after retries: {last}")


def _safe_description(resp) -> str:
    try:
        d = str(resp.json().get("description", ""))[:200]
    except ValueError:
        d = ""
    return d or "(no details)"


def _retry_after(resp) -> int:
    try:
        return int(resp.json().get("parameters", {}).get("retry_after", 5))
    except (ValueError, AttributeError):
        return 5
