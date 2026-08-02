"""SQLite engine and session management.

The database holds job history only. Transcripts live as JSON files next to it
(``transcripts/<job_id>.json``) so a corrupt database never costs the user
their text, and partial results can be flushed cheaply while a job runs.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .logging_conf import get_logger

log = get_logger("db")

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def _configure_sqlite(dbapi_connection: object, _record: object) -> None:
    cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
    # WAL keeps the API responsive while the worker writes progress rows.
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def init_engine(db_path: Path) -> Engine:
    """Create (or return) the process-wide engine and create the schema."""
    global _engine, _SessionFactory
    if _engine is not None:
        return _engine

    db_path.parent.mkdir(parents=True, exist_ok=True)
    # ``check_same_thread=False``: the worker thread and the event loop share
    # the engine's pool. Sessions are never shared across threads.
    _engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 15},
        future=True,
    )
    event.listen(_engine, "connect", _configure_sqlite)

    from .models_db import Base  # local import avoids a cycle

    Base.metadata.create_all(_engine)
    _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    log.info("database ready", extra={"db_path": str(db_path)})
    return _engine


def dispose_engine() -> None:
    global _engine, _SessionFactory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionFactory = None


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session. Commits on success, rolls back on any error."""
    if _SessionFactory is None:
        raise RuntimeError("database not initialised; call init_engine() first")
    session = _SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
