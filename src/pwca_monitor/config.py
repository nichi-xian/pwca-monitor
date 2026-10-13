"""Load and validate config/targets.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import yaml

from .models import Target


class ConfigError(Exception):
    pass


@dataclass
class Config:
    base_url: str
    events_path: str
    targets: list[Target]
    request: dict
    parser: dict
    keywords: list[str]
    notify: dict
    subpages: dict = field(default_factory=dict)

    @property
    def enabled_targets(self) -> list[Target]:
        return [t for t in self.targets if t.enabled]


def build_target_url(base_url: str, events_path: str, slug: str) -> str:
    """Build base_url + events_path + slug, tolerating extra or missing slashes."""
    directory = "/" + events_path.strip("/") + "/" if events_path.strip("/") else "/"
    return urljoin(base_url.rstrip("/") + "/", directory.lstrip("/") + slug.strip("/"))


def load_config(path: str | Path) -> Config:
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid config file YAML: {path}: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigError("The top level of the config file must be a mapping")

    site = raw.get("site") or {}
    base_url = os.environ.get("PWCA_BASE_URL") or site.get("base_url")
    events_path = os.environ.get("PWCA_EVENTS_PATH") or site.get("events_path", "/")
    if not base_url or not str(base_url).startswith(("http://", "https://")):
        raise ConfigError("site.base_url must be an http(s) URL")

    targets: list[Target] = []
    seen: set[str] = set()
    for i, t in enumerate(raw.get("targets") or []):
        tid = t.get("id")
        if not tid:
            raise ConfigError(f"targets[{i}] has no id")
        if tid in seen:
            raise ConfigError(f"Duplicate target id: {tid}")
        seen.add(tid)
        if t.get("url"):
            url = t["url"]
        elif t.get("slug"):
            url = build_target_url(base_url, events_path, t["slug"])
        else:
            raise ConfigError(f"targets[{tid}] needs either slug or url")
        scope = (t.get("subpage_scope") or (t.get("subpages") or {}).get("subpage_scope") or t.get("slug")
                 or urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1] or None)
        sub = t.get("subpages") or {}
        relevance = sub.get("notify_only_if_contains")
        targets.append(Target(
            id=tid, name=t.get("name", tid), url=url,
            enabled=bool(t.get("enabled", True)), scope=scope,
            parser=t.get("parser"),
            notify_page_changes=bool(t.get("notify_page_changes", True)),
            max_subpages=sub.get("max"),
            subpage_fetch_once=bool(sub.get("fetch_once", False)),
            relevance_words=[str(w) for w in relevance] if relevance else None,
            new_page_note=sub.get("new_page_note"),
        ))
    if not targets:
        raise ConfigError("targets is empty")

    keywords = [str(k) for k in (raw.get("keywords") or []) if str(k).strip()]
    return Config(
        base_url=base_url,
        events_path=events_path,
        targets=targets,
        request=raw.get("request") or {},
        parser=raw.get("parser") or {},
        keywords=keywords,
        notify=raw.get("notify") or {},
        subpages=raw.get("subpages") or {},
    )
