from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from .config import app_data_dir

MAX_RECENT = 8
MAX_UNDO = 80


def folder_key(path: str | Path) -> str:
    """Case-insensitive comparison key for a folder on Windows."""
    return os.path.normcase(os.path.abspath(os.path.expanduser(str(path))))


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.stem + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass


class Store:
    """Small, crash-safe JSON store for recents and the delete journal."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or app_data_dir()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._settings_path = self.base_dir / "settings.json"
        self._undo_path = self.base_dir / "undo.json"
        self._lock = threading.RLock()
        self._settings = self._load(self._settings_path, {"recent": []})
        self._undo = self._load(self._undo_path, {"entries": []})
        self._normalize()

    # ------------------------------------------------------------------ IO
    def _load(self, path: Path, default: dict[str, Any]) -> dict[str, Any]:
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else default
        except (OSError, ValueError):
            return default

    def _normalize(self) -> None:
        recent = [
            entry
            for entry in self._settings.get("recent", [])
            if isinstance(entry, dict) and entry.get("path")
        ]
        self._settings["recent"] = recent[:MAX_RECENT]
        entries = [
            entry
            for entry in self._undo.get("entries", [])
            if isinstance(entry, dict) and entry.get("id") and entry.get("folder")
        ]
        self._undo["entries"] = entries[-MAX_UNDO:]

    def _save_settings(self) -> None:
        _atomic_write(self._settings_path, self._settings)

    def _save_undo(self) -> None:
        _atomic_write(self._undo_path, self._undo)

    # -------------------------------------------------------------- recents
    def recent(self) -> list[dict[str, Any]]:
        with self._lock:
            entries = sorted(
                self._settings.get("recent", []),
                key=lambda item: item.get("lastOpened", 0),
                reverse=True,
            )
            return [dict(entry) for entry in entries[:MAX_RECENT]]

    def remember_folder(self, path: str | Path, count: int) -> None:
        path_obj = Path(path)
        key = folder_key(path_obj)
        with self._lock:
            recent = [
                entry
                for entry in self._settings.get("recent", [])
                if folder_key(entry.get("path", "")) != key
            ]
            recent.append(
                {
                    "path": str(path_obj),
                    "name": path_obj.name or str(path_obj),
                    "count": int(count),
                    "lastOpened": time.time(),
                }
            )
            recent.sort(key=lambda item: item.get("lastOpened", 0), reverse=True)
            self._settings["recent"] = recent[:MAX_RECENT]
            self._save_settings()

    # ----------------------------------------------------------------- undo
    def _matching_undo(self, folder: str | Path) -> list[dict[str, Any]]:
        key = folder_key(folder)
        return [
            entry
            for entry in self._undo.get("entries", [])
            if folder_key(entry.get("folder", "")) == key
        ]

    def undo_count(self, folder: str | Path) -> int:
        with self._lock:
            return len(self._matching_undo(folder))

    def last_undo(self, folder: str | Path) -> dict[str, Any] | None:
        with self._lock:
            matches = self._matching_undo(folder)
            return dict(matches[-1]) if matches else None

    def push_undo(self, entry: dict[str, Any]) -> None:
        with self._lock:
            entries = self._undo.setdefault("entries", [])
            entries.append(dict(entry))
            self._undo["entries"] = entries[-MAX_UNDO:]
            self._save_undo()

    def remove_undo(self, entry_id: str) -> None:
        with self._lock:
            self._undo["entries"] = [
                entry
                for entry in self._undo.get("entries", [])
                if entry.get("id") != entry_id
            ]
            self._save_undo()
