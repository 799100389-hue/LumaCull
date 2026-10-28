from __future__ import annotations

import hmac
import json
import logging
import mimetypes
import os
import secrets
import subprocess
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .config import APP_NAME, VERSION, web_dir
from .library import Library, LibraryError, UndoError

log = logging.getLogger("lumacull.server")

MAX_BODY = 1_048_576


class LumaServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], token: str, library: Library) -> None:
        self.token = token
        self.library = library
        self.activity_lock = threading.Lock()
        self.client_seen = False
        self.last_activity = time.monotonic()
        self.bye_at: float | None = None
        super().__init__(address, LumaRequestHandler)

    def touch(self) -> None:
        with self.activity_lock:
            self.client_seen = True
            self.last_activity = time.monotonic()
            self.bye_at = None

    def request_close(self) -> None:
        with self.activity_lock:
            self.bye_at = time.monotonic()


def _folder_picker() -> str:
    """Native Windows folder picker used by the browser fallback only."""
    script = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.Application]::EnableVisualStyles()
$owner = New-Object System.Windows.Forms.Form
$owner.Text = '光筛 LumaCull'
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.Opacity = 0
$owner.WindowState = 'Minimized'
$owner.Show()
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = '选择要筛选的照片文件夹'
$dialog.ShowNewFolderButton = $false
$result = $dialog.ShowDialog($owner)
if ($result -eq [System.Windows.Forms.DialogResult]::OK) {
    [Console]::Out.Write($dialog.SelectedPath)
}
$owner.Close()
"""
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-STA",
                "-Command",
                script,
            ],
            capture_output=True,
            timeout=300,
            creationflags=flags,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LibraryError(f"无法打开文件夹选择器：{exc}") from exc
    output = completed.stdout.decode("utf-8", "replace").strip()
    if completed.returncode != 0 and not output:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        raise LibraryError(f"无法打开文件夹选择器：{detail[-300:] or '未知错误'}")
    return output


class LumaRequestHandler(BaseHTTPRequestHandler):
    server: LumaServer
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------- helpers
    def log_message(self, fmt: str, *args: Any) -> None:
        if args and str(args[0]).startswith(("4", "5")):
            log.info("%s - %s", self.address_string(), fmt % args)

    def _url(self) -> urllib.parse.SplitResult:
        return urllib.parse.urlsplit(self.path)

    def _query(self) -> dict[str, list[str]]:
        return urllib.parse.parse_qs(self._url().query, keep_blank_values=True)

    def _supplied_token(self) -> str:
        header = self.headers.get("X-Luma-Token", "")
        if header:
            return header
        return (self._query().get("k") or [""])[0]

    def _authorized(self) -> bool:
        supplied = self._supplied_token()
        return bool(supplied) and hmac.compare_digest(supplied, self.server.token)

    def _json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _error(self, message: str, status: int = 400, code: str = "error") -> None:
        self._json({"ok": False, "error": message, "code": code}, status)

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise LibraryError("请求无效。")
        if length < 0 or length > MAX_BODY:
            raise LibraryError("请求过大。")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise LibraryError("请求格式不正确。") from exc
        if not isinstance(data, dict):
            raise LibraryError("请求格式不正确。")
        return data

    def _send_bytes(
        self,
        body: bytes,
        content_type: str,
        status: int = 200,
        cache: str = "no-store",
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_file(self, path: Path, content_type: str, cache: str = "no-cache") -> None:
        try:
            body = path.read_bytes()
        except OSError:
            self._error("文件不存在。", HTTPStatus.NOT_FOUND)
            return
        self._send_bytes(body, content_type, cache=cache)

    # ---------------------------------------------------------------- verbs
    def do_GET(self) -> None:  # noqa: N802
        route = urllib.parse.unquote(self._url().path)
        try:
            if route == "/api/ping":
                self._json({"ok": True, "app": APP_NAME, "version": VERSION})
                return
            if route in ("/", "/index.html"):
                self._send_index()
                return
            if route.startswith("/static/"):
                self._send_static(route.removeprefix("/static/"))
                return
            if route.startswith("/api/"):
                if not self._authorized():
                    self._error("访问令牌无效，请从光筛启动器打开。", 403, "forbidden")
                    return
                self.server.touch()
                self._handle_api_get(route)
                return
            if route.startswith("/media/"):
                if not self._authorized():
                    self.send_error(403)
                    return
                self.server.touch()
                self._handle_media(route)
                return
            self._error("页面不存在。", 404)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("GET %s failed", self.path)
            self._error(f"服务内部错误：{exc}", 500, "internal")

    def do_POST(self) -> None:  # noqa: N802
        route = urllib.parse.unquote(self._url().path)
        try:
            if not self._authorized():
                self._error("访问令牌无效，请从光筛启动器打开。", 403, "forbidden")
                return
            if not route.startswith("/api/"):
                self._error("接口不存在。", 404)
                return
            if route != "/api/bye":
                self.server.touch()
            self._handle_api_post(route)
        except UndoError as exc:
            self._error(str(exc), 409, "undo_empty")
        except LibraryError as exc:
            self._error(str(exc), 400, "library_error")
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("POST %s failed", self.path)
            self._error(f"服务内部错误：{exc}", 500, "internal")

    # -------------------------------------------------------------- content
    def _send_index(self) -> None:
        index = web_dir() / "index.html"
        try:
            body = index.read_bytes()
        except OSError:
            self._error("界面文件缺失，请重新安装光筛。", 500)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
            "frame-ancestors 'none'",
        )
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_static(self, relative: str) -> None:
        root = web_dir().resolve()
        try:
            target = (root / relative).resolve()
            target.relative_to(root)
        except (ValueError, OSError):
            self._error("页面不存在。", 404)
            return
        if not target.is_file():
            self._error("页面不存在。", 404)
            return
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if target.suffix == ".js":
            content_type = "text/javascript; charset=utf-8"
        elif target.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif target.suffix == ".svg":
            content_type = "image/svg+xml"
        self._send_file(target, content_type, cache="no-cache")

    # ------------------------------------------------------------------ API
    def _handle_api_get(self, route: str) -> None:
        if route == "/api/state":
            self._json({"ok": True, **self.server.library.state()})
            return
        prefix = "/api/meta/"
        if route.startswith(prefix):
            image_id = route[len(prefix) :]
            meta = self.server.library.get_meta(image_id)
            self._json({"ok": True, "meta": meta})
            return
        self._error("接口不存在。", 404)

    def _handle_api_post(self, route: str) -> None:
        if route == "/api/heartbeat":
            self.server.touch()
            self._json({"ok": True})
            return
        if route == "/api/bye":
            self.server.request_close()
            self._json({"ok": True})
            return
        if route == "/api/open-folder":
            data = self._read_json()
            path = str(data.get("path") or "").strip()
            if not path:
                raise LibraryError("请选择一个文件夹。")
            session = self.server.library.open_folder(path)
            self._json({"ok": True, "session": session})
            return
        if route == "/api/pick-folder":
            folder = _folder_picker()
            if not folder:
                self._json({"ok": True, "path": None, "cancelled": True})
                return
            session = self.server.library.open_folder(folder)
            self._json({"ok": True, "path": folder, "session": session})
            return
        if route == "/api/rescan":
            session = self.server.library.rescan()
            self._json({"ok": True, "session": session})
            return
        if route == "/api/delete":
            data = self._read_json()
            image_id = str(data.get("id") or "")
            if not image_id:
                raise LibraryError("没有指定要删除的照片。")
            payload = self.server.library.delete(image_id)
            self._json({"ok": True, **payload})
            return
        if route == "/api/undo":
            payload = self.server.library.undo()
            self._json({"ok": True, **payload})
            return
        if route == "/api/reveal":
            data = self._read_json()
            image_id = str(data.get("id") or "") or None
            self.server.library.reveal(image_id)
            self._json({"ok": True})
            return
        self._error("接口不存在。", 404)

    def _handle_media(self, route: str) -> None:
        parts = route.split("/")
        if len(parts) != 4 or parts[1] != "media":
            self.send_error(404)
            return
        kind, image_id = parts[2], parts[3]
        if kind == "thumb":
            path, item = self.server.library.thumbnail_path(image_id)
            self._send_file(
                path,
                "image/jpeg",
                cache="private, max-age=86400",
            )
            return
        if kind == "preview":
            body, _item = self.server.library.preview(image_id)
            self._send_bytes(
                body,
                "image/jpeg",
                cache="private, max-age=3600",
            )
            return
        self.send_error(404)


def make_server(
    library: Library, token: str | None = None, host: str = "127.0.0.1", port: int = 0
) -> LumaServer:
    token = token or secrets.token_urlsafe(32)
    return LumaServer((host, int(port)), token, library)


def make_app_token() -> str:
    return secrets.token_urlsafe(32)
