"""The Tiktuv desktop application — ``pythonw -m tiktuv.app``.

Runs the backend and its window in one process.

Why not a browser in ``--app`` mode: that window belongs to Edge or Chrome, so
it carries their icon, their taskbar identity, and their process lifetime. A
window hosted here is ours — it shows the Tiktuv icon, groups under Tiktuv on
the taskbar, and closing it stops the backend.

Rendering still uses the system WebView2 runtime, which ships with Windows 11
and is installed on demand on Windows 10, so nothing extra is bundled.

If the window host is unavailable (no ``pywebview``, no WebView2), the backend
still starts and the UI opens in the default browser rather than failing.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import __version__
from .config import Settings, get_settings
from .dialogs import register_folder_picker
from .logging_conf import configure_logging, get_logger
from .single_instance import RunningInstance, acquire, read_lock, release

log = get_logger("app")

WINDOW_TITLE = "תכתוב"
#: WebView2 keeps its profile here. Giving it a folder of our own is what lets
#: us recognise — and clean up — host processes that belonged to us.
WEBVIEW_STORAGE_DIRNAME = "webview"
#: Groups the window under Tiktuv on the taskbar instead of under "Python".
APP_USER_MODEL_ID = "ai.tiktuv.desktop"

# parents[0]=tiktuv, [1]=backend, [2]=apps, [3]=repository root.
_PACKAGE_DIR = Path(__file__).resolve().parent
_ICON_CANDIDATES = (
    # Bundled next to the package (packaged builds).
    _PACKAGE_DIR / "assets" / "icon.ico",
    # Source checkout: apps/desktop/src-tauri/icons/icon.ico
    _PACKAGE_DIR.parents[1] / "desktop" / "src-tauri" / "icons" / "icon.ico",
    # Alongside a frozen executable.
    Path(getattr(sys, "_MEIPASS", _PACKAGE_DIR)) / "icon.ico",
)


def find_icon() -> Path | None:
    for candidate in _ICON_CANDIDATES:
        if candidate.is_file():
            return candidate
    return None


def set_app_identity() -> None:
    """Claim our own taskbar identity before any window is created."""
    if os.name != "nt":
        return
    with contextlib.suppress(OSError, AttributeError):
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)


def apply_window_icon(window: object) -> bool:
    """Put the Tiktuv icon on the window, its title bar and the taskbar."""
    if os.name != "nt":
        return False
    icon = find_icon()
    if icon is None:
        return False

    # Preferred: set it on the WinForms form pywebview created for us.
    native = getattr(window, "native", None)
    if native is not None and hasattr(native, "Icon"):
        try:
            from System.Drawing import Icon

            native.Icon = Icon(str(icon))
            return True
        except Exception as exc:  # noqa: BLE001 - fall through to the Win32 route
            log.debug("could not set the form icon: %s", exc)

    # Fallback: WM_SETICON on our own top-level window.
    try:
        user32 = ctypes.windll.user32
        hwnd = _own_window_handle()
        if not hwnd:
            return False
        image_icon, from_file, default_size, wm_seticon = 1, 0x10, 0x40, 0x0080
        big = user32.LoadImageW(None, str(icon), image_icon, 0, 0, from_file | default_size)
        small = user32.LoadImageW(None, str(icon), image_icon, 16, 16, from_file)
        user32.SendMessageW(hwnd, wm_seticon, 1, big)
        user32.SendMessageW(hwnd, wm_seticon, 0, small)
        return bool(big or small)
    except OSError as exc:  # pragma: no cover - Win32 specific
        log.debug("could not set the window icon: %s", exc)
        return False


def _own_window_handle() -> int:
    """Handle of the first visible top-level window owned by this process."""
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    handles: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _callback(hwnd: int, _lparam: int) -> bool:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == os.getpid() and user32.IsWindowVisible(hwnd):
            handles.append(hwnd)
        return True

    user32.EnumWindows(callback_type(_callback), 0)
    return int(handles[0]) if handles else 0


# --------------------------------------------------------------------------
# Backend
# --------------------------------------------------------------------------
def find_free_port(preferred: int) -> int:
    """Return ``preferred`` if it is free, otherwise a nearby free port."""
    candidates: list[int] = [preferred, *range(preferred + 1, preferred + 21)]
    for candidate in candidates:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", candidate))
            except OSError:
                continue
            return candidate
    raise RuntimeError(f"no free port near {preferred}")


class BackendThread:
    """Runs uvicorn on a background thread.

    uvicorn skips installing signal handlers when it is not on the main thread,
    which is exactly what we need — the main thread belongs to the window.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._server: object | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        import uvicorn

        from .main import create_app

        config = uvicorn.Config(
            create_app(self._settings),
            host=self._settings.host,
            port=self._settings.port,
            log_config=None,
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._server.run, name="tiktuv-backend", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 15.0) -> None:
        if self._server is not None:
            self._server.should_exit = True  # type: ignore[attr-defined]
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


def wait_until_ready(url: str, *, timeout: float = 90.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/api/health", timeout=3) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError, TimeoutError):
            time.sleep(0.25)
    return False


def health_ok(instance: RunningInstance, *, attempts: int = 1, delay: float = 0.0) -> bool:
    """Probe ``/api/health``, optionally retrying with a pause between tries.

    A cold start can take several seconds before the loop starts answering, so
    a single probe is not enough to tell a healthy-but-slow instance from a
    genuinely hung one. Retrying before declaring it hung avoids killing a live
    app (and forcing a second, slower launch) after a double-click.
    """
    for _ in range(max(1, attempts)):
        try:
            with urllib.request.urlopen(f"{instance.base_url}/api/health", timeout=3) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError, TimeoutError):
            pass
        if delay:
            time.sleep(delay)
    return False


def _terminate_pid(pid: int) -> None:
    """Best-effort kill of a hung previous instance. Never raises, and never
    kills a process that is not provably a Tiktuv instance.

    A stale lock file can carry a PID that the OS has since recycled, so this
    checks the command line first and skips anything unrelated — killing the
    wrong process would be far worse than the hang this is meant to clear.
    """
    if pid <= 0 or pid == os.getpid():
        return
    try:
        import psutil
    except ImportError:  # pragma: no cover - psutil is a hard dependency
        return
    try:
        process = psutil.Process(pid)
        cmdline = " ".join(process.cmdline() or []).lower()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.Error):
        # If we cannot inspect it we cannot be sure it is ours, so leave it.
        log.warning("could not inspect a stale instance; leaving it alone", extra={"pid": pid})
        return
    if "tiktuv" not in cmdline:
        log.warning(
            "refusing to kill a non-Tiktuv process named by a stale lock",
            extra={"pid": pid, "cmdline": cmdline},
        )
        return
    try:
        process.terminate()
        try:
            process.wait(timeout=3)
        except psutil.TimeoutExpired:
            process.kill()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.Error):  # pragma: no cover
        pass


def clear_orphaned_webviews(storage_path: Path) -> int:
    """Kill WebView2 host processes left behind by a previous run.

    WebView2 renders in separate ``msedgewebview2.exe`` processes. A clean exit
    takes them with it, but a force-quit — Task Manager, a crash, a killed
    parent — orphans them, and they keep an exclusive lock on the profile
    folder. The next launch then starts the backend normally and simply never
    shows a window, which looks like the application is broken.

    Only processes using *our* profile folder are touched, so another
    application's WebView2 is never affected. The single-instance lock is held
    by the time this runs, so anything still using that folder is by definition
    an orphan.
    """
    if os.name != "nt":
        return 0
    try:
        import psutil
    except ImportError:  # pragma: no cover - psutil is a hard dependency
        return 0

    marker = str(storage_path).lower()
    killed = 0
    for process in psutil.process_iter(["name", "cmdline"]):
        try:
            if (process.info.get("name") or "").lower() != "msedgewebview2.exe":
                continue
            cmdline = " ".join(process.info.get("cmdline") or []).lower()
            if marker not in cmdline:
                continue
            process.kill()
            killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.Error):
            continue

    if killed:
        log.warning(
            "cleared orphaned WebView2 processes from a previous run",
            extra={"count": killed, "storage_path": str(storage_path)},
        )
    return killed


# --------------------------------------------------------------------------
def open_window(url: str, storage_path: Path | None = None) -> bool:
    """Open the application window. False if no window host is available."""
    try:
        import webview
    except ImportError:
        log.warning("pywebview is not installed; falling back to the default browser")
        return False

    window = webview.create_window(
        WINDOW_TITLE,
        url,
        width=1280,
        height=860,
        min_size=(960, 640),
        text_select=True,
    )

    # Let the rest of the app open native folder dialogs on this window.
    def _pick_folder(initial: str | None) -> str | None:
        result = window.create_file_dialog(
            webview.FOLDER_DIALOG, directory=initial or ""
        )
        # pywebview returns a tuple of selections, or None on cancel.
        if not result:
            return None
        return str(result[0]) if isinstance(result, list | tuple) else str(result)

    register_folder_picker(_pick_folder)

    icon = find_icon()
    if icon is None:
        log.warning(
            "application icon not found; the window will use the default",
            extra={"searched": [str(path) for path in _ICON_CANDIDATES]},
        )

    def _decorate() -> None:
        # The form only exists once the GUI loop is running, so poll briefly
        # rather than racing it. Logs once at the end, not once per attempt.
        if icon is None:
            return
        for _ in range(40):
            time.sleep(0.25)
            if apply_window_icon(window):
                log.info("window icon applied", extra={"icon": str(icon)})
                return
        log.warning("could not apply the window icon", extra={"icon": str(icon)})

    threading.Thread(target=_decorate, daemon=True).start()

    try:
        # A profile folder of our own, so orphaned host processes can be
        # identified and cleaned up on the next launch.
        webview.start(
            gui="edgechromium" if os.name == "nt" else None,
            storage_path=str(storage_path) if storage_path else None,
            private_mode=storage_path is None,
        )
    except Exception as exc:  # noqa: BLE001 - any GUI failure falls back
        log.error("could not open the application window", extra={"error": str(exc)})
        return False
    finally:
        register_folder_picker(None)
    return True


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--version" in args:
        print(f"Tiktuv {__version__}")
        return 0

    set_app_identity()
    settings = get_settings()
    configure_logging(
        level=settings.log_level,
        json_output=settings.log_json,
        log_dir=settings.logs_dir,
        redact=settings.redact_paths_in_errors,
    )

    # Attach to an instance that is already serving instead of starting a
    # second backend on the same data directory.
    existing = read_lock(settings)
    if existing is not None:
        if health_ok(existing, attempts=4, delay=1.5):
            log.info("attaching to the running backend", extra={"port": existing.port})
            opened = open_window(existing.base_url)
            if not opened:
                _open_in_browser(existing.base_url)
            return 0
        # The lock names a live PID that is not answering /api/health — a hung
        # previous launch. Without clearing it, acquire() refuses to start and
        # pythonw exits with nothing on screen, which looks like a dead icon.
        log.warning(
            "clearing a hung instance that still holds the lock",
            extra={"pid": existing.pid, "port": existing.port},
        )
        _terminate_pid(existing.pid)
        (settings.data_dir / "instance.lock").unlink(missing_ok=True)

    settings.port = find_free_port(settings.port)
    try:
        acquire(settings)
    except RuntimeError as exc:
        log.error("could not start", extra={"detail": str(exc)})
        return 3

    storage_path = settings.data_dir / WEBVIEW_STORAGE_DIRNAME
    storage_path.mkdir(parents=True, exist_ok=True)
    # Clearing orphaned WebView2 hosts enumerates every process on the machine,
    # so run it in parallel with the backend boot instead of stacking the two.
    cleanup = threading.Thread(
        target=clear_orphaned_webviews, args=(storage_path,), daemon=True
    )
    cleanup.start()

    backend = BackendThread(settings)
    url = f"http://{settings.host}:{settings.port}"
    try:
        backend.start()
        if not wait_until_ready(url):
            log.error("the backend did not become ready")
            return 2
        cleanup.join(timeout=10.0)
        log.info("desktop app ready", extra={"version": __version__, "url": url})

        if not open_window(url, storage_path):
            _open_in_browser(url)
            # No window to wait on: stay alive until the backend stops.
            while backend.alive:
                time.sleep(1)
    finally:
        backend.stop()
        release(settings)
        log.info("desktop app stopped")
    return 0


def _open_in_browser(url: str) -> None:
    import webbrowser

    log.info("opening the interface in the default browser", extra={"url": url})
    webbrowser.open(url)


if __name__ == "__main__":
    sys.exit(main())
