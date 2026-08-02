"""Local application security helpers.

Threat model: the backend listens on loopback, but any process on the machine
(including a browser page the user has open) can reach it. So we (a) require a
random per-session bearer token, (b) never accept a filesystem path from the
client that escapes the managed data directories, and (c) sanitize every
filename before it reaches the filesystem — while keeping Hebrew intact.
"""

from __future__ import annotations

import hmac
import re
import unicodedata
import uuid
from pathlib import Path, PurePath

from .errors import ErrorCode, SecurityError

#: Characters Windows forbids in a filename, plus path separators and controls.
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
#: Windows reserved device names (case-insensitive, with or without extension).
_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)
#: Bidi control characters. Stripped from filenames because they can be used to
#: disguise an extension (the classic ``exe.txt`` RTL-override trick).
_BIDI_CONTROLS = str.maketrans(
    {c: None for c in "‪‫‬‭‮⁦⁧⁨⁩‎‏"}
)

MAX_FILENAME_LENGTH = 150


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def sanitize_filename(name: str, *, fallback: str = "media") -> str:
    """Return a filesystem-safe filename that preserves Hebrew characters.

    Hebrew letters, digits, spaces, dots, dashes and underscores survive
    untouched. Everything Windows would reject — and anything that could be
    used to escape the target directory or spoof an extension — does not.
    """
    if not isinstance(name, str):
        raise SecurityError("filename must be a string", code=ErrorCode.INVALID_FILENAME)

    # Take the basename under *both* separator conventions: a POSIX backend
    # must still reject a Windows-style traversal string, and vice versa.
    name = name.replace("\\", "/").split("/")[-1]
    # NFC keeps Hebrew niqqud composed and stable across Windows/macOS.
    name = unicodedata.normalize("NFC", name).translate(_BIDI_CONTROLS)
    name = _ILLEGAL.sub("_", name).strip().strip(".")

    if not name or set(name) <= {"_", ".", " "}:
        return fallback

    stem, dot, suffix = name.rpartition(".")
    if not dot:
        stem, suffix = name, ""
    if stem.upper() in _RESERVED_NAMES:
        stem = f"_{stem}"

    if len(stem) > MAX_FILENAME_LENGTH:
        stem = stem[:MAX_FILENAME_LENGTH].rstrip()
    suffix = suffix[:20]

    result = f"{stem}.{suffix}" if suffix else stem
    return result or fallback


def unique_filename(directory: Path, name: str) -> Path:
    """Sanitize ``name`` and return a path inside ``directory`` that is free."""
    safe = sanitize_filename(name)
    candidate = directory / safe
    if not candidate.exists():
        return candidate
    stem, dot, suffix = safe.rpartition(".")
    if not dot:
        stem, suffix = safe, ""
    token = uuid.uuid4().hex[:8]
    return directory / (f"{stem}-{token}.{suffix}" if suffix else f"{stem}-{token}")


def _is_relative_to(child: PurePath, parent: PurePath) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def resolve_within(candidate: str | Path, roots: list[Path]) -> Path:
    """Resolve ``candidate`` and assert it stays inside one of ``roots``.

    Resolution happens *before* the containment check, so ``..`` segments,
    symlinks and Windows short names (``PROGRA~1``) cannot be used to escape.
    """
    if not roots:
        raise SecurityError("no writable roots configured", code=ErrorCode.PATH_NOT_ALLOWED)

    try:
        resolved = Path(candidate).resolve()
    except (OSError, ValueError) as exc:
        raise SecurityError(f"cannot resolve path: {exc}", code=ErrorCode.PATH_NOT_ALLOWED) from exc

    for root in roots:
        if resolved == root or _is_relative_to(resolved, root.resolve()):
            return resolved

    raise SecurityError(
        f"path {resolved} is outside the allowed directories",
        code=ErrorCode.PATH_NOT_ALLOWED,
    )


def validate_output_dir(candidate: str | Path | None, default: Path) -> Path:
    """Validate a user-chosen export directory.

    Unlike :func:`resolve_within`, an export destination is legitimately
    anywhere the user can write — they picked it in a native folder dialog. We
    therefore only require that it is an existing, writable directory, and we
    reject anything that is not.
    """
    if candidate is None or str(candidate).strip() == "":
        default.mkdir(parents=True, exist_ok=True)
        return default

    path = Path(candidate).expanduser()
    try:
        path = path.resolve()
    except (OSError, ValueError) as exc:
        raise SecurityError(f"cannot resolve output directory: {exc}") from exc

    if path.exists() and not path.is_dir():
        raise SecurityError(f"output path is not a directory: {path}")
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".tiktuv-write-test-{uuid.uuid4().hex[:8]}"
        probe.touch()
        probe.unlink()
    except OSError as exc:
        raise SecurityError(f"output directory is not writable: {exc}") from exc
    return path
