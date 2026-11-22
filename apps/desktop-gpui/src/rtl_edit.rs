//! Backspace and Delete that behave right-to-left in Hebrew fields.
//!
//! GPUI lays text out left-to-right, so Hebrew sits in a field in *visual*
//! order (see `format::rtl`). The caret an RTL writer expects after typing is
//! the visual left end of the text — offset 0 in the field — and Backspace
//! there deletes "the character before the caret", of which the field sees
//! none. So Backspace did nothing until the text was selected.
//!
//! In a Hebrew field the logical "before" is the visual right of the caret,
//! so Backspace and Delete (and their word-wise forms) swap sides here.

use std::any::TypeId;
use std::cell::Cell;

use gpui_kit::component::input::{
    Backspace, Delete, DeleteToNextWordEnd, DeleteToPreviousWordStart, Input, InputState,
    Textarea, TextareaState,
};
use gpui_kit::{
    Action, App, Div, Entity, InteractiveElement as _, ParentElement as _,
    Styled as _, div, relative,
};

thread_local! {
    /// The action this module just re-dispatched, so its own capture handler
    /// lets it through instead of swapping it back. `dispatch_action` is
    /// deferred, hence a flag rather than a recursion guard.
    static PASS: Cell<Option<TypeId>> = const { Cell::new(None) };
}

fn has_hebrew(text: &str) -> bool {
    text.chars().any(|c| ('\u{0590}'..='\u{05ff}').contains(&c))
}

fn swap<A: Action, B: Action + Clone>(
    el: Div,
    to: B,
    is_hebrew: impl Fn(&App) -> bool + Clone + 'static,
) -> Div {
    el.capture_action(move |_: &A, window, cx| {
        if PASS.with(|p| p.get()) == Some(TypeId::of::<A>()) {
            PASS.with(|p| p.set(None));
            return;
        }
        if !is_hebrew(cx) {
            return;
        }
        PASS.with(|p| p.set(Some(TypeId::of::<B>())));
        cx.stop_propagation();
        window.dispatch_action(Box::new(to.clone()), cx);
    })
}

fn wrap(el: Div, is_hebrew: impl Fn(&App) -> bool + Clone + 'static) -> Div {
    let el = swap::<Backspace, Delete>(el, Delete, is_hebrew.clone());
    let el = swap::<Delete, Backspace>(el, Backspace, is_hebrew.clone());
    let el = swap::<DeleteToPreviousWordStart, DeleteToNextWordEnd>(
        el,
        DeleteToNextWordEnd,
        is_hebrew.clone(),
    );
    swap::<DeleteToNextWordEnd, DeleteToPreviousWordStart>(
        el,
        DeleteToPreviousWordStart,
        is_hebrew,
    )
}

/// A single-line field whose Backspace/Delete follow Hebrew reading order.
pub fn rtl_input(state: &Entity<InputState>) -> Div {
    let watched = state.clone();
    wrap(div().w_full(), move |cx| {
        has_hebrew(&watched.read(cx).value().to_string())
    })
    .child(Input::new(state))
}

/// The multi-line counterpart of [`rtl_input`].
pub fn rtl_textarea(state: &Entity<TextareaState>) -> Div {
    let watched = state.clone();
    wrap(div().size_full().min_h_0(), move |cx| {
        has_hebrew(&watched.read(cx).value().to_string())
    })
    .child(Textarea::new(state).min_h_0().h(relative(1.)))
}
