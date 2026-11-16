"""Filename sanitization, path containment and error redaction."""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest

from tiktuv.errors import ErrorCode, SecurityError, redact_paths
from tiktuv.security import (
    constant_time_equals,
    is_within,
    resolve_within,
    sanitize_filename,
    unique_filename,
    validate_output_dir,
)


class TestSanitizeFilename:
    def test_preserves_hebrew(self) -> None:
        assert sanitize_filename("ישיבת הנהלה.mp3") == "ישיבת הנהלה.mp3"

    def test_preserves_hebrew_with_digits_and_dashes(self) -> None:
        assert sanitize_filename("פגישה-2026_01.mp4") == "פגישה-2026_01.mp4"

    def test_keeps_niqqud_composed(self) -> None:
        name = sanitize_filename("שָׁלוֹם.mp3")
        assert name == unicodedata.normalize("NFC", name)
        assert "שָׁלוֹם" in name

    @pytest.mark.parametrize(
        "hostile",
        [
            "../../etc/passwd",
            "..\\..\\Windows\\System32\\config\\SAM",
            "/absolute/path/file.mp3",
            r"C:\Windows\system.ini",
            "dir/subdir/file.mp3",
        ],
    )
    def test_strips_traversal(self, hostile: str) -> None:
        result = sanitize_filename(hostile)
        assert ".." not in result.replace("...", "")
        assert "/" not in result
        assert "\\" not in result

    @pytest.mark.parametrize("reserved", ["CON", "con.mp3", "NUL.txt", "COM1", "lpt9.wav"])
    def test_escapes_windows_reserved_names(self, reserved: str) -> None:
        assert sanitize_filename(reserved).startswith("_")

    def test_removes_bidi_override_extension_spoofing(self) -> None:
        # U+202E flips rendering so "exe.txt" displays as "txt.exe".
        spoofed = "invoice\u202Etxt.exe"
        result = sanitize_filename(spoofed)
        assert "\u202E" not in result

    @pytest.mark.parametrize("illegal", ['a<b.mp3', 'a>b.mp3', 'a:b.mp3', 'a"b.mp3', "a|b.mp3"])
    def test_replaces_illegal_windows_characters(self, illegal: str) -> None:
        assert not set(sanitize_filename(illegal)) & set('<>:"|?*')

    def test_strips_control_characters(self) -> None:
        assert "\x00" not in sanitize_filename("bad\x00name.mp3")

    def test_empty_and_dots_fall_back(self) -> None:
        assert sanitize_filename("") == "media"
        assert sanitize_filename("...") == "media"
        assert sanitize_filename("   ") == "media"

    def test_truncates_long_names_but_keeps_extension(self) -> None:
        result = sanitize_filename("א" * 500 + ".mp3")
        assert len(result) < 200
        assert result.endswith(".mp3")

    def test_rejects_non_string(self) -> None:
        with pytest.raises(SecurityError) as info:
            sanitize_filename(None)  # type: ignore[arg-type]
        assert info.value.code is ErrorCode.INVALID_FILENAME


class TestUniqueFilename:
    def test_returns_free_name(self, tmp_path: Path) -> None:
        assert unique_filename(tmp_path, "שיחה.mp3").name == "שיחה.mp3"

    def test_disambiguates_existing(self, tmp_path: Path) -> None:
        (tmp_path / "שיחה.mp3").write_bytes(b"x")
        result = unique_filename(tmp_path, "שיחה.mp3")
        assert result.name != "שיחה.mp3"
        assert result.name.startswith("שיחה-")
        assert result.suffix == ".mp3"


class TestResolveWithin:
    def test_allows_path_inside_root(self, tmp_path: Path) -> None:
        root = tmp_path / "exports"
        root.mkdir()
        target = root / "out.txt"
        target.write_text("x", encoding="utf-8")
        assert resolve_within(target, [root]) == target.resolve()

    def test_allows_the_root_itself(self, tmp_path: Path) -> None:
        assert resolve_within(tmp_path, [tmp_path]) == tmp_path.resolve()

    def test_blocks_traversal(self, tmp_path: Path) -> None:
        root = tmp_path / "exports"
        root.mkdir()
        with pytest.raises(SecurityError) as info:
            resolve_within(root / ".." / ".." / "secret.txt", [root])
        assert info.value.code is ErrorCode.PATH_NOT_ALLOWED

    def test_blocks_sibling_directory(self, tmp_path: Path) -> None:
        root = tmp_path / "exports"
        other = tmp_path / "private"
        root.mkdir()
        other.mkdir()
        with pytest.raises(SecurityError):
            resolve_within(other / "keys.txt", [root])

    def test_rejects_empty_root_list(self, tmp_path: Path) -> None:
        with pytest.raises(SecurityError):
            resolve_within(tmp_path, [])


class TestIsWithin:
    def test_file_inside_root(self, tmp_path: Path) -> None:
        root = tmp_path / "media"
        root.mkdir()
        target = root / "clip.mp3"
        target.write_text("x", encoding="utf-8")
        assert is_within(target, root)

    def test_the_root_itself(self, tmp_path: Path) -> None:
        assert is_within(tmp_path, tmp_path)

    def test_blocks_traversal(self, tmp_path: Path) -> None:
        root = tmp_path / "media"
        root.mkdir()
        assert not is_within(root / ".." / "secret.mp3", root)

    def test_blocks_sibling_directory(self, tmp_path: Path) -> None:
        root = tmp_path / "media"
        other = tmp_path / "private"
        root.mkdir()
        other.mkdir()
        assert not is_within(other / "keys.txt", root)

    def test_missing_file_inside_root_still_counts(self, tmp_path: Path) -> None:
        # The media route checks containment of a path it has already found on
        # disk, but containment itself must not depend on the file existing.
        root = tmp_path / "media"
        root.mkdir()
        assert is_within(root / "not-yet.mp3", root)

    def test_accepts_a_redirected_resolution(self, tmp_path: Path, monkeypatch) -> None:
        # Windows app-container redirection resolves a file under the package
        # cache while its directory resolves to the real location. The
        # normalised absolute form is what keeps such a file playable.
        root = tmp_path / "media"
        root.mkdir()
        target = root / "clip.mp3"
        target.write_text("x", encoding="utf-8")

        real_resolve = Path.resolve

        def fake_resolve(self, strict=False):  # type: ignore[no-untyped-def]
            if self == target:
                return tmp_path / "LocalCache" / "Local" / "media" / "clip.mp3"
            return real_resolve(self, strict) if strict else real_resolve(self)

        monkeypatch.setattr(Path, "resolve", fake_resolve)
        assert is_within(target, root)


class TestValidateOutputDir:
    def test_defaults_when_empty(self, tmp_path: Path) -> None:
        default = tmp_path / "exports"
        assert validate_output_dir(None, default) == default
        assert validate_output_dir("", default) == default

    def test_creates_and_accepts_hebrew_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "תמלולים" / "ישיבות"
        result = validate_output_dir(target, tmp_path / "default")
        assert result.is_dir()
        assert result == target.resolve()

    def test_rejects_a_file(self, tmp_path: Path) -> None:
        target = tmp_path / "notadir.txt"
        target.write_text("x", encoding="utf-8")
        with pytest.raises(SecurityError):
            validate_output_dir(target, tmp_path / "default")


class TestConstantTimeEquals:
    def test_matches(self) -> None:
        assert constant_time_equals("abc", "abc")

    def test_differs(self) -> None:
        assert not constant_time_equals("abc", "abd")
        assert not constant_time_equals("abc", "")

    def test_handles_unicode(self) -> None:
        assert constant_time_equals("סיסמה", "סיסמה")
        assert not constant_time_equals("סיסמה", "סיסמא")


class TestRedactPaths:
    def test_redacts_windows_path_keeping_basename(self) -> None:
        result = redact_paths(r"failed to open C:\Users\Dana Cohen\Videos\ישיבה.mp4")
        assert "Dana Cohen" not in result
        assert "ישיבה.mp4" in result

    def test_redacts_posix_path(self) -> None:
        result = redact_paths("cannot read /home/dana/recordings/a.mp3")
        assert "/home/dana" not in result
        assert "a.mp3" in result

    def test_redacts_unc_path(self) -> None:
        result = redact_paths(r"\\FILESERVER\private$\meeting.wav")
        assert "FILESERVER" not in result

    def test_redacts_a_repr_escaped_path(self) -> None:
        """``str(OSError)`` embeds the path as a repr, doubling backslashes.

        Regression: a pattern matching a single separator fragments on "\\\\"
        and leaks every directory name.
        """
        message = (
            "[WinError 3] The system cannot find the path specified: "
            r"'C:\\Users\\Dana Cohen\\Private Recordings\\confidential.mp3'"
        )
        result = redact_paths(message)
        assert "Dana Cohen" not in result
        assert "Private Recordings" not in result
        assert "confidential.mp3" in result

    def test_redacts_a_path_followed_by_prose(self) -> None:
        result = redact_paths(r"could not read C:\Users\Dana\a.mp3 because it was busy")
        assert "Dana" not in result
        assert "a.mp3" in result
        assert "because it was busy" in result

    def test_leaves_ordinary_text_alone(self) -> None:
        message = "the model could not be loaded"
        assert redact_paths(message) == message
