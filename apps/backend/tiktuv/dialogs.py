"""Native file dialogs, when a window host is available.

The backend cannot open a window on its own — it may be running headless, in a
browser-fallback session, or under pytest. So the desktop host registers a
picker at startup and everything else asks through here.

Keeping this behind a registry rather than importing the GUI toolkit directly
means the API module stays importable with no display, which is what lets the
whole test suite run.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from .logging_conf import get_logger

log = get_logger("dialogs")

#: Returns the chosen directory, or None when the user cancels.
FolderPicker = Callable[[str | None], str | None]

_picker: FolderPicker | None = None
#: One dialog at a time. A second modal dialog on the same window would either
#: be ignored or wedge the UI thread.
_lock = threading.Lock()


def register_folder_picker(picker: FolderPicker | None) -> None:
    global _picker
    _picker = picker
    log.info("folder picker %s", "registered" if picker else "cleared")


def is_available() -> bool:
    return _picker is not None


def pick_folder(initial: str | None = None) -> str | None:
    """Open a native folder chooser and return the selection.

    Returns ``None`` when the user cancels, and raises nothing on failure —
    a dialog that will not open must not take a request down with it.
    """
    picker = _picker
    if picker is None:
        return None

    # Only sanity-check the starting directory; the user is free to navigate
    # anywhere from there, and whatever they pick is validated on use.
    start: str | None = None
    if initial:
        candidate = Path(initial).expanduser()
        if candidate.is_dir():
            start = str(candidate)
        elif candidate.parent.is_dir():
            start = str(candidate.parent)

    if not _lock.acquire(blocking=False):
        log.info("a folder dialog is already open")
        return None
    try:
        chosen = picker(start)
    except Exception as exc:  # noqa: BLE001 - a GUI failure is not a crash
        log.error("folder dialog failed", extra={"error": str(exc)})
        return None
    finally:
        _lock.release()

    if not chosen:
        return None
    return str(Path(chosen))
