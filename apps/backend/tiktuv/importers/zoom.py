"""Resolve a Zoom cloud-recording share link to a downloadable media URL.

How the player works, and therefore how this works:

1. The ``/rec/play/…`` or ``/rec/share/…`` page is fetched. It sets the session
   cookies (``_zm_ssid``, ``cred``) that every later request needs.
2. The page embeds ``window.__data__`` — a JavaScript object literal, not JSON —
   containing a ``fileId``.
3. ``/nws/recording/1.0/play/info/<fileId>`` returns the real metadata,
   including ``viewMp4Url``.
4. That URL serves the MP4, but only with a ``zoom.us`` ``Referer`` and the
   cookies from step 1.

This is the same sequence the browser player performs. It is scraping, so it is
inherently tied to Zoom's current page structure: every extraction step raises a
specific, actionable error rather than returning something half-parsed, so a
future change to Zoom surfaces as "could not read the page" instead of a
mysterious download failure.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from ..errors import ErrorCode, TiktuvError
from ..logging_conf import get_logger

log = get_logger("importers.zoom")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

#: Zoom serves recordings from regional hosts; all end in this domain.
_ZOOM_HOST = re.compile(r"(^|\.)zoom\.us$", re.IGNORECASE)
_RECORDING_PATH = re.compile(r"^/rec/(play|share)/", re.IGNORECASE)

_FILE_ID = re.compile(r"\bfileId:\s*'([^']+)'")
#: Some builds quote the key instead.
_FILE_ID_JSON = re.compile(r'"fileId"\s*:\s*"([^"]+)"')

_PASSCODE_FORM = re.compile(r'name=["\']passwd["\']', re.IGNORECASE)

#: Media keys in order of preference. ``viewMp4Url`` is what the player uses.
_MEDIA_KEYS = ("viewMp4Url", "mp4Url", "viewMp4WithshareUrl", "audioUrl")

_ILLEGAL_TITLE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class ZoomError(TiktuvError):
    code = ErrorCode.MEDIA_CORRUPT


@dataclass(frozen=True, slots=True)
class ZoomRecording:
    """Everything needed to fetch one recording."""

    title: str
    media_url: str
    duration_seconds: float
    size_bytes: int
    #: Cookies established while resolving; the media host requires them.
    cookies: dict[str, str] = field(default_factory=dict)
    #: True when the owner disabled downloading in Zoom's own interface.
    download_disabled: bool = False
    passcode_required: bool = False

    @property
    def filename(self) -> str:
        """A safe, meaningful filename ending in .mp4."""
        stem = _ILLEGAL_TITLE.sub("_", self.title).strip().strip(".")
        stem = re.sub(r"\s+", " ", stem)[:120].strip() or "zoom-recording"
        return f"{stem}.mp4"


def is_zoom_url(url: str) -> bool:
    """True for a Zoom cloud-recording share or play link."""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    if not parsed.hostname or not _ZOOM_HOST.search(parsed.hostname):
        return False
    return bool(_RECORDING_PATH.match(parsed.path or ""))


def parse_embedded_data(html: str) -> dict[str, Any]:
    """Read the interesting fields out of ``window.__data__``.

    That blob is a JavaScript object literal — unquoted keys, single-quoted
    strings — so it is not valid JSON and is not parsed as such. Only the few
    fields that matter are extracted, which also keeps this robust to unrelated
    changes elsewhere in the object.
    """
    values: dict[str, Any] = {}
    for match in re.finditer(r"\b(\w+):\s*'((?:[^'\\]|\\.)*)'", html):
        key, raw = match.group(1), match.group(2)
        if key not in values:
            values[key] = raw.replace("\\'", "'").replace("\\\\", "\\")
    return values


def extract_file_id(html: str) -> str:
    for pattern in (_FILE_ID, _FILE_ID_JSON):
        match = pattern.search(html)
        if match and match.group(1):
            return match.group(1)
    raise ZoomError(
        "could not find the recording id on the Zoom page",
        code=ErrorCode.MEDIA_CORRUPT,
        context={"hint": "Zoom may have changed its player, or the link expired"},
    )


def needs_passcode(html: str) -> bool:
    return bool(_PASSCODE_FORM.search(html))


def select_media_url(result: dict[str, Any]) -> str:
    for key in _MEDIA_KEYS:
        value = result.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return value
    raise ZoomError(
        "the recording has no downloadable media stream",
        code=ErrorCode.MEDIA_NO_AUDIO,
        context={"available": sorted(k for k in result if "url" in k.lower())},
    )


#: The media path carries the recording's start time: …/GMT20260528-140325_R…
_GMT_STAMP = re.compile(r"GMT(\d{8})-(\d{6})")


def recording_started_at(result: dict[str, Any], media_url: str) -> datetime | None:
    """When the recording started, from whichever source has it.

    ``fileStartTime`` is epoch milliseconds; the media path also encodes the
    start time, which is the only source for an anonymously shared link.
    """
    for key in ("fileStartTime", "clipStartTime"):
        raw = result.get(key)
        if isinstance(raw, int | float) and raw > 0:
            seconds = raw / 1000 if raw > 1e11 else float(raw)
            try:
                return datetime.fromtimestamp(seconds, tz=UTC)
            except (OverflowError, OSError, ValueError):
                continue

    match = _GMT_STAMP.search(media_url or "")
    if match:
        try:
            return datetime.strptime(
                f"{match.group(1)}{match.group(2)}", "%Y%m%d%H%M%S"
            ).replace(tzinfo=UTC)
        except ValueError:
            pass
    return None


def choose_title(
    result: dict[str, Any], embedded: dict[str, Any], media_url: str = ""
) -> str:
    """A human-meaningful name for the recording.

    Zoom only exposes the meeting topic to someone signed in with access to it;
    an anonymous share link has none. Rather than name every import the same
    thing — unusable when importing a batch of links — fall back to the
    recording's own start time, which is always available.
    """
    for source in (result, embedded):
        for key in ("topic", "meetingTopic", "recordingName", "fileName"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    started = recording_started_at(result, media_url)
    if started is not None:
        return f"Zoom {started:%Y-%m-%d %H-%M}"
    return "Zoom recording"


def _play_info_url(host: str, file_id: str) -> str:
    return (
        f"https://{host}/nws/recording/1.0/play/info/{file_id}"
        "?canPlayFromShare=true&from=share_recording_detail"
        "&continueMode=true&componentName=rec-play"
    )


def resolve(url: str, passcode: str | None = None, *, timeout: float = 45.0) -> ZoomRecording:
    """Turn a share link into a :class:`ZoomRecording`. Requires network."""
    if not is_zoom_url(url):
        raise ZoomError(
            "that does not look like a Zoom recording link",
            code=ErrorCode.INVALID_REQUEST,
            context={"expected": "https://<company>.zoom.us/rec/share/… or /rec/play/…"},
        )

    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - httpx is a dependency
        raise ZoomError("the HTTP client is not installed") from exc

    host = urlparse(url).hostname or "zoom.us"

    with httpx.Client(
        follow_redirects=True, timeout=timeout, headers={"User-Agent": USER_AGENT}
    ) as client:
        try:
            page = client.get(url)
        except Exception as exc:  # noqa: BLE001 - httpx raises several types
            raise ZoomError(
                f"could not reach Zoom: {exc}", code=ErrorCode.MEDIA_CORRUPT
            ) from exc

        if page.status_code == 404:
            raise ZoomError(
                "the recording was not found — the link may have expired",
                code=ErrorCode.MEDIA_CORRUPT,
            )
        if page.status_code >= 400:
            raise ZoomError(
                f"Zoom returned {page.status_code} for that link",
                code=ErrorCode.MEDIA_CORRUPT,
            )

        html = page.text
        gate = needs_passcode(html)
        if gate:
            if not passcode:
                raise ZoomError(
                    "this recording is protected by a passcode",
                    code=ErrorCode.INVALID_REQUEST,
                    context={"passcode_required": True},
                )
            html = _submit_passcode(client, str(page.url), html, passcode, host)

        embedded = parse_embedded_data(html)
        file_id = extract_file_id(html)

        try:
            info = client.get(
                _play_info_url(host, file_id),
                headers={"Referer": str(page.url), "Accept": "application/json"},
            )
            payload = info.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise ZoomError(
                "Zoom's player data could not be read", code=ErrorCode.MEDIA_CORRUPT
            ) from exc
        except Exception as exc:  # noqa: BLE001 - httpx raises several types
            raise ZoomError(
                f"could not load the recording details: {exc}", code=ErrorCode.MEDIA_CORRUPT
            ) from exc

        result = payload.get("result")
        if not isinstance(result, dict):
            raise ZoomError(
                payload.get("errorMessage") or "Zoom did not return recording details",
                code=ErrorCode.MEDIA_CORRUPT,
            )

        media_url = select_media_url(result)
        cookies = {name: value for name, value in client.cookies.items()}

        # ``duration`` is seconds in some responses and milliseconds in others;
        # a meeting longer than 24 h is not plausible, so treat it as ms.
        raw_duration = float(result.get("duration") or 0)
        duration = raw_duration / 1000 if raw_duration > 86_400 else raw_duration

        recording = ZoomRecording(
            title=choose_title(result, embedded, media_url),
            media_url=media_url,
            duration_seconds=duration,
            size_bytes=int(result.get("fileSize") or 0),
            cookies=cookies,
            download_disabled=bool(result.get("disableDownload")),
            passcode_required=gate,
        )

    log.info(
        "resolved a Zoom recording",
        extra={
            "duration": round(recording.duration_seconds),
            "download_disabled": recording.download_disabled,
            "media_host": urlparse(recording.media_url).hostname,
        },
    )
    return recording


def _submit_passcode(
    client: Any, page_url: str, html: str, passcode: str, host: str
) -> str:
    """Post the passcode form and return the unlocked page.

    Unverified against a real protected recording — no such link was available
    — so a failure here reports the passcode as rejected rather than pretending
    to have succeeded.
    """
    meeting_id = ""
    match = re.search(r'name=["\']id["\'][^>]*value=["\']([^"\']+)["\']', html)
    if match:
        meeting_id = match.group(1)

    try:
        response = client.post(
            f"https://{host}/rec/validate_meet_passwd",
            data={"id": meeting_id, "passwd": passcode, "action": "viewdetailpage"},
            headers={"Referer": page_url, "Origin": f"https://{host}"},
        )
        if response.status_code >= 400:
            raise ZoomError(
                "the passcode was not accepted", code=ErrorCode.INVALID_REQUEST
            )
        unlocked = client.get(page_url)
    except ZoomError:
        raise
    except Exception as exc:  # noqa: BLE001 - httpx raises several types
        raise ZoomError(
            f"could not submit the passcode: {exc}", code=ErrorCode.INVALID_REQUEST
        ) from exc

    html_after: str = unlocked.text
    if needs_passcode(html_after):
        raise ZoomError("the passcode was not accepted", code=ErrorCode.INVALID_REQUEST)
    return html_after


def media_request_headers(recording: ZoomRecording) -> dict[str, str]:
    """Headers the media host requires. Without the Referer it returns 403."""
    return {
        "User-Agent": USER_AGENT,
        "Referer": "https://zoom.us/",
        "Accept": "*/*",
    }
