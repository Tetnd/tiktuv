//! Transcript editor screen: synchronized audio playback and segment editing.
//!
//! Kept as an `EditorState` owned by the root `App` (so toasts, API and screen
//! navigation stay in one place). The transcript is a list of paragraphs; the
//! segment being worked on becomes a text field in place, and every change is
//! autosaved to the backend.

use std::collections::HashMap;
use std::time::Duration;

use gpui_kit::base::StyledExt as _;
use gpui_kit::component::button::*;
use crate::rtl_field::{RtlField, RtlFieldEvent};
use gpui_kit::component::{ActiveTheme, Disableable as _, Selectable as _};
use gpui_kit::prelude::FluentBuilder as _;
use gpui_kit::{
    Focusable as _,
    AnyElement, AppContext as _, Context, Entity, InteractiveElement as _, IntoElement,
    ClickEvent, KeyDownEvent, ParentElement as _, StatefulInteractiveElement as _, Styled as _, Subscription,
    Window, div, px, rgb,
};

use crate::app::{App, Screen, ToastTone};
use crate::format::*;
use crate::ui::{self, Ic, Tone};
use crate::rtl_text::rtl_paragraph;
use crate::seek_bar::seek_bar;
use crate::models::*;
use crate::player::{Player, SKIP_SECONDS, SPEEDS};

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum SaveState {
    Idle,
    /// Edited, with the autosave still on its way out.
    Pending,
    Saving,
    Saved,
    Error,
}

/// How long typing settles before an autosave goes out.
///
/// Short enough that an edit is on disk by the time the user has read it back,
/// long enough that a sentence is one request rather than forty.
const AUTOSAVE_DEBOUNCE: Duration = Duration::from_millis(600);

/// The segment currently open as a text field.
///
/// The field holds the text in *visual* order (see [`crate::format::rtl`]), so
/// it is reordered back into logical order on the way into the segment. The
/// pair is lossless, which is what makes editing through the field safe.
pub struct SegmentEdit {
    pub index: usize,
    pub input: Entity<RtlField>,
    /// The text as it was when the field opened, for `Esc`.
    pub original: String,
    /// All segments as they were when the field opened, pushed onto the undo
    /// stack on the first keystroke so one edit is one undo step.
    pub snapshot: Vec<Segment>,
    pub history_pushed: bool,
    pub _subscription: Subscription,
}

/// One editable row of the יישויות tab.
///
/// A row is one text field plus the analysis entry it writes into. `key` is
/// stable for the row's lifetime, so removing a row above the one being typed
/// in cannot make this row's field adopt someone else's text. The
/// subscription writes every keystroke back into `analysis.named_entities`;
/// the row owns it so a removed row stops listening.
pub struct EntityRow {
    pub key: u64,
    pub name_input: Entity<RtlField>,
    _subscription: Subscription,
}

/// One editable row of the סיכום דוברים tab.
///
/// A row is the summary text field for one speaker plus the analysis entry it
/// writes into. `key` is stable for the row's lifetime; the subscription
/// writes every keystroke back into `analysis.speaker_summaries` and the row
/// owns it so a rebuilt row stops listening to the old field.
pub struct SpeakerSummaryRow {
    pub key: u64,
    pub summary_input: Entity<RtlField>,
    _subscription: Subscription,
}

/// One editable row of the משימות tab.
///
/// A row is two text fields — the task and its assignee — plus the analysis
/// entry they write into. `key` is stable for the row's lifetime, so removing
/// a row above the one being typed in cannot make its fields adopt another
/// row's text. The subscriptions write every keystroke back into
/// `analysis.action_items`; the row owns them so a removed row stops listening.
pub struct ActionItemRow {
    pub key: u64,
    pub task_input: Entity<RtlField>,
    pub assignee_input: Entity<RtlField>,
    _task_subscription: Subscription,
    _assignee_subscription: Subscription,
}

/// One editable row of the נקודות מפתח tab.
///
/// A row is one text field plus the analysis entry it writes into. `key` is
/// stable for the row's lifetime, so removing a row above the one being typed
/// in cannot make its field adopt another row's text. The subscription writes
/// every keystroke back into `analysis.key_points`; the row owns it so a
/// removed row stops listening.
pub struct KeyPointRow {
    pub key: u64,
    pub input: Entity<RtlField>,
    _subscription: Subscription,
}

/// One editable row of the ציטוטים tab.
///
/// A row is two text fields — the speaker and the quote — plus the analysis
/// entry they write into. `key` is stable for the row's lifetime; the
/// subscriptions write every keystroke back into `analysis.speaker_quotes`,
/// which this tab keeps flat (one `SpeakerQuote` with one quote per row), and
/// the row owns them so a removed row stops listening.
pub struct QuoteRow {
    pub key: u64,
    pub speaker_input: Entity<RtlField>,
    pub quote_input: Entity<RtlField>,
    _speaker_subscription: Subscription,
    _quote_subscription: Subscription,
}

/// The entity types the backend and the exporters understand, in the order
/// the type chip cycles through them.
const ENTITY_TYPES: [&str; 5] = ["person", "organization", "location", "date", "other"];

/// How long an entity edit settles before it is saved.
const ENTITY_AUTOSAVE_DEBOUNCE: Duration = Duration::from_millis(600);

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum AnalysisTab {
    Summary,
    KeyPoints,
    ActionItems,
    NamedEntities,
    SpeakerSummaries,
    SpeakerQuotes,
}

/// The export section that holds one analysis tab, so the tab on screen and
/// the file on disk are the same thing.
pub fn section_for_tab(tab: AnalysisTab) -> ExportSection {
    match tab {
        AnalysisTab::Summary => ExportSection::Summary,
        AnalysisTab::KeyPoints => ExportSection::KeyPoints,
        AnalysisTab::ActionItems => ExportSection::ActionItems,
        AnalysisTab::NamedEntities => ExportSection::NamedEntities,
        AnalysisTab::SpeakerSummaries => ExportSection::SpeakerSummaries,
        AnalysisTab::SpeakerQuotes => ExportSection::SpeakerQuotes,
    }
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
    /// Speaker keys in display order, so the rename rows never reshuffle and
    /// speakers added by hand keep the slot they were created in.
    pub speaker_order: Vec<String>,
    /// One rename subscription per speaker key, dropped when the speaker is
    /// removed so a deleted row stops writing back into `speakers`.
    pub speaker_subs: HashMap<String, Subscription>,
    pub history: Vec<Vec<Segment>>,
    pub future: Vec<Vec<Segment>>,
    pub save_state: SaveState,
    /// The segment open for editing, if any.
    pub editing: Option<SegmentEdit>,
    /// Set by an edit, cleared when a save goes out: what tells a flush
    /// (finishing an edit, leaving the screen) that there is work to do.
    pub dirty: bool,
    /// A save is in flight, so the next one waits for it rather than racing.
    pub saving: bool,
    /// Bumped by every edit. A debounced save only fires, and a save response
    /// only reports, while the token it captured is still the current one — so
    /// a burst of typing is one request and a stale reply cannot claim "saved".
    pub save_token: u64,
    pub current_time: f64,
    pub player: Player,
    pub media_ready: bool,
    pub exporting: bool,
    /// A proofreading request is in flight.
    pub proofreading: bool,
    pub analysis: Option<TranscriptAnalysis>,
    pub analysis_state: AnalysisState,
    pub analysis_error: Option<String>,
    pub analysis_tab: Option<AnalysisTab>,
    /// Editable rows for the יישויות tab, one per entry in
    /// `analysis.named_entities` and in the same order.
    pub entity_rows: Vec<EntityRow>,
    /// Source of `EntityRow::key`. Monotonic; never reused, so an element id
    /// from a removed row cannot be picked up by a later one.
    pub next_entity_key: u64,
    /// Entity edits have their own autosave: they go to the analysis file,
    /// not to the transcript.
    pub analysis_dirty: bool,
    pub analysis_saving: bool,
    /// Same token trick as the transcript autosave: a debounced save only
    /// fires, and its reply only reports, while its token is current.
    pub analysis_save_token: u64,
    pub analysis_save_state: SaveState,
    /// Editable rows for the סיכום דוברים tab, one per entry in
    /// `analysis.speaker_summaries` and in the same order.
    pub speaker_summary_rows: Vec<SpeakerSummaryRow>,
    /// Source of `SpeakerSummaryRow::key`. Monotonic; never reused.
    pub next_speaker_summary_key: u64,
    /// Speaker-summary edits have their own autosave, separate from the entity
    /// one so the two tabs never race each other's in-flight request.
    pub speaker_summaries_dirty: bool,
    pub speaker_summaries_saving: bool,
    pub speaker_summaries_save_token: u64,
    pub speaker_summaries_save_state: SaveState,
    /// Editable rows for the משימות tab, one per entry in
    /// `analysis.action_items` and in the same order.
    pub action_item_rows: Vec<ActionItemRow>,
    /// Source of `ActionItemRow::key`. Monotonic; never reused.
    pub next_action_item_key: u64,
    /// Action-item edits have their own autosave, separate from the entity and
    /// speaker-summary ones so the tabs never race each other's requests.
    pub action_items_dirty: bool,
    pub action_items_saving: bool,
    pub action_items_save_token: u64,
    pub action_items_save_state: SaveState,
    /// Editable rows for the נקודות מפתח tab, one per entry in
    /// `analysis.key_points` and in the same order.
    pub key_point_rows: Vec<KeyPointRow>,
    /// Source of `KeyPointRow::key`. Monotonic; never reused.
    pub next_key_point_key: u64,
    /// Key-point edits have their own autosave, separate from the other tabs'
    /// so they never race each other's in-flight request.
    pub key_points_dirty: bool,
    pub key_points_saving: bool,
    pub key_points_save_token: u64,
    pub key_points_save_state: SaveState,
    /// Editable rows for the ציטוטים tab, one per quote in
    /// `analysis.speaker_quotes` (kept flat: one quote per `SpeakerQuote`) and
    /// in the same order.
    pub quote_rows: Vec<QuoteRow>,
    /// Source of `QuoteRow::key`. Monotonic; never reused.
    pub next_quote_key: u64,
    /// Quote edits have their own autosave, separate from the other tabs' so
    /// they never race each other's in-flight request.
    pub quotes_dirty: bool,
    pub quotes_saving: bool,
    pub quotes_save_token: u64,
    pub quotes_save_state: SaveState,
    /// The תקציר field, editable in place like the other analysis tabs. It is
    /// multi-line — one logical line per row, no soft wrapping — so the field
    /// can span the whole page without breaking right-to-left rows mid-line.
    pub summary_input: Entity<RtlField>,
    /// Keeps the summary field's write-back subscription alive.
    pub _summary_subscription: Subscription,
    /// Summary edits have their own autosave, separate from the other tabs'
    /// so they never race each other's in-flight request.
    pub summary_dirty: bool,
    pub summary_saving: bool,
    pub summary_save_token: u64,
    pub summary_save_state: SaveState,
    pub search_query: Entity<RtlField>,
    pub search_replacement: Entity<RtlField>,
    pub search_matches: Vec<usize>,
    pub search_active: usize,
    pub search_case_sensitive: bool,
    pub speaker_inputs: HashMap<String, Entity<RtlField>>,
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
        let search_query = cx.new(|cx| RtlField::new(window, cx).placeholder("חיפוש בתמליל…"));
        let search_replacement = cx.new(|cx| RtlField::new(window, cx).placeholder("החלפה ב…"));

        // The transcript is shown as reordered paragraphs, and the segment
        // being worked on is swapped for a text field in place (`begin_edit`).
        //
        // That field is single-line on purpose: gpui-kit's multi-line input
        // lays its text out left-to-right and ignores `set_text_align` for
        // anything but a single line, so a wrapping field could read correctly
        // *or* sit on the right edge, never both. A single line is reordered,
        // right aligned and scrolls sideways instead of wrapping — so only the
        // segment being typed into gives up its wrapping, and only while the
        // caret is in it.
        let mut speaker_inputs = HashMap::new();
        let mut speaker_subs = HashMap::new();
        let mut speaker_order: Vec<String> = Vec::new();
        for segment in &segments {
            if let Some(key) = &segment.speaker {
                if !speaker_order.contains(key) {
                    speaker_order.push(key.clone());
                }
            }
        }
        // Speakers added in the editor may not be on any segment yet, so take
        // the saved map as a second source and keep its extras in a stable
        // (sorted) order after the ones the diarizer found.
        let mut unused: Vec<String> = speakers
            .keys()
            .filter(|key| !speaker_order.contains(key))
            .cloned()
            .collect();
        unused.sort();
        speaker_order.extend(unused);

        for key in &speaker_order {
            let name = speakers.get(key).cloned().unwrap_or_else(|| key.clone());
            let (state, sub) = speaker_input(key.clone(), &name, window, cx);
            speaker_inputs.insert(key.clone(), state);
            speaker_subs.insert(key.clone(), sub);
        }

        // The summary field lives for the whole editor, but starts empty: the
        // analysis (and with it the summary text) loads only when the user
        // opens one of the analysis tabs. `rebuild_summary_input` fills it in.
        let summary_input = cx.new(|cx| {
            RtlField::new(window, cx)
                .placeholder("כתבו תקציר…")
                .multiline(true)
                .min_rows(16)
        });
        let _summary_subscription =
            cx.subscribe_in(&summary_input, window, move |this: &mut App, state, event, _, cx| {
                if matches!(event, RtlFieldEvent::Change) {
                    let summary = state.read(cx).value().to_string();
                    this.on_summary_edited(summary, cx);
                }
            });

        Self {
            job_id,
            filename,
            transcript,
            segments,
            speakers,
            speaker_order,
            speaker_subs,
            history: Vec::new(),
            future: Vec::new(),
            save_state: SaveState::Idle,
            editing: None,
            dirty: false,
            saving: false,
            save_token: 0,
            current_time: 0.0,
            player: Player::new(),
            media_ready: false,
            exporting: false,
            proofreading: false,
            analysis: None,
            analysis_state: AnalysisState::Idle,
            analysis_error: None,
            analysis_tab: None,
            entity_rows: Vec::new(),
            next_entity_key: 1,
            analysis_dirty: false,
            analysis_saving: false,
            analysis_save_token: 0,
            analysis_save_state: SaveState::Idle,
            speaker_summary_rows: Vec::new(),
            next_speaker_summary_key: 1,
            speaker_summaries_dirty: false,
            speaker_summaries_saving: false,
            speaker_summaries_save_token: 0,
            speaker_summaries_save_state: SaveState::Idle,
            action_item_rows: Vec::new(),
            next_action_item_key: 1,
            action_items_dirty: false,
            action_items_saving: false,
            action_items_save_token: 0,
            action_items_save_state: SaveState::Idle,
            key_point_rows: Vec::new(),
            next_key_point_key: 1,
            key_points_dirty: false,
            key_points_saving: false,
            key_points_save_token: 0,
            key_points_save_state: SaveState::Idle,
            quote_rows: Vec::new(),
            next_quote_key: 1,
            quotes_dirty: false,
            quotes_saving: false,
            quotes_save_token: 0,
            quotes_save_state: SaveState::Idle,
            summary_input,
            _summary_subscription,
            summary_dirty: false,
            summary_saving: false,
            summary_save_token: 0,
            summary_save_state: SaveState::Idle,
            search_query,
            search_replacement,
            search_matches: Vec::new(),
            search_active: 0,
            search_case_sensitive: false,
            speaker_inputs,
        }
    }
}

/// Build the rename field for one speaker plus the subscription that writes
/// what is typed back into `EditorState::speakers`.
fn speaker_input(
    key: String,
    name: &str,
    window: &mut Window,
    cx: &mut Context<App>,
) -> (Entity<RtlField>, Subscription) {
    let state = cx.new(|cx| {
        let mut s = RtlField::new(window, cx);
        s.set_value(name, window, cx);
        s
    });
    let sub = cx.subscribe_in(&state, window, move |this: &mut App, state, event, window, cx| {
        if matches!(event, RtlFieldEvent::Change) {
            let name = state.read(cx).value().to_string();
            this.on_speaker_rename(key.clone(), name, window, cx);
        }
    });
    (state, sub)
}

/// Build the name field for one entity row plus the subscription that writes
/// what is typed back into the analysis.
fn entity_name_input(
    key: u64,
    name: &str,
    window: &mut Window,
    cx: &mut Context<App>,
) -> (Entity<RtlField>, Subscription) {
    let state = cx.new(|cx| {
        let mut s = RtlField::new(window, cx).placeholder("שם היישות");
        s.set_value(name, window, cx);
        s
    });
    let sub = cx.subscribe_in(&state, window, move |this: &mut App, state, event, _, cx| {
        if matches!(event, RtlFieldEvent::Change) {
            let name = state.read(cx).value().to_string();
            this.on_entity_renamed(key, name, cx);
        }
    });
    (state, sub)
}

/// Build the summary field for one speaker-summary row plus the subscription
/// that writes what is typed back into the analysis.
fn speaker_summary_input(
    key: u64,
    summary: &str,
    window: &mut Window,
    cx: &mut Context<App>,
) -> (Entity<RtlField>, Subscription) {
    let state = cx.new(|cx| {
        let mut s = RtlField::new(window, cx).placeholder("סיכום הדובר");
        s.set_value(summary, window, cx);
        s
    });
    let sub = cx.subscribe_in(&state, window, move |this: &mut App, state, event, _, cx| {
        if matches!(event, RtlFieldEvent::Change) {
            let summary = state.read(cx).value().to_string();
            this.on_speaker_summary_edited(key, summary, cx);
        }
    });
    (state, sub)
}

/// Build the two fields for one action-item row plus the subscriptions that
/// write what is typed back into the analysis.
fn action_item_inputs(
    key: u64,
    task: &str,
    assignee: Option<&str>,
    window: &mut Window,
    cx: &mut Context<App>,
) -> (Entity<RtlField>, Entity<RtlField>, Subscription, Subscription) {
    let task_state = cx.new(|cx| {
        let mut s = RtlField::new(window, cx).placeholder("המשימה");
        s.set_value(task, window, cx);
        s
    });
    let assignee_state = cx.new(|cx| {
        let mut s = RtlField::new(window, cx).placeholder("אחראי");
        s.set_value(assignee.unwrap_or_default(), window, cx);
        s
    });
    let task_sub = cx.subscribe_in(&task_state, window, move |this: &mut App, state, event, _, cx| {
        if matches!(event, RtlFieldEvent::Change) {
            let task = state.read(cx).value().to_string();
            this.on_action_task_edited(key, task, cx);
        }
    });
    let assignee_sub = cx.subscribe_in(&assignee_state, window, move |this: &mut App, state, event, _, cx| {
        if matches!(event, RtlFieldEvent::Change) {
            let assignee = state.read(cx).value().to_string();
            this.on_action_assignee_edited(key, assignee, cx);
        }
    });
    (task_state, assignee_state, task_sub, assignee_sub)
}

/// Build the text field for one key-point row plus the subscription that
/// writes what is typed back into the analysis.
fn key_point_input(
    key: u64,
    text: &str,
    window: &mut Window,
    cx: &mut Context<App>,
) -> (Entity<RtlField>, Subscription) {
    let state = cx.new(|cx| {
        let mut s = RtlField::new(window, cx).placeholder("נקודת מפתח");
        s.set_value(text, window, cx);
        s
    });
    let sub = cx.subscribe_in(&state, window, move |this: &mut App, state, event, _, cx| {
        if matches!(event, RtlFieldEvent::Change) {
            let text = state.read(cx).value().to_string();
            this.on_key_point_edited(key, text, cx);
        }
    });
    (state, sub)
}

/// Build the two fields for one quote row plus the subscriptions that write
/// what is typed back into the analysis.
fn quote_inputs(
    key: u64,
    speaker: &str,
    quote: &str,
    window: &mut Window,
    cx: &mut Context<App>,
) -> (Entity<RtlField>, Entity<RtlField>, Subscription, Subscription) {
    let speaker_state = cx.new(|cx| {
        let mut s = RtlField::new(window, cx).placeholder("שם הדובר");
        s.set_value(speaker, window, cx);
        s
    });
    let quote_state = cx.new(|cx| {
        let mut s = RtlField::new(window, cx).placeholder("הציטוט");
        s.set_value(quote, window, cx);
        s
    });
    let speaker_sub = cx.subscribe_in(&speaker_state, window, move |this: &mut App, state, event, _, cx| {
        if matches!(event, RtlFieldEvent::Change) {
            let speaker = state.read(cx).value().to_string();
            this.on_quote_speaker_edited(key, speaker, cx);
        }
    });
    let quote_sub = cx.subscribe_in(&quote_state, window, move |this: &mut App, state, event, _, cx| {
        if matches!(event, RtlFieldEvent::Change) {
            let quote = state.read(cx).value().to_string();
            this.on_quote_edited(key, quote, cx);
        }
    });
    (speaker_state, quote_state, speaker_sub, quote_sub)
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
        // A review dialog from the previous transcript must not pop over the
        // one being opened.
        self.corrections_open = false;
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

    pub fn render_editor(&self, window: &mut Window, cx: &mut Context<Self>) -> AnyElement {
        if self.editor_loading {
            return div()
                .p_10()
                .child(div().text_base().text_color(ui::muted()).child(rtl("טוען תמליל…")))
                .into_any_element();
        }
        let Some(editor) = &self.editor else {
            return div()
                .p_10()
                .child(div().text_base().text_color(ui::muted()).child(rtl("לא נבחר תמליל.")))
                .into_any_element();
        };

        let filename = editor.filename.clone();
        let segment_count = editor.segments.len();
        let duration = editor.transcript.duration;
        let partial = editor.transcript.partial;
        let corrections = editor.transcript.proofread_corrections;
        // How many segments the proofreader corrected in the *current* state.
        // The transcript counter accumulates across runs, so the review is
        // driven by the flags on the segments actually on screen.
        let proofread_count = editor.segments.iter().filter(|s| s.proofread).count();
        let proofreading = editor.proofreading;
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
        // On the summary tab the page column is exactly viewport height, so
        // the analysis card — and the תקציר field inside it — can stretch to
        // the bottom of the page.
        let summary_full_height = editor.analysis_tab == Some(AnalysisTab::Summary);
        let analysis_body = self.render_analysis(window, cx);
        let search_bar = self.search_bar(cx);
        let speaker_bar = self.speaker_rename_bar(cx);

        // -- segment rows -------------------------------------------------
        // While a job runs, the worker keeps flushing the whole segment list
        // over whatever is saved, so an edit made now would be lost: editing
        // opens once the transcript is final.
        let editable = !partial;
        let editing_index = editor.editing.as_ref().map(|e| e.index);
        let mut rows: Vec<AnyElement> = Vec::new();
        for (i, seg) in editor.segments.iter().enumerate() {
            let is_active = i as isize == active;
            let is_search_match = editor.search_matches.get(editor.search_active) == Some(&i);
            let is_editing = editing_index == Some(i);
            let start = seg.start;
            let speaker_label = seg
                .speaker
                .as_ref()
                .map(|k| editor.speakers.get(k).cloned().unwrap_or_else(|| k.clone()));

            // The segment's text: a paragraph, or the field that stands in for
            // it while this is the segment being edited.
            let body: AnyElement = if is_editing {
                let input = editor.editing.as_ref().map(|e| e.input.clone());
                div()
                    .py_0p5()
                    .flex()
                    .flex_row_reverse()
                    .items_center()
                    .gap_2()
                    .child(
                        div()
                            .flex_1()
                            .min_w_0()
                            .text_sm()
                            .children(input),
                    )
                    .child(
                        Button::new(format!("done-{i}"))
                            .secondary()
                            .compact()
                            .label(rtl("סיום"))
                            .on_click(cx.listener(|this, _, _, cx| this.finish_edit(cx))),
                    )
                    .into_any_element()
            } else {
                div()
                    .id(("segment-text", i))
                    .py_0p5()
                    .text_sm()
                    .cursor_pointer()
                    .on_click(cx.listener(move |this, event: &ClickEvent, window, cx| {
                        // One click jumps to the spot in the recording; a double
                        // click (whose first click already did that) opens the
                        // segment for editing, like the "עריכה" button.
                        let double = matches!(
                            event,
                            ClickEvent::Mouse(m) if m.up.click_count >= 2
                        );
                        if double && editable {
                            this.begin_edit(i, window, cx);
                        } else {
                            this.seek_to(start, cx);
                        }
                    }))
                    .child(rtl_paragraph(&seg.text))
                    .into_any_element()
            };

            let row = div()
                .id(("segment-row", i))
                .flex()
                .flex_row_reverse()
                .items_start()
                .gap_3()
                .pr_3()
                .pl_2()
                .py_2p5()
                .rounded_lg()
                .border_r_2()
                .border_color(gpui_kit::transparent_black())
                .when(is_active, |this| this.bg(ui::accent_bg()).border_color(ui::accent()))
                .when(is_search_match, |this| this.bg(ui::highlight_bg()))
                .when(!is_active && !is_search_match && !is_editing, |this| {
                    this.hover(|s| s.bg(ui::surface_alt()))
                })
                .when(is_editing, |this| {
                    // Esc puts the segment back as it was. The field is a
                    // descendant of this row, so the keystroke passes through
                    // here on its way up the focus path.
                    this.on_key_down(cx.listener(|this, event: &KeyDownEvent, _, cx| {
                        if event.keystroke.key == "escape" {
                            this.revert_edit(cx);
                        }
                    }))
                })
                .child(
                    div()
                        .id(("seek", i))
                        .mt_0p5()
                        .px_2()
                        .h_6()
                        .flex_shrink_0()
                        .flex()
                        .items_center()
                        .rounded_md()
                        .bg(if is_active { ui::surface() } else { ui::surface_alt() })
                        .text_xs()
                        .font_medium()
                        .text_color(if is_active { ui::accent_strong() } else { ui::muted() })
                        .cursor_pointer()
                        .hover(|s| s.text_color(ui::accent_strong()))
                        .on_click(cx.listener(move |this, _, _, cx| this.seek_to(start, cx)))
                        .child(format_clock(start)),
                )
                .child(
                    div()
                        .flex_1()
                        .min_w_0()
                        .v_flex()
                        .gap_0p5()
                        .when(speaker_label.is_some() || seg.proofread, |this| {
                            this.child(
                                ui::rtl_row()
                                    .gap_2()
                                    .when(speaker_label.is_some(), |this| {
                                        this.child(
                                            div()
                                                .text_xs()
                                                .font_semibold()
                                                .text_color(ui::accent())
                                                .child(rtl(&speaker_label.clone().unwrap_or_default())),
                                        )
                                    })
                                    .when(seg.proofread, |this| {
                                        // Marks a segment DeepSeek corrected, so the
                                        // parts that were edited are visible in place;
                                        // the full before/after is in the "תיקוני הגהה"
                                        // dialog.
                                        this.child(ui::icon_pill(
                                            Ic::Sparkles,
                                            "תוקן בהגהה",
                                            Tone::Success,
                                        ))
                                    }),
                            )
                        })
                        .child(body),
                )
                .when(!is_editing, |this| {
                    this.child(
                        Button::new(format!("edit-{i}"))
                            .ghost()
                            .compact()
                            .icon(ui::icon(Ic::Pencil, 15.0, ui::muted()))
                            .tooltip(rtl("עריכה"))
                            .disabled(!editable)
                            .on_click(cx.listener(move |this, _, window, cx| {
                                this.begin_edit(i, window, cx)
                            })),
                    )
                });
            rows.push(row.into_any_element());
        }

        let meta_line = format!(
            "{segment_count} מקטעים · {}{}{}",
            format_clock(duration),
            if partial { " · תמליל חלקי" } else { "" },
            if corrections > 0 {
                format!(" · {corrections} תיקוני הגהה")
            } else {
                String::new()
            }
        );
        let hint_text = if editable {
            "לחיצה על טקסט מדלגת לנקודה בהקלטה · לחיצה כפולה או על העיפרון פותחת את המקטע לתיקון · Enter לסיום, Esc לביטול"
        } else {
            "התמלול עדיין רץ; עריכת הטקסט תתאפשר בסיומו."
        };

        div()
            .w_full()
            .p_8()
            .v_flex()
            .gap_5()
            .when(summary_full_height, |this| this.flex_1())
            // -- header ----------------------------------------------------
            .child(
                ui::rtl_row()
                    .gap_3()
                    .child(
                        Button::new("close-editor")
                            .secondary()
                            .compact()
                            .icon(ui::icon(Ic::ChevronRight, 16.0, ui::subtle()))
                            .tooltip(rtl("סגירה"))
                            .on_click(cx.listener(|this, _, _, cx| this.close_editor(cx))),
                    )
                    .child(
                        div()
                            .flex_1()
                            .min_w_0()
                            .v_flex()
                            .child(
                                div()
                                    .truncate()
                                    .text_xl()
                                    .font_bold()
                                    .text_color(ui::ink())
                                    .child(rtl(&truncate_middle(&filename, 60))),
                            )
                            .child(
                                div()
                                    .text_xs()
                                    .text_color(ui::muted())
                                    .child(rtl(&meta_line)),
                            ),
                    ),
            )
            .child(
                ui::rtl_row()
                    .flex_wrap()
                    .gap_2()
                    .justify_between()
                    .child(
                        ui::rtl_row()
                            .flex_wrap()
                            .gap_2()
                            .child(
                                Button::new("undo")
                                    .ghost()
                                    .compact()
                                    .icon(ui::icon(Ic::Undo, 16.0, if can_undo { ui::subtle() } else { ui::faint() }))
                                    .tooltip(rtl("ביטול פעולה"))
                                    .disabled(!can_undo)
                                    .on_click(cx.listener(|this, _, _, cx| this.undo_edit(cx))),
                            )
                            .child(
                                Button::new("redo")
                                    .ghost()
                                    .compact()
                                    .icon(ui::icon(Ic::Redo, 16.0, if can_redo { ui::subtle() } else { ui::faint() }))
                                    .tooltip(rtl("ביצוע חוזר"))
                                    .disabled(!can_redo)
                                    .on_click(cx.listener(|this, _, _, cx| this.redo_edit(cx))),
                            )
                            .child(
                                Button::new("proofread")
                                    .secondary()
                                    .icon(ui::icon(Ic::Sparkles, 15.0, ui::accent()))
                                    .label(rtl(if proofreading {
                                        "מגיה…"
                                    } else {
                                        "הגהה חכמה"
                                    }))
                                    .disabled(proofreading || partial)
                                    .on_click(
                                        cx.listener(|this, _, _, cx| this.proofread_transcript(cx)),
                                    ),
                            )
                            .when(proofread_count > 0, |this| {
                                this.child(
                                    Button::new("review-corrections")
                                        .ghost()
                                        .label(rtl(&format!("תיקוני הגהה ({proofread_count})")))
                                        .on_click(cx.listener(|this, _, _, cx| {
                                            this.open_corrections(cx)
                                        })),
                                )
                            })
                            .child(
                                Button::new("identify-speakers")
                                    .secondary()
                                    .icon(ui::icon(Ic::Users, 15.0, ui::accent()))
                                    .label(rtl("זיהוי דוברים"))
                                    .disabled(partial)
                                    .on_click(cx.listener(|this, _, _, cx| this.open_speakers(cx))),
                            )
                            .child(
                                Button::new("copy-all")
                                    .secondary()
                                    .icon(ui::icon(Ic::Copy, 15.0, ui::subtle()))
                                    .label(rtl("העתק הכול"))
                                    .on_click(cx.listener(|this, _, _, cx| this.copy_all(cx))),
                            )
                            .child(
                                Button::new("export")
                                    .primary()
                                    .icon(ui::icon(Ic::FileDown, 15.0, gpui_kit::rgb(0xffffff)))
                                    .label(rtl("ייצוא"))
                                    .on_click(cx.listener(|this, _, _, cx| this.open_export(cx))),
                            ),
                    )
                    .child(save_indicator(save_state)),
            )
            .child(self.analysis_tab_bar(cx))
            .when(show_transcript, |this| {
                this
            // -- player ----------------------------------------------------
            .child(
                ui::card()
                    .p_4()
                    .gap_3()
                    .child(
                        div()
                            .flex()
                            .flex_row()
                            .items_center()
                            .gap_3()
                            .child(
                                Button::new("play")
                                    .primary()
                                    .icon(ui::icon(
                                        if playing { Ic::Pause } else { Ic::Play },
                                        16.0,
                                        gpui_kit::rgb(0xffffff),
                                    ))
                                    .label(rtl(if playing { "השהיה" } else { "ניגון" }))
                                    .disabled(!editor.media_ready)
                                    .on_click(cx.listener(|this, _, _, cx| this.toggle_play(cx))),
                            )
                            .child(
                                Button::new("skip-back")
                                    .ghost()
                                    .compact()
                                    .icon(ui::icon(Ic::SkipBack, 15.0, ui::subtle()))
                                    .label("5s")
                                    .on_click(cx.listener(|this, _, _, cx| this.skip_media(-SKIP_SECONDS, cx))),
                            )
                            .child(
                                Button::new("skip-fwd")
                                    .ghost()
                                    .compact()
                                    .icon(ui::icon(Ic::SkipForward, 15.0, ui::subtle()))
                                    .label("5s")
                                    .on_click(cx.listener(|this, _, _, cx| this.skip_media(SKIP_SECONDS, cx))),
                            )
                            .child(
                                div()
                                    .flex_1()
                                    .text_sm()
                                    .font_medium()
                                    .text_color(ui::subtle())
                                    .child(rtl(&isolate(format!("{} / {}", format_clock(pos), format_clock(total))))),
                            )
                            .when(!editor.media_ready && media_error.is_none(), |this| {
                                this.child(
                                    ui::rtl_row()
                                        .gap_1p5()
                                        .text_xs()
                                        .text_color(ui::muted())
                                        .child(ui::icon(Ic::Refresh, 13.0, ui::muted()))
                                        .child(rtl("מכין שמע לניגון…")),
                                )
                            })
                            .child(
                                Button::new("speed")
                                    .secondary()
                                    .compact()
                                    .icon(ui::icon(Ic::Gauge, 15.0, ui::subtle()))
                                    .label(format!("{speed}×"))
                                    .tooltip(rtl("מהירות ניגון"))
                                    .on_click(cx.listener(|this, _, _, cx| this.cycle_speed(cx))),
                            ),
                    )
                    .child(
                        seek_bar("timeline", progress as f32).on_seek(cx.listener(
                            move |this, fraction: &f32, _, cx| {
                                this.seek_to(total * *fraction as f64, cx)
                            },
                        )),
                    )
                    .when(media_error.is_some(), |this| {
                        this.child(
                            ui::notice(Ic::CircleAlert, Tone::Danger).child(
                                div()
                                    .flex_1()
                                    .min_w_0()
                                    .text_xs()
                                    .text_color(ui::danger_text())
                                    .child(rtl_paragraph(media_error.clone().unwrap_or_default())),
                            ),
                        )
                    }),
            )
            .child(search_bar)
            .child(speaker_bar)
            // -- segments --------------------------------------------------
            .child(
                ui::card()
                    .p_3()
                    .gap_1()
                    .child(
                        ui::rtl_row()
                            .gap_2()
                            .px_2()
                            .pb_2()
                            .items_start()
                            .border_b_1()
                            .border_color(ui::divider())
                            .child(div().pt_0p5().child(ui::icon(Ic::Info, 13.0, ui::faint())))
                            .child(
                                div()
                                    .flex_1()
                                    .min_w_0()
                                    .text_xs()
                                    .text_color(ui::muted())
                                    .child(rtl_paragraph(hint_text)),
                            ),
                    )
                    .children(rows),
            )
            })
            .when(!show_transcript, |this| this.child(analysis_body))
            .into_any_element()
    }

    // -- editor actions ---------------------------------------------------
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

    // -- editing a segment's text -----------------------------------------
    /// Open segment `index` as a text field, closing whichever was open.
    fn begin_edit(&mut self, index: usize, window: &mut Window, cx: &mut Context<Self>) {
        // Finishing the previous field first flushes its edit, so moving
        // between segments cannot leave one unsaved.
        self.finish_edit(cx);
        let Some(e) = &self.editor else { return };
        if e.transcript.partial {
            return;
        }
        let Some(segment) = e.segments.get(index) else {
            return;
        };
        let original = segment.text.clone();
        let snapshot = e.segments.clone();
        let input = cx.new(|cx| {
            let mut state = RtlField::new(window, cx).multiline(false);
            state.set_value(&original, window, cx);
            state
        });
        // Typing starts at once; the field takes focus as soon as it appears.
        input.update(cx, |field, cx| field.focus(window, cx));
        let subscription = cx.subscribe_in(&input, window, move |this: &mut App, state, event, _, cx| {
            match event {
                RtlFieldEvent::Change => {
                    let text = state.read(cx).value().to_string();
                    this.on_segment_edited(index, text, cx);
                }
                // Enter finishes the segment rather than inserting a line
                // break: a segment is one line here, and the next one is a
                // click away.
                RtlFieldEvent::PressEnter => this.finish_edit(cx),
                // Clicking elsewhere, or tabbing away, is also "done". The
                // text is already applied, so this only has to flush it.
                RtlFieldEvent::Blur => this.finish_edit(cx),
                RtlFieldEvent::Focus => {}
            }
        });
        if let Some(e) = &mut self.editor {
            e.editing = Some(SegmentEdit {
                index,
                input,
                original,
                snapshot,
                history_pushed: false,
                _subscription: subscription,
            });
        }
        cx.notify();
    }

    /// Apply what was typed to the segment and schedule the autosave.
    fn on_segment_edited(&mut self, index: usize, text: String, cx: &mut Context<Self>) {
        let changed = {
            let Some(e) = &mut self.editor else { return };
            let Some(segment) = e.segments.get_mut(index) else {
                return;
            };
            if segment.text == text {
                false
            } else {
                segment.text = text;
                segment.edited = true;
                // One editing session is one undo step: the pre-edit list goes
                // onto the stack at the first keystroke, not at every one.
                if let Some(edit) = &mut e.editing {
                    if !edit.history_pushed {
                        edit.history_pushed = true;
                        let snapshot = edit.snapshot.clone();
                        e.history.push(snapshot);
                        if e.history.len() > 60 {
                            e.history.remove(0);
                        }
                        e.future.clear();
                    }
                }
                true
            }
        };
        if changed {
            self.queue_save(cx);
        }
    }

    /// Close the open field, keeping what was typed, and save it now.
    fn finish_edit(&mut self, cx: &mut Context<Self>) {
        let Some(e) = &mut self.editor else { return };
        if e.editing.take().is_none() {
            return;
        }
        cx.notify();
        self.flush_save(cx);
    }

    /// Close the open field and put the segment back as it was (`Esc`).
    fn revert_edit(&mut self, cx: &mut Context<Self>) {
        let Some(e) = &mut self.editor else { return };
        let Some(edit) = e.editing.take() else { return };
        if let Some(segment) = e.segments.get_mut(edit.index) {
            if segment.text != edit.original {
                segment.text = edit.original.clone();
                // The pre-edit list is back on screen, so the undo step this
                // session pushed has nothing left to undo.
                if edit.history_pushed {
                    e.history.pop();
                }
                e.dirty = true;
            }
        }
        cx.notify();
        self.flush_save(cx);
    }

    /// Drop the open field without saving or restoring anything.
    ///
    /// For the paths that replace the segment list under it (undo, redo),
    /// where the field's buffer no longer describes anything on screen.
    fn abandon_edit(&mut self, cx: &mut Context<Self>) {
        if let Some(e) = &mut self.editor {
            if e.editing.take().is_some() {
                cx.notify();
            }
        }
    }

    fn undo_edit(&mut self, cx: &mut Context<Self>) {
        self.abandon_edit(cx);
        let Some(prev) = self.editor.as_mut().and_then(|e| e.history.pop()) else {
            return;
        };
        if let Some(e) = &mut self.editor {
            e.future.push(e.segments.clone());
        }
        self.apply_segments(prev, cx);
    }

    fn redo_edit(&mut self, cx: &mut Context<Self>) {
        self.abandon_edit(cx);
        let Some(next) = self.editor.as_mut().and_then(|e| e.future.pop()) else {
            return;
        };
        if let Some(e) = &mut self.editor {
            e.history.push(e.segments.clone());
        }
        self.apply_segments(next, cx);
    }

    fn apply_segments(&mut self, segs: Vec<Segment>, cx: &mut Context<Self>) {
        if let Some(e) = &mut self.editor {
            e.segments = segs;
        }
        cx.notify();
        self.save_now(cx);
    }

    // -- saving ------------------------------------------------------------
    /// Mark the transcript edited and save it once typing settles.
    ///
    /// Every keystroke lands here, so the request is debounced: the token the
    /// timer captured has to still be the current one when it fires, which it
    /// is only if nothing else was typed meanwhile.
    fn queue_save(&mut self, cx: &mut Context<Self>) {
        let token = {
            let Some(e) = &mut self.editor else { return };
            e.dirty = true;
            e.save_token += 1;
            e.save_state = SaveState::Pending;
            e.save_token
        };
        cx.notify();
        cx.spawn(async move |this, cx| {
            cx.background_executor().timer(AUTOSAVE_DEBOUNCE).await;
            let _ = this.update(cx, |this, cx| {
                let current = this
                    .editor
                    .as_ref()
                    .map(|e| e.save_token == token && e.dirty)
                    .unwrap_or(false);
                if current {
                    this.save_now(cx);
                }
            });
        })
        .detach();
    }

    /// Save straight away if anything is waiting to be saved.
    ///
    /// Called where an edit must not be left in flight: finishing a field,
    /// leaving the screen.
    fn flush_save(&mut self, cx: &mut Context<Self>) {
        let dirty = self.editor.as_ref().map(|e| e.dirty).unwrap_or(false);
        if dirty {
            self.save_now(cx);
        }
    }

    fn save_now(&mut self, cx: &mut Context<Self>) {
        let Some(e) = &self.editor else { return };
        // One request at a time, so two PUTs cannot land out of order and
        // leave the older text on disk. Whatever is still unsaved when this
        // one comes back is sent then.
        if e.saving {
            if let Some(e) = &mut self.editor {
                e.dirty = true;
            }
            return;
        }
        let job_id = e.job_id.clone();
        let segments = e.segments.clone();
        let speakers = e.speakers.clone();
        let token = {
            let Some(e) = &mut self.editor else { return };
            e.save_state = SaveState::Saving;
            e.saving = true;
            // Nothing is waiting any more: this request carries it.
            e.dirty = false;
            e.save_token
        };
        let api = self.api.clone();
        cx.notify();
        cx.spawn(async move |this, cx| {
            let result = api.save_transcript(&job_id, &segments, &speakers).await;
            let _ = this.update(cx, |this, cx| {
                let mut pending = false;
                if let Some(e) = &mut this.editor {
                    e.saving = false;
                    // A reply that another edit has already overtaken says
                    // nothing about what is on screen now; the save that edit
                    // queued reports instead.
                    if e.save_token == token {
                        e.save_state =
                            if result.is_ok() { SaveState::Saved } else { SaveState::Error };
                    } else if result.is_err() {
                        e.save_state = SaveState::Error;
                    }
                    pending = e.dirty;
                }
                if let Err(err) = result {
                    this.toast(
                        ToastTone::Error,
                        "שמירת התמליל נכשלה.".to_string(),
                        Some(err.message),
                    );
                } else if pending {
                    // Typed while this request was out: send that too.
                    this.save_now(cx);
                }
                cx.notify();
            });
        })
        .detach();
    }

    fn toggle_play(&mut self, cx: &mut Context<Self>) {
        if let Some(e) = &mut self.editor {
            e.player.toggle();
            // Playing again after the end starts over: show it at once.
            e.current_time = e.player.position_seconds();
        }
        cx.notify();
    }

    pub(crate) fn seek_to(&mut self, seconds: f64, cx: &mut Context<Self>) {
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

    /// Ask DeepSeek to correct transcription mistakes in the open transcript.
    ///
    /// Pending edits are saved first and awaited, so the proofreader works on
    /// what is on screen rather than on a stale autosave. The result comes
    /// back through `commit_segments`, which means it lands in the undo
    /// history like any other change — one "ביטול פעולה" puts the raw
    /// transcription back.
    fn proofread_transcript(&mut self, cx: &mut Context<Self>) {
        let Some(e) = &self.editor else { return };
        if e.proofreading || e.transcript.partial {
            return;
        }
        let job_id = e.job_id.clone();
        let segments = e.segments.clone();
        let speakers = e.speakers.clone();
        // The review dialog opens after the run, but only when this run added
        // corrections the user has not seen yet.
        let corrected_before = segments.iter().filter(|s| s.proofread).count();
        if let Some(e) = &mut self.editor {
            e.proofreading = true;
        }
        let api = self.api.clone();
        cx.notify();
        cx.spawn(async move |this, cx| {
            let saved = api.save_transcript(&job_id, &segments, &speakers).await;
            let result = match saved {
                Ok(_) => api.proofread(&job_id).await,
                Err(err) => Err(err),
            };
            let _ = this.update(cx, |this, cx| {
                if let Some(e) = &mut this.editor {
                    e.proofreading = false;
                }
                match result {
                    Ok(transcript) => {
                        let corrected = transcript.proofread_corrections;
                        let segments = transcript.segments.clone();
                        let corrected_now = segments.iter().filter(|s| s.proofread).count();
                        if let Some(e) = &mut this.editor {
                            e.transcript = transcript;
                        }
                        this.commit_segments(segments, cx);
                        this.toast(
                            ToastTone::Success,
                            if corrected > 0 {
                                format!("ההגהה הושלמה — {corrected} מקטעים תוקנו.")
                            } else {
                                "ההגהה הושלמה — לא נמצאו שגיאות.".to_string()
                            },
                            None,
                        );
                        // Show which parts were edited, not just how many.
                        if corrected_now > corrected_before {
                            this.open_corrections(cx);
                        }
                    }
                    Err(err) => this.toast(ToastTone::Error, err.message, Some(err.remedy)),
                }
                cx.notify();
            });
        })
        .detach();
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
            let result = api
                .export_job(&job_id, &formats, &[ExportSection::Transcript], None)
                .await;
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
        // Leaving the screen must not outrun the autosave: finishing the open
        // field flushes it, and the request itself owns a copy of the data, so
        // it still completes after the editor is gone.
        self.finish_edit(cx);
        self.flush_save(cx);
        self.flush_entity_save(cx);
        self.flush_speaker_summaries_save(cx);
        self.flush_key_points_save(cx);
        self.flush_quotes_save(cx);
        self.flush_summary_save(cx);
        self.flush_action_items_save(cx);
        self.corrections_open = false;
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
        let url = api.playback_url(&job_id);
        // A new name: copies of the raw media cached by earlier versions are
        // not on the transcript's timeline and must not be reused.
        let dest = std::env::temp_dir().join(format!("tiktuv-playback-{job_id}.wav"));

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
                div()
                    .id(label)
                    .px_3p5()
                    .h_9()
                    .flex()
                    .items_center()
                    .rounded_lg()
                    .cursor_pointer()
                    .text_sm()
                    .whitespace_nowrap()
                    .when(active, |this| {
                        this.bg(ui::accent_bg()).text_color(ui::accent_strong()).font_semibold()
                    })
                    .when(!active, |this| {
                        this.text_color(ui::muted())
                            .font_medium()
                            .hover(|s| s.bg(ui::surface_alt()).text_color(ui::ink()))
                    })
                    .on_click(
                        cx.listener(move |this, _, window, cx| this.set_editor_tab(tab, window, cx)),
                    )
                    .child(rtl(label))
                    .into_any_element(),
            );
        }
        div()
            .flex()
            .flex_row_reverse()
            .flex_wrap()
            .gap_1()
            .p_1()
            .rounded_xl()
            .border_1()
            .border_color(ui::border())
            .bg(ui::surface())
            .children(buttons)
    }

    fn set_editor_tab(
        &mut self,
        tab: Option<AnalysisTab>,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
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
            self.load_analysis(false, window, cx);
        }
    }

    fn load_analysis(&mut self, force: bool, window: &mut Window, cx: &mut Context<Self>) {
        let Some(job_id) = self.editor.as_ref().map(|e| e.job_id.clone()) else {
            return;
        };
        if force {
            // The new analysis replaces every hand-edited tab; write manual
            // edits out first so a failed regeneration does not lose them.
            self.flush_entity_save(cx);
            self.flush_speaker_summaries_save(cx);
            self.flush_key_points_save(cx);
            self.flush_quotes_save(cx);
            self.flush_summary_save(cx);
            self.flush_action_items_save(cx);
        }
        if let Some(e) = &mut self.editor {
            e.analysis_state = AnalysisState::Loading;
            e.analysis_error = None;
        }
        cx.notify();
        let api = self.api.clone();
        cx.spawn_in(window, async move |this, cx| {
            let result = if force {
                api.create_analysis(&job_id).await
            } else {
                match api.get_analysis(&job_id).await {
                    Ok(a) => Ok(a),
                    Err(e) if e.status == 404 => api.create_analysis(&job_id).await,
                    Err(e) => Err(e),
                }
            };
            let _ = this.update_in(cx, |this, window, cx| {
                let mut refreshed = false;
                if let Some(ed) = &mut this.editor {
                    match result {
                        Ok(a) => {
                            ed.analysis = Some(a);
                            ed.analysis_state = AnalysisState::Ready;
                            // A fresh analysis replaces whatever the rows
                            // were showing, including an edit still in the
                            // debounce: invalidate its token so a late reply
                            // cannot report a save that no longer matches.
                            ed.analysis_dirty = false;
                            ed.analysis_save_token += 1;
                            ed.analysis_save_state = SaveState::Idle;
                            ed.speaker_summaries_dirty = false;
                            ed.speaker_summaries_save_token += 1;
                            ed.speaker_summaries_save_state = SaveState::Idle;
                            ed.key_points_dirty = false;
                            ed.key_points_save_token += 1;
                            ed.key_points_save_state = SaveState::Idle;
                            ed.quotes_dirty = false;
                            ed.quotes_save_token += 1;
                            ed.quotes_save_state = SaveState::Idle;
                            ed.summary_dirty = false;
                            ed.summary_save_token += 1;
                            ed.summary_save_state = SaveState::Idle;
                            ed.action_items_dirty = false;
                            ed.action_items_save_token += 1;
                            ed.action_items_save_state = SaveState::Idle;
                            refreshed = true;
                        }
                        Err(e) => {
                            ed.analysis_error = Some(e.message);
                            ed.analysis_state = AnalysisState::Error;
                        }
                    }
                }
                if refreshed {
                    this.rebuild_entity_rows(window, cx);
                    this.rebuild_speaker_summary_rows(window, cx);
                    this.rebuild_key_point_rows(window, cx);
                    this.rebuild_quote_rows(window, cx);
                    this.rebuild_summary_input(window, cx);
                    this.rebuild_action_item_rows(window, cx);
                }
                cx.notify();
            });
        })
        .detach();
    }

    // -- named entities ---------------------------------------------------
    /// Build one text field per entity in the loaded analysis.
    ///
    /// The rows mirror `analysis.named_entities` by position, so they are
    /// rebuilt only when the analysis itself is replaced (first load,
    /// regeneration) — never on an ordinary re-render, which would drop the
    /// caret out of the field being typed in.
    fn rebuild_entity_rows(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let names: Vec<String> = self
            .editor
            .as_ref()
            .and_then(|e| e.analysis.as_ref())
            .map(|a| {
                a.named_entities
                    .iter()
                    .map(|entity| entity.name.clone())
                    .collect()
            })
            .unwrap_or_default();
        let Some(editor) = &mut self.editor else {
            return;
        };
        editor.entity_rows.clear();
        for name in names {
            let key = editor.next_entity_key;
            editor.next_entity_key += 1;
            let (name_input, sub) = entity_name_input(key, &name, window, cx);
            editor.entity_rows.push(EntityRow {
                key,
                name_input,
                _subscription: sub,
            });
        }
        cx.notify();
    }

    /// A row's position is the entity's position: the two lists are built and
    /// changed together.
    fn entity_position(&self, key: u64) -> Option<usize> {
        self.editor
            .as_ref()?
            .entity_rows
            .iter()
            .position(|row| row.key == key)
    }

    fn on_entity_renamed(&mut self, key: u64, name: String, cx: &mut Context<Self>) {
        let Some(position) = self.entity_position(key) else {
            return;
        };
        let changed = self
            .editor
            .as_mut()
            .and_then(|e| e.analysis.as_mut())
            .and_then(|a| a.named_entities.get_mut(position))
            .map(|entity| {
                if entity.name == name {
                    false
                } else {
                    entity.name = name;
                    true
                }
            })
            .unwrap_or(false);
        if changed {
            self.queue_entity_save(cx);
        }
    }

    /// Move one entity to the next type the exporters know. Part of the
    /// editor because a new entity has to be classified somehow, and the type
    /// decides which heading it is exported under.
    fn cycle_entity_type(&mut self, key: u64, cx: &mut Context<Self>) {
        let Some(position) = self.entity_position(key) else {
            return;
        };
        let changed = self
            .editor
            .as_mut()
            .and_then(|e| e.analysis.as_mut())
            .and_then(|a| a.named_entities.get_mut(position))
            .map(|entity| {
                let next = ENTITY_TYPES
                    .iter()
                    .position(|kind| *kind == entity.r#type)
                    .map(|index| ENTITY_TYPES[(index + 1) % ENTITY_TYPES.len()])
                    .unwrap_or(ENTITY_TYPES[0]);
                entity.r#type = next.to_string();
                true
            })
            .unwrap_or(false);
        if changed {
            self.queue_entity_save(cx);
        }
    }

    fn add_entity(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let Some(editor) = &mut self.editor else {
            return;
        };
        let Some(analysis) = &mut editor.analysis else {
            return;
        };
        analysis.named_entities.push(NamedEntity {
            name: String::new(),
            r#type: "other".to_string(),
        });
        let key = editor.next_entity_key;
        editor.next_entity_key += 1;
        let (name_input, sub) = entity_name_input(key, "", window, cx);
        editor.entity_rows.push(EntityRow {
            key,
            name_input,
            _subscription: sub,
        });
        cx.notify();
    }

    fn remove_entity(&mut self, key: u64, cx: &mut Context<Self>) {
        let Some(editor) = &mut self.editor else {
            return;
        };
        let Some(position) = editor.entity_rows.iter().position(|row| row.key == key) else {
            return;
        };
        editor.entity_rows.remove(position);
        if let Some(analysis) = &mut editor.analysis {
            if position < analysis.named_entities.len() {
                analysis.named_entities.remove(position);
            }
        }
        cx.notify();
        // A removal is a finished decision, not typing: save it now rather
        // than leave the old entity on disk for another keystroke's delay.
        self.save_entity_edits(cx);
    }

    /// Mark the entity list edited and save it once typing settles.
    fn queue_entity_save(&mut self, cx: &mut Context<Self>) {
        let token = {
            let Some(editor) = &mut self.editor else {
                return;
            };
            editor.analysis_dirty = true;
            editor.analysis_save_token += 1;
            editor.analysis_save_state = SaveState::Pending;
            editor.analysis_save_token
        };
        cx.notify();
        cx.spawn(async move |this, cx| {
            cx.background_executor().timer(ENTITY_AUTOSAVE_DEBOUNCE).await;
            let _ = this.update(cx, |this, cx| {
                let current = this
                    .editor
                    .as_ref()
                    .map(|e| e.analysis_save_token == token && e.analysis_dirty)
                    .unwrap_or(false);
                if current {
                    this.save_entity_edits(cx);
                }
            });
        })
        .detach();
    }

    /// Save waiting entity edits straight away.
    ///
    /// Called where they must not be left in flight: leaving the editor, and
    /// opening the export dialog (exports read the analysis back from disk).
    pub(crate) fn flush_entity_save(&mut self, cx: &mut Context<Self>) {
        let dirty = self
            .editor
            .as_ref()
            .map(|e| e.analysis_dirty)
            .unwrap_or(false);
        if dirty {
            self.save_entity_edits(cx);
        }
    }

    fn save_entity_edits(&mut self, cx: &mut Context<Self>) {
        let Some(editor) = &self.editor else {
            return;
        };
        let Some(analysis) = &editor.analysis else {
            return;
        };
        if editor.analysis_saving {
            if let Some(editor) = &mut self.editor {
                editor.analysis_dirty = true;
            }
            return;
        }
        let job_id = editor.job_id.clone();
        // A row the user added but has not named yet is not an entity: saving
        // it would put an empty entry in every export.
        let entities: Vec<NamedEntity> = analysis
            .named_entities
            .iter()
            .filter(|entity| !entity.name.trim().is_empty())
            .cloned()
            .collect();
        let token = {
            let Some(editor) = &mut self.editor else {
                return;
            };
            editor.analysis_saving = true;
            editor.analysis_dirty = false;
            editor.analysis_save_state = SaveState::Saving;
            editor.analysis_save_token
        };
        let api = self.api.clone();
        cx.notify();
        cx.spawn(async move |this, cx| {
            let result = api.save_named_entities(&job_id, &entities).await;
            let _ = this.update(cx, |this, cx| {
                let mut pending = false;
                if let Some(editor) = &mut this.editor {
                    editor.analysis_saving = false;
                    if editor.analysis_save_token == token {
                        editor.analysis_save_state = if result.is_ok() {
                            SaveState::Saved
                        } else {
                            SaveState::Error
                        };
                    } else if result.is_err() {
                        editor.analysis_save_state = SaveState::Error;
                    }
                    pending = editor.analysis_dirty;
                }
                if let Err(err) = result {
                    this.toast(
                        ToastTone::Error,
                        "שמירת היישויות נכשלה.".to_string(),
                        Some(err.message),
                    );
                } else if pending {
                    // Renamed while this request was out: send that too.
                    this.save_entity_edits(cx);
                }
                cx.notify();
            });
        })
        .detach();
    }

    // -- speaker summaries ------------------------------------------------
    /// Build one text field per speaker summary in the loaded analysis.
    ///
    /// Like the entity rows, these mirror `analysis.speaker_summaries` by
    /// position and are rebuilt only when the analysis itself is replaced
    /// (first load, regeneration) — never on an ordinary re-render, which
    /// would drop the caret out of the field being typed in.
    fn rebuild_speaker_summary_rows(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let summaries: Vec<String> = self
            .editor
            .as_ref()
            .and_then(|e| e.analysis.as_ref())
            .map(|a| {
                a.speaker_summaries
                    .iter()
                    .map(|item| item.summary.clone())
                    .collect()
            })
            .unwrap_or_default();
        let Some(editor) = &mut self.editor else {
            return;
        };
        editor.speaker_summary_rows.clear();
        for summary in summaries {
            let key = editor.next_speaker_summary_key;
            editor.next_speaker_summary_key += 1;
            let (summary_input, sub) = speaker_summary_input(key, &summary, window, cx);
            editor.speaker_summary_rows.push(SpeakerSummaryRow {
                key,
                summary_input,
                _subscription: sub,
            });
        }
        cx.notify();
    }

    /// A row's position is the speaker summary's position: the two lists are
    /// built and changed together.
    fn speaker_summary_position(&self, key: u64) -> Option<usize> {
        self.editor
            .as_ref()?
            .speaker_summary_rows
            .iter()
            .position(|row| row.key == key)
    }

    fn on_speaker_summary_edited(&mut self, key: u64, summary: String, cx: &mut Context<Self>) {
        let Some(position) = self.speaker_summary_position(key) else {
            return;
        };
        let changed = self
            .editor
            .as_mut()
            .and_then(|e| e.analysis.as_mut())
            .and_then(|a| a.speaker_summaries.get_mut(position))
            .map(|item| {
                if item.summary == summary {
                    false
                } else {
                    item.summary = summary;
                    true
                }
            })
            .unwrap_or(false);
        if changed {
            self.queue_speaker_summaries_save(cx);
        }
    }

    /// Mark the speaker summaries edited and save them once typing settles.
    fn queue_speaker_summaries_save(&mut self, cx: &mut Context<Self>) {
        let token = {
            let Some(editor) = &mut self.editor else {
                return;
            };
            editor.speaker_summaries_dirty = true;
            editor.speaker_summaries_save_token += 1;
            editor.speaker_summaries_save_state = SaveState::Pending;
            editor.speaker_summaries_save_token
        };
        cx.notify();
        cx.spawn(async move |this, cx| {
            cx.background_executor().timer(ENTITY_AUTOSAVE_DEBOUNCE).await;
            let _ = this.update(cx, |this, cx| {
                let current = this
                    .editor
                    .as_ref()
                    .map(|e| e.speaker_summaries_save_token == token && e.speaker_summaries_dirty)
                    .unwrap_or(false);
                if current {
                    this.save_speaker_summaries(cx);
                }
            });
        })
        .detach();
    }

    /// Save waiting speaker-summary edits straight away.
    ///
    /// Called where they must not be left in flight: leaving the editor, and
    /// opening the export dialog (exports read the analysis back from disk).
    pub(crate) fn flush_speaker_summaries_save(&mut self, cx: &mut Context<Self>) {
        let dirty = self
            .editor
            .as_ref()
            .map(|e| e.speaker_summaries_dirty)
            .unwrap_or(false);
        if dirty {
            self.save_speaker_summaries(cx);
        }
    }

    fn save_speaker_summaries(&mut self, cx: &mut Context<Self>) {
        let Some(editor) = &self.editor else {
            return;
        };
        let Some(analysis) = &editor.analysis else {
            return;
        };
        if editor.speaker_summaries_saving {
            if let Some(editor) = &mut self.editor {
                editor.speaker_summaries_dirty = true;
            }
            return;
        }
        let job_id = editor.job_id.clone();
        let summaries: Vec<SpeakerSummary> = analysis.speaker_summaries.clone();
        let token = {
            let Some(editor) = &mut self.editor else {
                return;
            };
            editor.speaker_summaries_saving = true;
            editor.speaker_summaries_dirty = false;
            editor.speaker_summaries_save_state = SaveState::Saving;
            editor.speaker_summaries_save_token
        };
        let api = self.api.clone();
        cx.notify();
        cx.spawn(async move |this, cx| {
            let result = api.save_speaker_summaries(&job_id, &summaries).await;
            let _ = this.update(cx, |this, cx| {
                let mut pending = false;
                if let Some(editor) = &mut this.editor {
                    editor.speaker_summaries_saving = false;
                    if editor.speaker_summaries_save_token == token {
                        editor.speaker_summaries_save_state = if result.is_ok() {
                            SaveState::Saved
                        } else {
                            SaveState::Error
                        };
                    } else if result.is_err() {
                        editor.speaker_summaries_save_state = SaveState::Error;
                    }
                    pending = editor.speaker_summaries_dirty;
                }
                if let Err(err) = result {
                    this.toast(
                        ToastTone::Error,
                        "שמירת סיכום הדוברים נכשלה.".to_string(),
                        Some(err.message),
                    );
                } else if pending {
                    // Edited while this request was out: send that too.
                    this.save_speaker_summaries(cx);
                }
                cx.notify();
            });
        })
        .detach();
    }

    // -- key points --------------------------------------------------------
    /// Build one text field per key point in the loaded analysis.
    ///
    /// Like the entity rows, these mirror `analysis.key_points` by position
    /// and are rebuilt only when the analysis itself is replaced (first load,
    /// regeneration) — never on an ordinary re-render, which would drop the
    /// caret out of the field being typed in.
    fn rebuild_key_point_rows(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let points: Vec<String> = self
            .editor
            .as_ref()
            .and_then(|e| e.analysis.as_ref())
            .map(|a| a.key_points.clone())
            .unwrap_or_default();
        let Some(editor) = &mut self.editor else {
            return;
        };
        editor.key_point_rows.clear();
        for point in points {
            let key = editor.next_key_point_key;
            editor.next_key_point_key += 1;
            let (input, sub) = key_point_input(key, &point, window, cx);
            editor.key_point_rows.push(KeyPointRow {
                key,
                input,
                _subscription: sub,
            });
        }
        cx.notify();
    }

    /// A row's position is the key point's position: the two lists are built
    /// and changed together.
    fn key_point_position(&self, key: u64) -> Option<usize> {
        self.editor
            .as_ref()?
            .key_point_rows
            .iter()
            .position(|row| row.key == key)
    }

    fn on_key_point_edited(&mut self, key: u64, text: String, cx: &mut Context<Self>) {
        let Some(position) = self.key_point_position(key) else {
            return;
        };
        let changed = self
            .editor
            .as_mut()
            .and_then(|e| e.analysis.as_mut())
            .and_then(|a| a.key_points.get_mut(position))
            .map(|point| {
                if *point == text {
                    false
                } else {
                    *point = text;
                    true
                }
            })
            .unwrap_or(false);
        if changed {
            self.queue_key_points_save(cx);
        }
    }

    fn add_key_point(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let Some(editor) = &mut self.editor else {
            return;
        };
        let Some(analysis) = &mut editor.analysis else {
            return;
        };
        analysis.key_points.push(String::new());
        let key = editor.next_key_point_key;
        editor.next_key_point_key += 1;
        let (input, sub) = key_point_input(key, "", window, cx);
        editor.key_point_rows.push(KeyPointRow {
            key,
            input,
            _subscription: sub,
        });
        cx.notify();
    }

    fn remove_key_point(&mut self, key: u64, cx: &mut Context<Self>) {
        let Some(editor) = &mut self.editor else {
            return;
        };
        let Some(position) = editor.key_point_rows.iter().position(|row| row.key == key) else {
            return;
        };
        editor.key_point_rows.remove(position);
        if let Some(analysis) = &mut editor.analysis {
            if position < analysis.key_points.len() {
                analysis.key_points.remove(position);
            }
        }
        cx.notify();
        // A removal is a finished decision, not typing: save it now rather
        // than leave the old point on disk for another keystroke's delay.
        self.save_key_points(cx);
    }

    /// Mark the key points edited and save them once typing settles.
    fn queue_key_points_save(&mut self, cx: &mut Context<Self>) {
        let token = {
            let Some(editor) = &mut self.editor else {
                return;
            };
            editor.key_points_dirty = true;
            editor.key_points_save_token += 1;
            editor.key_points_save_state = SaveState::Pending;
            editor.key_points_save_token
        };
        cx.notify();
        cx.spawn(async move |this, cx| {
            cx.background_executor().timer(ENTITY_AUTOSAVE_DEBOUNCE).await;
            let _ = this.update(cx, |this, cx| {
                let current = this
                    .editor
                    .as_ref()
                    .map(|e| e.key_points_save_token == token && e.key_points_dirty)
                    .unwrap_or(false);
                if current {
                    this.save_key_points(cx);
                }
            });
        })
        .detach();
    }

    /// Save waiting key-point edits straight away.
    ///
    /// Called where they must not be left in flight: leaving the editor, and
    /// opening the export dialog (exports read the analysis back from disk).
    pub(crate) fn flush_key_points_save(&mut self, cx: &mut Context<Self>) {
        let dirty = self
            .editor
            .as_ref()
            .map(|e| e.key_points_dirty)
            .unwrap_or(false);
        if dirty {
            self.save_key_points(cx);
        }
    }

    fn save_key_points(&mut self, cx: &mut Context<Self>) {
        let Some(editor) = &self.editor else {
            return;
        };
        let Some(analysis) = &editor.analysis else {
            return;
        };
        if editor.key_points_saving {
            if let Some(editor) = &mut self.editor {
                editor.key_points_dirty = true;
            }
            return;
        }
        let job_id = editor.job_id.clone();
        // A row the user added but has not typed into yet is not a key point:
        // saving it would put an empty entry in every export.
        let points: Vec<String> = analysis
            .key_points
            .iter()
            .filter(|point| !point.trim().is_empty())
            .cloned()
            .collect();
        let token = {
            let Some(editor) = &mut self.editor else {
                return;
            };
            editor.key_points_saving = true;
            editor.key_points_dirty = false;
            editor.key_points_save_state = SaveState::Saving;
            editor.key_points_save_token
        };
        let api = self.api.clone();
        cx.notify();
        cx.spawn(async move |this, cx| {
            let result = api.save_key_points(&job_id, &points).await;
            let _ = this.update(cx, |this, cx| {
                let mut pending = false;
                if let Some(editor) = &mut this.editor {
                    editor.key_points_saving = false;
                    if editor.key_points_save_token == token {
                        editor.key_points_save_state = if result.is_ok() {
                            SaveState::Saved
                        } else {
                            SaveState::Error
                        };
                    } else if result.is_err() {
                        editor.key_points_save_state = SaveState::Error;
                    }
                    pending = editor.key_points_dirty;
                }
                if let Err(err) = result {
                    this.toast(
                        ToastTone::Error,
                        "שמירת נקודות המפתח נכשלה.".to_string(),
                        Some(err.message),
                    );
                } else if pending {
                    // Edited while this request was out: send that too.
                    this.save_key_points(cx);
                }
                cx.notify();
            });
        })
        .detach();
    }

    // -- speaker quotes -----------------------------------------------------
    /// Build two fields per quote in the loaded analysis.
    ///
    /// The quotes tab keeps `analysis.speaker_quotes` flat — one `SpeakerQuote`
    /// holding one quote per row — because a row edits exactly one quote.
    /// Grouping quotes by speaker on load would break the position mapping
    /// between rows and entries, and the exporters write the same output for
    /// grouped and flat lists. Rows are rebuilt only when the analysis itself
    /// is replaced (first load, regeneration), never on an ordinary re-render.
    fn rebuild_quote_rows(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let pairs: Vec<(String, String)> = self
            .editor
            .as_ref()
            .and_then(|e| e.analysis.as_ref())
            .map(|a| {
                a.speaker_quotes
                    .iter()
                    .flat_map(|entry| {
                        entry
                            .quotes
                            .iter()
                            .map(|quote| (entry.speaker.clone(), quote.clone()))
                    })
                    .collect()
            })
            .unwrap_or_default();
        let Some(editor) = &mut self.editor else {
            return;
        };
        // Flatten the stored list too, so row positions and entry positions
        // are the same list from now on.
        if let Some(analysis) = &mut editor.analysis {
            analysis.speaker_quotes = pairs
                .iter()
                .map(|(speaker, quote)| SpeakerQuote {
                    speaker: speaker.clone(),
                    quotes: vec![quote.clone()],
                })
                .collect();
        }
        editor.quote_rows.clear();
        for (speaker, quote) in pairs {
            let key = editor.next_quote_key;
            editor.next_quote_key += 1;
            let (speaker_input, quote_input, speaker_sub, quote_sub) =
                quote_inputs(key, &speaker, &quote, window, cx);
            editor.quote_rows.push(QuoteRow {
                key,
                speaker_input,
                quote_input,
                _speaker_subscription: speaker_sub,
                _quote_subscription: quote_sub,
            });
        }
        cx.notify();
    }

    /// A row's position is the quote's position: the two lists are built and
    /// changed together.
    fn quote_position(&self, key: u64) -> Option<usize> {
        self.editor
            .as_ref()?
            .quote_rows
            .iter()
            .position(|row| row.key == key)
    }

    fn on_quote_speaker_edited(&mut self, key: u64, speaker: String, cx: &mut Context<Self>) {
        let Some(position) = self.quote_position(key) else {
            return;
        };
        let changed = self
            .editor
            .as_mut()
            .and_then(|e| e.analysis.as_mut())
            .and_then(|a| a.speaker_quotes.get_mut(position))
            .map(|entry| {
                if entry.speaker == speaker {
                    false
                } else {
                    entry.speaker = speaker;
                    true
                }
            })
            .unwrap_or(false);
        if changed {
            self.queue_quotes_save(cx);
        }
    }

    fn on_quote_edited(&mut self, key: u64, quote: String, cx: &mut Context<Self>) {
        let Some(position) = self.quote_position(key) else {
            return;
        };
        let changed = self
            .editor
            .as_mut()
            .and_then(|e| e.analysis.as_mut())
            .and_then(|a| a.speaker_quotes.get_mut(position))
            .map(|entry| {
                let Some(current) = entry.quotes.first_mut() else {
                    entry.quotes = vec![quote];
                    return true;
                };
                if *current == quote {
                    false
                } else {
                    *current = quote;
                    true
                }
            })
            .unwrap_or(false);
        if changed {
            self.queue_quotes_save(cx);
        }
    }

    fn add_quote(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let Some(editor) = &mut self.editor else {
            return;
        };
        let Some(analysis) = &mut editor.analysis else {
            return;
        };
        // Consecutive additions usually quote the same speaker, so start the
        // new row with the last one's name rather than a blank field.
        let speaker = analysis
            .speaker_quotes
            .last()
            .map(|entry| entry.speaker.clone())
            .unwrap_or_default();
        analysis.speaker_quotes.push(SpeakerQuote {
            speaker: speaker.clone(),
            quotes: vec![String::new()],
        });
        let key = editor.next_quote_key;
        editor.next_quote_key += 1;
        let (speaker_input, quote_input, speaker_sub, quote_sub) =
            quote_inputs(key, &speaker, "", window, cx);
        editor.quote_rows.push(QuoteRow {
            key,
            speaker_input,
            quote_input,
            _speaker_subscription: speaker_sub,
            _quote_subscription: quote_sub,
        });
        cx.notify();
    }

    fn remove_quote(&mut self, key: u64, cx: &mut Context<Self>) {
        let Some(editor) = &mut self.editor else {
            return;
        };
        let Some(position) = editor.quote_rows.iter().position(|row| row.key == key) else {
            return;
        };
        editor.quote_rows.remove(position);
        if let Some(analysis) = &mut editor.analysis {
            if position < analysis.speaker_quotes.len() {
                analysis.speaker_quotes.remove(position);
            }
        }
        cx.notify();
        // A removal is a finished decision, not typing: save it now rather
        // than leave the old quote on disk for another keystroke's delay.
        self.save_quotes(cx);
    }

    /// Mark the quotes edited and save them once typing settles.
    fn queue_quotes_save(&mut self, cx: &mut Context<Self>) {
        let token = {
            let Some(editor) = &mut self.editor else {
                return;
            };
            editor.quotes_dirty = true;
            editor.quotes_save_token += 1;
            editor.quotes_save_state = SaveState::Pending;
            editor.quotes_save_token
        };
        cx.notify();
        cx.spawn(async move |this, cx| {
            cx.background_executor().timer(ENTITY_AUTOSAVE_DEBOUNCE).await;
            let _ = this.update(cx, |this, cx| {
                let current = this
                    .editor
                    .as_ref()
                    .map(|e| e.quotes_save_token == token && e.quotes_dirty)
                    .unwrap_or(false);
                if current {
                    this.save_quotes(cx);
                }
            });
        })
        .detach();
    }

    /// Save waiting quote edits straight away.
    ///
    /// Called where they must not be left in flight: leaving the editor, and
    /// opening the export dialog (exports read the analysis back from disk).
    pub(crate) fn flush_quotes_save(&mut self, cx: &mut Context<Self>) {
        let dirty = self.editor.as_ref().map(|e| e.quotes_dirty).unwrap_or(false);
        if dirty {
            self.save_quotes(cx);
        }
    }

    fn save_quotes(&mut self, cx: &mut Context<Self>) {
        let Some(editor) = &self.editor else {
            return;
        };
        let Some(analysis) = &editor.analysis else {
            return;
        };
        if editor.quotes_saving {
            if let Some(editor) = &mut self.editor {
                editor.quotes_dirty = true;
            }
            return;
        }
        let job_id = editor.job_id.clone();
        // A row the user added but has not typed into yet is not a quote:
        // saving it would put an empty entry in every export.
        let quotes: Vec<SpeakerQuote> = analysis
            .speaker_quotes
            .iter()
            .filter(|entry| entry.quotes.iter().any(|quote| !quote.trim().is_empty()))
            .cloned()
            .collect();
        let token = {
            let Some(editor) = &mut self.editor else {
                return;
            };
            editor.quotes_saving = true;
            editor.quotes_dirty = false;
            editor.quotes_save_state = SaveState::Saving;
            editor.quotes_save_token
        };
        let api = self.api.clone();
        cx.notify();
        cx.spawn(async move |this, cx| {
            let result = api.save_speaker_quotes(&job_id, &quotes).await;
            let _ = this.update(cx, |this, cx| {
                let mut pending = false;
                if let Some(editor) = &mut this.editor {
                    editor.quotes_saving = false;
                    if editor.quotes_save_token == token {
                        editor.quotes_save_state = if result.is_ok() {
                            SaveState::Saved
                        } else {
                            SaveState::Error
                        };
                    } else if result.is_err() {
                        editor.quotes_save_state = SaveState::Error;
                    }
                    pending = editor.quotes_dirty;
                }
                if let Err(err) = result {
                    this.toast(
                        ToastTone::Error,
                        "שמירת הציטוטים נכשלה.".to_string(),
                        Some(err.message),
                    );
                } else if pending {
                    // Edited while this request was out: send that too.
                    this.save_quotes(cx);
                }
                cx.notify();
            });
        })
        .detach();
    }

    // -- summary (תקציר) --------------------------------------------------
    /// Fill the summary field from the loaded analysis. Rebuilt like the row
    /// lists, so a regenerated analysis replaces whatever was being edited.
    fn rebuild_summary_input(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let summary = self
            .editor
            .as_ref()
            .and_then(|e| e.analysis.as_ref())
            .map(|a| a.summary.clone())
            .unwrap_or_default();
        let input = cx.new(|cx| {
            let mut state = RtlField::new(window, cx)
                .placeholder("כתבו תקציר…")
                .multiline(true)
                .min_rows(16);
            state.set_value(&summary, window, cx);
            state
        });
        let sub = cx.subscribe_in(&input, window, move |this: &mut App, state, event, _, cx| {
            if matches!(event, RtlFieldEvent::Change) {
                let summary = state.read(cx).value().to_string();
                this.on_summary_edited(summary, cx);
            }
        });
        if let Some(editor) = &mut self.editor {
            editor.summary_input = input;
            editor._summary_subscription = sub;
        }
    }

    fn on_summary_edited(&mut self, summary: String, cx: &mut Context<Self>) {
        let changed = self
            .editor
            .as_mut()
            .and_then(|e| e.analysis.as_mut())
            .map(|a| {
                if a.summary == summary {
                    false
                } else {
                    a.summary = summary;
                    true
                }
            })
            .unwrap_or(false);
        if changed {
            self.queue_summary_save(cx);
        }
    }

    /// Mark the summary edited and save it once typing settles.
    fn queue_summary_save(&mut self, cx: &mut Context<Self>) {
        let token = {
            let Some(editor) = &mut self.editor else {
                return;
            };
            editor.summary_dirty = true;
            editor.summary_save_token += 1;
            editor.summary_save_state = SaveState::Pending;
            editor.summary_save_token
        };
        cx.notify();
        cx.spawn(async move |this, cx| {
            cx.background_executor().timer(ENTITY_AUTOSAVE_DEBOUNCE).await;
            let _ = this.update(cx, |this, cx| {
                let current = this
                    .editor
                    .as_ref()
                    .map(|e| e.summary_save_token == token && e.summary_dirty)
                    .unwrap_or(false);
                if current {
                    this.save_summary(cx);
                }
            });
        })
        .detach();
    }

    /// Save waiting summary edits straight away.
    pub(crate) fn flush_summary_save(&mut self, cx: &mut Context<Self>) {
        let dirty = self
            .editor
            .as_ref()
            .map(|e| e.summary_dirty)
            .unwrap_or(false);
        if dirty {
            self.save_summary(cx);
        }
    }

    fn save_summary(&mut self, cx: &mut Context<Self>) {
        let Some(editor) = &self.editor else {
            return;
        };
        let Some(analysis) = &editor.analysis else {
            return;
        };
        if editor.summary_saving {
            if let Some(editor) = &mut self.editor {
                editor.summary_dirty = true;
            }
            return;
        }
        let job_id = editor.job_id.clone();
        let summary = analysis.summary.clone();
        let token = {
            let Some(editor) = &mut self.editor else {
                return;
            };
            editor.summary_saving = true;
            editor.summary_dirty = false;
            editor.summary_save_state = SaveState::Saving;
            editor.summary_save_token
        };
        let api = self.api.clone();
        cx.notify();
        cx.spawn(async move |this, cx| {
            let result = api.save_summary(&job_id, &summary).await;
            let _ = this.update(cx, |this, cx| {
                let mut pending = false;
                if let Some(editor) = &mut this.editor {
                    editor.summary_saving = false;
                    if editor.summary_save_token == token {
                        editor.summary_save_state = if result.is_ok() {
                            SaveState::Saved
                        } else {
                            SaveState::Error
                        };
                    } else if result.is_err() {
                        editor.summary_save_state = SaveState::Error;
                    }
                    pending = editor.summary_dirty;
                }
                if let Err(err) = result {
                    this.toast(
                        ToastTone::Error,
                        "שמירת התקציר נכשלה.".to_string(),
                        Some(err.message),
                    );
                } else if pending {
                    // Edited while this request was out: send that too.
                    this.save_summary(cx);
                }
                cx.notify();
            });
        })
        .detach();
    }

    fn summary_section(&self, _cx: &mut Context<Self>) -> AnyElement {
        let Some(editor) = &self.editor else {
            return div().into_any_element();
        };
        let Some(_analysis) = &editor.analysis else {
            return div().into_any_element();
        };
        // The section grows with the card's body wrapper (see
        // `render_analysis`), so the field below can span the whole page.
        div()
            .flex_1()
            .min_h_0()
            .v_flex()
            .gap_2()
            .child(
                div()
                    .flex()
                    .flex_row_reverse()
                    .items_center()
                    .justify_between()
                    .child(
                        div()
                            .flex()
                            .flex_row_reverse()
                            .items_center()
                            .gap_2()
                            .child(div().text_sm().child(rtl("תקציר")))
                            .child(save_indicator(editor.summary_save_state)),
                    ),
            )
            .child(
                div()
                    .text_xs()
                    .text_color(ui::muted())
                    .child(rtl_paragraph(
                        "אפשר לערוך את התקציר ישירות בשדה. השינויים נשמרים אוטומטית.",
                    )),
            )
            // The field fills whatever height the page leaves it: the section
            // above sits in a flex column that stretches with the analysis
            // card, and the textarea is 100% of this wrapper. Soft wrapping
            // stays off (see `summary_field_value`) so every row is one
            // logical line and never breaks mid-paragraph.
            .child(
                div()
                    .flex_1()
                    .min_w_0()
                    .min_h_0()
                    .child(editor.summary_input.clone()),
            )
            .into_any_element()
    }

    fn speaker_summaries_section(&self, _cx: &mut Context<Self>) -> AnyElement {
        let Some(editor) = &self.editor else {
            return div().into_any_element();
        };
        let Some(analysis) = &editor.analysis else {
            return div().into_any_element();
        };

        let mut rows: Vec<AnyElement> = Vec::new();
        for (index, row) in editor.speaker_summary_rows.iter().enumerate() {
            let Some(item) = analysis.speaker_summaries.get(index) else {
                continue;
            };
            let speaker = item.speaker.clone();
            rows.push(
                div()
                    .id(("speaker-summary-row", index))
                    .v_flex()
                    .gap_1()
                    .py_1()
                    .child(
                        div()
                            .text_xs()
                            .text_color(ui::accent())
                            .child(rtl(&speaker)),
                    )
                    .child(
                        div()
                            .flex()
                            .flex_row_reverse()
                            .items_center()
                            .gap_2()
                            .child(div().flex_1().min_w_0().child(row.summary_input.clone())),
                    )
                    .into_any_element(),
            );
        }

        let empty = rows.is_empty();
        div()
            .v_flex()
            .gap_2()
            .child(
                div()
                    .flex()
                    .flex_row_reverse()
                    .items_center()
                    .justify_between()
                    .child(
                        div()
                            .flex()
                            .flex_row_reverse()
                            .items_center()
                            .gap_2()
                            .child(div().text_sm().child(rtl("סיכום דוברים")))
                            .child(save_indicator(editor.speaker_summaries_save_state)),
                    ),
            )
            .child(
                div()
                    .text_xs()
                    .text_color(ui::muted())
                    .child(rtl_paragraph(
                        "אפשר לערוך את הסיכום של כל דובר ישירות בשדה. השינויים נשמרים אוטומטית.",
                    )),
            )
            .when(empty, |this| {
                this.child(
                    div()
                        .text_sm()
                        .text_color(ui::muted())
                        .child(rtl_paragraph("לא זוהו דוברים בתמליל.")),
                )
            })
            .child(div().v_flex().gap_1().children(rows))
            .into_any_element()
    }

    // -- action items -----------------------------------------------------
    /// Build two fields per action item in the loaded analysis: the task and
    /// its assignee. Like the entity rows, these mirror
    /// `analysis.action_items` by position and are rebuilt only when the
    /// analysis itself is replaced — never on an ordinary re-render.
    fn rebuild_action_item_rows(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let items: Vec<(String, Option<String>)> = self
            .editor
            .as_ref()
            .and_then(|e| e.analysis.as_ref())
            .map(|a| {
                a.action_items
                    .iter()
                    .map(|item| (item.task.clone(), item.assignee.clone()))
                    .collect()
            })
            .unwrap_or_default();
        let Some(editor) = &mut self.editor else {
            return;
        };
        editor.action_item_rows.clear();
        for (task, assignee) in items {
            let key = editor.next_action_item_key;
            editor.next_action_item_key += 1;
            let (task_input, assignee_input, task_sub, assignee_sub) =
                action_item_inputs(key, &task, assignee.as_deref(), window, cx);
            editor.action_item_rows.push(ActionItemRow {
                key,
                task_input,
                assignee_input,
                _task_subscription: task_sub,
                _assignee_subscription: assignee_sub,
            });
        }
        cx.notify();
    }

    /// A row's position is the action item's position: the two lists are built
    /// and changed together.
    fn action_item_position(&self, key: u64) -> Option<usize> {
        self.editor
            .as_ref()?
            .action_item_rows
            .iter()
            .position(|row| row.key == key)
    }

    fn on_action_task_edited(&mut self, key: u64, task: String, cx: &mut Context<Self>) {
        let Some(position) = self.action_item_position(key) else {
            return;
        };
        let changed = self
            .editor
            .as_mut()
            .and_then(|e| e.analysis.as_mut())
            .and_then(|a| a.action_items.get_mut(position))
            .map(|item| {
                if item.task == task {
                    false
                } else {
                    item.task = task;
                    true
                }
            })
            .unwrap_or(false);
        if changed {
            self.queue_action_items_save(cx);
        }
    }

    fn on_action_assignee_edited(&mut self, key: u64, assignee: String, cx: &mut Context<Self>) {
        let Some(position) = self.action_item_position(key) else {
            return;
        };
        let next = if assignee.trim().is_empty() {
            None
        } else {
            Some(assignee)
        };
        let changed = self
            .editor
            .as_mut()
            .and_then(|e| e.analysis.as_mut())
            .and_then(|a| a.action_items.get_mut(position))
            .map(|item| {
                if item.assignee == next {
                    false
                } else {
                    item.assignee = next;
                    true
                }
            })
            .unwrap_or(false);
        if changed {
            self.queue_action_items_save(cx);
        }
    }

    fn add_action_item(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let Some(editor) = &mut self.editor else {
            return;
        };
        let Some(analysis) = &mut editor.analysis else {
            return;
        };
        analysis.action_items.push(ActionItem {
            task: String::new(),
            assignee: None,
        });
        let key = editor.next_action_item_key;
        editor.next_action_item_key += 1;
        let (task_input, assignee_input, task_sub, assignee_sub) =
            action_item_inputs(key, "", None, window, cx);
        editor.action_item_rows.push(ActionItemRow {
            key,
            task_input,
            assignee_input,
            _task_subscription: task_sub,
            _assignee_subscription: assignee_sub,
        });
        cx.notify();
    }

    fn remove_action_item(&mut self, key: u64, cx: &mut Context<Self>) {
        let Some(editor) = &mut self.editor else {
            return;
        };
        let Some(position) = editor.action_item_rows.iter().position(|row| row.key == key) else {
            return;
        };
        editor.action_item_rows.remove(position);
        if let Some(analysis) = &mut editor.analysis {
            if position < analysis.action_items.len() {
                analysis.action_items.remove(position);
            }
        }
        cx.notify();
        // A removal is a finished decision, not typing: save it now rather
        // than leave the old item on disk for another keystroke's delay.
        self.save_action_items(cx);
    }

    /// Mark the action-item list edited and save it once typing settles.
    fn queue_action_items_save(&mut self, cx: &mut Context<Self>) {
        let token = {
            let Some(editor) = &mut self.editor else {
                return;
            };
            editor.action_items_dirty = true;
            editor.action_items_save_token += 1;
            editor.action_items_save_state = SaveState::Pending;
            editor.action_items_save_token
        };
        cx.notify();
        cx.spawn(async move |this, cx| {
            cx.background_executor().timer(ENTITY_AUTOSAVE_DEBOUNCE).await;
            let _ = this.update(cx, |this, cx| {
                let current = this
                    .editor
                    .as_ref()
                    .map(|e| e.action_items_save_token == token && e.action_items_dirty)
                    .unwrap_or(false);
                if current {
                    this.save_action_items(cx);
                }
            });
        })
        .detach();
    }

    /// Save waiting action-item edits straight away.
    ///
    /// Called where they must not be left in flight: leaving the editor, and
    /// opening the export dialog (exports read the analysis back from disk).
    pub(crate) fn flush_action_items_save(&mut self, cx: &mut Context<Self>) {
        let dirty = self
            .editor
            .as_ref()
            .map(|e| e.action_items_dirty)
            .unwrap_or(false);
        if dirty {
            self.save_action_items(cx);
        }
    }

    fn save_action_items(&mut self, cx: &mut Context<Self>) {
        let Some(editor) = &self.editor else {
            return;
        };
        let Some(analysis) = &editor.analysis else {
            return;
        };
        if editor.action_items_saving {
            if let Some(editor) = &mut self.editor {
                editor.action_items_dirty = true;
            }
            return;
        }
        let job_id = editor.job_id.clone();
        // A row the user added but has not written a task for is not an item:
        // saving it would put an empty entry in every export.
        let items: Vec<ActionItem> = analysis
            .action_items
            .iter()
            .filter(|item| !item.task.trim().is_empty())
            .cloned()
            .collect();
        let token = {
            let Some(editor) = &mut self.editor else {
                return;
            };
            editor.action_items_saving = true;
            editor.action_items_dirty = false;
            editor.action_items_save_state = SaveState::Saving;
            editor.action_items_save_token
        };
        let api = self.api.clone();
        cx.notify();
        cx.spawn(async move |this, cx| {
            let result = api.save_action_items(&job_id, &items).await;
            let _ = this.update(cx, |this, cx| {
                let mut pending = false;
                if let Some(editor) = &mut this.editor {
                    editor.action_items_saving = false;
                    if editor.action_items_save_token == token {
                        editor.action_items_save_state = if result.is_ok() {
                            SaveState::Saved
                        } else {
                            SaveState::Error
                        };
                    } else if result.is_err() {
                        editor.action_items_save_state = SaveState::Error;
                    }
                    pending = editor.action_items_dirty;
                }
                if let Err(err) = result {
                    this.toast(
                        ToastTone::Error,
                        "שמירת המשימות נכשלה.".to_string(),
                        Some(err.message),
                    );
                } else if pending {
                    // Edited while this request was out: send that too.
                    this.save_action_items(cx);
                }
                cx.notify();
            });
        })
        .detach();
    }

    fn action_items_section(&self, cx: &mut Context<Self>) -> AnyElement {
        let Some(editor) = &self.editor else {
            return div().into_any_element();
        };
        let Some(analysis) = &editor.analysis else {
            return div().into_any_element();
        };

        let mut rows: Vec<AnyElement> = Vec::new();
        for (index, row) in editor.action_item_rows.iter().enumerate() {
            if index >= analysis.action_items.len() {
                continue;
            }
            let key = row.key;
            rows.push(
                div()
                    .id(("action-item-row", index))
                    .flex()
                    .flex_row_reverse()
                    .items_center()
                    .gap_2()
                    .py_1()
                    .child(div().flex_1().min_w_0().child(row.task_input.clone()))
                    .child(div().w(px(160.)).child(row.assignee_input.clone()))
                    .child(
                        Button::new(format!("action-remove-{key}"))
                            .ghost()
                            .compact()
                            .label(rtl("הסרה"))
                            .on_click(cx.listener(move |this, _, _, cx| {
                                this.remove_action_item(key, cx)
                            })),
                    )
                    .into_any_element(),
            );
        }

        let empty = rows.is_empty();
        div()
            .v_flex()
            .gap_2()
            .child(
                div()
                    .flex()
                    .flex_row_reverse()
                    .items_center()
                    .justify_between()
                    .child(
                        div()
                            .flex()
                            .flex_row_reverse()
                            .items_center()
                            .gap_2()
                            .child(div().text_sm().child(rtl("משימות")))
                            .child(save_indicator(editor.action_items_save_state)),
                    )
                    .child(
                        Button::new("add-action-item")
                            .secondary()
                            .compact()
                            .label(rtl("הוספת משימה"))
                            .on_click(
                                cx.listener(|this, _, window, cx| this.add_action_item(window, cx)),
                            ),
                    ),
            )
            .child(
                div()
                    .text_xs()
                    .text_color(ui::muted())
                    .child(rtl_paragraph(
                        "אפשר לערוך את המשימה ואת האחראי ישירות בשדות, להוסיף משימה חדשה ולהסיר קיימת. השינויים נשמרים אוטומטית.",
                    )),
            )
            .when(empty, |this| {
                this.child(
                    div()
                        .text_sm()
                        .text_color(ui::muted())
                        .child(rtl_paragraph("לא נמצאו משימות או פריטי פעולה.")),
                )
            })
            .child(div().v_flex().gap_1().children(rows))
            .into_any_element()
    }

    fn named_entities_section(&self, cx: &mut Context<Self>) -> AnyElement {
        let Some(editor) = &self.editor else {
            return div().into_any_element();
        };
        let Some(analysis) = &editor.analysis else {
            return div().into_any_element();
        };

        let mut rows: Vec<AnyElement> = Vec::new();
        for (index, row) in editor.entity_rows.iter().enumerate() {
            let Some(entity) = analysis.named_entities.get(index) else {
                continue;
            };
            let key = row.key;
            let kind = entity.r#type.clone();
            rows.push(
                div()
                    .id(("entity-row", index))
                    .flex()
                    .flex_row_reverse()
                    .items_center()
                    .gap_2()
                    .py_1()
                    .child(div().flex_1().min_w_0().child(row.name_input.clone()))
                    .child(
                        Button::new(format!("entity-type-{key}"))
                            .ghost()
                            .compact()
                            .label(rtl(&format!("סוג: {}", entity_type_label(&kind))))
                            .on_click(
                                cx.listener(move |this, _, _, cx| this.cycle_entity_type(key, cx)),
                            ),
                    )
                    .child(
                        Button::new(format!("entity-remove-{key}"))
                            .ghost()
                            .compact()
                            .label(rtl("הסרה"))
                            .on_click(cx.listener(move |this, _, _, cx| {
                                this.remove_entity(key, cx)
                            })),
                    )
                    .into_any_element(),
            );
        }

        let empty = rows.is_empty();
        div()
            .v_flex()
            .gap_2()
            .child(
                div()
                    .flex()
                    .flex_row_reverse()
                    .items_center()
                    .justify_between()
                    .child(
                        div()
                            .flex()
                            .flex_row_reverse()
                            .items_center()
                            .gap_2()
                            .child(div().text_sm().child(rtl("יישויות בעלות שם")))
                            .child(save_indicator(editor.analysis_save_state)),
                    )
                    .child(
                        Button::new("add-entity")
                            .secondary()
                            .compact()
                            .label(rtl("הוספת יישות"))
                            .on_click(
                                cx.listener(|this, _, window, cx| this.add_entity(window, cx)),
                            ),
                    ),
            )
            .child(
                div()
                    .text_xs()
                    .text_color(ui::muted())
                    .child(rtl_paragraph(
                        "אפשר לשנות שם ישירות בשדה, להחליף סוג בלחיצה על הכפתור, להוסיף ישות חדשה ולהסיר קיימת. השינויים נשמרים אוטומטית.",
                    )),
            )
            .when(empty, |this| {
                this.child(
                    div()
                        .text_sm()
                        .text_color(ui::muted())
                        .child(rtl_paragraph("לא נמצאו ישויות בעלות שם.")),
                )
            })
            .child(div().v_flex().gap_1().children(rows))
            .into_any_element()
    }

    fn key_points_section(&self, cx: &mut Context<Self>) -> AnyElement {
        let Some(editor) = &self.editor else {
            return div().into_any_element();
        };
        let Some(analysis) = &editor.analysis else {
            return div().into_any_element();
        };

        let mut rows: Vec<AnyElement> = Vec::new();
        for (index, row) in editor.key_point_rows.iter().enumerate() {
            let Some(_point) = analysis.key_points.get(index) else {
                continue;
            };
            let key = row.key;
            rows.push(
                div()
                    .id(("key-point-row", index))
                    .flex()
                    .flex_row_reverse()
                    .items_center()
                    .gap_2()
                    .py_1()
                    .child(div().flex_1().min_w_0().child(row.input.clone()))
                    .child(
                        Button::new(format!("key-point-remove-{key}"))
                            .ghost()
                            .compact()
                            .label(rtl("הסרה"))
                            .on_click(cx.listener(move |this, _, _, cx| {
                                this.remove_key_point(key, cx)
                            })),
                    )
                    .into_any_element(),
            );
        }

        let empty = rows.is_empty();
        div()
            .v_flex()
            .gap_2()
            .child(
                div()
                    .flex()
                    .flex_row_reverse()
                    .items_center()
                    .justify_between()
                    .child(
                        div()
                            .flex()
                            .flex_row_reverse()
                            .items_center()
                            .gap_2()
                            .child(div().text_sm().child(rtl("נקודות מפתח")))
                            .child(save_indicator(editor.key_points_save_state)),
                    )
                    .child(
                        Button::new("add-key-point")
                            .secondary()
                            .compact()
                            .label(rtl("הוספת נקודה"))
                            .on_click(cx.listener(|this, _, window, cx| {
                                this.add_key_point(window, cx)
                            })),
                    ),
            )
            .child(
                div()
                    .text_xs()
                    .text_color(ui::muted())
                    .child(rtl_paragraph(
                        "אפשר לערוך כל נקודה ישירות בשדה, להוסיף נקודה חדשה ולהסיר קיימת. השינויים נשמרים אוטומטית.",
                    )),
            )
            .when(empty, |this| {
                this.child(
                    div()
                        .text_sm()
                        .text_color(ui::muted())
                        .child(rtl_paragraph("לא נמצאו נקודות מפתח.")),
                )
            })
            .child(div().v_flex().gap_1().children(rows))
            .into_any_element()
    }

    fn quotes_section(&self, window: &mut Window, cx: &mut Context<Self>) -> AnyElement {
        let Some(editor) = &self.editor else {
            return div().into_any_element();
        };
        let Some(analysis) = &editor.analysis else {
            return div().into_any_element();
        };

        let mut rows: Vec<AnyElement> = Vec::new();
        for (index, row) in editor.quote_rows.iter().enumerate() {
            let Some(_entry) = analysis.speaker_quotes.get(index) else {
                continue;
            };
            let key = row.key;
            rows.push(
                div()
                    .id(("quote-row", index))
                    .v_flex()
                    .gap_1()
                    .py_1()
                    .child(
                        div()
                            .flex()
                            .flex_row_reverse()
                            .items_center()
                            .gap_2()
                            .child(
                                div()
                                    .text_xs()
                                    .text_color(ui::muted())
                                    .child(rtl("דובר:")),
                            )
                            .child(
                                div()
                                    .w(px(180.))
                                    .child(row.speaker_input.clone()),
                            )
                            .child(
                                Button::new(format!("quote-remove-{key}"))
                                    .ghost()
                                    .compact()
                                    .label(rtl("הסרה"))
                                    .on_click(cx.listener(move |this, _, _, cx| {
                                        this.remove_quote(key, cx)
                                    })),
                            ),
                    )
                    .child(div().flex_1().min_w_0().child(row.quote_input.clone()))
                    .into_any_element(),
            );
        }

        let empty = rows.is_empty();
        div()
            .v_flex()
            .gap_2()
            .child(
                div()
                    .flex()
                    .flex_row_reverse()
                    .items_center()
                    .justify_between()
                    .child(
                        div()
                            .flex()
                            .flex_row_reverse()
                            .items_center()
                            .gap_2()
                            .child(div().text_sm().child(rtl("ציטוטים")))
                            .child(save_indicator(editor.quotes_save_state)),
                    )
                    .child(
                        Button::new("add-quote")
                            .secondary()
                            .compact()
                            .label(rtl("הוספת ציטוט"))
                            .on_click(cx.listener(|this, _, window, cx| {
                                this.add_quote(window, cx)
                            })),
                    ),
            )
            .child(
                div()
                    .text_xs()
                    .text_color(ui::muted())
                    .child(rtl_paragraph(
                        "אפשר לערוך את הציטוט ואת שם הדובר ישירות בשדות, להוסיף ציטוט חדש ולהסיר קיים. השינויים נשמרים אוטומטית.",
                    )),
            )
            .when(empty, |this| {
                this.child(
                    div()
                        .text_sm()
                        .text_color(ui::muted())
                        .child(rtl_paragraph("לא זוהו דוברים בתמליל.")),
                )
            })
            .child(div().v_flex().gap_1().children(rows))
            .into_any_element()
    }

    fn render_analysis(&self, window: &mut Window, cx: &mut Context<Self>) -> AnyElement {
        let Some(editor) = &self.editor else {
            return div().into_any_element();
        };
        let tab = editor.analysis_tab.unwrap_or(AnalysisTab::Summary);
        match editor.analysis_state {
            AnalysisState::Loading => div()
                .p_8()
                .child(div().text_base().text_color(ui::muted()).child(rtl("מנתח בעזרת DeepSeek…")))
                .into_any_element(),
            AnalysisState::Error => div()
                .p_8()
                .v_flex()
                .gap_2()
                .child(
                    div()
                        .text_sm()
                        .text_color(ui::danger())
                        .child(rtl_paragraph(editor.analysis_error.clone().unwrap_or_else(|| "יצירת הניתוח נכשלה.".to_string()))),
                )
                .child(
                    Button::new("retry-analysis")
                        .secondary()
                        .label(rtl("נסה שוב"))
                        .on_click(cx.listener(|this, _, window, cx| {
                            this.load_analysis(false, window, cx)
                        })),
                )
                .into_any_element(),
            _ => {
                let Some(analysis) = &editor.analysis else {
                    return div().p_8().child(rtl("טוען…")).into_any_element();
                };
                let body = if tab == AnalysisTab::Summary {
                    // The summary is hand-edited too, as one multi-line field
                    // that spans the rest of the page vertically.
                    self.summary_section(cx)
                } else if tab == AnalysisTab::ActionItems {
                    // Tasks are hand-edited: a task and assignee field per row,
                    // with add and remove controls.
                    self.action_items_section(cx)
                } else if tab == AnalysisTab::NamedEntities {
                    // The entities tab is one analysis tab whose content the
                    // user maintains, so it gets editable rows instead of the
                    // read-only list every other tab uses.
                    self.named_entities_section(cx)
                } else if tab == AnalysisTab::SpeakerSummaries {
                    // Speaker summaries are also hand-edited, in the same way.
                    self.speaker_summaries_section(cx)
                } else if tab == AnalysisTab::KeyPoints {
                    // Key points are hand-edited too: one field per point,
                    // with add and remove controls.
                    self.key_points_section(cx)
                } else if tab == AnalysisTab::SpeakerQuotes {
                    // Quotes are hand-edited too: a speaker and quote field
                    // per row, with add and remove controls.
                    self.quotes_section(window, cx)
                } else {
                    analysis_section(tab, analysis)
                };
                ui::card()
                    .p_5()
                    .gap_3()
                    // On the summary tab the card stretches to the bottom of
                    // the page so the field inside can fill it; the other
                    // tabs keep their natural height.
                    .when(tab == AnalysisTab::Summary, |this| {
                        this.flex_1().min_h_0()
                    })
                    .child(
                        div()
                            .flex()
                            .flex_row_reverse()
                            .items_center()
                            .justify_between()
                            .child(
                                div()
                                    .text_xs()
                                    .text_color(ui::muted())
                                    .child(rtl(&format!("נוצר בעזרת DeepSeek {}", latin(&analysis.model)))),
                            )
                            .child(
                                div()
                                    .flex()
                                    .flex_row_reverse()
                                    .gap_2()
                                    .child(
                                        Button::new("export-this-tab")
                                            .secondary()
                                            .label(rtl("ייצוא לשונית זו"))
                                            .on_click(cx.listener(move |this, _, _, cx| {
                                                this.open_export_for(section_for_tab(tab), cx)
                                            })),
                                    )
                                    .child(
                                        Button::new("regen-analysis")
                                            .ghost()
                                            .label(rtl("יצירה מחדש"))
                                            .on_click(cx.listener(|this, _, window, cx| {
                                                this.load_analysis(true, window, cx)
                                            })),
                                    ),
                            ),
                    )
                    .child(
                        div()
                            .v_flex()
                            .when(tab == AnalysisTab::Summary, |this| {
                                this.flex_1().min_h_0()
                            })
                            .child(body),
                    )
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
        ui::card()
            .p_3p5()
            .gap_2()
            .child(
                div()
                    .flex()
                    .flex_row_reverse()
                    .items_center()
                    .gap_2()
                    .child(div().flex_1().min_w_0().child(query.clone()))
                    .child(
                        Button::new("run-search")
                            .secondary()
                            .icon(ui::icon(Ic::Search, 15.0, ui::subtle()))
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
                    .child(div().text_xs().text_color(ui::muted()).child(rtl(&position)))
                    .child(
                        Button::new("search-prev")
                            .ghost()
                            .compact()
                            .icon(ui::icon(Ic::ChevronRight, 15.0, ui::subtle()))
                            .disabled(count == 0)
                            .on_click(cx.listener(|this, _, _, cx| this.step_search(-1, cx))),
                    )
                    .child(
                        Button::new("search-next")
                            .ghost()
                            .compact()
                            .icon(ui::icon(Ic::ChevronLeft, 15.0, ui::subtle()))
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
                    .child(div().flex_1().min_w_0().child(replacement.clone()))
                    .child(
                        Button::new("replace-current")
                            .ghost()
                            .icon(ui::icon(Ic::Replace, 15.0, ui::subtle()))
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

    fn on_speaker_rename(
        &mut self,
        key: String,
        name: String,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        // The analysis calls a speaker by what the transcript called them, and
        // an unnamed one by "דובר N". Work out both ends of the change.
        let fallback = default_speaker_label(&key);
        let old = self
            .editor
            .as_ref()
            .and_then(|e| e.speakers.get(&key))
            .filter(|n| !n.trim().is_empty())
            .cloned()
            .unwrap_or_else(|| fallback.clone());
        let new = if name.trim().is_empty() { fallback } else { name.trim().to_string() };
        if let Some(e) = &mut self.editor {
            e.speakers.insert(key, name);
        }
        if old != new {
            self.relabel_open_analysis(&old, &new, window, cx);
        }
        cx.notify();
        // Renaming is typing too, so it goes through the same debounce rather
        // than a request per keystroke.
        self.queue_save(cx);
    }

    /// Rename a speaker in every analysis tab that is open: the summary, key
    /// points, tasks (text and assignee), speaker summaries and quotes. The
    /// stored analysis is updated by the backend when the transcript saves; this
    /// keeps what is on screen in step, field by field.
    fn relabel_open_analysis(
        &mut self,
        old: &str,
        new: &str,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        let mut updates: Vec<(Entity<RtlField>, String)> = Vec::new();
        {
            let Some(e) = &mut self.editor else { return };
            let Some(a) = &mut e.analysis else { return };

            a.summary = replace_label(&a.summary, old, new);
            for point in &mut a.key_points {
                *point = replace_label(point, old, new);
            }
            for item in &mut a.speaker_summaries {
                if item.speaker == old {
                    item.speaker = new.to_string();
                }
                item.summary = replace_label(&item.summary, old, new);
            }
            for quote in &mut a.speaker_quotes {
                if quote.speaker == old {
                    quote.speaker = new.to_string();
                }
            }
            for item in &mut a.action_items {
                if item.assignee.as_deref() == Some(old) {
                    item.assignee = Some(new.to_string());
                }
                item.task = replace_label(&item.task, old, new);
            }

            updates.push((e.summary_input.clone(), a.summary.clone()));
            for (row, text) in e.key_point_rows.iter().zip(&a.key_points) {
                updates.push((row.input.clone(), text.clone()));
            }
            for (row, item) in e.speaker_summary_rows.iter().zip(&a.speaker_summaries) {
                updates.push((row.summary_input.clone(), item.summary.clone()));
            }
            for (row, item) in e.action_item_rows.iter().zip(&a.action_items) {
                updates.push((row.task_input.clone(), item.task.clone()));
                updates.push((
                    row.assignee_input.clone(),
                    item.assignee.clone().unwrap_or_default(),
                ));
            }
            for (row, item) in e.quote_rows.iter().zip(&a.speaker_quotes) {
                updates.push((row.speaker_input.clone(), item.speaker.clone()));
            }
        }
        for (field, text) in updates {
            if field.read(cx).value() != text {
                field.update(cx, |field, cx| field.set_value(&text, window, cx));
            }
        }
    }

    /// Add a speaker that the diarizer did not find. The new key is unused by
    /// any segment until the transcript is re-attributed, but it is saved with
    /// the speaker map so the name survives a reload.
    fn add_speaker(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        let Some(e) = &self.editor else { return };
        let mut index = 0usize;
        let key = loop {
            let candidate = format!("SPEAKER_{index:02}");
            if !e.speaker_order.contains(&candidate) && !e.speakers.contains_key(&candidate) {
                break candidate;
            }
            index += 1;
        };
        let name = format!("דובר {}", e.speaker_order.len() + 1);
        let (state, sub) = speaker_input(key.clone(), &name, window, cx);
        if let Some(e) = &mut self.editor {
            e.speakers.insert(key.clone(), name);
            e.speaker_order.push(key.clone());
            e.speaker_inputs.insert(key.clone(), state);
            e.speaker_subs.insert(key, sub);
        }
        cx.notify();
        self.save_now(cx);
    }

    /// Remove a speaker and unassign it from every segment that carried it, so
    /// no segment is left pointing at a label that no longer exists.
    fn remove_speaker(&mut self, key: String, cx: &mut Context<Self>) {
        let Some(e) = &mut self.editor else { return };
        e.speakers.remove(&key);
        e.speaker_order.retain(|k| k != &key);
        e.speaker_inputs.remove(&key);
        e.speaker_subs.remove(&key);
        let mut next = e.segments.clone();
        let mut touched = false;
        for segment in &mut next {
            if segment.speaker.as_deref() == Some(key.as_str()) {
                segment.speaker = None;
                segment.edited = true;
                touched = true;
            }
        }
        if touched {
            // Goes through the undo stack, like any other segment edit.
            self.commit_segments(next, cx);
        } else {
            cx.notify();
            self.save_now(cx);
        }
    }

    fn speaker_rename_bar(&self, cx: &mut Context<Self>) -> AnyElement {
        let Some(editor) = &self.editor else {
            return div().into_any_element();
        };
        let mut rows: Vec<AnyElement> = Vec::new();
        for key in &editor.speaker_order {
            let Some(state) = editor.speaker_inputs.get(key) else {
                continue;
            };
            let removed = key.clone();
            let count = editor
                .segments
                .iter()
                .filter(|s| s.speaker.as_deref() == Some(key.as_str()))
                .count();
            let hint = if count == 0 {
                rtl("ללא קטעים")
            } else {
                rtl(&format!("{count} קטעים"))
            };
            rows.push(
                div()
                    .flex()
                    .flex_row_reverse()
                    .items_center()
                    .gap_2()
                    .child(div().text_xs().text_color(ui::muted()).child(rtl("שם דובר:")))
                    .child(div().w(px(160.)).child(state.clone()))
                    .child(div().text_xs().text_color(ui::faint()).child(hint))
                    .child(
                        Button::new(format!("remove-speaker-{key}"))
                            .ghost()
                            .compact()
                            .label(rtl("הסרה"))
                            .on_click(cx.listener(move |this, _, _, cx| {
                                this.remove_speaker(removed.clone(), cx)
                            })),
                    )
                    .into_any_element(),
            );
        }
        let empty = rows.is_empty();
        ui::card()
            .p_3p5()
            .gap_2()
            .child(
                div()
                    .flex()
                    .flex_row_reverse()
                    .items_center()
                    .justify_between()
                    .child(ui::section_header(Ic::Users, Tone::Neutral, "שמות דוברים", None))
                    .child(
                        ui::rtl_row()
                            .gap_2()
                            .child(
                                Button::new("find-speaker-names")
                                    .secondary()
                                    .compact()
                                    .icon(ui::icon(Ic::Sparkles, 15.0, ui::accent()))
                                    .label(rtl("חפש שמות"))
                                    .tooltip(rtl("מוצא שמות אמיתיים לדוברים שנאמרו בהקלטה"))
                                    .on_click(cx.listener(|this, _, window, cx| {
                                        this.find_speaker_names(window, cx)
                                    })),
                            )
                            .child(
                        Button::new("add-speaker")
                            .secondary()
                            .compact()
                            .label(rtl("הוספת דובר"))
                            .on_click(cx.listener(|this, _, window, cx| {
                                this.add_speaker(window, cx)
                            })),
                            ),
                    ),
            )
            .when(empty, |this| {
                this.child(
                    div()
                        .text_xs()
                        .text_color(ui::muted())
                        .child(rtl("לא זוהו דוברים בתמליל.")),
                )
            })
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

/// Hebrew label for one of the canonical entity types. A type the model
/// invented is shown as it is, the same way the exporters keep it as a
/// heading of its own.
fn entity_type_label(kind: &str) -> String {
    match kind {
        "person" => "אדם".to_string(),
        "organization" => "ארגון".to_string(),
        "location" => "מקום".to_string(),
        "date" => "תאריך".to_string(),
        "other" => "אחר".to_string(),
        other => other.to_string(),
    }
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
            .text_color(ui::muted())
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
    let (text, tone, ic) = match state {
        SaveState::Pending => ("שינויים לא נשמרו", Tone::Warning, Ic::CircleAlert),
        SaveState::Saving => ("שומר…", Tone::Neutral, Ic::Refresh),
        SaveState::Saved => ("נשמר", Tone::Success, Ic::CircleCheck),
        SaveState::Error => ("השמירה נכשלה", Tone::Danger, Ic::CircleAlert),
        SaveState::Idle => ("", Tone::Neutral, Ic::Check),
    };
    let (fg, _) = tone.colors();
    ui::rtl_row()
        .gap_1p5()
        .text_xs()
        .text_color(fg)
        .when(!text.is_empty(), |this| {
            this.child(ui::icon(ic, 13.0, fg)).child(rtl(text))
        })
}

/// How the analysis names a speaker nobody has named: "דובר 1".
fn default_speaker_label(key: &str) -> String {
    if !key.is_empty() && key.chars().all(|c| c.is_ascii_digit()) {
        format!("דובר {key}")
    } else {
        key.to_string()
    }
}

/// Replace a speaker label inside free text, only where it is that label.
///
/// "דובר 1" is left alone inside "דובר 12" and inside a word that merely ends
/// with the same letters, but found behind a Hebrew one-letter prefix
/// ("לדובר 1" becomes "לדנה"). The backend applies the same rule to the stored
/// analysis.
fn replace_label(text: &str, old: &str, new: &str) -> String {
    const PREFIXES: &str = "ובכלמשה";
    if old.is_empty() || old == new || !text.contains(old) {
        return text.to_string();
    }
    let mut out = String::with_capacity(text.len());
    let mut last = 0;
    for (pos, _) in text.match_indices(old) {
        if pos < last {
            continue;
        }
        let end = pos + old.len();
        if text[end..].chars().next().is_some_and(|c| c.is_ascii_digit()) {
            continue;
        }
        let mut before = text[..pos].chars().rev().peekable();
        let mut skipped = 0;
        while skipped < 2 && before.peek().is_some_and(|c| PREFIXES.contains(*c)) {
            before.next();
            skipped += 1;
        }
        if before.peek().is_some_and(|c| c.is_alphanumeric()) {
            continue;
        }
        out.push_str(&text[last..pos]);
        out.push_str(new);
        last = end;
    }
    out.push_str(&text[last..]);
    out
}

#[cfg(test)]
mod rename_tests {
    use super::*;

    #[test]
    fn only_the_label_itself_is_replaced() {
        let text = "דובר 1 שאל את דובר 12 ואז לדובר 1 ענו, לא בגדובר 1";
        assert_eq!(
            replace_label(text, "דובר 1", "דנה"),
            "דנה שאל את דובר 12 ואז לדנה ענו, לא בגדובר 1"
        );
    }

    #[test]
    fn unnamed_speakers_are_numbered() {
        assert_eq!(default_speaker_label("2"), "דובר 2");
        assert_eq!(default_speaker_label("SPEAKER_00"), "SPEAKER_00");
    }
}
