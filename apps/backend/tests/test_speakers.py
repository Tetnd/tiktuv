"""Speaker recognition: turn alignment, the DeepSeek labeller, and the route."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tiktuv.asr.soniox import _smooth_word_speakers, _TokenWord, _turns_from_words
from tiktuv.diarization import SpeakerTurn
from tiktuv.diarization.deepseek import (
    label_speakers,
    normalise_speaker,
    parse_names,
    parse_reply,
)
from tiktuv.diarization.turns import apply_turns, smooth_turns
from tiktuv.errors import ModelError
from tiktuv.schemas import JobStatus, Segment, SpeakerMethod
from tiktuv.speakers import resolve_method

from .conftest import requires_ffmpeg
from .test_api import import_media, wait_for_status


def seg(i: int, start: float, end: float, text: str, speaker: str | None = None) -> Segment:
    return Segment(id=i, start=start, end=end, text=text, speaker=speaker)


# ------------------------------------------------------------ turn smoothing
class TestSmoothTurns:
    def test_a_half_second_flip_inside_one_speaker_is_absorbed(self) -> None:
        turns = [
            SpeakerTurn(0.0, 10.0, "1"),
            SpeakerTurn(10.0, 10.4, "2"),
            SpeakerTurn(10.4, 20.0, "1"),
        ]
        assert smooth_turns(turns) == [SpeakerTurn(0.0, 20.0, "1")]

    def test_a_real_turn_is_kept(self) -> None:
        turns = [SpeakerTurn(0.0, 8.0, "1"), SpeakerTurn(8.0, 14.0, "2")]
        assert smooth_turns(turns) == turns

    def test_a_longer_interjection_between_the_same_speaker_survives(self) -> None:
        turns = [
            SpeakerTurn(0.0, 10.0, "1"),
            SpeakerTurn(10.0, 12.5, "2"),
            SpeakerTurn(12.5, 20.0, "1"),
        ]
        assert [t.speaker for t in smooth_turns(turns)] == ["1", "2", "1"]


# ------------------------------------------------------------- applying turns
class TestApplyTurns:
    def test_each_segment_takes_the_speaker_who_holds_it(self) -> None:
        segments = [seg(0, 0, 5, "שלום לכולם"), seg(1, 5, 10, "תודה רבה")]
        turns = [SpeakerTurn(0, 5, "1"), SpeakerTurn(5, 10, "2")]
        out = apply_turns(segments, turns)
        assert [s.speaker for s in out] == ["1", "2"]

    def test_a_segment_spanning_a_handover_is_split_on_a_word_boundary(self) -> None:
        text = "כן בטח. ומה לגבי התקציב של השנה הבאה"
        out = apply_turns([seg(0, 0, 10, text)], [SpeakerTurn(0, 3, "1"), SpeakerTurn(3, 10, "2")])
        assert [s.speaker for s in out] == ["1", "2"]
        assert out[0].text == "כן בטח."  # cut after the sentence end
        # Nothing is lost or reordered.
        assert " ".join(s.text for s in out) == text
        assert out[0].end <= out[1].start + 0.001
        assert [s.id for s in out] == [0, 1]

    def test_a_sliver_of_another_speaker_does_not_split_a_segment(self) -> None:
        text = "זה משפט ארוך שאדם אחד אומר ברצף"
        out = apply_turns(
            [seg(0, 0, 8, text)],
            [SpeakerTurn(0, 7.5, "1"), SpeakerTurn(7.5, 8, "2")],
        )
        assert len(out) == 1
        assert out[0].speaker == "1"

    def test_split_pieces_keep_real_word_timings(self) -> None:
        from tiktuv.schemas import Word

        words = [Word(text=w, start=i, end=i + 0.9) for i, w in enumerate("א ב ג ד".split())]
        segment = Segment(id=0, start=0, end=4, text="א ב ג ד", words=words)
        out = apply_turns([segment], [SpeakerTurn(0, 2, "1"), SpeakerTurn(2, 4, "2")])
        assert [s.text for s in out] == ["א ב", "ג ד"]
        assert [len(s.words) for s in out] == [2, 2]

    def test_a_segment_no_turn_reaches_inherits_the_previous_speaker(self) -> None:
        segments = [seg(0, 0, 5, "ראשון"), seg(1, 20, 25, "שני")]
        out = apply_turns(segments, [SpeakerTurn(0, 5, "1")])
        assert [s.speaker for s in out] == ["1", "1"]

    def test_no_turns_changes_nothing(self) -> None:
        segments = [seg(0, 0, 5, "x")]
        assert apply_turns(segments, []) == segments


# ------------------------------------------------- Soniox word-level smoothing
class TestSonioxFlicker:
    @staticmethod
    def w(text: str, start: float, speaker: str) -> _TokenWord:
        return _TokenWord(text=text, start=start, end=start + 0.3, speaker=speaker)

    def test_a_single_word_flicker_is_reassigned(self) -> None:
        words = [
            self.w("אני", 0, "1"),
            self.w("חושב", 0.4, "2"),
            self.w("שכן", 0.8, "1"),
        ]
        assert [w.speaker for w in _smooth_word_speakers(words)] == ["1", "1", "1"]

    def test_a_two_word_reply_is_a_real_speaker(self) -> None:
        words = [
            self.w("אני", 0, "1"),
            self.w("כן", 0.4, "2"),
            self.w("בטח", 0.8, "2"),
            self.w("אז", 1.2, "1"),
        ]
        assert [w.speaker for w in _smooth_word_speakers(words)] == ["1", "2", "2", "1"]

    def test_words_become_contiguous_turns(self) -> None:
        words = [self.w("א", 0, "1"), self.w("ב", 0.4, "1"), self.w("ג", 0.8, "2")]
        turns = _turns_from_words(words)
        assert [t.speaker for t in turns] == ["1", "2"]


# --------------------------------------------------------- DeepSeek labelling
def _chat(content: object) -> object:
    class _R:
        status_code = 200

        def json(self) -> dict:
            return {"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}]}

    return _R()


class TestDeepSeekParsing:
    def test_speaker_ids_are_normalised(self) -> None:
        assert normalise_speaker("S2") == "2"
        assert normalise_speaker("speaker_3") == "3"
        assert normalise_speaker("דובר 1") == "1"
        assert normalise_speaker(2) == "2"
        assert normalise_speaker("nobody") is None
        assert normalise_speaker("99") is None

    def test_unknown_ids_are_ignored_and_missing_labels_inherit(self) -> None:
        batch = [seg(0, 0, 2, "שלום"), seg(1, 2, 4, "מה שלומך"), seg(2, 4, 6, "טוב")]
        reply = parse_reply(
            json.dumps({"labels": [{"id": 0, "speaker": "1"}, {"id": 99, "speaker": "2"}]}),
            batch,
            {},
        )
        assert reply.labels == {0: "1", 1: "1", 2: "1"}

    def test_a_split_must_preserve_the_exact_words(self) -> None:
        batch = [seg(0, 0, 4, "כן. ומה איתך")]
        good = {"splits": [{"id": 0, "parts": [
            {"speaker": "1", "text": "כן."}, {"speaker": "2", "text": "ומה איתך"}]}]}
        assert parse_reply(json.dumps(good), batch, {}).splits[0] == [
            ("1", "כן."),
            ("2", "ומה איתך"),
        ]
        rewritten = {"splits": [{"id": 0, "parts": [
            {"speaker": "1", "text": "כן בהחלט."}, {"speaker": "2", "text": "ומה איתך"}]}]}
        assert parse_reply(json.dumps(rewritten), batch, {}).splits == {}

    def test_a_name_needs_evidence_that_is_really_in_the_transcript(self) -> None:
        segments = [seg(0, 0, 3, "שלום אני דנה מהחברה", "1"), seg(1, 3, 6, "נעים מאוד", "2")]
        reply = json.dumps({"names": [
            {"speaker": "1", "name": "דנה", "evidence": "אני דנה"},
            {"speaker": "2", "name": "יוסי", "evidence": "אני יוסי"},  # never said
        ]})
        assert parse_names(reply, segments) == {"1": "דנה"}

    def test_a_name_must_occur_inside_its_own_evidence(self) -> None:
        segments = [seg(0, 0, 3, "שלום אני דנה", "1")]
        reply = json.dumps({"names": [{"speaker": "1", "name": "רונית", "evidence": "אני דנה"}]})
        assert parse_names(reply, segments) == {}


class TestLabelSpeakers:
    def test_labels_flow_back_and_context_is_carried(self, monkeypatch: pytest.MonkeyPatch) -> None:
        segments = [seg(i, i * 3.0, i * 3.0 + 2.5, f"משפט {i}") for i in range(6)]
        sent: list[dict] = []

        def fake_post(payload: dict, headers: dict) -> object:
            body = json.loads(payload["messages"][1]["content"])
            sent.append(body)
            labels = [
                {"id": s["id"], "speaker": "1" if s["id"] % 2 == 0 else "2"}
                for s in body["segments"]
            ]
            return _chat({"speakers": [{"id": "1", "profile": "שואל"}], "labels": labels})

        monkeypatch.setattr("tiktuv.diarization.deepseek._post_with_retry", fake_post)
        out = label_speakers("key", segments)
        assert [s.speaker for s in out] == ["1", "2", "1", "2", "1", "2"]
        # The pause before each segment is sent as a turn-taking cue.
        assert "gap" not in sent[0]["segments"][0]
        assert sent[0]["segments"][1]["gap"] == 0.5

    def test_a_split_creates_two_segments_inside_the_original_span(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        segments = [seg(0, 0.0, 4.0, "כן. ומה איתך")]

        def fake_post(payload: dict, headers: dict) -> object:
            return _chat({
                "labels": [{"id": 0, "speaker": "1"}],
                "splits": [{"id": 0, "parts": [
                    {"speaker": "1", "text": "כן."}, {"speaker": "2", "text": "ומה איתך"}]}],
            })

        monkeypatch.setattr("tiktuv.diarization.deepseek._post_with_retry", fake_post)
        out = label_speakers("key", segments)
        assert [(s.speaker, s.text) for s in out] == [("1", "כן."), ("2", "ומה איתך")]
        assert out[0].start == 0.0 and out[-1].end == 4.0
        assert out[0].end == pytest.approx(out[1].start)

    def test_an_empty_reply_fails_loudly_instead_of_doing_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "tiktuv.diarization.deepseek._post_with_retry", lambda p, h: _chat({})
        )
        with pytest.raises(ModelError):
            label_speakers("key", [seg(0, 0, 2, "שלום")])


class TestMethodResolution:
    def test_auto_never_uploads_audio(self) -> None:
        assert resolve_method(SpeakerMethod.AUTO, soniox_key="s", deepseek_key="d") is (
            SpeakerMethod.DEEPSEEK
        )

    def test_auto_without_deepseek_asks_for_a_key_rather_than_using_soniox(self) -> None:
        with pytest.raises(ModelError):
            resolve_method(SpeakerMethod.AUTO, soniox_key="s", deepseek_key=None)

    def test_an_explicit_choice_needs_its_own_key(self) -> None:
        with pytest.raises(ModelError) as err:
            resolve_method(SpeakerMethod.SONIOX, soniox_key=None, deepseek_key="d")
        assert err.value.code.value == "SONIOX_KEY_MISSING"
        assert resolve_method(SpeakerMethod.SONIOX, soniox_key="s", deepseek_key=None) is (
            SpeakerMethod.SONIOX
        )


# ------------------------------------------------------------------ the route
@requires_ffmpeg
@pytest.mark.usefixtures("client")
class TestSpeakersApi:
    def _job(self, client: TestClient, sample_mp3: Path) -> str:
        token = import_media(client, sample_mp3)
        job_id = client.post(
            "/api/jobs",
            json={"media_token": token, "settings": {"enable_proofreading": False}},
        ).json()["id"]
        wait_for_status(client, job_id, JobStatus.COMPLETED, JobStatus.FAILED)
        return job_id

    def test_deepseek_labels_a_stored_transcript(
        self, client: TestClient, sample_mp3: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        job_id = self._job(client, sample_mp3)

        # Without a key the user gets a clear error.
        response = client.post(f"/api/jobs/{job_id}/speakers", json={"method": "deepseek"})
        assert response.status_code == 422
        assert response.json()["code"] == "DEEPSEEK_KEY_MISSING"

        client.put("/api/deepseek/key", json={"api_key": "sk-deepseek-test"})

        def fake_post(payload: dict, headers: dict) -> object:
            if "שמות האמיתיים" in payload["messages"][0]["content"]:
                return _chat({"names": []})  # names are searched by default
            body = json.loads(payload["messages"][1]["content"])
            return _chat({"labels": [
                {"id": s["id"], "speaker": "1" if s["id"] % 2 == 0 else "2"}
                for s in body["segments"]
            ]})

        monkeypatch.setattr("tiktuv.diarization.deepseek._post_with_retry", fake_post)
        response = client.post(f"/api/jobs/{job_id}/speakers", json={"method": "auto"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["method"] == "deepseek"
        assert body["speaker_count"] >= 1
        assert body["transcript"]["speakers"]["1"] == "דובר 1"

        stored = client.get(f"/api/jobs/{job_id}/transcript").json()
        assert stored["segments"] == body["transcript"]["segments"]
        assert all(s["speaker"] for s in stored["segments"])

    def test_404_without_a_transcript(self, client: TestClient) -> None:
        response = client.post("/api/jobs/missing/speakers", json={"method": "auto"})
        assert response.status_code == 404


# ------------------------------------------------------------------- names
class TestNamesAreUsed:
    def test_only_default_labels_count_as_unnamed(self) -> None:
        from tiktuv.speakers import is_default_label

        assert is_default_label("דובר 1")
        assert is_default_label("  דובר 12 ")
        assert is_default_label("")
        assert not is_default_label("דנה")
        assert not is_default_label("דובר ראשי")

    def test_names_typed_by_the_user_survive_re_recognition(self) -> None:
        from tiktuv.speakers import carry_over_names

        old = [seg(0, 0, 10, "א", "1"), seg(1, 10, 20, "ב", "2")]
        # The new run numbers the same people the other way round.
        new = [seg(0, 0, 10, "א", "2"), seg(1, 10, 20, "ב", "1")]
        merged, carried = carry_over_names(
            old, {"1": "דנה", "2": "דובר 2"}, new, {"1": "דובר 1", "2": "דובר 2"}
        )
        assert carried == 1
        assert merged["2"] == "דנה"  # the one who spoke 0-10 keeps her name
        assert merged["1"] == "דובר 1"

    def test_a_name_is_not_carried_to_an_unclear_match(self) -> None:
        from tiktuv.speakers import carry_over_names

        old = [seg(0, 0, 10, "א", "1")]
        # The new speaker overlaps the named one for only a quarter of their time.
        new = [seg(0, 8, 16, "ב", "1")]
        merged, carried = carry_over_names(old, {"1": "דנה"}, new, {"1": "דובר 1"})
        assert carried == 0 and merged["1"] == "דובר 1"

    def test_names_are_searched_only_for_unnamed_speakers_and_never_reused(self) -> None:
        segments = [seg(0, 0, 3, "שלום אני דנה", "1"), seg(1, 3, 6, "ואני יוסי", "2")]
        reply = json.dumps({"names": [
            {"speaker": "1", "name": "דנה", "evidence": "אני דנה"},
            {"speaker": "2", "name": "יוסי", "evidence": "ואני יוסי"},
        ]})
        # Speaker 1 already has a name chosen by the user; "יוסי" is taken too.
        assert parse_names(reply, segments, wanted={"2"}, taken={"יוסי"}) == {}
        assert parse_names(reply, segments, wanted={"2"}, taken={"דנה"}) == {"2": "יוסי"}

    def test_a_name_said_late_in_a_long_recording_is_still_found(self) -> None:
        from tiktuv.diarization.deepseek import name_context

        filler = [seg(i, i, i + 1, f"משפט רגיל מספר {i}", "1") for i in range(200)]
        filler.append(seg(200, 200, 201, "תודה יוסי על הזמן", "1"))
        filler.append(seg(201, 201, 202, "בשמחה", "2"))
        lines = name_context(filler)
        assert any("תודה יוסי" in line for line in lines)
        assert any("בשמחה" in line for line in lines)  # the reply that names who יוסי is
        assert len(lines) < 80  # not the whole recording

    def test_the_analysis_follows_a_rename(self) -> None:
        from tiktuv.schemas import ActionItem, SpeakerQuote, SpeakerSummary, TranscriptAnalysis
        from tiktuv.speakers import relabel_analysis

        analysis = TranscriptAnalysis(
            job_id="j",
            speaker_summaries=[SpeakerSummary(speaker="דובר 1", summary="x")],
            speaker_quotes=[SpeakerQuote(speaker="דובר 2", quotes=["q"])],
            action_items=[ActionItem(task="t", assignee="דובר 1"), ActionItem(task="u")],
        )
        out = relabel_analysis(analysis, {"דובר 1": "דנה"})
        assert out.speaker_summaries[0].speaker == "דנה"
        assert out.speaker_quotes[0].speaker == "דובר 2"
        assert [a.assignee for a in out.action_items] == ["דנה", None]


@requires_ffmpeg
@pytest.mark.usefixtures("client")
class TestNamesApi:
    def test_names_found_for_a_transcript_whose_speakers_are_still_numbered(
        self, client: TestClient, sample_mp3: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        token = import_media(client, sample_mp3)
        job_id = client.post(
            "/api/jobs",
            json={"media_token": token, "settings": {"enable_proofreading": False}},
        ).json()["id"]
        wait_for_status(client, job_id, JobStatus.COMPLETED, JobStatus.FAILED)
        client.put("/api/deepseek/key", json={"api_key": "sk-test"})

        # No speakers yet: the user is told to recognise them first.
        assert client.post(f"/api/jobs/{job_id}/speakers/names").status_code == 422

        stored = client.get(f"/api/jobs/{job_id}/transcript").json()
        first = stored["segments"][0]["text"]
        stored["segments"] = [
            {**s, "speaker": "1" if i == 0 else "2"} for i, s in enumerate(stored["segments"])
        ]
        stored["speakers"] = {"1": "דובר 1", "2": "מיכל"}  # 2 was named by the user
        assert client.put(
            f"/api/jobs/{job_id}/transcript",
            json={"segments": stored["segments"], "speakers": stored["speakers"]},
        ).status_code == 200

        def fake_post(payload: dict, headers: dict) -> object:
            return _chat({"names": [
                {"speaker": "1", "name": first.split()[0], "evidence": first.split()[0]},
                {"speaker": "2", "name": "אחר", "evidence": "אחר"},
            ]})

        monkeypatch.setattr("tiktuv.diarization.deepseek._post_with_retry", fake_post)
        response = client.post(f"/api/jobs/{job_id}/speakers/names")
        assert response.status_code == 200, response.text
        speakers = response.json()["transcript"]["speakers"]
        assert speakers["2"] == "מיכל"  # a typed name is never overwritten
        assert speakers["1"] == first.split()[0]
        assert client.get(f"/api/jobs/{job_id}/transcript").json()["speakers"] == speakers


# ------------------------------------------------------------ renaming a speaker
class TestRenameEverywhere:
    def test_a_label_is_replaced_only_where_it_is_that_label(self) -> None:
        from tiktuv.speakers import replace_label

        text = "דובר 1 שאל את דובר 12 ואז לדובר 1 ענו. לא נוגע בגדובר 1, אבל כן במדובר 1"
        out = replace_label(text, "דובר 1", "דנה")
        assert out == "דנה שאל את דובר 12 ואז לדנה ענו. לא נוגע בגדובר 1, אבל כן במדנה"

    def test_changes_are_read_from_the_speaker_map(self) -> None:
        from tiktuv.speakers import label_changes

        # Speaker 1 was never renamed, so the analysis called them "דובר 1".
        assert label_changes({}, {"1": "דנה", "2": "דובר 2"}) == {"דובר 1": "דנה"}
        # A second rename starts from the name the analysis now holds.
        assert label_changes({"1": "דנה"}, {"1": "דנה כהן"}) == {"דנה": "דנה כהן"}
        # Clearing the field must not wipe the label out of the analysis.
        assert label_changes({"1": "דנה"}, {"1": "  "}) == {}

    def test_every_tab_of_the_analysis_follows_a_rename(self) -> None:
        from tiktuv.schemas import ActionItem, SpeakerQuote, SpeakerSummary, TranscriptAnalysis
        from tiktuv.speakers import relabel_analysis

        analysis = TranscriptAnalysis(
            job_id="j",
            summary="דובר 1 הציג את הנושא ו-דובר 2 הגיב.",
            key_points=["דובר 1 מציע שינוי"],
            speaker_summaries=[SpeakerSummary(speaker="דובר 1", summary="דובר 1 מסביר")],
            speaker_quotes=[SpeakerQuote(speaker="דובר 1", quotes=["שלום"])],
            action_items=[ActionItem(task="דובר 1 ישלח דוח", assignee="דובר 1")],
        )
        out = relabel_analysis(analysis, {"דובר 1": "דנה"})
        assert out.summary == "דנה הציג את הנושא ו-דובר 2 הגיב."
        assert out.key_points == ["דנה מציע שינוי"]
        assert out.speaker_summaries[0].speaker == "דנה"
        assert out.speaker_summaries[0].summary == "דנה מסביר"
        assert out.speaker_quotes[0].speaker == "דנה"
        assert out.action_items[0].assignee == "דנה"
        assert out.action_items[0].task == "דנה ישלח דוח"


@requires_ffmpeg
@pytest.mark.usefixtures("client")
class TestRenameApi:
    def test_saving_a_renamed_speaker_updates_the_stored_analysis(
        self, client: TestClient, sample_mp3: Path
    ) -> None:
        token = import_media(client, sample_mp3)
        job_id = client.post(
            "/api/jobs",
            json={"media_token": token, "settings": {
                "enable_proofreading": False, "enable_diarization": False,
                "enable_analysis": False}},
        ).json()["id"]
        wait_for_status(client, job_id, JobStatus.COMPLETED, JobStatus.FAILED)

        transcript = client.get(f"/api/jobs/{job_id}/transcript").json()
        segments = [{**s, "speaker": "1"} for s in transcript["segments"]]
        assert client.put(
            f"/api/jobs/{job_id}/transcript",
            json={"segments": segments, "speakers": {"1": "דובר 1"}},
        ).status_code == 200

        # An analysis written while the speaker was still "דובר 1".
        state = client.app.state.tiktuv
        from tiktuv.schemas import SpeakerQuote, TranscriptAnalysis

        state.transcripts.save_analysis(TranscriptAnalysis(
            job_id=job_id,
            summary="דובר 1 פתח את הישיבה.",
            speaker_quotes=[SpeakerQuote(speaker="דובר 1", quotes=["שלום"])],
        ))

        assert client.put(
            f"/api/jobs/{job_id}/transcript",
            json={"segments": segments, "speakers": {"1": "ליאור שלין"}},
        ).status_code == 200

        analysis = client.get(f"/api/jobs/{job_id}/analysis").json()
        assert analysis["summary"] == "ליאור שלין פתח את הישיבה."
        assert analysis["speaker_quotes"][0]["speaker"] == "ליאור שלין"
