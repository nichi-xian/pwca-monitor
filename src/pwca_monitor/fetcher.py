"""HTTP fetching. Uses limited retries and never attempts to bypass access restrictions."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import requests

from .models import FetchResult

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {500, 502, 503, 504}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class HttpFetcher:
    def __init__(self, request_cfg: dict, session: requests.Session | None = None,
                 sleep=time.sleep):
        self.user_agent = request_cfg.get("user_agent", "pwca-monitor/0.1")
        self.timeout = (float(request_cfg.get("connect_timeout", 10)),
                        float(request_cfg.get("read_timeout", 30)))
        self.max_retries = int(request_cfg.get("max_retries", 2))
        self.backoff = float(request_cfg.get("retry_backoff", 5))
        self.session = session or requests.Session()
        self.sleep = sleep

    def fetch(self, url: str) -> FetchResult:
        attempt = 0
        while True:
            result = self._fetch_once(url)
            retryable = (result.error_kind in ("timeout", "connection")
                         or (result.status in RETRYABLE_STATUS))
            if result.ok or not retryable or attempt >= self.max_retries:
                return result
            attempt += 1
            wait = self.backoff * attempt
            log.warning("Fetch failed (%s); retrying in %.0fs (%d/%d)",
                        result.error_kind, wait, attempt, self.max_retries)
            self.sleep(wait)

    def _fetch_once(self, url: str) -> FetchResult:
        fetched_at = utc_now_iso()
        try:
            resp = self.session.get(
                url, timeout=self.timeout, allow_redirects=True,
                headers={"User-Agent": self.user_agent,
                         "Accept": "text/html,application/xhtml+xml"},
            )
        except requests.exceptions.Timeout as e:
            return FetchResult(url, False, fetched_at=fetched_at, error_kind="timeout",
                               error_message=type(e).__name__)
        except requests.exceptions.SSLError as e:
            return FetchResult(url, False, fetched_at=fetched_at, error_kind="tls",
                               error_message=type(e).__name__)
        except requests.exceptions.ConnectionError as e:
            return FetchResult(url, False, fetched_at=fetched_at, error_kind="connection",
                               error_message=f"{type(e).__name__} (DNS/connection error)")
        except requests.exceptions.RequestException as e:
            return FetchResult(url, False, fetched_at=fetched_at, error_kind="request",
                               error_message=type(e).__name__)

        ctype = resp.headers.get("Content-Type", "")
        base = FetchResult(url, False, status=resp.status_code, final_url=resp.url,
                           content_type=ctype, fetched_at=fetched_at)
        if resp.status_code != 200:
            base.error_kind = f"http_{resp.status_code}"
            base.error_message = f"HTTP {resp.status_code}"
            return base
        if "html" not in ctype.lower():
            base.error_kind = "content_type"
            base.error_message = f"Unexpected Content-Type: {ctype or '(none)'}"
            return base
        text = resp.text
        if not text.strip():
            base.error_kind = "empty"
            base.error_message = "Empty response body"
            return base
        base.ok = True
        base.html = text
        return base
