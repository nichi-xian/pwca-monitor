"""Accommodation / booking keyword detection. Only added/changed text is checked."""

from __future__ import annotations

import re

from .models import DiffResult

PRIORITY_LODGING = "宿泊関連の可能性"
PRIORITY_NORMAL = "通常更新"


def _compile(keyword: str) -> re.Pattern:
    kw = keyword.strip().lower()
    # Treat spaces and hyphens as equivalent ("check-in" == "check in")
    parts = [re.escape(p) for p in re.split(r"[\s\-]+", kw) if p]
    body = r"[\s\-]*".join(parts)
    if kw.isascii():
        return re.compile(rf"(?<![a-z0-9]){body}(?![a-z0-9])", re.IGNORECASE)
    return re.compile(body, re.IGNORECASE)


class KeywordClassifier:
    def __init__(self, keywords: list[str]):
        self.patterns = [(k, _compile(k)) for k in keywords]

    def find(self, texts: list[str]) -> list[str]:
        hits: list[str] = []
        for kw, pat in self.patterns:
            if any(pat.search(t) for t in texts) and kw not in hits:
                hits.append(kw)
        return hits

    def classify(self, diff: DiffResult) -> tuple[str, list[str]]:
        texts = list(diff.added_lines)
        texts += [l.text for l in diff.added_links]
        texts += [n.text for _, n in diff.changed_links]
        texts += [l.text for l in diff.added_attachments]
        texts += diff.heading_added
        if diff.title_change:
            texts.append(diff.title_change[1])
        hits = self.find(texts)
        return (PRIORITY_LODGING if hits else PRIORITY_NORMAL), hits
