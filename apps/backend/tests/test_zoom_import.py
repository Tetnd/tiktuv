"""Zoom link parsing and the URL-import fetcher.

Everything here is offline: the network-facing parts are the thin ``resolve``
and ``_download`` wrappers, and what they depend on — URL recognition, page
scraping, media selection, filename safety — is pure and tested directly.
"""

from __future__ import annotations

import pytest

from tiktuv.errors import ErrorCode
from tiktuv.importers import zoom

EXAMPLE = (
    "https://us06web.zoom.us/rec/play/g-sIMAh9_mspB3kBmg_k3tVoYBMRvLkM6E4QZCaOYrYWcoDRs3jmh"
    "-x-WMj9TT1OB2zkpj_bvtdAWvRj.JcdUMGI55kC3GbOA?accessLevel=meeting&canPlayFromShare=true"
)

#: Shaped like the real page: a JavaScript object literal, not JSON.
PAGE = """
<html><script nonce="x">
window.__data__ = {
logo: { img: "", link: 'https://us06web.zoom.us/', alt: "Zoom Logo" },
meetingId: '',
fileId: 'MM0CWzx7jG19HoXn0zr8uRsuVROlPsCtDBf.6pYj78g5AdfD89oN',
topic: 'ישיבת הנהלה שבועית',
isLogin: false
};
</script></html>
"""


class TestIsZoomUrl:
    @pytest.mark.parametrize(
        "url",
        [
            EXAMPLE,
            "https://us06web.zoom.us/rec/share/abc.def",
            "https://zoom.us/rec/play/xyz",
            "https://acme.zoom.us/rec/share/token",
            "HTTPS://US06WEB.ZOOM.US/REC/PLAY/X",
        ],
    )
    def test_accepts_recording_links(self, url: str) -> None:
        assert zoom.is_zoom_url(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://us06web.zoom.us/j/12345",          # a live meeting, not a recording
            "https://us06web.zoom.us/",                  # no recording path
            "https://youtube.com/watch?v=x",             # another service
            "https://notzoom.us/rec/play/x",             # different domain
            "https://zoom.us.evil.com/rec/play/x",       # lookalike host
            "ftp://zoom.us/rec/play/x",                  # wrong scheme
            "",
            "not a url",
        ],
    )
    def test_rejects_everything_else(self, url: str) -> None:
        assert zoom.is_zoom_url(url) is False

    def test_tolerates_surrounding_whitespace(self) -> None:
        assert zoom.is_zoom_url(f"  {EXAMPLE}  ") is True


class TestExtractFileId:
    def test_reads_the_javascript_literal(self) -> None:
        assert zoom.extract_file_id(PAGE).startswith("MM0CWzx7jG19")

    def test_reads_a_json_style_key(self) -> None:
        assert zoom.extract_file_id('{"fileId": "abc123"}') == "abc123"

    def test_reports_a_missing_id_clearly(self) -> None:
        with pytest.raises(zoom.ZoomError) as info:
            zoom.extract_file_id("<html>nothing useful</html>")
        assert info.value.code is ErrorCode.MEDIA_CORRUPT
        assert "recording id" in info.value.detail


class TestParseEmbeddedData:
    def test_extracts_single_quoted_fields(self) -> None:
        values = zoom.parse_embedded_data(PAGE)
        assert values["fileId"].startswith("MM0CWzx7")
        assert values["topic"] == "ישיבת הנהלה שבועית"

    def test_keeps_hebrew_intact(self) -> None:
        assert zoom.parse_embedded_data(PAGE)["topic"] == "ישיבת הנהלה שבועית"

    def test_survives_an_empty_page(self) -> None:
        assert zoom.parse_embedded_data("") == {}


class TestSelectMediaUrl:
    def test_prefers_the_url_the_player_uses(self) -> None:
        result = {
            "audioUrl": "https://ssrweb.zoom.us/a.m4a",
            "mp4Url": "https://ssrweb.zoom.us/b.mp4",
            "viewMp4Url": "https://ssrweb.zoom.us/c.mp4",
        }
        assert zoom.select_media_url(result) == "https://ssrweb.zoom.us/c.mp4"

    def test_falls_back_through_the_alternatives(self) -> None:
        assert zoom.select_media_url({"audioUrl": "https://x/a.m4a"}) == "https://x/a.m4a"

    def test_ignores_empty_and_relative_values(self) -> None:
        with pytest.raises(zoom.ZoomError) as info:
            zoom.select_media_url({"viewMp4Url": "", "mp4Url": "/relative/path.mp4"})
        assert info.value.code is ErrorCode.MEDIA_NO_AUDIO

    def test_reports_when_there_is_no_stream(self) -> None:
        with pytest.raises(zoom.ZoomError):
            zoom.select_media_url({"chapterUrl": "/nws/x"})


class TestRecordingStartTime:
    def test_reads_epoch_milliseconds(self) -> None:
        started = zoom.recording_started_at({"fileStartTime": 1779977005000}, "")
        assert started is not None
        assert started.year == 2026 and started.month == 5 and started.day == 28

    def test_reads_the_stamp_in_the_media_path(self) -> None:
        """The only source available for an anonymously shared link."""
        started = zoom.recording_started_at(
            {}, "https://ssrweb.zoom.us/replay03/2026/05/28/X/GMT20260528-140325_R.mp4"
        )
        assert started is not None
        assert (started.year, started.month, started.day) == (2026, 5, 28)

    def test_ignores_nonsense(self) -> None:
        assert zoom.recording_started_at({"fileStartTime": 0}, "") is None
        assert zoom.recording_started_at({}, "https://x/no-stamp.mp4") is None


class TestTitleAndFilename:
    def test_prefers_the_meeting_topic(self) -> None:
        assert zoom.choose_title({"topic": "שיעור 3"}, {}) == "שיעור 3"

    def test_falls_back_to_the_embedded_data(self) -> None:
        assert zoom.choose_title({}, {"topic": "מפגש"}) == "מפגש"

    def test_uses_the_recording_date_when_there_is_no_topic(self) -> None:
        # Zoom exposes no topic on an anonymous share link, so a batch of
        # imports would otherwise all share one name.
        title = zoom.choose_title({"fileStartTime": 1779977005000}, {}, "")
        assert title.startswith("Zoom 2026-05-28")

    def test_a_topic_still_wins_over_the_date(self) -> None:
        title = zoom.choose_title(
            {"topic": "ישיבה", "fileStartTime": 1779977005000}, {}, ""
        )
        assert title == "ישיבה"

    def test_has_a_last_resort(self) -> None:
        assert zoom.choose_title({}, {}) == "Zoom recording"

    def test_dated_names_differ_between_recordings(self) -> None:
        first = zoom.choose_title({"fileStartTime": 1779977005000}, {}, "")
        second = zoom.choose_title({"fileStartTime": 1779977005000 + 86_400_000}, {}, "")
        assert first != second

    def _recording(self, title: str) -> zoom.ZoomRecording:
        return zoom.ZoomRecording(
            title=title, media_url="https://x/y.mp4", duration_seconds=1, size_bytes=1
        )

    def test_filename_keeps_hebrew(self) -> None:
        assert self._recording("ישיבת הנהלה").filename == "ישיבת הנהלה.mp4"

    def test_filename_strips_path_separators(self) -> None:
        name = self._recording("a/b\\c").filename
        assert "/" not in name and "\\" not in name

    def test_filename_strips_windows_illegal_characters(self) -> None:
        assert not set(self._recording('a<b>c:d"e|f?g*h').filename) & set('<>:"|?*')

    def test_filename_is_bounded(self) -> None:
        assert len(self._recording("א" * 500).filename) < 160

    def test_blank_title_still_yields_a_filename(self) -> None:
        assert self._recording("   ").filename == "zoom-recording.mp4"

    def test_always_ends_in_mp4(self) -> None:
        assert self._recording("meeting").filename.endswith(".mp4")


class TestPasscodeDetection:
    def test_detects_the_gate(self) -> None:
        assert zoom.needs_passcode('<input name="passwd" type="password">') is True

    def test_ignores_an_ordinary_page(self) -> None:
        assert zoom.needs_passcode(PAGE) is False


class TestResolveGuards:
    def test_rejects_a_non_zoom_url_before_any_request(self) -> None:
        # No network is touched: the guard runs first.
        with pytest.raises(zoom.ZoomError) as info:
            zoom.resolve("https://example.com/video.mp4")
        assert info.value.code is ErrorCode.INVALID_REQUEST


class TestMediaHeaders:
    def test_sends_a_zoom_referer(self) -> None:
        """The media host answers 403 without it."""
        recording = zoom.ZoomRecording(
            title="x", media_url="https://ssrweb.zoom.us/a.mp4", duration_seconds=1,
            size_bytes=1,
        )
        headers = zoom.media_request_headers(recording)
        assert headers["Referer"].startswith("https://zoom.us")
        assert "User-Agent" in headers
