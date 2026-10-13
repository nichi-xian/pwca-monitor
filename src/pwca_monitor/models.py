"""Shared data structures."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Link:
    text: str
    url: str

    def to_dict(self) -> dict:
        return {"text": self.text, "url": self.url}


@dataclass
class Target:
    id: str
    name: str
    url: str
    enabled: bool = True
    scope: str | None = None       # key identifying pages of the same event (usually the slug)
    parent_id: str | None = None   # set for subpages: id of the event page that links to it


@dataclass
class FetchResult:
    """Result of an HTTP fetch. ok=False always means failure."""

    url: str
    ok: bool
    status: int | None = None
    final_url: str | None = None
    content_type: str | None = None
    html: str | None = None
    fetched_at: str = ""
    error_kind: str | None = None   # e.g. "http_403", "timeout", "content_type"
    error_message: str | None = None


@dataclass
class PageSnapshot:
    """Normalized page content (the unit stored in state)."""

    url: str
    final_url: str
    title: str
    headings: list[str]
    lines: list[str]
    links: list[Link]
    attachments: list[Link]
    content_hash: str
    fetched_at: str

    @property
    def normalized_text(self) -> str:
        return "\n".join(self.lines)

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "final_url": self.final_url,
            "title": self.title,
            "headings": self.headings,
            "normalized_text": self.normalized_text,
            "links": [l.to_dict() for l in self.links],
            "attachments": [l.to_dict() for l in self.attachments],
            "content_hash": self.content_hash,
            "last_success_at": self.fetched_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PageSnapshot":
        text = d.get("normalized_text", "")
        return cls(
            url=d["url"],
            final_url=d.get("final_url", d["url"]),
            title=d.get("title", ""),
            headings=list(d.get("headings", [])),
            lines=text.split("\n") if text else [],
            links=[Link(**l) for l in d.get("links", [])],
            attachments=[Link(**l) for l in d.get("attachments", [])],
            content_hash=d["content_hash"],
            fetched_at=d.get("last_success_at", ""),
        )


@dataclass
class DiffResult:
    added_lines: list[str] = field(default_factory=list)
    removed_lines: list[str] = field(default_factory=list)
    added_links: list[Link] = field(default_factory=list)
    removed_links: list[Link] = field(default_factory=list)
    changed_links: list[tuple[Link, Link]] = field(default_factory=list)  # (old, new)
    added_attachments: list[Link] = field(default_factory=list)
    title_change: tuple[str, str] | None = None
    heading_added: list[str] = field(default_factory=list)
    heading_removed: list[str] = field(default_factory=list)
    unified_diff: str = ""
    diff_hash: str = ""

    @property
    def has_changes(self) -> bool:
        return bool(
            self.added_lines or self.removed_lines or self.added_links
            or self.removed_links or self.changed_links or self.added_attachments
            or self.title_change or self.heading_added or self.heading_removed
        )


@dataclass
class Notification:
    kind: str          # "update" | "error" | "recovery" | "test"
    priority: str      # "宿泊関連の可能性" | "通常更新" | "監視エラー" | "復旧" | "テスト"
    text: str
