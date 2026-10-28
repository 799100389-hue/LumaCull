from __future__ import annotations

import argparse
import ctypes
import logging
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

from .config import (
    APP_ID,
    APP_NAME,
    VERSION,
    ensure_dirs,
    local_data_dir,
    log_path,
)
from .library import Library, LibraryError
from .server import make_server
from .store import Store

log = logging.getLogger("lumacull")

HEARTBEAT_TIMEOUT = 180.0
STARTUP_TIMEOUT = 45.0
BYE_GRACE = 6.0
BROWSER_EXIT_GRACE = 10.0
MUTEX_NAME = "Local\\LumaCull.SingleInstance.1"


def setup_logging(debug: bool = False) -> None:
    ensure_dirs()
    level = logging.DEBUG if debug else logging.INFO
    handlers: list[logging.Handler] = []
    for candidate in (
        log_path(),
        local_data_dir() / f"lumacull-{os.getpid()}.log",
    ):
        try:
            handlers.append(logging.FileHandler(candidate, encoding="utf-8"))
            break
        except OSError:
            continue
    if sys.stderr:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
    if not handlers:
        logging.getLogger().addHandler(logging.NullHandler())


def _message_box(title: str, message: str, error: bool = True) -> None:
    if os.name != "nt":
        return
    try:
        flags = 0x10 if error else 0x40
        ctypes.windll.user32.MessageBoxW(None, message, title, flags)
    except Exception:
        pass


def report_fatal(exc: BaseException) -> None:
    """Last-resort reporting for a windowed build without a console."""
    try:
        log.exception("Fatal startup error")
    except Exception:
        pass
    _message_box(
        APP_NAME,
        f"光筛启动失败：\n\n{exc}\n\n"
        f"日志位于：{local_data_dir()}\n"
        "如果问题持续出现，请把日志文件反馈给开发者。",
        error=True,
    )


def _set_windows_identity() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


def _acquire_single_instance() -> int | None:
    """Keep a second launch from starting a competing server."""
    if os.name != "nt":
        return 1
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        handle = kernel32.CreateMutexW(None, 0, MUTEX_NAME)
        if not handle:
            return 1
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(ctypes.c_void_p(handle))
            return None
        return int(handle)
    except Exception:
        log.debug("Single-instance mutex unavailable", exc_info=True)
        return 1


def _release_single_instance(handle: int | None) -> None:
    if not handle or os.name != "nt":
        return
    try:
        ctypes.WinDLL("kernel32").CloseHandle(ctypes.c_void_p(handle))
    except Exception:
        pass


def _find_browser() -> str | None:
    override = os.environ.get("LUMACULL_BROWSER", "").strip()
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override))
    local = os.environ.get("LOCALAPPDATA")
    program_files = os.environ.get("ProgramFiles")
    program_files_x86 = os.environ.get("ProgramFiles(x86)")
    if local:
        candidates.append(Path(local) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
        candidates.append(Path(local) / "Google" / "Chrome" / "Application" / "chrome.exe")
    if program_files_x86:
        candidates.append(Path(program_files_x86) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
        candidates.append(Path(program_files_x86) / "Google" / "Chrome" / "Application" / "chrome.exe")
    if program_files:
        candidates.append(Path(program_files) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
        candidates.append(Path(program_files) / "Google" / "Chrome" / "Application" / "chrome.exe")
    for name in ("msedge.exe", "chrome.exe"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    for candidate in candidates:
        try:
            if candidate.is_file():
                return str(candidate)
        except OSError:
            continue
    return None


def _window_geometry() -> tuple[int, int, int, int]:
    try:
        user32 = ctypes.windll.user32
        screen_w = int(user32.GetSystemMetrics(0))
        screen_h = int(user32.GetSystemMetrics(1))
    except Exception:
        screen_w, screen_h = 1920, 1080
    if screen_w < 900 or screen_h < 600:
        screen_w, screen_h = max(screen_w, 1280), max(screen_h, 800)
    width = min(1480, max(960, screen_w - 80))
    height = min(940, max(640, screen_h - 100))
    x = max(0, (screen_w - width) // 2)
    y = max(0, (screen_h - height) // 2 - 8)
    return x, y, width, height



def _browser_environment() -> dict[str, str]:
    """Keep bundled DLL folders out of the browser's child-process search path."""
    env = dict(os.environ)
    roots: list[str] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(os.path.normcase(os.path.abspath(meipass)))
    try:
        roots.append(os.path.normcase(os.path.abspath(Path(sys.executable).parent / "_internal")))
    except Exception:
        pass
    roots = list(dict.fromkeys(roots))
    kept: list[str] = []
    removed: list[str] = []
    for raw in env.get("PATH", "").split(os.pathsep):
        if not raw:
            continue
        try:
            normalized = os.path.normcase(os.path.abspath(raw))
        except Exception:
            normalized = os.path.normcase(raw)
        if any(normalized == root or normalized.startswith(root + os.sep) for root in roots):
            removed.append(raw)
        else:
            kept.append(raw)
    env["PATH"] = os.pathsep.join(kept)
    for key in list(env):
        upper = key.upper()
        if upper.startswith("_PYI") or upper in {
            "PYTHONHOME",
            "PYTHONPATH",
            "TCL_LIBRARY",
            "TK_LIBRARY",
        }:
            env.pop(key, None)
    if removed:
        log.debug("Removed bundled paths from browser PATH: %s", removed)
    return env


def _launch_browser(url: str) -> tuple[bool, subprocess.Popen | None]:
    browser = _find_browser()
    if not browser:
        try:
            return bool(webbrowser.open(url)), None
        except Exception:
            return False, None

    profile = local_data_dir() / "browser"
    try:
        profile.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    x, y, width, height = _window_geometry()
    command = [
        browser,
        f"--app={url}",
        f"--user-data-dir={profile}",
        f"--window-size={width},{height}",
        f"--window-position={x},{y}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-mode",
        "--disable-sync",
        # AppContainer renderer isolation can fail when Edge is started by a
        # frozen windowed process under restrictive Windows policies. This is a
        # local-only UI, so keep the other Chromium sandbox layers and turn off
        # only the AppContainer part to avoid an unrendered window.
        "--disable-features=msEdgeFirstRunExperience,msEdgeSidebarV2,msEdgeWorkspaces,RendererAppContainer",
        "--hide-crash-restore-bubble",
    ]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
            env=_browser_environment(),
        )
        log.info("Opened UI with %s (%sx%s at %s,%s)", browser, width, height, x, y)
        return True, process
    except OSError as exc:
        log.warning("Could not launch %s: %s", browser, exc)
        try:
            return bool(webbrowser.open(url)), None
        except Exception:
            return False, None


def _run_desktop(server: object, token: str) -> int:
    host, port = server.server_address[:2]  # type: ignore[attr-defined]
    url = f"http://{host}:{port}/?k={urllib.parse.quote(token)}"
    launched, browser_process = _launch_browser(url)
    if not launched:
        _message_box(
            APP_NAME,
            "无法打开应用窗口。\n\n"
            "请确认电脑上安装了 Microsoft Edge 或 Google Chrome，"
            f"或在浏览器中手动访问：\n{url}",
        )
        return 1

    started = time.monotonic()
    last_loop = started
    browser_seen_alive = False
    browser_exit_at: float | None = None
    stop = threading.Event()
    while not stop.wait(0.5):
        now = time.monotonic()
        # A large gap means the machine slept. Don't treat the paused page as
        # an abandoned window; give it a chance to send a heartbeat on resume.
        if now - last_loop > 5.0:
            last_loop = now
            with server.activity_lock:  # type: ignore[attr-defined]
                server.last_activity = now  # type: ignore[attr-defined]
            continue
        last_loop = now

        with server.activity_lock:  # type: ignore[attr-defined]
            seen = server.client_seen  # type: ignore[attr-defined]
            last = server.last_activity  # type: ignore[attr-defined]
            bye_at = server.bye_at  # type: ignore[attr-defined]
        if bye_at is not None and now - bye_at > BYE_GRACE:
            log.info("UI closed; shutting down")
            break
        if seen and now - last > HEARTBEAT_TIMEOUT:
            log.info("UI heartbeat lost; shutting down")
            break

        if browser_process is not None:
            if browser_process.poll() is None:
                browser_seen_alive = True
                browser_exit_at = None
            elif browser_seen_alive and browser_exit_at is None:
                browser_exit_at = now
            if browser_exit_at is not None and now - browser_exit_at > BROWSER_EXIT_GRACE:
                log.info("Browser process exited; shutting down")
                break

        if not seen and now - started > STARTUP_TIMEOUT:
            log.warning("No UI heartbeat received; shutting down")
            _message_box(
                APP_NAME,
                "应用窗口没有成功连接。\n\n"
                f"请检查 Edge 是否被安全软件拦截，或手动打开：\n{url}",
            )
            break
    server.shutdown()  # type: ignore[attr-defined]
    log.info("Desktop session ended after %.1fs", time.monotonic() - started)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} · 摄影师选片台")
    parser.add_argument("folder", nargs="?", help="启动时直接打开的照片文件夹")
    parser.add_argument("-f", "--folder", dest="folder_option", help="启动时直接打开的照片文件夹")
    parser.add_argument("--serve", action="store_true", help="只启动本地服务（调试用）")
    parser.add_argument("--port", type=int, default=0, help="本地服务端口，0 为自动分配")
    parser.add_argument("--token", default=None, help="固定访问令牌（调试用）")
    parser.add_argument("--debug", action="store_true", help="输出调试日志")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {VERSION}")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.debug)
    _set_windows_identity()
    log.info("Starting %s %s", APP_NAME, VERSION)

    instance = None
    if not args.serve:
        instance = _acquire_single_instance()
        if instance is None:
            _message_box(
                APP_NAME,
                "光筛已经在运行。\n\n请切换到已经打开的窗口；"
                "如果刚刚关闭，请稍等几秒再启动。",
                error=False,
            )
            return 0

    try:
        store = Store()
        library = Library(store=store)
        initial_folder = args.folder_option or args.folder
        if initial_folder:
            try:
                library.open_folder(Path(initial_folder).expanduser())
            except LibraryError as exc:
                log.warning("Could not open initial folder: %s", exc)

        token = args.token or secrets.token_urlsafe(32)
        try:
            server = make_server(library, token=token, port=args.port)
        except OSError as exc:
            _message_box(APP_NAME, f"无法启动本地服务：{exc}")
            return 1

        thread = threading.Thread(target=server.serve_forever, name="lumacull-http", daemon=True)
        thread.start()
        time.sleep(0.25)
        host, port = server.server_address[:2]
        url = f"http://{host}:{port}/?k={urllib.parse.quote(token)}"

        if args.serve:
            print(f"{APP_NAME} {VERSION}")
            print(url)
            print(f"data: {local_data_dir()}")
            try:
                thread.join()
            except KeyboardInterrupt:
                pass
            finally:
                server.shutdown()
            return 0

        try:
            return _run_desktop(server, token)
        finally:
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass
    finally:
        _release_single_instance(instance)


if __name__ == "__main__":
    raise SystemExit(main())
