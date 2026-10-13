"""Find pages of the same event among the links on an event page (one level deep, capped)."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit, urlunsplit

from .models import PageSnapshot, Target


def _strip_fragment(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, p.path, p.query, ""))


def subpage_id(parent_id: str, url: str) -> str:
    return f"{parent_id}~{hashlib.sha1(url.encode('utf-8')).hexdigest()[:10]}"


def discover_subpages(target: Target, snap: PageSnapshot, sub_cfg: dict,
                      attachment_exts: tuple[str, ...]) -> list[Target]:
    """Linked pages whose URL contains the event's scope (slug), minus exclusions."""
    if not sub_cfg.get("enabled") or not target.scope:
        return []
    limit = int(target.max_subpages or sub_cfg.get("max_per_target", 10))
    excludes = [re.compile(p) for p in sub_cfg.get("exclude_url_patterns") or []]
    self_url = _strip_fragment(target.url).rstrip("/")
    found: list[Target] = []
    seen: set[str] = set()
    for link in snap.links:
        url = _strip_fragment(link.url)
        path = urlsplit(url).path.lower()
        if (url.rstrip("/") == self_url or url in seen
                or target.scope not in url
                or path.endswith(attachment_exts)          # PDFs etc. are reported as new links
                or any(p.search(url) for p in excludes)):
            continue
        seen.add(url)
        label = link.text or path.rsplit("/", 1)[-1] or url
        found.append(Target(id=subpage_id(target.id, url), name=f"{target.name} › {label}",
                            url=url, scope=target.scope, parent_id=target.id,
                            relevance_words=target.relevance_words,
                            new_page_note=target.new_page_note))
        if len(found) >= limit:
            break
    return found
