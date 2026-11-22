"""Backend entry point.

Started by the desktop app (or by hand during development). On startup it
writes a handshake file containing the port and the random session token, so
the desktop app can reach the API without either side hard-coding a secret.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import sys
from pathlib import Path
from types import FrameType

import uvicorn

from . import __version__
from .config import DEFAULT_PORT, Settings, get_settings
from .logging_conf import configure_logging, get_logger
from .main import create_app
from .single_instance import acquire, release

log = get_logger("cli")

HANDSHAKE_FILENAME = "runtime.json"


def write_handshake(settings: Settings, port: int) -> Path:
    """Write ``runtime.json`` with the live port and token.

    Written with owner-only permissions where the platform supports it. On
    Windows the file inherits the user's profile ACL, which is already private
    to the account.
    """
    path = settings.data_dir / HANDSHAKE_FILENAME
    payload = {
        "version": __version__,
        "host": settings.host,
        "port": port,
        "token": settings.auth_token,
        "pid": os.getpid(),
        "data_dir": str(settings.data_dir),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o600)
    return path


def clear_handshake(settings: Settings) -> None:
    (settings.data_dir / HANDSHAKE_FILENAME).unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tiktuv", description="Tiktuv backend")
    parser.add_argument("--host", default=None, help="loopback address to bind (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help=f"port (default {DEFAULT_PORT})")
    parser.add_argument("--data-dir", default=None, help="override the data directory")
    parser.add_argument("--log-level", default=None, help="DEBUG, INFO, WARNING, ERROR")
    parser.add_argument(
        "--print-token", action="store_true", help="print the session token and exit"
    )
    parser.add_argument("--version", action="version", version=f"Tiktuv {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # Env overrides are how Settings is configured, so translate CLI flags into
    # them before the settings singleton is built.
    if args.data_dir:
        os.environ["TIKTUV_DATA_DIR"] = args.data_dir
    if args.host:
        os.environ["TIKTUV_HOST"] = args.host
    if args.port:
        os.environ["TIKTUV_PORT"] = str(args.port)
    if args.log_level:
        os.environ["TIKTUV_LOG_LEVEL"] = args.log_level

    settings = get_settings()
    configure_logging(
        level=settings.log_level,
        json_output=settings.log_json,
        log_dir=settings.logs_dir,
        redact=settings.redact_paths_in_errors,
    )

    if args.print_token:
        print(settings.auth_token)
        return 0

    # Refuse to start a second backend on this data directory: it would
    # overwrite the live instance's handshake token and break its UI.
    try:
        acquire(settings)
    except RuntimeError as exc:
        log.error("another instance is already running", extra={"detail": str(exc)})
        print(f"TIKTUV_ALREADY_RUNNING {exc}", flush=True)
        return 3

    handshake = write_handshake(settings, settings.port)
    # A single line on stdout the desktop shell waits for. Everything else
    # goes to stderr, so this stays parseable.
    print(f"TIKTUV_READY {handshake}", flush=True)

    def _handle_signal(signum: int, _frame: FrameType | None) -> None:
        log.info("received signal, shutting down", extra={"signal": signum})
        raise SystemExit(0)

    for sig in (signal.SIGINT, signal.SIGTERM):
        # Fails when not running on the main thread; harmless either way.
        with contextlib.suppress(ValueError, OSError):  # pragma: no cover
            signal.signal(sig, _handle_signal)

    try:
        uvicorn.run(
            create_app(settings),
            host=settings.host,
            port=settings.port,
            log_config=None,
            access_log=False,
            # Loopback only; no proxy headers to trust.
            proxy_headers=False,
        )
    except OSError as exc:
        log.error(
            "could not bind the backend port",
            extra={"port": settings.port, "error": str(exc)},
        )
        return 2
    finally:
        clear_handshake(settings)
        release(settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
