"""Single-instance guard.

Two backends sharing one data directory is not a benign duplicate: the second
one overwrites ``runtime.json`` with its own session token while the first
still owns the port. The served page then carries a token the listening process
rejects, and every request comes back 401 — with nothing in the UI to explain
why.

So starting a second instance against the same data directory is refused, and
the caller is told where the live one is so it can attach to it instead.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

import psutil

from .config import Settings
from .logging_conf import get_logger

log = get_logger("single_instance")

LOCK_FILENAME = "instance.lock"


@dataclass(frozen=True, slots=True)
class RunningInstance:
    """Details of a backend already serving this data directory."""

    pid: int
    port: int
    token: str

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def _process_alive(pid: int) -> bool:
    """True when ``pid`` names a live process.

    Not ``os.kill(pid, 0)``: on Windows that probe is unreliable for a PID that
    has already exited. It usually raises ``OSError`` (WinError 87), but it can
    instead surface as ``SystemError: <built-in function kill> returned a
    result with an exception set`` — which is not an ``OSError``, so no amount
    of ``except OSError`` catches it, and startup dies on a stale lock file
    left by a previous crash. Observed exactly that way on Windows 11 with
    CPython 3.12.

    ``psutil`` is already a dependency and probes this correctly on every
    platform, including reporting a process owned by another user as alive.
    """
    if pid <= 0:
        return False
    try:
        return bool(psutil.pid_exists(pid))
    except Exception:
        # A liveness probe must never take the application down with it.
        log.warning("could not probe pid liveness", extra={"pid": pid})
        return False


def read_lock(settings: Settings) -> RunningInstance | None:
    """Return the live instance holding this data directory, if any.

    A lock file left behind by a crash is stale and is ignored — and removed —
    rather than blocking startup forever.
    """
    lock_path = settings.data_dir / LOCK_FILENAME
    if not lock_path.is_file():
        return None

    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        pid = int(payload["pid"])
        port = int(payload["port"])
        token = str(payload["token"])
    except (OSError, ValueError, KeyError, TypeError):
        log.warning("ignoring an unreadable lock file")
        lock_path.unlink(missing_ok=True)
        return None

    if not _process_alive(pid):
        log.info("clearing a stale lock file", extra={"stale_pid": pid})
        lock_path.unlink(missing_ok=True)
        return None

    return RunningInstance(pid=pid, port=port, token=token)


def acquire(settings: Settings) -> None:
    """Claim this data directory. Raises RuntimeError if already claimed."""
    existing = read_lock(settings)
    if existing is not None:
        raise RuntimeError(
            f"Tiktuv is already running (pid {existing.pid}, port {existing.port}). "
            f"Close it first, or use the running instance at {existing.base_url}"
        )

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    lock_path = settings.data_dir / LOCK_FILENAME
    lock_path.write_text(
        json.dumps(
            {"pid": os.getpid(), "port": settings.port, "token": settings.auth_token},
            indent=2,
        ),
        encoding="utf-8",
    )
    if os.name != "nt":
        lock_path.chmod(0o600)
    log.info("acquired the instance lock", extra={"pid": os.getpid(), "port": settings.port})


def release(settings: Settings) -> None:
    """Release the lock, but only if this process owns it."""
    lock_path = settings.data_dir / LOCK_FILENAME
    if not lock_path.is_file():
        return
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        if int(payload.get("pid", -1)) != os.getpid():
            # Another instance owns it now; leaving it alone is correct.
            return
    except (OSError, ValueError, TypeError):
        pass
    lock_path.unlink(missing_ok=True)
