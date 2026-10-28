from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import imaging
from .config import IMAGE_EXTENSIONS, local_data_dir
from .store import Store, folder_key

_SORT_RE = re.compile(r"(\d+)")


def natural_key(name: str) -> list[tuple[int, Any]]:
    parts = _SORT_RE.split(name.casefold())
    key: list[tuple[int, Any]] = []
    for part in parts:
        if not part:
            continue
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part))
    return key


def file_id(name: str) -> str:
    return hashlib.sha1(name.casefold().encode("utf-8", "surrogatepass")).hexdigest()[:16]


@dataclass(frozen=True)
class ImageItem:
    id: str
    path: Path
    name: str
    mtime: float
    size: int

    @classmethod
    def from_path(cls, path: Path) -> "ImageItem":
        stat = path.stat()
        return cls(
            id=file_id(path.name),
            path=path,
            name=path.name,
            mtime=stat.st_mtime,
            size=stat.st_size,
        )

    def stat(self) -> os.stat_result:
        return self.path.stat()


class LibraryError(Exception):
    pass


class UndoError(LibraryError):
    pass


class Library:
    """The open folder, its image list and the reversible delete queue."""

    def __init__(self, store: Store | None = None) -> None:
        self.store = store or Store()
        self._lock = threading.RLock()
        self.folder: Path | None = None
        self.items: list[ImageItem] = []
        self._meta_cache: dict[tuple[str, int], dict[str, Any]] = {}
        self.trash_dir = local_data_dir() / "trash"
        self.trash_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ scanning
    def _scan(self, folder: Path) -> list[ImageItem]:
        items: list[ImageItem] = []
        try:
            entries = list(os.scandir(folder))
        except OSError as exc:
            raise LibraryError(f"无法读取这个文件夹：{exc.strerror or exc}") from exc
        for entry in entries:
            try:
                if not entry.is_file(follow_symlinks=True):
                    continue
                suffix = Path(entry.name).suffix.casefold()
                if suffix not in IMAGE_EXTENSIONS:
                    continue
                path = Path(entry.path)
                items.append(ImageItem.from_path(path))
            except OSError:
                continue
        items.sort(key=lambda item: natural_key(item.name))
        return items

    def open_folder(self, folder: str | Path) -> dict[str, Any]:
        path = Path(os.path.abspath(os.path.expanduser(str(folder))))
        if not path.is_dir():
            raise LibraryError("这个文件夹不存在，或已经被移动。")
        scanned = self._scan(path)
        with self._lock:
            self.folder = path
            self.items = scanned
            self._meta_cache.clear()
            self.store.remember_folder(path, len(scanned))
            return self._session_payload()

    def rescan(self) -> dict[str, Any]:
        with self._lock:
            if not self.folder:
                raise LibraryError("还没有打开文件夹。")
            path = self.folder
        scanned = self._scan(path)
        with self._lock:
            if not self.folder or folder_key(self.folder) != folder_key(path):
                raise LibraryError("文件夹已经切换，请重试。")
            self.items = scanned
            self._meta_cache.clear()
            self.store.remember_folder(path, len(scanned))
            return self._session_payload()

    # --------------------------------------------------------------- access
    def _session_payload(self) -> dict[str, Any]:
        assert self.folder is not None
        images = [
            {
                "id": item.id,
                "name": item.name,
                "m": int(item.mtime * 1000),
                "size": item.size,
                "ext": item.path.suffix.lstrip(".").lower(),
            }
            for item in self.items
        ]
        return {
            "folder": str(self.folder),
            "folderName": self.folder.name or str(self.folder),
            "count": len(images),
            "images": images,
            "undoCount": self.store.undo_count(self.folder),
        }

    def state(self) -> dict[str, Any]:
        with self._lock:
            session = self._session_payload() if self.folder else None
        return {
            "recent": self.store.recent(),
            "session": session,
            "trashDir": str(self.trash_dir),
        }

    def get_item(self, image_id: str) -> ImageItem:
        with self._lock:
            for item in self.items:
                if item.id == image_id:
                    return item
        raise LibraryError("这张照片不在当前列表中。")

    def get_meta(self, image_id: str) -> dict[str, Any]:
        item = self.get_item(image_id)
        key = (item.id, int(item.mtime * 1000))
        with self._lock:
            cached = self._meta_cache.get(key)
        if cached is not None:
            return cached
        payload = imaging.metadata_for(item)
        with self._lock:
            self._meta_cache[key] = payload
        return payload

    def thumbnail_path(self, image_id: str) -> tuple[Path, ImageItem]:
        item = self.get_item(image_id)
        return imaging.thumbnail_for(item), item

    def preview(self, image_id: str) -> tuple[bytes, ImageItem]:
        item = self.get_item(image_id)
        return imaging.preview_bytes(item), item

    # --------------------------------------------------------------- delete
    def delete(self, image_id: str) -> dict[str, Any]:
        with self._lock:
            if not self.folder:
                raise LibraryError("还没有打开文件夹。")
            index = next(
                (i for i, item in enumerate(self.items) if item.id == image_id), None
            )
            if index is None:
                raise LibraryError("这张照片不在当前列表中。")
            item = self.items[index]
            original = item.path
            if not original.exists():
                raise LibraryError("文件已经被移动或删除，请重新扫描文件夹。")

            stamp = time.strftime("%Y%m%d-%H%M%S")
            safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", item.name)
            destination = self.trash_dir / f"{stamp}_{uuid.uuid4().hex[:8]}_{safe_name}"
            entry = {
                "id": uuid.uuid4().hex,
                "imageId": item.id,
                "folder": str(self.folder),
                "original": str(original),
                "trash": str(destination),
                "name": item.name,
                "deletedAt": time.time(),
                "mtime": item.mtime,
                "size": item.size,
            }
            # Journal first. If the process dies between the journal write and
            # the move, nothing is lost; if the move fails we remove the entry.
            try:
                self.store.push_undo(entry)
            except OSError as exc:
                raise LibraryError(f"无法写入撤回记录，照片没有移动：{exc}") from exc
            try:
                shutil.move(str(original), str(destination))
            except OSError as exc:
                self.store.remove_undo(entry["id"])
                raise LibraryError(f"删除失败，照片仍在原处：{exc.strerror or exc}") from exc

            self.items.pop(index)
            self._meta_cache.pop((item.id, int(item.mtime * 1000)), None)
            try:
                self.store.remember_folder(self.folder, len(self.items))
            except OSError:
                pass
            return {
                "deleted": {
                    "id": item.id,
                    "name": item.name,
                    "index": index,
                },
                "session": self._session_payload(),
            }

    def undo(self) -> dict[str, Any]:
        with self._lock:
            if not self.folder:
                raise LibraryError("还没有打开文件夹。")
            entry = self.store.last_undo(self.folder)
            if not entry:
                raise UndoError("没有可以撤回的删除。")
            trash_path = Path(entry["trash"])
            if not trash_path.is_file():
                self.store.remove_undo(entry["id"])
                raise UndoError("回收站中的文件已经不在了，无法恢复。")
            original = Path(entry["original"])
            try:
                original.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise UndoError(f"无法恢复：{exc.strerror or exc}") from exc

            destination = original
            if destination.exists():
                stem, suffix = original.stem, original.suffix
                for counter in range(1, 1000):
                    marker = " (恢复)" if counter == 1 else f" (恢复 {counter})"
                    candidate = original.with_name(f"{stem}{marker}{suffix}")
                    if not candidate.exists():
                        destination = candidate
                        break
                else:
                    raise UndoError("无法恢复：原文件名已被占用。")

            try:
                shutil.move(str(trash_path), str(destination))
            except OSError as exc:
                raise UndoError(f"恢复失败：{exc.strerror or exc}") from exc

            self.store.remove_undo(entry["id"])
            restored = ImageItem.from_path(destination)
            self.items.append(restored)
            self.items.sort(key=lambda item: natural_key(item.name))
            index = next(
                (i for i, item in enumerate(self.items) if item.id == restored.id), 0
            )
            self.store.remember_folder(self.folder, len(self.items))
            return {
                "restored": {
                    "id": restored.id,
                    "name": restored.name,
                    "index": index,
                },
                "session": self._session_payload(),
            }

    # -------------------------------------------------------------- actions
    def reveal(self, image_id: str | None = None) -> None:
        if image_id:
            item = self.get_item(image_id)
            try:
                subprocess.Popen(["explorer", "/select,", str(item.path)])
                return
            except OSError as exc:
                raise LibraryError(f"无法打开资源管理器：{exc}") from exc
        with self._lock:
            if not self.folder:
                raise LibraryError("还没有打开文件夹。")
            try:
                os.startfile(str(self.folder))  # type: ignore[attr-defined]
            except OSError as exc:
                raise LibraryError(f"无法打开文件夹：{exc}") from exc
