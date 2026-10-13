"""Diff two normalized snapshots."""

from __future__ import annotations

import difflib
import hashlib
import json

from .models import DiffResult, Link, PageSnapshot


def diff_snapshots(old: PageSnapshot, new: PageSnapshot) -> DiffResult:
    r = DiffResult()
    sm = difflib.SequenceMatcher(a=old.lines, b=new.lines, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op in ("delete", "replace"):
            r.removed_lines.extend(old.lines[i1:i2])
        if op in ("insert", "replace"):
            r.added_lines.extend(new.lines[j1:j2])

    old_links, new_links = set(old.links), set(new.links)
    old_by_text = {l.text: l for l in old.links if l.text}
    old_urls = {l.url for l in old.links}
    for l in new.links:
        if l in old_links:
            continue
        prev = old_by_text.get(l.text)
        if prev is not None and prev.url != l.url and prev not in new_links:
            r.changed_links.append((prev, l))
        elif l.url not in old_urls:
            r.added_links.append(l)
    changed_old = {o for o, _ in r.changed_links}
    new_urls = {l.url for l in new.links}
    r.removed_links = [l for l in old.links
                       if l not in new_links and l not in changed_old and l.url not in new_urls]

    old_att = {a.url for a in old.attachments}
    r.added_attachments = [a for a in new.attachments if a.url not in old_att]

    if old.title != new.title:
        r.title_change = (old.title, new.title)
    r.heading_added = [h for h in new.headings if h not in old.headings]
    r.heading_removed = [h for h in old.headings if h not in new.headings]

    r.unified_diff = "\n".join(difflib.unified_diff(
        old.lines, new.lines, fromfile="before", tofile="after", lineterm="", n=2))

    canonical = json.dumps({
        "added": r.added_lines, "removed": r.removed_lines,
        "links_added": [l.to_dict() for l in r.added_links],
        "links_removed": [l.to_dict() for l in r.removed_links],
        "links_changed": [[o.to_dict(), n.to_dict()] for o, n in r.changed_links],
        "title": r.title_change, "h_add": r.heading_added, "h_rm": r.heading_removed,
        "new_hash": new.content_hash,
    }, ensure_ascii=False, sort_keys=True)
    r.diff_hash = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return r


def is_attachment(link: Link, attachments: list[Link]) -> bool:
    return any(a.url == link.url for a in attachments)
