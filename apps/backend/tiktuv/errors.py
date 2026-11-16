"""Explicit error types for Tiktuv.

Every failure the user can hit has a stable machine code, a Hebrew message
shown in the UI, and an English message for logs. Nothing is ever raised as a
bare ``Exception`` and nothing is swallowed.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    # Environment
    FFMPEG_MISSING = "FFMPEG_MISSING"
    FFMPEG_FAILED = "FFMPEG_FAILED"
    UNSUPPORTED_CODEC = "UNSUPPORTED_CODEC"
    MEDIA_CORRUPT = "MEDIA_CORRUPT"
    MEDIA_NO_AUDIO = "MEDIA_NO_AUDIO"
    MEDIA_TOO_LARGE = "MEDIA_TOO_LARGE"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    # Resources
    INSUFFICIENT_DISK = "INSUFFICIENT_DISK"
    INSUFFICIENT_RAM = "INSUFFICIENT_RAM"
    INSUFFICIENT_VRAM = "INSUFFICIENT_VRAM"
    # Model
    MODEL_LOAD_FAILED = "MODEL_LOAD_FAILED"
    MODEL_NOT_DOWNLOADED = "MODEL_NOT_DOWNLOADED"
    MODEL_DOWNLOAD_FAILED = "MODEL_DOWNLOAD_FAILED"
    ASR_BACKEND_MISSING = "ASR_BACKEND_MISSING"
    UNKNOWN_MODEL = "UNKNOWN_MODEL"
    # Cloud ASR (Soniox)
    SONIOX_KEY_MISSING = "SONIOX_KEY_MISSING"
    SONIOX_API_ERROR = "SONIOX_API_ERROR"
    # AI analysis (DeepSeek)
    DEEPSEEK_KEY_MISSING = "DEEPSEEK_KEY_MISSING"
    DEEPSEEK_API_ERROR = "DEEPSEEK_API_ERROR"
    # Jobs
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    JOB_CANCELLED = "JOB_CANCELLED"
    JOB_INVALID_STATE = "JOB_INVALID_STATE"
    TRANSCRIPT_NOT_FOUND = "TRANSCRIPT_NOT_FOUND"
    # Security / input
    UNAUTHORIZED = "UNAUTHORIZED"
    PATH_NOT_ALLOWED = "PATH_NOT_ALLOWED"
    INVALID_FILENAME = "INVALID_FILENAME"
    INVALID_REQUEST = "INVALID_REQUEST"
    # Export
    EXPORT_FAILED = "EXPORT_FAILED"
    UNSUPPORTED_EXPORT_FORMAT = "UNSUPPORTED_EXPORT_FORMAT"
    # Catch-all
    INTERNAL = "INTERNAL"


#: Hebrew user-facing text + an actionable remedy for every code.
MESSAGES_HE: dict[ErrorCode, tuple[str, str]] = {
    ErrorCode.FFMPEG_MISSING: (
        "FFmpeg לא נמצא במחשב.",
        "התקינו FFmpeg והוסיפו אותו ל-PATH, או הגדירו את הנתיב בהגדרות.",
    ),
    ErrorCode.FFMPEG_FAILED: (
        "עיבוד המדיה נכשל.",
        "ודאו שהקובץ תקין ונסו שוב. פרטי השגיאה נמצאים ביומן.",
    ),
    ErrorCode.UNSUPPORTED_CODEC: (
        "קודק המדיה אינו נתמך.",
        "המירו את הקובץ ל-WAV או MP3 ונסו שוב.",
    ),
    ErrorCode.MEDIA_CORRUPT: (
        "קובץ המדיה פגום או שאינו קובץ מדיה.",
        "בדקו שהקובץ נפתח בנגן רגיל ונסו שוב.",
    ),
    ErrorCode.MEDIA_NO_AUDIO: (
        "לא נמצא ערוץ אודיו בקובץ.",
        "בחרו קובץ שמכיל פס קול.",
    ),
    ErrorCode.MEDIA_TOO_LARGE: (
        "הקובץ גדול מהמותר.",
        "פצלו את ההקלטה או הגדילו את המגבלה בהגדרות.",
    ),
    ErrorCode.UNSUPPORTED_FORMAT: (
        "סוג הקובץ אינו נתמך.",
        "נתמכים: MP3, MP4, WAV, M4A, MOV, MKV, WEBM.",
    ),
    ErrorCode.INSUFFICIENT_DISK: (
        "אין מספיק מקום פנוי בדיסק.",
        "פנו מקום בדיסק ונסו שוב.",
    ),
    ErrorCode.INSUFFICIENT_RAM: (
        "אין מספיק זיכרון פנוי.",
        "סגרו יישומים אחרים, או בחרו מודל קטן יותר.",
    ),
    ErrorCode.INSUFFICIENT_VRAM: (
        "אין מספיק זיכרון בכרטיס המסך.",
        "הקטינו את גודל האצווה, עברו ל-FP16, או השתמשו במעבד (CPU).",
    ),
    ErrorCode.MODEL_LOAD_FAILED: (
        "טעינת מודל התמלול נכשלה.",
        "בדקו את התקנת PyTorch ואת שלמות קבצי המודל.",
    ),
    ErrorCode.MODEL_NOT_DOWNLOADED: (
        "המודל טרם הורד למחשב.",
        "הורידו את המודל ממסך ההגדרות לפני התמלול.",
    ),
    ErrorCode.MODEL_DOWNLOAD_FAILED: (
        "הורדת המודל נכשלה.",
        "בדקו את החיבור לאינטרנט ואת המקום הפנוי בדיסק ונסו שוב.",
    ),
    ErrorCode.ASR_BACKEND_MISSING: (
        "רכיבי התמלול אינם מותקנים.",
        "התקינו את חבילות ה-ASR: pip install -e apps/backend[asr]",
    ),
    ErrorCode.UNKNOWN_MODEL: ("המודל המבוקש אינו מוכר.", "בחרו מודל מהרשימה."),
    ErrorCode.SONIOX_KEY_MISSING: (
        "מפתח ה-API של Soniox אינו מוגדר.",
        "הגדירו אותו בכרטיסיית 'מערכת ומודלים' לפני תמלול דרך Soniox.",
    ),
    ErrorCode.SONIOX_API_ERROR: (
        "התמלול דרך שירות Soniox נכשל.",
        "בדקו את מפתח ה-API, את החיבור לאינטרנט ואת יתרת החשבון, ונסו שוב.",
    ),
    ErrorCode.DEEPSEEK_KEY_MISSING: (
        "מפתח ה-API של DeepSeek אינו מוגדר.",
        "הגדירו אותו בכרטיסיית 'מערכת ומודלים' לפני יצירת ניתוח.",
    ),
    ErrorCode.DEEPSEEK_API_ERROR: (
        "יצירת הניתוח נכשלה.",
        "בדקו את מפתח ה-API, את החיבור לאינטרנט ואת יתרת החשבון, ונסו שוב.",
    ),
    ErrorCode.JOB_NOT_FOUND: ("העבודה לא נמצאה.", "רעננו את רשימת העבודות."),
    ErrorCode.JOB_CANCELLED: ("העבודה בוטלה.", "ניתן להריץ אותה מחדש מההיסטוריה."),
    ErrorCode.JOB_INVALID_STATE: (
        "לא ניתן לבצע את הפעולה במצב הנוכחי של העבודה.",
        "רעננו את הרשימה ונסו שוב.",
    ),
    ErrorCode.TRANSCRIPT_NOT_FOUND: ("התמליל לא נמצא.", "ייתכן שהעבודה עדיין רצה או נכשלה."),
    ErrorCode.UNAUTHORIZED: ("גישה נדחתה.", "הפעילו מחדש את היישום."),
    ErrorCode.PATH_NOT_ALLOWED: (
        "הנתיב אינו מורשה.",
        "בחרו תיקייה בתוך תיקיות העבודה של היישום.",
    ),
    ErrorCode.INVALID_FILENAME: ("שם הקובץ אינו חוקי.", "שנו את שם הקובץ ונסו שוב."),
    ErrorCode.INVALID_REQUEST: ("הבקשה אינה תקינה.", "בדקו את הנתונים שנשלחו."),
    ErrorCode.EXPORT_FAILED: ("הייצוא נכשל.", "ודאו שתיקיית היעד קיימת וניתנת לכתיבה."),
    ErrorCode.UNSUPPORTED_EXPORT_FORMAT: (
        "פורמט הייצוא אינו נתמך.",
        "נתמכים: TXT, TXT מתוזמן, DOCX, PDF, SRT, VTT, JSON.",
    ),
    ErrorCode.INTERNAL: ("אירעה שגיאה פנימית.", "פרטים נוספים ביומן היישום."),
}

#: HTTP status per code, used by the API exception handler.
HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.PATH_NOT_ALLOWED: 403,
    ErrorCode.JOB_NOT_FOUND: 404,
    ErrorCode.TRANSCRIPT_NOT_FOUND: 404,
    ErrorCode.UNKNOWN_MODEL: 404,
    ErrorCode.SONIOX_KEY_MISSING: 422,
    ErrorCode.SONIOX_API_ERROR: 502,
    ErrorCode.DEEPSEEK_KEY_MISSING: 422,
    ErrorCode.DEEPSEEK_API_ERROR: 502,
    ErrorCode.JOB_INVALID_STATE: 409,
    ErrorCode.MEDIA_TOO_LARGE: 413,
    ErrorCode.UNSUPPORTED_FORMAT: 415,
    ErrorCode.UNSUPPORTED_EXPORT_FORMAT: 415,
    ErrorCode.INVALID_FILENAME: 422,
    ErrorCode.INVALID_REQUEST: 422,
    ErrorCode.MEDIA_CORRUPT: 422,
    ErrorCode.MEDIA_NO_AUDIO: 422,
    ErrorCode.UNSUPPORTED_CODEC: 422,
    ErrorCode.INSUFFICIENT_DISK: 507,
}

# Path components may legitimately contain spaces ("C:\Users\Dana Cohen\..."),
# so the component character class permits them. That makes the match greedy
# past the end of the real path when prose follows it; _basename trims it back.
_COMPONENT = r'[^\\/:*?"<>|\r\n]'
# Separators are matched as a *run*. ``str(OSError)`` embeds the offending path
# as a repr, so its backslashes arrive doubled ("C:\\Users\\..."). Matching a
# single separator would treat "\\" as an empty component, fragment the match,
# and leak every directory name — which is exactly what this function exists to
# prevent.
_SEP = r"[\\/]+"
_WINDOWS_PATH = re.compile(rf"[A-Za-z]:{_SEP}(?:{_COMPONENT}+{_SEP})*{_COMPONENT}*")
_UNC_PATH = re.compile(rf"\\{{2,}}{_COMPONENT}+(?:{_SEP}{_COMPONENT}+)*")
_POSIX_PATH = re.compile(rf"(?<![\w.])/+(?:{_COMPONENT}+/+)+{_COMPONENT}*")

#: Shortest prefix of the final component that ends in a file extension. Used
#: to cut trailing prose back off a greedy match.
_FILENAME_IN_TAIL = re.compile(r"^\S+(?:[ \t]\S+)*?\.[A-Za-z0-9]{1,8}(?![\w.])")


def redact_paths(text: str) -> str:
    """Replace absolute filesystem paths with ``<path>/<basename>``.

    Error text can travel into logs and (opt-in) reports. The basename is kept
    because it is what makes an error actionable; the directory chain — which
    routinely contains the user's real name — is not.

    Over-redaction is the deliberate failure mode here: if the boundary between
    a path and the words after it is ambiguous, more is removed, not less.
    """

    def _basename(match: re.Match[str]) -> str:
        raw = match.group(0).rstrip()
        tail = re.split(_SEP, raw.rstrip("\\/"))[-1].strip()
        if not tail:
            return "<path>"
        # The component class allows spaces, so a greedy match can have picked
        # up the rest of the sentence. Keep only up to the file extension.
        extension_match = _FILENAME_IN_TAIL.match(tail)
        if extension_match:
            filename = extension_match.group(0)
            remainder = tail[len(filename):]
            return f"<path>/{filename}{remainder}"
        # No extension: it is a directory. Keep just the first token.
        first, _, remainder = tail.partition(" ")
        return f"<path>/{first}{(' ' + remainder) if remainder else ''}"

    for pattern in (_UNC_PATH, _WINDOWS_PATH, _POSIX_PATH):
        text = pattern.sub(_basename, text)
    return text


class TiktuvError(Exception):
    """Base class for every error Tiktuv raises deliberately."""

    code: ErrorCode = ErrorCode.INTERNAL

    def __init__(
        self,
        detail: str = "",
        *,
        code: ErrorCode | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        if code is not None:
            self.code = code
        self.detail = detail
        self.context = context or {}
        super().__init__(detail or self.code.value)

    @property
    def message_he(self) -> str:
        return MESSAGES_HE[self.code][0]

    @property
    def remedy_he(self) -> str:
        return MESSAGES_HE[self.code][1]

    @property
    def http_status(self) -> int:
        return HTTP_STATUS.get(self.code, 500)

    def to_payload(self, *, redact: bool = True) -> dict[str, Any]:
        detail = redact_paths(self.detail) if redact else self.detail
        return {
            "code": self.code.value,
            "message": self.message_he,
            "remedy": self.remedy_he,
            "detail": detail,
            "context": self.context,
        }

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"[{self.code.value}] {self.detail}" if self.detail else f"[{self.code.value}]"


class MediaError(TiktuvError):
    code = ErrorCode.MEDIA_CORRUPT


class FFmpegMissingError(TiktuvError):
    code = ErrorCode.FFMPEG_MISSING


class FFmpegError(TiktuvError):
    code = ErrorCode.FFMPEG_FAILED


class ModelError(TiktuvError):
    code = ErrorCode.MODEL_LOAD_FAILED


class ResourceError(TiktuvError):
    code = ErrorCode.INSUFFICIENT_DISK


class JobError(TiktuvError):
    code = ErrorCode.JOB_INVALID_STATE


class JobCancelled(TiktuvError):
    """Raised inside a worker when a cancel was requested. Not a failure."""

    code = ErrorCode.JOB_CANCELLED


class SecurityError(TiktuvError):
    code = ErrorCode.PATH_NOT_ALLOWED


class ExportError(TiktuvError):
    code = ErrorCode.EXPORT_FAILED
