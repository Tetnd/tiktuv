//! Transcript editor screen: synchronized audio playback and segment editing.
//!
//! Kept as an `EditorState` owned by the root `App` (so toasts, API and screen
//! navigation stay in one place). Each segment is a `Textarea` backed by a
//! `TextareaState`; edits are autosaved to the backend.

use std::collections::HashMap;
use std::time::Duration;

use gpui_kit::base::StyledExt as _;
use gpui_kit::component::button::*;
use gpui_kit::component::input::{Input, InputEvent, InputState, Textarea, TextareaState};
use gpui_kit::component::{ActiveTheme, Disableable as _, Selectable as _};
use gpui_kit::prelude::FluentBuilder as _;
use gpui_kit::{
    AnyElement, AppContext as _, Context, Entity, IntoElement, ParentElement as _, Styled as _,
    Subscription, Window, div, px, relative, rgb,
};

use crate::app::{App, Screen, ToastTone};
use crate::format::*;
use crate::rtl_text::rtl_paragraph;
use crate::models::*;
use crate::player::{Player, SKIP_SECONDS, SPEEDS};

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum SaveState {
    Idle,
    Saving,
    Saved,
    Error,
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum AnalysisTab {
    Summary,
    KeyPoints,
    ActionItems,
    NamedEntities,
    SpeakerSummaries,
    SpeakerQuotes,
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum AnalysisState {
    Idle,
    Loading,
    Ready,
    Error,
}

pub struct EditorState {
    pub job_id: String,
    pub filename: String,
    pub transcript: Transcript,
    pub segments: Vec<Segment>,
    pub speakers: HashMap<String, String>,
    pub states: Vec<Entity<TextareaState>>,
    pub _subscriptions: Vec<Subscription>,
    pub history: Vec<Vec<Segment>>,
    pub future: Vec<Vec<Segment>>,
    pub save_state: SaveState,
    pub current_time: f64,
    pub player: Player,
    pub media_ready: bool,
    pub exporting: bool,
    pub analysis: Option<TranscriptAnalysis>,
    pub analysis_state: AnalysisState,
    pub analysis_error: Option<String>,
    pub analysis_tab: Option<AnalysisTab>,
    pub search_query: Entity<InputState>,
    pub search_replacement: Entity<InputState>,
    pub search_matches: Vec<usize>,
    pub search_active: usize,
    pub search_case_sensitive: bool,
    pub speaker_inputs: HashMap<String, Entity<InputState>>,
}

impl EditorState {
    pub fn new(
        job_id: String,
        filename: String,
        transcript: Transcript,
        window: &mut Window,
        cx: &mut Context<App>,
    ) -> Self {
        let segments = transcript.segments.clone();
        let speakers = transcript.speakers.clone();
        let search_query = cx.new(|cx| InputState::new(window, cx).placeholder(rtl("חיפוש בתמליל…")));
        let search_replacement = cx.new(|cx| InputState::new(window, cx).placeholder(rtl("החלפה ב…")));

        let mut states = Vec::new();
        for segment in &segments {
            let text = segment.text.clone();
            let state = cx.new(|cx| {
                let mut s = TextareaState::new(window, cx);
                s.set_value(rtl(&text), window, cx);
                s.set_text_align(gpui_kit::TextAlign::Right, cx);
                s.auto_grow(1, 12)
            });
            states.push(state);
        }

        let mut subscriptions = Vec::new();
        for (index, state) in states.clone().iter().enumerate() {
            let sub = cx.subscribe_in(state, window, move |this, state, event, _, cx| {
                if matches!(event, InputEvent::Change) {
                    let text = state.read(cx).value().to_string();
                    this.on_segment_edit(index, text, cx);
                }
            });
            subscriptions.push(sub);
        }

        let mut speaker_inputs = HashMap::new();
        let mut speaker_keys: Vec<String> = Vec::new();
        for segment in &segments {
            if let Some(key) = &segment.speaker {
                if !speaker_keys.contains(key) {
                    speaker_keys.push(key.clone());
                }
            }
        }
        for key in speaker_keys {
            let name = speakers.get(&key).cloned().unwrap_or_else(|| key.clone());
            let state = cx.new(|cx| {
                let mut s = InputState::new(window, cx);
                s.set_value(rtl(&name), window, cx);
                s
            });
            speaker_inputs.insert(key, state);
        }
        for (key, state) in speaker_inputs.clone().iter() {
            let key = key.clone();
            let sub = cx.subscribe_in(state, window, move |this, state, event, _, cx| {
                if matches!(event, InputEvent::Change) {
                    let name = rtl(&state.read(cx).value().to_string());
                    this.on_speaker_rename(key.clone(), name, cx);
                }
            });
            subscriptions.push(sub);
        }

        Self {
            job_id,
            filename,
            transcript,
            segments,
            speakers,
            states,
            _subscriptions: subscriptions,
            history: Vec::new(),
            future: Vec::new(),
            save_state: SaveState::Idle,
            current_time: 0.0,
            player: Player::new(),
            media_ready: false,
            exporting: false,
            analysis: None,
            analysis_state: AnalysisState::Idle,
            analysis_error: None,
            analysis_tab: None,
            search_query,
            search_replacement,
            search_matches: Vec::new(),
            search_active: 0,
            search_case_sensitive: false,
            speaker_inputs,
        }
    }
}

fn active_segment_index(segments: &[Segment], time: f64) -> isize {
    for (i, s) in segments.iter().enumerate() {
        if time >= s.start && time < s.end {
            return i as isize;
        }
    }
    let mut candidate = -1;
    for (i, s) in segments.iter().enumerate() {
        if s.start <= time {
            candidate = i as isize;
        }
    }
    candidate
}

impl App {
    pub fn open_transcript(&mut self, id: String, window: &mut Window, cx: &mut Context<Self>) {
        self.screen = Screen::Editor;
        self.editor_loading = true;
        self.editor = None;
        cx.notify();

        let api = self.api.clone();
        let filename = self
            .jobs
            .iter()
            .find(|j| j.id == id)
            .map(|j| j.filename.clone())
            .unwrap_or_else(|| "תמליל".to_string());

        cx.spawn_in(window, async move |this, cx| {
            let result = api.get_transcript(&id).await;
            let _ = this.update_in(cx, |this, window, cx| {
                this.editor_loading = false;
                match result {
                    Ok(t) => {
                        this.editor = Some(EditorState::new(id, filename, t, window, cx));
                        this.start_media_download(window, cx);
                    }
                    Err(e) => {
                        this.toast(ToastTone::Error, e.message, Some(e.remedy));
                    }
                }
                cx.notify();
            });
        })
        .detach();
    }

    pub fn render_editor(&self, cx: &mut Context<Self>) -> AnyElement {
        if self.editor_loading {
            return div()
                .p_10()
                .child(div().text_base().text_color(rgb(0x6b7280)).child(rtl("טוען תמליל…")))
                .into_any_element();
        }
        let Some(editor) = &self.editor else {
            return div()
                .p_10()
                .child(div().text_base().text_color(rgb(0x6b7280)).child(rtl("לא נבחר תמליל.")))
                .into_any_element();
        };

        let filename = editor.filename.clone();
        let segment_count = editor.segments.len();
        let duration = editor.transcript.duration;
        let partial = editor.transcript.partial;
        let save_state = editor.save_state;
        let can_undo = !editor.history.is_empty();
        let can_redo = !editor.future.is_empty();
        let playing = editor.player.playing;
        let speed = editor.player.speed;
        let pos = editor.player.position_seconds();
        let total = editor
            .player
            .duration
            .map(|d| d.as_secs_f64())
            .filter(|d| *d > 0.0)
            .unwrap_or(duration);
        let active = active_segment_index(&editor.segments, editor.current_time);
        let progress = if total > 0.0 { (pos / total).clamp(0.0, 1.0) } else { 0.0 };
        let media_error = editor.player.error.clone();
        let show_transcript = editor.analysis_tab.is_none();
        let analysis_body = self.render_analysis(cx);
        let search_bar = self.search_bar(cx);
        let speaker_bar = self.speaker_rename_bar(cx);

        // -- segment rows -------------------------------------------------
        let mut rows: Vec<AnyElement> = Vec::new();
        for (i, seg) in editor.segments.iter().enumerate() {
            let state = &editor.states[i];
            let is_active = i as isize == active;
            let is_search_match = editor.search_matches.get(editor.search_active) == Some(&i);
            let start = seg.start;
            let speaker_label = seg
                .speaker
                .as_ref()
                .map(|k| editor.speakers.get(k).cloned().unwrap_or_else(|| k.clone()));
            let row = div()
                .flex()
                .flex_row_reverse()
                .items_start()
                .gap_2()
                .px_2()
                .py_2()
                .rounded_lg()
                .when(is_active, |this| this.bg(rgb(0xeef2ff)))
                .when(is_search_match, |this| this.bg(rgb(0xfffbeb)))
                .child(
                    Button::new(format!("seek-{i}"))
                        .ghost()
                        .compact()
                        .label(format_clock(start))
                        .on_click(cx.listener(move |this, _, _, cx| this.seek_to(start, cx))),
                )
                .child(
                    div()
                        .flex_1()
                        .min_w_0()
                        .v_flex()
                        .when(speaker_label.is_some(), |this| {
                            this.child(
                                div()
                                    .text_xs()
                                    .text_color(rgb(0x4f46e5))
                                    .child(rtl(&speaker_label.clone().unwrap_or_default())),
                            )
                        })
                        .child(Textarea::new(state).bordered(false).appearance(false)),
                );
            rows.push(row.into_any_element());
        }

        div()
            .w_full()
            .p_6()
            .v_flex()
            .gap_4()
            // -- header ----------------------------------------------------
            .child(
                div()
                    .flex()
                    .flex_row_reverse()
                    .items_center()
                    .justify_between()
                    .gap_3()
                    .child(
                        div()
                            .min_w_0()
                            .v_flex()
                            .child(div().truncate().text_lg().child(rtl(&truncate_middle(&filename, 60))))
                            .child(
                                div()
                                    .text_xs()
                                    .text_color(rgb(0x6b7280))
                                    .child(rtl(&format!(
                                        "{segment_count} מקטעים · {}{}",
                                        format_clock(duration),
                                        if partial { " · תמליל חלקי" } else { "" }
                                    ))),
                            ),
                    )
                    .child(
                        div()
                            .flex()
                            .flex_row_reverse()
                            .gap_2()
                            .child(save_indicator(save_state))
                            .child(
                                Button::new("undo")
                                    .ghost()
                                    .label(rtl("ביטול פעולה"))
                                    .disabled(!can_undo)
                                    .on_click(cx.listener(|this, _, window, cx| {
                                        this.undo_edit(window, cx)
                                    })),
                            )
                            .child(
                                Button::new("redo")
                                    .ghost()
                                    .label(rtl("ביצוע חוזר"))
                                    .disabled(!can_redo)
                                    .on_click(cx.listener(|this, _, window, cx| {
                                        this.redo_edit(window, cx)
                                    })),
                            )
                            .child(
                                Button::new("copy-all")
                                    .secondary()
                                    .label(rtl("העתק הכול"))
                                    .on_click(cx.listener(|this, _, _, cx| this.copy_all(cx))),
                            )
                            .child(
                                Button::new("export")
                                    .primary()
                                    .label(rtl("ייצוא"))
                                    .on_click(cx.listener(|this, _, _, cx| this.open_export(cx))),
                            )
                            .child(
                                Button::new("close-editor")
                                    .ghost()
                                    .label(rtl("סגירה"))
                                    .on_click(cx.listener(|this, _, _, cx| this.close_editor(cx))),
                            ),
                    ),
            )
            .child(self.analysis_tab_bar(cx))
            .when(show_transcript, |this| {
                this
            // -- player ----------------------------------------------------
            .child(
                div()
                    .rounded_lg()
                    .border_1()
                    .border_color(rgb(0xe5e7eb))
                    .bg(cx.theme().background)
                    .p_3()
                    .v_flex()
                    .gap_2()
                    .child(
                        div()
                            .flex()
                            .flex_row()
                            .items_center()
                            .gap_3()
                            .child(
                                Button::new("skip-back")
                                    .ghost()
                                    .label(rtl("−5s"))
                                    .on_click(cx.listener(|this, _, _, cx| this.skip_media(-SKIP_SECONDS, cx))),
                            )
                            .child(
                                Button::new("play")
                                    .primary()
                                    .label(rtl(if playing { "השהיה" } else { "ניגון" }))
                                    .disabled(!editor.media_ready)
                                    .on_click(cx.listener(|this, _, _, cx| this.toggle_play(cx))),
                            )
                            .child(
                                Button::new("skip-fwd")
                                    .ghost()
                                    .label(rtl("+5s"))
                                    .on_click(cx.listener(|this, _, _, cx| this.skip_media(SKIP_SECONDS, cx))),
                            )
                            .child(
                                div()
                                    .text_sm()
                                    .text_color(rgb(0x6b7280))
                                    .child(rtl(&format!("{} / {}", format_clock(pos), format_clock(total)))),
                            )
                            .child(
                                Button::new("speed")
                                    .ghost()
                                    .label(format!("{speed}×"))
                                    .on_click(cx.listener(|this, _, _, cx| this.cycle_speed(cx))),
                            ),
                    )
                    .child(
                        div()
                            .w_full()
                            .h_2()
                            .rounded_full()
                            .bg(rgb(0xe5e7eb))
                            .child(
                                div()
                                    .h_full()
                                    .rounded_full()
                                    .bg(rgb(0x4f46e5))
                                    .w(relative(progress as f32)),
                            ),
                    )
                    .when(media_error.is_some(), |this| {
                        this.child(
                            div()
                                .text_xs()
                                .text_color(rgb(0xdc2626))
                                .child(rtl_paragraph(media_error.clone().unwrap_or_default())),
                        )
                    }),
            )
            .child(search_bar)
            .child(speaker_bar)
            // -- segments --------------------------------------------------
            .child(
                div()
                    .rounded_lg()
                    .border_1()
                    .border_color(rgb(0xe5e7eb))
                    .bg(cx.theme().background)
                    .p_2()
                    .v_flex()
                    .children(rows),
            )
            })
            .when(!show_transcript, |this| this.child(analysis_body))
            .into_any_element()
    }

    // -- editor actions ---------------------------------------------------
    fn on_segment_edit(&mut self, index: usize, text: String, cx: &mut Context<Self>) {
        let mut next = self
            .editor
            .as_ref()
            .map(|e| e.segments.clone())
            .unwrap_or_default();
        if let Some(s) = next.get_mut(index) {
            s.text = rtl(&text);
            s.edited = true;
        }
        self.commit_segments(next, cx);
    }

    fn commit_segments(&mut self, next: Vec<Segment>, cx: &mut Context<Self>) {
        if let Some(e) = &mut self.editor {
            e.history.push(e.segments.clone());
            if e.history.len() > 60 {
                e.history.remove(0);
            }
            e.future.clear();
            e.segments = next;
        }
        cx.notify();
        self.save_now(cx);
    }

    fn undo_edit(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let Some(prev) = self.editor.as_mut().and_then(|e| e.history.pop()) else {
            return;
        };
        if let Some(e) = &mut self.editor {
            e.future.push(e.segments.clone());
        }
        self.apply_segments(prev, window, cx);
    }

    fn redo_edit(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let Some(next) = self.editor.as_mut().and_then(|e| e.future.pop()) else {
            return;
        };
        if let Some(e) = &mut self.editor {
            e.history.push(e.segments.clone());
        }
        self.apply_segments(next, window, cx);
    }

    fn apply_segments(&mut self, segs: Vec<Segment>, window: &mut Window, cx: &mut Context<Self>) {
        if let Some(e) = &mut self.editor {
            e.segments = segs;
            let texts: Vec<String> = e.segments.iter().map(|s| s.text.clone()).collect();
            for (i, state) in e.states.iter().enumerate() {
                if let Some(text) = texts.get(i) {
                    let _ = state.update(cx, |state, cx| state.set_value(rtl(text), window, cx));
                }
            }
        }
        cx.notify();
        self.save_now(cx);
    }

    fn save_now(&mut self, cx: &mut Context<Self>) {
        let Some(e) = &self.editor else { return };
        let job_id = e.job_id.clone();
        let segments = e.segments.clone();
        let speakers = e.speakers.clone();
        if let Some(e) = &mut self.editor {
            e.save_state = SaveState::Saving;
        }
        let api = self.api.clone();
        cx.notify();
        cx.spawn(async move |this, cx| {
            let result = api.save_transcript(&job_id, &segments, &speakers).await;
            let _ = this.update(cx, |this, cx| {
                if let Some(e) = &mut this.editor {
                    e.save_state = if result.is_ok() { SaveState::Saved } else { SaveState::Error };
                }
                cx.notify();
            });
        })
        .detach();
    }

    fn toggle_play(&mut self, cx: &mut Context<Self>) {
        if let Some(e) = &mut self.editor {
            e.player.toggle();
        }
        cx.notify();
    }

    fn seek_to(&mut self, seconds: f64, cx: &mut Context<Self>) {
        if let Some(e) = &mut self.editor {
            e.player.seek(seconds);
            e.player.play();
            e.current_time = seconds;
        }
        cx.notify();
    }

    fn skip_media(&mut self, delta: f64, cx: &mut Context<Self>) {
        if let Some(e) = &mut self.editor {
            e.player.skip(delta);
            e.current_time = e.player.position_seconds();
        }
        cx.notify();
    }

    fn cycle_speed(&mut self, cx: &mut Context<Self>) {
        if let Some(e) = &mut self.editor {
            let cur = e.player.speed;
            let idx = SPEEDS
                .iter()
                .position(|s| (*s - cur).abs() < 0.01)
                .unwrap_or(2);
            e.player.set_speed(SPEEDS[(idx + 1) % SPEEDS.len()]);
        }
        cx.notify();
    }

    fn copy_all(&mut self, cx: &mut Context<Self>) {
        let Some(e) = &self.editor else { return };
        let text = e
            .segments
            .iter()
            .map(|s| s.text.as_str())
            .collect::<Vec<_>>()
            .join("\n");
        cx.write_to_clipboard(text.into());
        self.toast(ToastTone::Success, "התמליל הועתק.".to_string(), None);
        cx.notify();
    }

    fn export_default(&mut self, cx: &mut Context<Self>) {
        let Some(e) = &self.editor else { return };
        let job_id = e.job_id.clone();
        if let Some(e) = &mut self.editor {
            e.exporting = true;
        }
        let api = self.api.clone();
        cx.notify();
        let formats = vec![
            ExportFormat::Txt,
            ExportFormat::Docx,
            ExportFormat::Pdf,
            ExportFormat::Srt,
        ];
        cx.spawn(async move |this, cx| {
            let result = api.export_job(&job_id, &formats, None).await;
            let _ = this.update(cx, |this, cx| {
                if let Some(e) = &mut this.editor {
                    e.exporting = false;
                }
                match result {
                    Ok(r) => this.toast(
                        ToastTone::Success,
                        format!("יוצאו {} קבצים.", r.results.len()),
                        None,
                    ),
                    Err(e) => this.toast(ToastTone::Error, e.message, Some(e.remedy)),
                }
                cx.notify();
            });
        })
        .detach();
    }

    fn close_editor(&mut self, cx: &mut Context<Self>) {
        self.editor = None;
        self.editor_loading = false;
        self.screen = Screen::Queue;
        self.refresh_jobs(cx);
        cx.notify();
    }

    fn start_media_download(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let Some(e) = &self.editor else { return };
        let job_id = e.job_id.clone();
        let api = self.api.clone();
        let url = api.media_url(&job_id);
        let dest = std::env::temp_dir().join(format!("tiktuv-media-{job_id}.bin"));

        if dest.is_file() && dest.metadata().map(|m| m.len() > 0).unwrap_or(false) {
            if let Some(e) = &mut self.editor {
                e.player.load(&dest);
                e.media_ready = true;
            }
            cx.notify();
            return;
        }

        cx.spawn_in(window, async move |this, cx| {
            let result = api.download(&url, &dest).await;
            let _ = this.update_in(cx, |this, _window, cx| {
                if let Some(e) = &mut this.editor {
                    if result.is_ok() {
                        e.player.load(&dest);
                        e.media_ready = true;
                    } else {
                        e.player.error = Some("לא ניתן להוריד את המדיה לניגון.".to_string());
                    }
                }
                cx.notify();
            });
        })
        .detach();
    }

    pub fn start_position_tick(&self, cx: &mut Context<Self>) {
        let rt = self.api.rt().clone();
        let (tx, rx) = async_channel::unbounded::<()>();
        rt.spawn(async move {
            loop {
                tokio::time::sleep(Duration::from_millis(250)).await;
                if tx.send(()).await.is_err() {
                    break;
                }
            }
        });
        cx.spawn(async move |this, cx| {
            while rx.recv().await.is_ok() {
                let _ = this.update(cx, |this, cx| {
                    if let Some(e) = &mut this.editor {
                        if e.player.playing {
                            if e.player.ended() {
                                e.player.playing = false;
                                e.player.pause();
                            }
                            e.current_time = e.player.position_seconds();
                            cx.notify();
                        }
                    }
                });
            }
        })
        .detach();
    }

    fn analysis_tab_bar(&self, cx: &mut Context<Self>) -> impl IntoElement {
        let current = self.editor.as_ref().and_then(|e| e.analysis_tab);
        let tabs: [(&'static str, Option<AnalysisTab>); 7] = [
            ("תמלול", None),
            ("תקציר", Some(AnalysisTab::Summary)),
            ("נקודות מפתח", Some(AnalysisTab::KeyPoints)),
            ("משימות", Some(AnalysisTab::ActionItems)),
            ("יישויות", Some(AnalysisTab::NamedEntities)),
            ("סיכום דוברים", Some(AnalysisTab::SpeakerSummaries)),
            ("ציטוטים", Some(AnalysisTab::SpeakerQuotes)),
        ];
        let mut buttons: Vec<AnyElement> = Vec::new();
        for (label, tab) in tabs {
            let active = current == tab;
            buttons.push(
                Button::new(label)
                    .ghost()
                    .selected(active)
                    .label(rtl(label))
                    .on_click(cx.listener(move |this, _, _, cx| this.set_editor_tab(tab, cx)))
                    .into_any_element(),
            );
        }
        div()
            .flex()
            .flex_row_reverse()
            .flex_wrap()
            .gap_1()
            .border_b_1()
            .border_color(rgb(0xe5e7eb))
            .pb_2()
            .children(buttons)
    }

    fn set_editor_tab(&mut self, tab: Option<AnalysisTab>, cx: &mut Context<Self>) {
        let needs_load = tab.is_some()
            && self
                .editor
                .as_ref()
                .map(|e| e.analysis_state == AnalysisState::Idle)
                .unwrap_or(false);
        if let Some(e) = &mut self.editor {
            e.analysis_tab = tab;
        }
        cx.notify();
        if needs_load {
            self.load_analysis(false, cx);
        }
    }

    fn load_analysis(&mut self, force: bool, cx: &mut Context<Self>) {
        let Some(job_id) = self.editor.as_ref().map(|e| e.job_id.clone()) else {
            return;
        };
        if let Some(e) = &mut self.editor {
            e.analysis_state = AnalysisState::Loading;
            e.analysis_error = None;
        }
        cx.notify();
        let api = self.api.clone();
        cx.spawn(async move |this, cx| {
            let result = if force {
                api.create_analysis(&job_id).await
            } else {
                match api.get_analysis(&job_id).await {
                    Ok(a) => Ok(a),
                    Err(e) if e.status == 404 => api.create_analysis(&job_id).await,
                    Err(e) => Err(e),
                }
            };
            let _ = this.update(cx, |this, cx| {
                if let Some(ed) = &mut this.editor {
                    match result {
                        Ok(a) => {
                            ed.analysis = Some(a);
                            ed.analysis_state = AnalysisState::Ready;
                        }
                        Err(e) => {
                            ed.analysis_error = Some(e.message);
                            ed.analysis_state = AnalysisState::Error;
                        }
                    }
                }
                cx.notify();
            });
        })
        .detach();
    }

    fn render_analysis(&self, cx: &mut Context<Self>) -> AnyElement {
        let Some(editor) = &self.editor else {
            return div().into_any_element();
        };
        let tab = editor.analysis_tab.unwrap_or(AnalysisTab::Summary);
        match editor.analysis_state {
            AnalysisState::Loading => div()
                .p_8()
                .child(div().text_base().text_color(rgb(0x6b7280)).child(rtl("מנתח בעזרת DeepSeek…")))
                .into_any_element(),
            AnalysisState::Error => div()
                .p_8()
                .v_flex()
                .gap_2()
                .child(
                    div()
                        .text_sm()
                        .text_color(rgb(0xdc2626))
                        .child(rtl_paragraph(editor.analysis_error.clone().unwrap_or_else(|| "יצירת הניתוח נכשלה.".to_string()))),
                )
                .child(
                    Button::new("retry-analysis")
                        .secondary()
                        .label(rtl("נסה שוב"))
                        .on_click(cx.listener(|this, _, _, cx| this.load_analysis(false, cx))),
                )
                .into_any_element(),
            _ => {
                let Some(analysis) = &editor.analysis else {
                    return div().p_8().child(rtl("טוען…")).into_any_element();
                };
                let body = analysis_section(tab, analysis);
                div()
                    .rounded_lg()
                    .border_1()
                    .border_color(rgb(0xe5e7eb))
                    .bg(cx.theme().background)
                    .p_5()
                    .v_flex()
                    .gap_3()
                    .child(
                        div()
                            .flex()
                            .flex_row_reverse()
                            .items_center()
                            .justify_between()
                            .child(
                                div()
                                    .text_xs()
                                    .text_color(rgb(0x6b7280))
                                    .child(rtl(&format!("נוצר בעזרת DeepSeek {}", latin(&analysis.model)))),
                            )
                            .child(
                                Button::new("regen-analysis")
                                    .ghost()
                                    .label(rtl("יצירה מחדש"))
                                    .on_click(cx.listener(|this, _, _, cx| this.load_analysis(true, cx))),
                            ),
                    )
                    .child(body)
                    .into_any_element()
            }
        }
    }

    fn search_bar(&self, cx: &mut Context<Self>) -> AnyElement {
        let Some(editor) = &self.editor else {
            return div().into_any_element();
        };
        let query = editor.search_query.clone();
        let replacement = editor.search_replacement.clone();
        let count = editor.search_matches.len();
        let active = editor.search_active;
        let case = editor.search_case_sensitive;
        let position = if count == 0 {
            "0 / 0".to_string()
        } else {
            format!("{} / {}", active + 1, count)
        };
        div()
            .rounded_lg()
            .border_1()
            .border_color(rgb(0xe5e7eb))
            .bg(cx.theme().background)
            .p_3()
            .v_flex()
            .gap_2()
            .child(
                div()
                    .flex()
                    .flex_row_reverse()
                    .items_center()
                    .gap_2()
                    .child(div().flex_1().min_w_0().child(Input::new(&query)))
                    .child(
                        Button::new("run-search")
                            .secondary()
                            .label(rtl("חיפוש"))
                            .on_click(cx.listener(|this, _, _, cx| this.run_search(cx))),
                    )
                    .child(
                        Button::new("search-case")
                            .ghost()
                            .selected(case)
                            .label(rtl("Aa"))
                            .on_click(cx.listener(|this, _, _, cx| this.toggle_search_case(cx))),
                    )
                    .child(div().text_xs().text_color(rgb(0x6b7280)).child(rtl(&position)))
                    .child(
                        Button::new("search-prev")
                            .ghost()
                            .label(rtl("‹"))
                            .disabled(count == 0)
                            .on_click(cx.listener(|this, _, _, cx| this.step_search(-1, cx))),
                    )
                    .child(
                        Button::new("search-next")
                            .ghost()
                            .label(rtl("›"))
                            .disabled(count == 0)
                            .on_click(cx.listener(|this, _, _, cx| this.step_search(1, cx))),
                    ),
            )
            .child(
                div()
                    .flex()
                    .flex_row_reverse()
                    .items_center()
                    .gap_2()
                    .child(div().flex_1().min_w_0().child(Input::new(&replacement)))
                    .child(
                        Button::new("replace-current")
                            .ghost()
                            .label(rtl("החלף"))
                            .disabled(count == 0)
                            .on_click(cx.listener(|this, _, _, cx| this.replace_current(cx))),
                    )
                    .child(
                        Button::new("replace-all")
                            .ghost()
                            .label(rtl("החלף הכול"))
                            .disabled(count == 0)
                            .on_click(cx.listener(|this, _, _, cx| this.replace_all(cx))),
                    ),
            )
            .into_any_element()
    }

    fn run_search(&mut self, cx: &mut Context<Self>) {
        let (query, case) = {
            let Some(e) = &self.editor else { return };
            (e.search_query.read(cx).value().to_string(), e.search_case_sensitive)
        };
        let query = query.trim().to_string();
        let segments = self
            .editor
            .as_ref()
            .map(|e| e.segments.clone())
            .unwrap_or_default();
        let needle = if case { query.clone() } else { query.to_lowercase() };
        let mut matches = Vec::new();
        for (i, seg) in segments.iter().enumerate() {
            let hay = if case { seg.text.clone() } else { seg.text.to_lowercase() };
            if !needle.is_empty() && hay.contains(&needle) {
                matches.push(i);
            }
        }
        if let Some(e) = &mut self.editor {
            e.search_matches = matches;
            e.search_active = 0;
        }
        cx.notify();
    }

    fn toggle_search_case(&mut self, cx: &mut Context<Self>) {
        if let Some(e) = &mut self.editor {
            e.search_case_sensitive = !e.search_case_sensitive;
        }
        self.run_search(cx);
    }

    fn step_search(&mut self, delta: isize, cx: &mut Context<Self>) {
        let Some(e) = &mut self.editor else { return };
        let n = e.search_matches.len();
        if n == 0 {
            return;
        }
        let cur = e.search_active as isize;
        e.search_active = (cur + delta).rem_euclid(n as isize) as usize;
        cx.notify();
    }

    fn replace_current(&mut self, cx: &mut Context<Self>) {
        let (query, replacement, case) = {
            let Some(e) = &self.editor else { return };
            (
                e.search_query.read(cx).value().to_string(),
                e.search_replacement.read(cx).value().to_string(),
                e.search_case_sensitive,
            )
        };
        let (seg_idx, old) = {
            let Some(e) = &self.editor else { return };
            if e.search_active >= e.search_matches.len() {
                return;
            }
            let idx = e.search_matches[e.search_active];
            (idx, e.segments[idx].text.clone())
        };
        let new_text = replace_once(&old, &query, &replacement, case);
        let mut next = self.editor.as_ref().map(|e| e.segments.clone()).unwrap_or_default();
        if let Some(s) = next.get_mut(seg_idx) {
            s.text = new_text;
            s.edited = true;
        }
        self.commit_segments(next, cx);
        self.run_search(cx);
    }

    fn replace_all(&mut self, cx: &mut Context<Self>) {
        let (query, replacement, case) = {
            let Some(e) = &self.editor else { return };
            (
                e.search_query.read(cx).value().to_string(),
                e.search_replacement.read(cx).value().to_string(),
                e.search_case_sensitive,
            )
        };
        let matches = self
            .editor
            .as_ref()
            .map(|e| e.search_matches.clone())
            .unwrap_or_default();
        let mut next = self.editor.as_ref().map(|e| e.segments.clone()).unwrap_or_default();
        for idx in matches {
            let old = next[idx].text.clone();
            let new = replace_all_in(&old, &query, &replacement, case);
            if new != old {
                next[idx].text = new;
                next[idx].edited = true;
            }
        }
        self.commit_segments(next, cx);
        self.run_search(cx);
    }

    fn on_speaker_rename(&mut self, key: String, name: String, cx: &mut Context<Self>) {
        if let Some(e) = &mut self.editor {
            e.speakers.insert(key, name);
        }
        cx.notify();
        self.save_now(cx);
    }

    fn speaker_rename_bar(&self, cx: &mut Context<Self>) -> AnyElement {
        let Some(editor) = &self.editor else {
            return div().into_any_element();
        };
        if editor.speaker_inputs.is_empty() {
            return div().into_any_element();
        }
        let mut rows: Vec<AnyElement> = Vec::new();
        for state in editor.speaker_inputs.values() {
            rows.push(
                div()
                    .flex()
                    .flex_row_reverse()
                    .items_center()
                    .gap_2()
                    .child(div().text_xs().text_color(rgb(0x6b7280)).child(rtl("שם דובר:")))
                    .child(div().w(px(160.)).child(Input::new(state)))
                    .into_any_element(),
            );
        }
        div()
            .rounded_lg()
            .border_1()
            .border_color(rgb(0xe5e7eb))
            .bg(cx.theme().background)
            .p_3()
            .v_flex()
            .gap_2()
            .child(div().text_sm().child(rtl("שמות דוברים")))
            .child(div().v_flex().gap_1().children(rows))
            .into_any_element()
    }
}

fn replace_once(text: &str, from: &str, to: &str, case_sensitive: bool) -> String {
    if from.is_empty() {
        return text.to_string();
    }
    if case_sensitive {
        text.replacen(from, to, 1)
    } else {
        let lower = text.to_lowercase();
        let needle = from.to_lowercase();
        match lower.find(&needle) {
            Some(pos) => {
                let end = pos + needle.len();
                format!("{}{}{}", &text[..pos], to, &text[end..])
            }
            None => text.to_string(),
        }
    }
}

fn replace_all_in(text: &str, from: &str, to: &str, case_sensitive: bool) -> String {
    if from.is_empty() {
        return text.to_string();
    }
    if case_sensitive {
        return text.replace(from, to);
    }
    let lower = text.to_lowercase();
    let needle = from.to_lowercase();
    let mut out = String::with_capacity(text.len());
    let mut start = 0;
    while let Some(pos) = lower[start..].find(&needle) {
        let abs = start + pos;
        out.push_str(&text[start..abs]);
        out.push_str(to);
        start = abs + needle.len();
    }
    out.push_str(&text[start..]);
    out
}

fn analysis_section(tab: AnalysisTab, a: &TranscriptAnalysis) -> AnyElement {
    let (items, empty): (Vec<String>, &str) = match tab {
        AnalysisTab::Summary => (
            if a.summary.is_empty() {
                vec![]
            } else {
                vec![a.summary.clone()]
            },
            "לא הופק תקציר.",
        ),
        AnalysisTab::KeyPoints => (a.key_points.clone(), "לא נמצאו נקודות מפתח."),
        AnalysisTab::ActionItems => (
            a.action_items
                .iter()
                .map(|i| match &i.assignee {
                    Some(x) => format!("{} — {}", i.task, x),
                    None => i.task.clone(),
                })
                .collect(),
            "לא נמצאו משימות או פריטי פעולה.",
        ),
        AnalysisTab::NamedEntities => (
            a.named_entities
                .iter()
                .map(|e| format!("{} ({})", e.name, e.r#type))
                .collect(),
            "לא נמצאו ישויות בעלות שם.",
        ),
        AnalysisTab::SpeakerSummaries => (
            a.speaker_summaries
                .iter()
                .map(|s| format!("{}: {}", s.speaker, s.summary))
                .collect(),
            "לא זוהו דוברים בתמליל.",
        ),
        AnalysisTab::SpeakerQuotes => (
            a.speaker_quotes
                .iter()
                .flat_map(|s| s.quotes.iter().map(|q| format!("{}: {}", s.speaker, q)))
                .collect(),
            "לא זוהו דוברים בתמליל.",
        ),
    };
    if items.is_empty() {
        return div()
            .text_sm()
            .text_color(rgb(0x6b7280))
            .child(rtl_paragraph(empty))
            .into_any_element();
    }
    let mut rows: Vec<AnyElement> = Vec::new();
    for item in &items {
        rows.push(
            div()
                .py_1()
                .text_sm()
                .child(rtl_paragraph(item))
                .into_any_element(),
        );
    }
    div().v_flex().gap_1().children(rows).into_any_element()
}

fn save_indicator(state: SaveState) -> impl IntoElement {
    let (text, color) = match state {
        SaveState::Saving => ("שומר…", rgb(0x6b7280)),
        SaveState::Saved => ("נשמר", rgb(0x059669)),
        SaveState::Error => ("השמירה נכשלה", rgb(0xdc2626)),
        SaveState::Idle => ("", rgb(0x6b7280)),
    };
    div().text_xs().text_color(color).child(rtl(text))
}
