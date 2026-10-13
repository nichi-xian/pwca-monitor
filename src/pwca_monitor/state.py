"""Read/write the monitor state JSON. Writes are atomic (temp file + rename)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .models import PageSnapshot

SCHEMA_VERSION = 1
MAX_NOTIFIED_HASHES = 50


class StateError(Exception):
    pass


class State:
    def __init__(self, data: dict | None = None):
        data = data or {}
        self.data = {
            "schema_version": SCHEMA_VERSION,
            "pages": data.get("pages", {}),
            "notified": data.get("notified", {}),
            "errors": data.get("errors", {}),
            "subpages": data.get("subpages", {}),
            "last_heartbeat_at": data.get("last_heartbeat_at"),
        }
        self.dirty = False

    # --- pages
    def get_snapshot(self, target_id: str) -> PageSnapshot | None:
        d = self.data["pages"].get(target_id)
        return PageSnapshot.from_dict(d) if d else None

    def set_snapshot(self, target_id: str, snap: PageSnapshot) -> None:
        self.data["pages"][target_id] = snap.to_dict()
        self.dirty = True

    # --- duplicate-notification suppression
    def was_notified(self, target_id: str, diff_hash: str) -> bool:
        return diff_hash in self.data["notified"].get(target_id, [])

    def mark_notified(self, target_id: str, diff_hash: str) -> None:
        lst = self.data["notified"].setdefault(target_id, [])
        if diff_hash not in lst:
            lst.append(diff_hash)
            del lst[:-MAX_NOTIFIED_HASHES]
            self.dirty = True

    # --- error tracking
    def get_error(self, target_id: str) -> dict | None:
        return self.data["errors"].get(target_id)

    def set_error(self, target_id: str, err: dict) -> None:
        self.data["errors"][target_id] = err
        self.dirty = True

    def clear_error(self, target_id: str) -> None:
        if self.data["errors"].pop(target_id, None) is not None:
            self.dirty = True

    # --- subpages (pages linked from an event page)
    def subpages_initialized(self, parent_id: str) -> bool:
        return parent_id in self.data["subpages"]

    def set_subpages(self, parent_id: str, url_to_id: dict[str, str]) -> None:
        """Record the current subpage set and drop state for subpages no longer linked."""
        old = self.data["subpages"].get(parent_id)
        if old == url_to_id:
            return
        for sid in set((old or {}).values()) - set(url_to_id.values()):
            for key in ("pages", "notified", "errors"):
                self.data[key].pop(sid, None)
        self.data["subpages"][parent_id] = url_to_id
        self.dirty = True

    def heartbeat(self, now_iso: str) -> None:
        """Update at most once a day (so the state branch does not get a commit every 5 minutes)."""
        last = self.data.get("last_heartbeat_at") or ""
        if last[:10] != now_iso[:10]:
            self.data["last_heartbeat_at"] = now_iso
            self.dirty = True


def load_state(path: str | Path) -> State:
    path = Path(path)
    if not path.exists():
        return State()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise StateError(f"Cannot read state file: {path}: {type(e).__name__}: {e}") from e
    if not isinstance(data, dict):
        raise StateError(f"Invalid state file format: {path}")
    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        raise StateError(f"Unsupported state schema_version: {version!r}")
    return State(data)


def save_state(state: State, path: str | Path) -> None:
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".state-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state.data, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    except OSError as e:
        raise StateError(f"Cannot write state file: {path}: {e}") from e
    state.dirty = False
