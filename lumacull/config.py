from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "光筛 LumaCull"
APP_NAME_EN = "LumaCull"
VERSION = "1.0.0"
APP_ID = "LumaCull.PhotoDesk.1"

IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
    ".gif",
    ".heic",
    ".heif",
}

PREVIEW_EDGE = 2800
THUMB_SIZE = (420, 300)
JPEG_QUALITY_PREVIEW = 90
JPEG_QUALITY_THUMB = 82


def resource_dir() -> Path:
    """Directory containing the web/ and assets/ folders."""
    frozen_base = getattr(sys, "_MEIPASS", None)
    if frozen_base:
        return Path(frozen_base)
    return Path(__file__).resolve().parent.parent


def web_dir() -> Path:
    return resource_dir() / "web"


def assets_dir() -> Path:
    return resource_dir() / "assets"


def icon_path() -> Path:
    return assets_dir() / "icon.ico"


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    return Path(value).expanduser() if value else None


def app_data_dir() -> Path:
    override = _env_path("LUMACULL_DATA_DIR")
    if override:
        return override
    if os.name == "nt":
        root = os.environ.get("APPDATA")
        if root:
            return Path(root) / APP_NAME_EN
    return Path.home() / ".lumacull"


def local_data_dir() -> Path:
    override = _env_path("LUMACULL_LOCAL_DIR")
    if override:
        return override
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA")
        if root:
            return Path(root) / APP_NAME_EN
    return Path.home() / ".lumacull"


def ensure_dirs() -> None:
    for directory in (
        app_data_dir(),
        local_data_dir(),
        local_data_dir() / "cache" / "thumbs",
        local_data_dir() / "trash",
    ):
        directory.mkdir(parents=True, exist_ok=True)


def log_path() -> Path:
    return local_data_dir() / "lumacull.log"
