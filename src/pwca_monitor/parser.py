"""Extract and normalize text, links and headings from HTML. HTML is untrusted input and is never executed."""

from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Comment, NavigableString

from .models import Link, PageSnapshot

ALWAYS_REMOVE = ["script", "style", "noscript", "template", "svg", "iframe",
                 "head", "header", "nav", "footer", "form"]
_WS = re.compile(r"\s+")
# Elements that start a line. Inline elements (span, a, b, ...) stay on the same line.
BLOCK_TAGS = ["address", "article", "aside", "blockquote", "br", "button", "dd", "details",
              "div", "dl", "dt", "figcaption", "figure", "h1", "h2", "h3", "h4", "h5", "h6",
              "hr", "li", "main", "ol", "p", "pre", "section", "summary", "table", "td", "th",
              "tr", "ul", "ui-disclosure", "ui-disclosure-group"]
_BREAK = "\x00"


class ParseError(Exception):
    """Extraction root not found, etc. Indicates a page structure change or an error page."""


def normalize_ws(s: str) -> str:
    return _WS.sub(" ", s).strip()


def clean_url(href: str, base: str, strip_params: set[str]) -> str | None:
    href = (href or "").strip()
    if not href or href.startswith(("javascript:", "mailto:", "tel:", "data:")):
        return None
    absolute = urljoin(base, href)
    parts = urlsplit(absolute)
    if parts.scheme not in ("http", "https"):
        return None
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() not in strip_params]
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path,
                       urlencode(query), parts.fragment))


def parse_page(html: str, url: str, final_url: str, fetched_at: str,
               parser_cfg: dict) -> PageSnapshot:
    soup = BeautifulSoup(html, "html.parser")
    title = normalize_ws(soup.title.get_text()) if soup.title else ""

    selectors = parser_cfg.get("content_selector")
    if isinstance(selectors, str):
        selectors = [selectors]
    if selectors:
        root = next((r for r in (soup.select_one(sel) for sel in selectors) if r is not None), None)
    else:
        root = soup.body or soup
    if root is None:
        raise ParseError(f"Extraction root {selectors} not found "
                         "(page structure change, login page, or error page?)")

    for c in root.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()
    for tag in root.find_all(ALWAYS_REMOVE):
        tag.decompose()
    for sel in parser_cfg.get("exclude_selectors") or []:
        for tag in root.select(sel):
            tag.decompose()

    headings = [h for h in (normalize_ws(t.get_text(" ")) for t in root.find_all(["h1", "h2", "h3"])) if h]

    strip_params = {p.lower() for p in parser_cfg.get("strip_query_params") or []}
    exts = tuple(e.lower() for e in parser_cfg.get("attachment_extensions") or [".pdf"])
    links: list[Link] = []
    seen = set()
    for a in root.find_all("a", href=True):
        u = clean_url(a["href"], final_url or url, strip_params)
        if not u:
            continue
        link = Link(normalize_ws(a.get_text(" ")) or normalize_ws(a.get("title", "")), u)
        if link not in seen:
            seen.add(link)
            links.append(link)
    attachments = [l for l in links if urlsplit(l.url).path.lower().endswith(exts)]

    lines = _block_lines(root)

    payload = json.dumps({"title": title, "headings": headings, "lines": lines,
                          "links": [l.to_dict() for l in links]},
                         ensure_ascii=False, sort_keys=True)
    content_hash = "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return PageSnapshot(url=url, final_url=final_url or url, title=title, headings=headings,
                        lines=lines, links=links, attachments=attachments,
                        content_hash=content_hash, fetched_at=fetched_at)


def _block_lines(root) -> list[str]:
    """Split text into lines at block-element boundaries; collapse whitespace/newlines inside text nodes."""
    for tag in root.find_all(BLOCK_TAGS):
        tag.insert_before(NavigableString(_BREAK))
        tag.insert_after(NavigableString(_BREAK))
    raw = "".join(str(s) for s in root.find_all(string=True))
    return [l for l in (normalize_ws(x) for x in raw.split(_BREAK)) if l]
