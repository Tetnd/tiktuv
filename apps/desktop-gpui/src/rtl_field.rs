//! A real right-to-left text field.
//!
//! GPUI's own inputs lay text out strictly left to right. A Hebrew field built
//! on them has to hold its text in *visual* order, which is why such a field
//! cannot wrap (rows would be read bottom-up), sits at the left edge, scrolls
//! sideways, puts punctuation on the wrong side and clips long text.
//!
//! This field does what a Hebrew field should:
//!
//! * The value is the **logical** text, exactly as typed, copied and saved —
//!   no reordering is ever stored.
//! * Text **wraps** at the field's width and every row is reordered on its own
//!   (the same approach as [`crate::rtl_text::rtl_paragraph`]), flush with the
//!   right edge. Numbers and Latin words stay left-to-right inside it.
//! * The **caret, selection and mouse** follow the bidi layout: arrow keys move
//!   visually, a click lands where it was made, a selection paints one block per
//!   contiguous run. Backspace and Delete are logical, so they just work.
//! * It speaks the platform's text-input protocol, so IME and composition work.
//!
//! Single-line fields (a name, a task) wrap too, growing taller rather than
//! clipping; multi-line fields (the summary) take Enter as a new line.

use std::ops::Range;
use std::rc::Rc;
use std::time::{Duration, Instant};

use gpui_kit::{
    App, Bounds, ClipboardItem, Context, CursorStyle, Element, ElementId, ElementInputHandler,
    Entity, EntityInputHandler, EventEmitter, FocusHandle, Focusable, GlobalElementId,
    InspectorElementId, InteractiveElement as _, IntoElement, KeyBinding, LayoutId, MouseButton,
    MouseDownEvent, MouseMoveEvent, MouseUpEvent, ParentElement as _, Pixels, Point, Render,
    ShapedLine, SharedString, Size, Style, Styled as _, Subscription, TextAlign, TextRun,
    UTF16Selection, Window, actions, div, fill, point, px, relative, size,
};
use unicode_bidi::{BidiInfo, Level};

use crate::ui;

actions!(
    rtl_field,
    [
        Backspace,
        Delete,
        Left,
        Right,
        Up,
        Down,
        SelectLeft,
        SelectRight,
        SelectUp,
        SelectDown,
        Home,
        End,
        SelectHome,
        SelectEnd,
        DocStart,
        DocEnd,
        SelectDocStart,
        SelectDocEnd,
        WordLeft,
        WordRight,
        SelectWordLeft,
        SelectWordRight,
        DeleteWordBack,
        DeleteWordForward,
        SelectAll,
        Paste,
        Cut,
        Copy,
        Undo,
        Redo,
        Enter,
    ]
);

/// Key bindings for every [`RtlField`]. Call once at start-up.
pub fn init(cx: &mut App) {
    let m = if cfg!(target_os = "macos") { "cmd" } else { "ctrl" };
    let ctx = Some("RtlField");
    // Key bindings keep their keystroke strings for the life of the program.
    let k = |keys: String| -> &'static str { Box::leak(keys.into_boxed_str()) };
    cx.bind_keys([
        KeyBinding::new("backspace", Backspace, ctx),
        KeyBinding::new("delete", Delete, ctx),
        KeyBinding::new("left", Left, ctx),
        KeyBinding::new("right", Right, ctx),
        KeyBinding::new("up", Up, ctx),
        KeyBinding::new("down", Down, ctx),
        KeyBinding::new("shift-left", SelectLeft, ctx),
        KeyBinding::new("shift-right", SelectRight, ctx),
        KeyBinding::new("shift-up", SelectUp, ctx),
        KeyBinding::new("shift-down", SelectDown, ctx),
        KeyBinding::new("home", Home, ctx),
        KeyBinding::new("end", End, ctx),
        KeyBinding::new("shift-home", SelectHome, ctx),
        KeyBinding::new("shift-end", SelectEnd, ctx),
        KeyBinding::new(k(format!("{m}-home")), DocStart, ctx),
        KeyBinding::new(k(format!("{m}-end")), DocEnd, ctx),
        KeyBinding::new(k(format!("{m}-shift-home")), SelectDocStart, ctx),
        KeyBinding::new(k(format!("{m}-shift-end")), SelectDocEnd, ctx),
        KeyBinding::new(k(format!("{m}-left")), WordLeft, ctx),
        KeyBinding::new(k(format!("{m}-right")), WordRight, ctx),
        KeyBinding::new(k(format!("{m}-shift-left")), SelectWordLeft, ctx),
        KeyBinding::new(k(format!("{m}-shift-right")), SelectWordRight, ctx),
        KeyBinding::new(k(format!("{m}-backspace")), DeleteWordBack, ctx),
        KeyBinding::new(k(format!("{m}-delete")), DeleteWordForward, ctx),
        KeyBinding::new(k(format!("{m}-a")), SelectAll, ctx),
        KeyBinding::new(k(format!("{m}-v")), Paste, ctx),
        KeyBinding::new(k(format!("{m}-c")), Copy, ctx),
        KeyBinding::new(k(format!("{m}-x")), Cut, ctx),
        KeyBinding::new(k(format!("{m}-z")), Undo, ctx),
        KeyBinding::new(k(format!("{m}-y")), Redo, ctx),
        KeyBinding::new(k(format!("{m}-shift-z")), Redo, ctx),
        KeyBinding::new("enter", Enter, ctx),
    ]);
}

/// What a field tells its owner.
#[derive(Clone, Debug)]
pub enum RtlFieldEvent {
    /// The user changed the text (never emitted by [`RtlField::set_value`]).
    Change,
    /// Enter in a single-line field.
    PressEnter,
    Focus,
    Blur,
}

#[derive(Clone)]
struct Snapshot {
    text: String,
    selected: Range<usize>,
}

pub struct RtlField {
    focus_handle: FocusHandle,
    text: String,
    placeholder: String,
    multiline: bool,
    ltr: bool,
    min_rows: usize,
    selected: Range<usize>,
    reversed: bool,
    marked: Option<Range<usize>>,
    layout: Option<Rc<Layout>>,
    bounds: Option<Bounds<Pixels>>,
    selecting: bool,
    preferred_x: Option<Pixels>,
    undo: Vec<Snapshot>,
    redo: Vec<Snapshot>,
    last_typed: Option<Instant>,
    _subs: Vec<Subscription>,
}

impl EventEmitter<RtlFieldEvent> for RtlField {}

impl Focusable for RtlField {
    fn focus_handle(&self, _: &App) -> FocusHandle {
        self.focus_handle.clone()
    }
}

// ---------------------------------------------------------------------------
// Layout: rows, bidi order and geometry
// ---------------------------------------------------------------------------

/// One wrapped row of text.
struct Row {
    /// Logical byte range of the row in the field's text.
    start: usize,
    end: usize,
    line: ShapedLine,
    /// Byte offset of every logical char within the row (plus the row length).
    char_bytes: Vec<usize>,
    /// Byte offset of every visual char within the shaped line (plus its length).
    vis_bytes: Vec<usize>,
    /// Visual position of each logical char.
    log_to_vis: Vec<usize>,
    /// Is each logical char right-to-left?
    rtl: Vec<bool>,
    width: Pixels,
    /// The row's base direction is left-to-right (URLs, keys, codes).
    ltr_base: bool,
}

impl Row {
    fn chars(&self) -> usize {
        self.rtl.len()
    }

    fn left_x(&self, visual: usize) -> Pixels {
        self.line.x_for_index(self.vis_bytes[visual])
    }

    fn right_x(&self, visual: usize) -> Pixels {
        self.line.x_for_index(self.vis_bytes[visual + 1])
    }

    /// `[left, right]` of a logical char, in row-local coordinates.
    fn char_span(&self, c: usize) -> (Pixels, Pixels) {
        let v = self.log_to_vis[c];
        (self.left_x(v), self.right_x(v))
    }

    /// Row-local x of the caret at char boundary `i` (0..=chars).
    fn boundary_x(&self, i: usize) -> Pixels {
        let n = self.chars();
        if n == 0 {
            return if self.ltr_base { px(0.) } else { self.width };
        }
        if i == 0 {
            let (l, r) = self.char_span(0);
            return if self.rtl[0] { r } else { l };
        }
        let (l, r) = self.char_span(i - 1);
        if self.rtl[i - 1] { l } else { r }
    }

    /// Char index (row-relative) of a byte offset in the logical text.
    fn char_at(&self, offset: usize) -> usize {
        let rel = offset.saturating_sub(self.start).min(self.char_bytes[self.chars()]);
        match self.char_bytes.binary_search(&rel) {
            Ok(i) => i,
            Err(i) => i.saturating_sub(1),
        }
    }

    fn offset_of(&self, boundary: usize) -> usize {
        self.start + self.char_bytes[boundary.min(self.chars())]
    }
}

struct Layout {
    rows: Vec<Row>,
    line_height: Pixels,
    wrap_width: Pixels,
    ltr: bool,
}

impl Layout {
    /// Where a row's line starts, from the field's left edge: flush right for
    /// a right-to-left field, flush left for a left-to-right one.
    fn x0(&self, row: &Row) -> Pixels {
        if self.ltr { px(0.) } else { self.wrap_width - row.width }
    }

    /// The row holding `offset`. At a soft wrap the later row wins, so the
    /// caret shows where the next character will appear.
    fn row_of(&self, offset: usize) -> usize {
        let mut found = 0;
        for (i, row) in self.rows.iter().enumerate() {
            if row.start <= offset {
                found = i;
            } else {
                break;
            }
        }
        found
    }
}

fn is_mark(c: char) -> bool {
    ('\u{0591}'..='\u{05C7}').contains(&c)
        && !matches!(c, '\u{05BE}' | '\u{05C0}' | '\u{05C3}' | '\u{05C6}')
        || ('\u{0300}'..='\u{036F}').contains(&c)
        || ('\u{200C}'..='\u{200D}').contains(&c)
}

fn is_bidi_control(c: char) -> bool {
    matches!(
        c,
        '\u{200e}' | '\u{200f}' | '\u{202a}'..='\u{202e}' | '\u{2066}'..='\u{2069}'
    )
}

/// Left-to-right order of the chars on a line (rule L2).
fn visual_order(levels: &[Level]) -> Vec<usize> {
    let n = levels.len();
    let mut order: Vec<usize> = (0..n).collect();
    let max = levels.iter().map(|l| l.number()).max().unwrap_or(0);
    let min_odd = levels.iter().map(|l| l.number()).filter(|v| v % 2 == 1).min();
    if let Some(min_odd) = min_odd {
        for level in (min_odd..=max).rev() {
            let mut i = 0;
            while i < n {
                if levels[order[i]].number() >= level {
                    let start = i;
                    while i < n && levels[order[i]].number() >= level {
                        i += 1;
                    }
                    order[start..i].reverse();
                } else {
                    i += 1;
                }
            }
        }
    }
    order
}

fn build_row(
    text: &str,
    start: usize,
    window: &mut Window,
    style: &gpui_kit::TextStyle,
    font_size: Pixels,
    ltr: bool,
) -> Row {
    let base = if ltr { Level::ltr() } else { Level::rtl() };
    let chars: Vec<char> = text.chars().collect();
    let n = chars.len();
    let mut char_bytes = Vec::with_capacity(n + 1);
    let mut b = 0;
    for ch in &chars {
        char_bytes.push(b);
        b += ch.len_utf8();
    }
    char_bytes.push(b);

    let levels: Vec<Level> = if n == 0 {
        Vec::new()
    } else {
        let info = BidiInfo::new(text, Some(base));
        match info.paragraphs.first() {
            Some(para) => info.reordered_levels_per_char(para, para.range.clone()),
            None => vec![base; n],
        }
    };
    let order = visual_order(&levels);

    let mut visual = String::with_capacity(text.len());
    let mut vis_bytes = Vec::with_capacity(n + 1);
    for &li in &order {
        vis_bytes.push(visual.len());
        let mut ch = chars[li];
        if levels[li].is_rtl() {
            ch = unicode_bidi_mirroring::get_mirrored(ch).unwrap_or(ch);
        }
        visual.push(ch);
    }
    vis_bytes.push(visual.len());

    let mut log_to_vis = vec![0; n];
    for (v, &l) in order.iter().enumerate() {
        log_to_vis[l] = v;
    }

    let shared: SharedString = visual.into();
    let run = style.to_run(shared.len());
    let line = window.text_system().shape_line(shared, font_size, &[run], None);
    let width = line.width();
    Row {
        start,
        end: start + text.len(),
        line,
        char_bytes,
        vis_bytes,
        log_to_vis,
        rtl: levels.iter().map(|l| l.is_rtl()).collect(),
        width,
        ltr_base: ltr,
    }
}

/// Wrap `text` at `wrap_width` and lay out every row.
fn compute_layout(
    text: &str,
    wrap_width: Pixels,
    ltr: bool,
    window: &mut Window,
    style: &gpui_kit::TextStyle,
) -> Layout {
    let font_size = style.font_size.to_pixels(window.rem_size());
    let line_height = window.pixel_snap(
        style
            .line_height
            .to_pixels(font_size.into(), window.rem_size()),
    );

    let mut rows = Vec::new();
    let mut base = 0usize;
    for paragraph in text.split('\n') {
        if paragraph.is_empty() {
            rows.push(build_row("", base, window, style, font_size, ltr));
        } else {
            for (from, to) in wrap_ranges(paragraph, wrap_width, font_size, window, style) {
                rows.push(build_row(
                    &paragraph[from..to],
                    base + from,
                    window,
                    style,
                    font_size,
                    ltr,
                ));
            }
        }
        base += paragraph.len() + 1;
    }
    if rows.is_empty() {
        rows.push(build_row("", 0, window, style, font_size, ltr));
    }
    Layout {
        rows,
        line_height,
        wrap_width,
        ltr,
    }
}

/// Where the text system would wrap `paragraph`, as byte ranges.
fn wrap_ranges(
    paragraph: &str,
    wrap_width: Pixels,
    font_size: Pixels,
    window: &mut Window,
    style: &gpui_kit::TextStyle,
) -> Vec<(usize, usize)> {
    let shared: SharedString = paragraph.to_string().into();
    let runs = [style.to_run(shared.len())];
    let Ok(lines) = window
        .text_system()
        .shape_text(shared, font_size, &runs, Some(wrap_width), None)
    else {
        return vec![(0, paragraph.len())];
    };
    let mut out = Vec::new();
    let mut start = 0usize;
    for line in &lines {
        for boundary in line.wrap_boundaries.iter() {
            let Some(run) = line.unwrapped_layout.runs.get(boundary.run_ix) else {
                continue;
            };
            let Some(glyph) = run.glyphs.get(boundary.glyph_ix) else {
                continue;
            };
            let end = glyph.index;
            if end > start && paragraph.is_char_boundary(end) {
                out.push((start, end));
                start = end;
            }
        }
    }
    out.push((start, paragraph.len()));
    out
}

// ---------------------------------------------------------------------------
// The field
// ---------------------------------------------------------------------------
impl RtlField {
    pub fn new(window: &mut Window, cx: &mut Context<Self>) -> Self {
        let focus_handle = cx.focus_handle();
        let focus_in = cx.on_focus_in(&focus_handle, window, |_, _, cx| {
            cx.emit(RtlFieldEvent::Focus);
            cx.notify();
        });
        let focus_out = cx.on_focus_out(&focus_handle, window, |_, _, _, cx| {
            cx.emit(RtlFieldEvent::Blur);
            cx.notify();
        });
        Self {
            focus_handle,
            text: String::new(),
            placeholder: String::new(),
            multiline: false,
            ltr: false,
            min_rows: 1,
            selected: 0..0,
            reversed: false,
            marked: None,
            layout: None,
            bounds: None,
            selecting: false,
            preferred_x: None,
            undo: Vec::new(),
            redo: Vec::new(),
            last_typed: None,
            _subs: vec![focus_in, focus_out],
        }
    }

    pub fn placeholder(mut self, placeholder: impl Into<String>) -> Self {
        self.placeholder = placeholder.into();
        self
    }

    /// Enter inserts a new line instead of ending the edit.
    pub fn multiline(mut self, multiline: bool) -> Self {
        self.multiline = multiline;
        self
    }

    /// A left-to-right field, flush left: for URLs, keys and codes.
    pub fn ltr(mut self, ltr: bool) -> Self {
        self.ltr = ltr;
        self
    }

    /// The field is at least this many rows tall.
    pub fn min_rows(mut self, rows: usize) -> Self {
        self.min_rows = rows.max(1);
        self
    }

    /// The logical text.
    pub fn value(&self) -> &str {
        &self.text
    }

    /// Replace the text without telling the owner (no `Change` event).
    pub fn set_value(&mut self, value: &str, window: &mut Window, cx: &mut Context<Self>) {
        self.text = self.clean(value);
        let end = self.text.len();
        self.selected = end..end;
        self.reversed = false;
        self.marked = None;
        self.undo.clear();
        self.redo.clear();
        self.preferred_x = None;
        window.notify_text_input_changed(&self.focus_handle);
        cx.notify();
    }

    pub fn focus(&self, window: &mut Window, cx: &mut App) {
        window.focus(&self.focus_handle, cx);
    }

    pub fn is_focused(&self, window: &Window) -> bool {
        self.focus_handle.is_focused(window)
    }

    /// Strip direction controls (the field decides direction itself) and
    /// normalise line breaks.
    fn clean(&self, text: &str) -> String {
        let mut out: String = text
            .replace("\r\n", "\n")
            .replace('\r', "\n")
            .chars()
            .filter(|c| !is_bidi_control(*c))
            .collect();
        if !self.multiline {
            out = out.replace('\n', " ");
        }
        out
    }

    // -- selection helpers ------------------------------------------------
    fn cursor(&self) -> usize {
        if self.reversed {
            self.selected.start
        } else {
            self.selected.end
        }
    }

    fn move_to(&mut self, offset: usize, keep_x: bool, window: &Window, cx: &mut Context<Self>) {
        let offset = offset.min(self.text.len());
        self.selected = offset..offset;
        self.reversed = false;
        if !keep_x {
            self.preferred_x = None;
        }
        window.notify_text_selection_changed(&self.focus_handle);
        cx.notify();
    }

    fn select_to(&mut self, offset: usize, keep_x: bool, window: &Window, cx: &mut Context<Self>) {
        let offset = offset.min(self.text.len());
        if self.reversed {
            self.selected.start = offset;
        } else {
            self.selected.end = offset;
        }
        if self.selected.end < self.selected.start {
            self.reversed = !self.reversed;
            self.selected = self.selected.end..self.selected.start;
        }
        if !keep_x {
            self.preferred_x = None;
        }
        window.notify_text_selection_changed(&self.focus_handle);
        cx.notify();
    }

    // -- logical boundaries (clusters: a letter keeps its niqqud) ----------
    fn prev_boundary(&self, offset: usize) -> usize {
        let mut i = offset.min(self.text.len());
        if i == 0 {
            return 0;
        }
        loop {
            i -= 1;
            while i > 0 && !self.text.is_char_boundary(i) {
                i -= 1;
            }
            let c = self.text[i..].chars().next().unwrap_or(' ');
            if !is_mark(c) || i == 0 {
                return i;
            }
        }
    }

    fn next_boundary(&self, offset: usize) -> usize {
        let len = self.text.len();
        let mut i = offset.min(len);
        if i >= len {
            return len;
        }
        i += self.text[i..].chars().next().map(char::len_utf8).unwrap_or(1);
        while i < len {
            let c = self.text[i..].chars().next().unwrap_or(' ');
            if !is_mark(c) {
                break;
            }
            i += c.len_utf8();
        }
        i
    }

    /// End of the word after `offset` (logical order).
    fn next_word(&self, offset: usize) -> usize {
        let mut i = offset;
        let len = self.text.len();
        while i < len && !self.is_word_at(i) {
            i = self.next_boundary(i);
        }
        while i < len && self.is_word_at(i) {
            i = self.next_boundary(i);
        }
        i
    }

    /// Start of the word before `offset` (logical order).
    fn prev_word(&self, offset: usize) -> usize {
        let mut i = offset;
        while i > 0 && !self.is_word_at(self.prev_boundary(i)) {
            i = self.prev_boundary(i);
        }
        while i > 0 && self.is_word_at(self.prev_boundary(i)) {
            i = self.prev_boundary(i);
        }
        i
    }

    fn is_word_at(&self, i: usize) -> bool {
        self.text[i..]
            .chars()
            .next()
            .map(|c| c.is_alphanumeric() || is_mark(c))
            .unwrap_or(false)
    }

    // -- visual movement ---------------------------------------------------
    /// Caret stops of a row as `(row-local x, byte offset)`, left to right.
    fn stops(&self, row: &Row) -> Vec<(Pixels, usize)> {
        let mut stops = Vec::new();
        for i in 0..=row.chars() {
            let offset = row.offset_of(i);
            // Not between a letter and its niqqud.
            if i < row.chars() {
                let c = self.text[offset..].chars().next().unwrap_or(' ');
                if is_mark(c) && i > 0 {
                    continue;
                }
            }
            stops.push((row.boundary_x(i), offset));
        }
        stops.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal));
        stops
    }

    /// Where the caret is, as `(row index, field-relative x)`.
    fn caret_position(&self, offset: usize) -> Option<(usize, Pixels)> {
        let layout = self.layout.as_ref()?;
        let r = layout.row_of(offset);
        let row = &layout.rows[r];
        let local = row.boundary_x(row.char_at(offset));
        Some((r, layout.x0(row) + local))
    }

    /// The offset one visual step left (`dir < 0`) or right (`dir > 0`).
    fn step_visual(&self, from: usize, dir: i32) -> usize {
        let Some(layout) = self.layout.as_ref() else {
            let forward = (dir < 0) != self.ltr;
            return if forward { self.next_boundary(from) } else { self.prev_boundary(from) };
        };
        let row = &layout.rows[layout.row_of(from)];
        let stops = self.stops(row);
        let here = row.boundary_x(row.char_at(from));
        let eps = px(0.5);
        if dir < 0 {
            if let Some(stop) = stops.iter().rev().find(|s| s.0 < here - eps) {
                return stop.1;
            }
            // Past the left end of the row: on to the character beyond it.
            if layout.ltr { self.prev_boundary(from) } else { self.next_boundary(from) }
        } else {
            if let Some(stop) = stops.iter().find(|s| s.0 > here + eps) {
                return stop.1;
            }
            if layout.ltr { self.next_boundary(from) } else { self.prev_boundary(from) }
        }
    }

    /// The offset one row up/down, at the remembered x.
    fn step_row(&mut self, from: usize, delta: i32) -> usize {
        let Some(layout) = self.layout.clone() else {
            return from;
        };
        let Some((r, x)) = self.caret_position(from) else {
            return from;
        };
        let x = *self.preferred_x.get_or_insert(x);
        let target = r as i32 + delta;
        if target < 0 {
            return 0;
        }
        if target as usize >= layout.rows.len() {
            return self.text.len();
        }
        self.offset_at_x(&layout, target as usize, x)
    }

    fn offset_at_x(&self, layout: &Layout, row_index: usize, x: Pixels) -> usize {
        let row = &layout.rows[row_index];
        let local = x - layout.x0(row);
        let stops = self.stops(row);
        stops
            .iter()
            .min_by(|a, b| {
                (a.0 - local)
                    .abs()
                    .partial_cmp(&(b.0 - local).abs())
                    .unwrap_or(std::cmp::Ordering::Equal)
            })
            .map(|s| s.1)
            .unwrap_or(row.start)
    }

    fn offset_for_point(&self, position: Point<Pixels>) -> usize {
        let (Some(bounds), Some(layout)) = (self.bounds.as_ref(), self.layout.as_ref()) else {
            return 0;
        };
        if self.text.is_empty() {
            return 0;
        }
        let y = (position.y - bounds.top()).max(px(0.));
        let r = ((y / layout.line_height).floor() as usize).min(layout.rows.len() - 1);
        self.offset_at_x(layout, r, position.x - bounds.left())
    }

    // -- editing -----------------------------------------------------------
    fn snapshot(&self) -> Snapshot {
        Snapshot {
            text: self.text.clone(),
            selected: self.selected.clone(),
        }
    }

    /// Replace `range` with `new_text`, as one undoable step.
    fn edit(
        &mut self,
        range: Range<usize>,
        new_text: &str,
        typing: bool,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        let new_text = self.clean(new_text);
        let coalesce = typing
            && self
                .last_typed
                .map(|t| t.elapsed() < Duration::from_millis(900))
                .unwrap_or(false)
            && !self.undo.is_empty();
        if !coalesce {
            self.undo.push(self.snapshot());
            if self.undo.len() > 200 {
                self.undo.remove(0);
            }
        }
        self.last_typed = typing.then(Instant::now);
        self.redo.clear();

        let range = range.start.min(self.text.len())..range.end.min(self.text.len());
        self.text.replace_range(range.clone(), &new_text);
        let caret = range.start + new_text.len();
        self.selected = caret..caret;
        self.reversed = false;
        self.marked = None;
        self.preferred_x = None;
        window.notify_text_input_changed(&self.focus_handle);
        cx.emit(RtlFieldEvent::Change);
        cx.notify();
    }

    fn restore(&mut self, snapshot: Snapshot, window: &mut Window, cx: &mut Context<Self>) {
        self.text = snapshot.text;
        self.selected = snapshot.selected;
        self.reversed = false;
        self.marked = None;
        self.last_typed = None;
        window.notify_text_input_changed(&self.focus_handle);
        cx.emit(RtlFieldEvent::Change);
        cx.notify();
    }

    // -- action handlers -----------------------------------------------------
    fn backspace(&mut self, _: &Backspace, window: &mut Window, cx: &mut Context<Self>) {
        if self.selected.is_empty() {
            let prev = self.prev_boundary(self.cursor());
            if prev == self.cursor() {
                return;
            }
            self.selected = prev..self.cursor();
        }
        let range = self.selected.clone();
        self.edit(range, "", false, window, cx);
    }

    fn delete(&mut self, _: &Delete, window: &mut Window, cx: &mut Context<Self>) {
        if self.selected.is_empty() {
            let next = self.next_boundary(self.cursor());
            if next == self.cursor() {
                return;
            }
            self.selected = self.cursor()..next;
        }
        let range = self.selected.clone();
        self.edit(range, "", false, window, cx);
    }

    fn delete_word_back(&mut self, _: &DeleteWordBack, window: &mut Window, cx: &mut Context<Self>) {
        if self.selected.is_empty() {
            let start = self.prev_word(self.cursor());
            self.selected = start..self.cursor();
        }
        let range = self.selected.clone();
        if !range.is_empty() {
            self.edit(range, "", false, window, cx);
        }
    }

    fn delete_word_forward(
        &mut self,
        _: &DeleteWordForward,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        if self.selected.is_empty() {
            let end = self.next_word(self.cursor());
            self.selected = self.cursor()..end;
        }
        let range = self.selected.clone();
        if !range.is_empty() {
            self.edit(range, "", false, window, cx);
        }
    }

    /// Collapse a selection to its visually left (`-1`) or right (`1`) end.
    fn collapse(&self, dir: i32) -> usize {
        let (a, b) = (self.selected.start, self.selected.end);
        match (self.caret_position(a), self.caret_position(b)) {
            (Some((ra, xa)), Some((rb, xb))) => {
                let a_is_left = (ra, xa) < (rb, xb);
                let left = if a_is_left { a } else { b };
                let right = if a_is_left { b } else { a };
                if dir < 0 { left } else { right }
            }
            _ => if dir < 0 { b } else { a },
        }
    }

    fn left(&mut self, _: &Left, window: &mut Window, cx: &mut Context<Self>) {
        if self.selected.is_empty() {
            let to = self.step_visual(self.cursor(), -1);
            self.move_to(to, false, window, cx);
        } else {
            let to = self.collapse(-1);
            self.move_to(to, false, window, cx);
        }
    }

    fn right(&mut self, _: &Right, window: &mut Window, cx: &mut Context<Self>) {
        if self.selected.is_empty() {
            let to = self.step_visual(self.cursor(), 1);
            self.move_to(to, false, window, cx);
        } else {
            let to = self.collapse(1);
            self.move_to(to, false, window, cx);
        }
    }

    fn select_left(&mut self, _: &SelectLeft, window: &mut Window, cx: &mut Context<Self>) {
        let to = self.step_visual(self.cursor(), -1);
        self.select_to(to, false, window, cx);
    }

    fn select_right(&mut self, _: &SelectRight, window: &mut Window, cx: &mut Context<Self>) {
        let to = self.step_visual(self.cursor(), 1);
        self.select_to(to, false, window, cx);
    }

    fn up(&mut self, _: &Up, window: &mut Window, cx: &mut Context<Self>) {
        let to = self.step_row(self.cursor(), -1);
        self.move_to(to, true, window, cx);
    }

    fn down(&mut self, _: &Down, window: &mut Window, cx: &mut Context<Self>) {
        let to = self.step_row(self.cursor(), 1);
        self.move_to(to, true, window, cx);
    }

    fn select_up(&mut self, _: &SelectUp, window: &mut Window, cx: &mut Context<Self>) {
        let to = self.step_row(self.cursor(), -1);
        self.select_to(to, true, window, cx);
    }

    fn select_down(&mut self, _: &SelectDown, window: &mut Window, cx: &mut Context<Self>) {
        let to = self.step_row(self.cursor(), 1);
        self.select_to(to, true, window, cx);
    }

    fn row_start(&self) -> usize {
        match self.layout.as_ref() {
            Some(l) => l.rows[l.row_of(self.cursor())].start,
            None => 0,
        }
    }

    fn row_end(&self) -> usize {
        let Some(layout) = self.layout.as_ref() else {
            return self.text.len();
        };
        let r = layout.row_of(self.cursor());
        let row = &layout.rows[r];
        let soft = layout.rows.get(r + 1).is_some_and(|next| next.start == row.end);
        if soft {
            // Stay on this row: before the space the wrap broke on.
            self.prev_boundary(row.end)
        } else {
            row.end
        }
    }

    fn home(&mut self, _: &Home, window: &mut Window, cx: &mut Context<Self>) {
        self.move_to(self.row_start(), false, window, cx);
    }

    fn end(&mut self, _: &End, window: &mut Window, cx: &mut Context<Self>) {
        self.move_to(self.row_end(), false, window, cx);
    }

    fn select_home(&mut self, _: &SelectHome, window: &mut Window, cx: &mut Context<Self>) {
        self.select_to(self.row_start(), false, window, cx);
    }

    fn select_end(&mut self, _: &SelectEnd, window: &mut Window, cx: &mut Context<Self>) {
        self.select_to(self.row_end(), false, window, cx);
    }

    fn doc_start(&mut self, _: &DocStart, window: &mut Window, cx: &mut Context<Self>) {
        self.move_to(0, false, window, cx);
    }

    fn doc_end(&mut self, _: &DocEnd, window: &mut Window, cx: &mut Context<Self>) {
        self.move_to(self.text.len(), false, window, cx);
    }

    fn select_doc_start(&mut self, _: &SelectDocStart, window: &mut Window, cx: &mut Context<Self>) {
        self.select_to(0, false, window, cx);
    }

    fn select_doc_end(&mut self, _: &SelectDocEnd, window: &mut Window, cx: &mut Context<Self>) {
        self.select_to(self.text.len(), false, window, cx);
    }

    // In a right-to-left field "left" is forward, so the word keys follow.
    fn word_step(&self, left: bool) -> usize {
        if left != self.ltr {
            self.next_word(self.cursor())
        } else {
            self.prev_word(self.cursor())
        }
    }

    fn word_left(&mut self, _: &WordLeft, window: &mut Window, cx: &mut Context<Self>) {
        self.move_to(self.word_step(true), false, window, cx);
    }

    fn word_right(&mut self, _: &WordRight, window: &mut Window, cx: &mut Context<Self>) {
        self.move_to(self.word_step(false), false, window, cx);
    }

    fn select_word_left(&mut self, _: &SelectWordLeft, window: &mut Window, cx: &mut Context<Self>) {
        self.select_to(self.word_step(true), false, window, cx);
    }

    fn select_word_right(
        &mut self,
        _: &SelectWordRight,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        self.select_to(self.word_step(false), false, window, cx);
    }

    fn select_all(&mut self, _: &SelectAll, window: &mut Window, cx: &mut Context<Self>) {
        self.selected = 0..self.text.len();
        self.reversed = false;
        window.notify_text_selection_changed(&self.focus_handle);
        cx.notify();
    }

    fn paste(&mut self, _: &Paste, window: &mut Window, cx: &mut Context<Self>) {
        if let Some(text) = cx.read_from_clipboard().and_then(|item| item.text()) {
            let range = self.selected.clone();
            self.edit(range, &text, false, window, cx);
        }
    }

    fn copy(&mut self, _: &Copy, _: &mut Window, cx: &mut Context<Self>) {
        if !self.selected.is_empty() {
            cx.write_to_clipboard(ClipboardItem::new_string(
                self.text[self.selected.clone()].to_string(),
            ));
        }
    }

    fn cut(&mut self, _: &Cut, window: &mut Window, cx: &mut Context<Self>) {
        if !self.selected.is_empty() {
            cx.write_to_clipboard(ClipboardItem::new_string(
                self.text[self.selected.clone()].to_string(),
            ));
            let range = self.selected.clone();
            self.edit(range, "", false, window, cx);
        }
    }

    fn undo(&mut self, _: &Undo, window: &mut Window, cx: &mut Context<Self>) {
        if let Some(previous) = self.undo.pop() {
            self.redo.push(self.snapshot());
            self.restore(previous, window, cx);
        }
    }

    fn redo(&mut self, _: &Redo, window: &mut Window, cx: &mut Context<Self>) {
        if let Some(next) = self.redo.pop() {
            self.undo.push(self.snapshot());
            self.restore(next, window, cx);
        }
    }

    fn enter(&mut self, _: &Enter, window: &mut Window, cx: &mut Context<Self>) {
        if self.multiline {
            let range = self.selected.clone();
            self.edit(range, "\n", false, window, cx);
        } else {
            cx.emit(RtlFieldEvent::PressEnter);
        }
    }

    // -- mouse ---------------------------------------------------------------
    fn on_mouse_down(&mut self, event: &MouseDownEvent, window: &mut Window, cx: &mut Context<Self>) {
        window.focus(&self.focus_handle, cx);
        self.selecting = true;
        let offset = self.offset_for_point(event.position);
        if event.modifiers.shift {
            self.select_to(offset, false, window, cx);
        } else if event.click_count == 2 {
            let start = self.prev_word(self.next_boundary(offset).min(self.text.len()));
            let start = if self.is_word_at(offset.min(self.text.len().saturating_sub(1))) {
                start
            } else {
                offset
            };
            let end = self.next_word(start);
            self.selected = start..end.max(start);
            self.reversed = false;
            window.notify_text_selection_changed(&self.focus_handle);
            cx.notify();
        } else if event.click_count >= 3 {
            let start = self.text[..offset].rfind('\n').map(|i| i + 1).unwrap_or(0);
            let end = self.text[offset..]
                .find('\n')
                .map(|i| offset + i)
                .unwrap_or(self.text.len());
            self.selected = start..end;
            self.reversed = false;
            window.notify_text_selection_changed(&self.focus_handle);
            cx.notify();
        } else {
            self.move_to(offset, false, window, cx);
        }
    }

    fn on_mouse_up(&mut self, _: &MouseUpEvent, _: &mut Window, _: &mut Context<Self>) {
        self.selecting = false;
    }

    fn on_mouse_move(&mut self, event: &MouseMoveEvent, window: &mut Window, cx: &mut Context<Self>) {
        if self.selecting {
            let offset = self.offset_for_point(event.position);
            self.select_to(offset, false, window, cx);
        }
    }

    // -- UTF-16 (platform text input) ------------------------------------------
    fn offset_from_utf16(&self, offset: usize) -> usize {
        let mut utf8 = 0;
        let mut utf16 = 0;
        for ch in self.text.chars() {
            if utf16 >= offset {
                break;
            }
            utf16 += ch.len_utf16();
            utf8 += ch.len_utf8();
        }
        utf8
    }

    fn offset_to_utf16(&self, offset: usize) -> usize {
        let mut utf16 = 0;
        let mut utf8 = 0;
        for ch in self.text.chars() {
            if utf8 >= offset {
                break;
            }
            utf8 += ch.len_utf8();
            utf16 += ch.len_utf16();
        }
        utf16
    }

    fn range_to_utf16(&self, range: &Range<usize>) -> Range<usize> {
        self.offset_to_utf16(range.start)..self.offset_to_utf16(range.end)
    }

    fn range_from_utf16(&self, range: &Range<usize>) -> Range<usize> {
        self.offset_from_utf16(range.start)..self.offset_from_utf16(range.end)
    }
}

impl EntityInputHandler for RtlField {
    fn text_for_range(
        &mut self,
        range_utf16: Range<usize>,
        actual: &mut Option<Range<usize>>,
        _: &mut Window,
        _: &mut Context<Self>,
    ) -> Option<String> {
        let range = self.range_from_utf16(&range_utf16);
        actual.replace(self.range_to_utf16(&range));
        Some(self.text[range].to_string())
    }

    fn selected_text_range(
        &mut self,
        _: bool,
        _: &mut Window,
        _: &mut Context<Self>,
    ) -> Option<UTF16Selection> {
        Some(UTF16Selection {
            range: self.range_to_utf16(&self.selected),
            reversed: self.reversed,
        })
    }

    fn set_selected_text_range(
        &mut self,
        range_utf16: Range<usize>,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        self.selected = self.range_from_utf16(&range_utf16);
        self.reversed = false;
        window.notify_text_selection_changed(&self.focus_handle);
        cx.notify();
    }

    fn text_length_utf16(&mut self, _: &mut Window, _: &mut Context<Self>) -> Option<usize> {
        Some(self.text.encode_utf16().count())
    }

    fn marked_text_range(&self, _: &mut Window, _: &mut Context<Self>) -> Option<Range<usize>> {
        self.marked.as_ref().map(|r| self.range_to_utf16(r))
    }

    fn unmark_text(&mut self, window: &mut Window, cx: &mut Context<Self>) {
        self.marked = None;
        window.notify_text_input_changed(&self.focus_handle);
        cx.notify();
    }

    fn replace_text_in_range(
        &mut self,
        range_utf16: Option<Range<usize>>,
        new_text: &str,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        let range = range_utf16
            .as_ref()
            .map(|r| self.range_from_utf16(r))
            .or(self.marked.clone())
            .unwrap_or(self.selected.clone());
        self.edit(range, new_text, true, window, cx);
    }

    fn replace_and_mark_text_in_range(
        &mut self,
        range_utf16: Option<Range<usize>>,
        new_text: &str,
        new_selected_utf16: Option<Range<usize>>,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        let range = range_utf16
            .as_ref()
            .map(|r| self.range_from_utf16(r))
            .or(self.marked.clone())
            .unwrap_or(self.selected.clone());
        let start = range.start;
        self.edit(range, new_text, true, window, cx);
        let inserted = self.clean(new_text);
        if !inserted.is_empty() {
            self.marked = Some(start..start + inserted.len());
        }
        if let Some(selected) = new_selected_utf16 {
            let base = self.offset_to_utf16(start);
            let len = inserted.encode_utf16().count();
            self.selected = self.range_from_utf16(
                &(base + selected.start.min(len)..base + selected.end.min(len)),
            );
        }
        cx.notify();
    }

    fn bounds_for_range(
        &mut self,
        range_utf16: Range<usize>,
        element_bounds: Bounds<Pixels>,
        _: &mut Window,
        _: &mut Context<Self>,
    ) -> Option<Bounds<Pixels>> {
        let layout = self.layout.as_ref()?;
        let range = self.range_from_utf16(&range_utf16);
        let r = layout.row_of(range.start);
        let row = &layout.rows[r];
        let x = layout.x0(row) + row.boundary_x(row.char_at(range.start));
        Some(Bounds::new(
            point(
                element_bounds.left() + x,
                element_bounds.top() + layout.line_height * r as f32,
            ),
            size(px(1.), layout.line_height),
        ))
    }

    fn character_index_for_point(
        &mut self,
        position: Point<Pixels>,
        _: &mut Window,
        _: &mut Context<Self>,
    ) -> Option<usize> {
        self.bounds?;
        let offset = self.offset_for_point(position);
        Some(self.offset_to_utf16(offset))
    }
}

// ---------------------------------------------------------------------------
// Element: measures, paints text, selection and caret
// ---------------------------------------------------------------------------
struct RtlFieldElement {
    field: Entity<RtlField>,
    text: String,
    placeholder: String,
    min_rows: usize,
    ltr: bool,
}

impl IntoElement for RtlFieldElement {
    type Element = Self;

    fn into_element(self) -> Self::Element {
        self
    }
}

impl Element for RtlFieldElement {
    type RequestLayoutState = ();
    type PrepaintState = ();

    fn id(&self) -> Option<ElementId> {
        None
    }

    fn source_location(&self) -> Option<&'static core::panic::Location<'static>> {
        None
    }

    fn request_layout(
        &mut self,
        _id: Option<&GlobalElementId>,
        _inspector_id: Option<&InspectorElementId>,
        window: &mut Window,
        _cx: &mut App,
    ) -> (LayoutId, Self::RequestLayoutState) {
        let mut style = Style::default();
        style.size.width = relative(1.).into();
        let text = self.text.clone();
        let min_rows = self.min_rows;
        let ltr = self.ltr;
        let text_style = window.text_style();

        let id = window.request_measured_layout(style, move |known, available, window, _cx| {
            let width = known.width.or(match available.width {
                gpui_kit::AvailableSpace::Definite(w) => Some(w),
                _ => None,
            });
            let width = width.unwrap_or(px(400.));
            let layout = compute_layout(&text, width, ltr, window, &text_style);
            let rows = layout.rows.len().max(min_rows);
            Size {
                width,
                height: layout.line_height * rows as f32,
            }
        });
        (id, ())
    }

    fn prepaint(
        &mut self,
        _id: Option<&GlobalElementId>,
        _inspector_id: Option<&InspectorElementId>,
        _bounds: Bounds<Pixels>,
        _request_layout: &mut Self::RequestLayoutState,
        _window: &mut Window,
        _cx: &mut App,
    ) -> Self::PrepaintState {
    }

    fn paint(
        &mut self,
        _id: Option<&GlobalElementId>,
        _inspector_id: Option<&InspectorElementId>,
        bounds: Bounds<Pixels>,
        _request_layout: &mut Self::RequestLayoutState,
        _prepaint: &mut Self::PrepaintState,
        window: &mut Window,
        cx: &mut App,
    ) {
        let focus_handle = self.field.read(cx).focus_handle.clone();
        window.handle_input(
            &focus_handle,
            ElementInputHandler::new(bounds, self.field.clone()),
            cx,
        );

        let text_style = window.text_style();
        let layout = Rc::new(compute_layout(
            &self.text,
            bounds.size.width,
            self.ltr,
            window,
            &text_style,
        ));
        let line_height = layout.line_height;
        let focused = focus_handle.is_focused(window);
        let (selected, cursor) = {
            let field = self.field.read(cx);
            (field.selected.clone(), field.cursor())
        };

        // Selection: one block per run of visually adjacent selected chars.
        if !selected.is_empty() {
            for (r, row) in layout.rows.iter().enumerate() {
                let from = selected.start.max(row.start);
                let to = selected.end.min(row.end);
                let origin_x = bounds.left() + layout.x0(row);
                let top = bounds.top() + line_height * r as f32;
                if from < to {
                    let a = row.char_at(from);
                    let b = row.char_at(to);
                    let mut spans: Vec<(Pixels, Pixels)> =
                        (a..b).map(|c| row.char_span(c)).collect();
                    spans.sort_by(|x, y| x.0.partial_cmp(&y.0).unwrap_or(std::cmp::Ordering::Equal));
                    let mut merged: Vec<(Pixels, Pixels)> = Vec::new();
                    for span in spans {
                        match merged.last_mut() {
                            Some(last) if span.0 <= last.1 + px(0.5) => last.1 = last.1.max(span.1),
                            _ => merged.push(span),
                        }
                    }
                    for (l, rgt) in merged {
                        window.paint_quad(fill(
                            Bounds::from_corners(
                                point(origin_x + l, top),
                                point(origin_x + rgt, top + line_height),
                            ),
                            ui::accent().opacity(0.28),
                        ));
                    }
                } else if selected.start <= row.end
                    && selected.end > row.end
                    && row.chars() == 0
                {
                    // A selected empty line: show a small block at its edge.
                    let edge = if layout.ltr { bounds.left() } else { bounds.right() - px(6.) };
                    window.paint_quad(fill(
                        Bounds::new(point(edge, top), size(px(6.), line_height)),
                        ui::accent().opacity(0.28),
                    ));
                }
            }
        }

        // Text (or the placeholder).
        if self.text.is_empty() {
            if !self.placeholder.is_empty() {
                let font_size = text_style.font_size.to_pixels(window.rem_size());
                let mut faint = text_style.clone();
                faint.color = ui::faint().into();
                let row = build_row(&self.placeholder, 0, window, &faint, font_size, self.ltr);
                let x = if self.ltr { bounds.left() } else { bounds.right() - row.width };
                let _ = row.line.paint(
                    point(x, bounds.top()),
                    line_height,
                    TextAlign::Left,
                    None,
                    window,
                    cx,
                );
            }
        } else {
            for (r, row) in layout.rows.iter().enumerate() {
                let _ = row.line.paint(
                    point(bounds.left() + layout.x0(row), bounds.top() + line_height * r as f32),
                    line_height,
                    TextAlign::Left,
                    None,
                    window,
                    cx,
                );
            }
        }

        // Caret.
        if focused && selected.is_empty() {
            let r = layout.row_of(cursor);
            let row = &layout.rows[r];
            let x = bounds.left() + layout.x0(row) + row.boundary_x(row.char_at(cursor));
            window.paint_quad(fill(
                Bounds::new(
                    point(x - px(0.75), bounds.top() + line_height * r as f32),
                    size(px(1.5), line_height),
                ),
                ui::accent(),
            ));
        }

        self.field.update(cx, |field, _| {
            field.layout = Some(layout);
            field.bounds = Some(bounds);
        });
    }
}

impl Render for RtlField {
    fn render(&mut self, window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let focused = self.focus_handle.is_focused(window);
        div()
            .key_context("RtlField")
            .track_focus(&self.focus_handle)
            .cursor(CursorStyle::IBeam)
            .on_action(cx.listener(Self::backspace))
            .on_action(cx.listener(Self::delete))
            .on_action(cx.listener(Self::delete_word_back))
            .on_action(cx.listener(Self::delete_word_forward))
            .on_action(cx.listener(Self::left))
            .on_action(cx.listener(Self::right))
            .on_action(cx.listener(Self::up))
            .on_action(cx.listener(Self::down))
            .on_action(cx.listener(Self::select_left))
            .on_action(cx.listener(Self::select_right))
            .on_action(cx.listener(Self::select_up))
            .on_action(cx.listener(Self::select_down))
            .on_action(cx.listener(Self::home))
            .on_action(cx.listener(Self::end))
            .on_action(cx.listener(Self::select_home))
            .on_action(cx.listener(Self::select_end))
            .on_action(cx.listener(Self::doc_start))
            .on_action(cx.listener(Self::doc_end))
            .on_action(cx.listener(Self::select_doc_start))
            .on_action(cx.listener(Self::select_doc_end))
            .on_action(cx.listener(Self::word_left))
            .on_action(cx.listener(Self::word_right))
            .on_action(cx.listener(Self::select_word_left))
            .on_action(cx.listener(Self::select_word_right))
            .on_action(cx.listener(Self::select_all))
            .on_action(cx.listener(Self::paste))
            .on_action(cx.listener(Self::copy))
            .on_action(cx.listener(Self::cut))
            .on_action(cx.listener(Self::undo))
            .on_action(cx.listener(Self::redo))
            .on_action(cx.listener(Self::enter))
            .on_mouse_down(MouseButton::Left, cx.listener(Self::on_mouse_down))
            .on_mouse_up(MouseButton::Left, cx.listener(Self::on_mouse_up))
            .on_mouse_up_out(MouseButton::Left, cx.listener(Self::on_mouse_up))
            .on_mouse_move(cx.listener(Self::on_mouse_move))
            .w_full()
            .px_3()
            .py_2()
            .rounded_md()
            .border_1()
            .border_color(if focused { ui::accent() } else { ui::border_strong() })
            .bg(ui::surface())
            .text_sm()
            .text_color(ui::ink())
            .child(RtlFieldElement {
                field: cx.entity(),
                text: self.text.clone(),
                placeholder: self.placeholder.clone(),
                min_rows: self.min_rows,
                ltr: self.ltr,
            })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn levels_for(text: &str) -> Vec<Level> {
        let info = BidiInfo::new(text, Some(Level::rtl()));
        let para = &info.paragraphs[0];
        info.reordered_levels_per_char(para, para.range.clone())
    }

    /// The text in left-to-right display order, as the renderer is given it.
    fn display(text: &str) -> String {
        let chars: Vec<char> = text.chars().collect();
        visual_order(&levels_for(text)).iter().map(|&i| chars[i]).collect()
    }

    #[test]
    fn pure_hebrew_is_simply_reversed() {
        assert_eq!(display("שלום"), "םולש");
    }

    #[test]
    fn a_latin_word_inside_hebrew_keeps_its_own_order() {
        // Hebrew reads right to left, but "cd" must still read c then d.
        assert_eq!(display("אב cd"), "cd בא");
    }

    #[test]
    fn numbers_stay_left_to_right() {
        assert_eq!(display("שנת 2026"), "2026 תנש");
    }

    #[test]
    fn an_empty_line_has_no_order() {
        assert!(visual_order(&[]).is_empty());
    }

    #[test]
    fn niqqud_belongs_to_its_letter() {
        assert!(is_mark('\u{05B0}'));
        assert!(!is_mark('א'));
        assert!(!is_mark('\u{05BE}')); // maqaf is punctuation, not a mark
    }

    #[test]
    fn direction_controls_are_dropped_from_input() {
        assert!(is_bidi_control('\u{200F}'));
        assert!(is_bidi_control('\u{2066}'));
        assert!(!is_bidi_control('א'));
    }
}
