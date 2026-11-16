//! A paragraph of Hebrew text that wraps correctly.
//!
//! GPUI shapes and lays out text left-to-right, so `format::rtl` reorders a
//! string into visual order before it is handed over (see that function). That
//! works for anything on one line — and breaks the moment a line wraps: the
//! renderer fills its rows left-to-right from the start of the string it was
//! given, which for reordered text is the *end* of the sentence. A paragraph
//! that wraps onto three rows is read bottom-up.
//!
//! The fix is to reorder per row rather than per paragraph, which means
//! knowing where the rows fall, which means knowing the width — so this is an
//! element rather than a string helper:
//!
//! 1. shape the *logical* text to the available width and read back the wrap
//!    boundaries (this is the same wrapping GPUI would have done);
//! 2. cut the logical text at those boundaries;
//! 3. reorder each row on its own, and shape it for painting;
//! 4. paint the rows top to bottom, each aligned to the right edge.
//!
//! Single-line labels do not need this and keep using `rtl` directly.

use std::cell::RefCell;
use std::rc::Rc;

use gpui_kit::{
    App, Bounds, Element, ElementId, GlobalElementId, InspectorElementId, IntoElement, LayoutId,
    Pixels, Point, ShapedLine, Size, TextAlign, WhiteSpace, Window, point,
};

use crate::format::rtl;

/// A Hebrew paragraph that is reordered row by row as it wraps.
///
/// The text is the *logical* string — not one already passed through
/// [`crate::format::rtl`]. Font, size, colour and line height come from the
/// surrounding element, as they do for an ordinary string child.
pub fn rtl_paragraph(text: impl AsRef<str>) -> RtlParagraph {
    RtlParagraph {
        text: text.as_ref().to_string(),
        state: Rc::new(RefCell::new(None)),
    }
}

pub struct RtlParagraph {
    text: String,
    state: Rc<RefCell<Option<LaidOut>>>,
}

struct LaidOut {
    rows: Vec<ShapedLine>,
    line_height: Pixels,
    size: Size<Pixels>,
}

impl Element for RtlParagraph {
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
        let text_style = window.text_style();
        let font_size = text_style.font_size.to_pixels(window.rem_size());
        let line_height = window.pixel_snap(
            text_style
                .line_height
                .to_pixels(font_size.into(), window.rem_size()),
        );
        let text = self.text.clone();
        let state = self.state.clone();

        let layout_id = window.request_measured_layout(Default::default(), {
            move |known_dimensions, available_space, window, _cx| {
                // The width the rows have to fit in. `None` means "as wide as
                // it likes", which is also what a nowrap style asks for.
                let wrap_width = if text_style.white_space == WhiteSpace::Normal {
                    known_dimensions.width.or(match available_space.width {
                        gpui_kit::AvailableSpace::Definite(width) => Some(width),
                        _ => None,
                    })
                } else {
                    None
                };

                // Step 1 and 2: let the text system wrap the logical text, and
                // cut it where it said the rows end.
                let logical_rows = wrap_rows(&text, font_size, wrap_width, window, &text_style);

                // Step 3: each row is its own little paragraph, reordered on
                // its own. Shaped here, so painting is just positioning.
                let mut rows: Vec<ShapedLine> = Vec::with_capacity(logical_rows.len());
                let mut size = Size {
                    width: Pixels::ZERO,
                    height: line_height * logical_rows.len() as f32,
                };
                for logical in &logical_rows {
                    let visual: gpui_kit::SharedString = rtl(logical.trim_end()).into();
                    let run = text_style.to_run(visual.len());
                    let shaped =
                        window
                            .text_system()
                            .shape_line(visual, font_size, &[run], None);
                    size.width = size.width.max(shaped.width()).ceil();
                    rows.push(shaped);
                }

                state.borrow_mut().replace(LaidOut {
                    rows,
                    line_height,
                    size,
                });
                size
            }
        });

        (layout_id, ())
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
        let state = self.state.borrow();
        let Some(laid_out) = state.as_ref() else {
            return;
        };

        // Step 4: top to bottom, each row flush with the right edge — the
        // start of the line in a right-to-left paragraph. The element is given
        // the full width of its container by a column layout, so aligning to
        // `bounds` is what puts the text where a reader expects it.
        let align_width = bounds.size.width.max(laid_out.size.width);
        for (row, line) in laid_out.rows.iter().enumerate() {
            let origin: Point<Pixels> = point(
                bounds.origin.x,
                bounds.origin.y + laid_out.line_height * row as f32,
            );
            // A failed paint is a glyph-cache problem, not something the user
            // can act on: skip the row rather than take the window down.
            let _ = line.paint(
                origin,
                laid_out.line_height,
                TextAlign::Right,
                Some(align_width),
                window,
                cx,
            );
        }
    }
}

impl IntoElement for RtlParagraph {
    type Element = Self;

    fn into_element(self) -> Self::Element {
        self
    }
}

/// Splits `text` into the rows it wraps into at `wrap_width`, in logical order.
///
/// The rows come from the text system's own wrapping of the logical string, so
/// they break where GPUI would have broken them — including at the hard line
/// breaks the string already contains.
fn wrap_rows(
    text: &str,
    font_size: Pixels,
    wrap_width: Option<Pixels>,
    window: &mut Window,
    text_style: &gpui_kit::TextStyle,
) -> Vec<String> {
    if text.is_empty() {
        return Vec::new();
    }
    let shared: gpui_kit::SharedString = text.to_string().into();
    let runs = [text_style.to_run(shared.len())];
    let Ok(lines) = window
        .text_system()
        .shape_text(shared, font_size, &runs, wrap_width, None)
    else {
        // Without a layout we can still show the text; it is one row, which is
        // exactly what the old behaviour was.
        return text.lines().map(str::to_string).collect();
    };

    let mut rows = Vec::new();
    for line in &lines {
        let line_text = line.text.clone();
        let mut start = 0usize;
        for boundary in line.wrap_boundaries.iter() {
            let Some(run) = line.unwrapped_layout.runs.get(boundary.run_ix) else {
                continue;
            };
            let Some(glyph) = run.glyphs.get(boundary.glyph_ix) else {
                continue;
            };
            let end = glyph.index;
            if end > start && line_text.is_char_boundary(start) && line_text.is_char_boundary(end) {
                rows.push(line_text[start..end].to_string());
                start = end;
            }
        }
        rows.push(line_text[start..].to_string());
    }
    rows
}
