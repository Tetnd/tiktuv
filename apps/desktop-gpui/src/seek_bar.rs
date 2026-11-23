//! The player's timeline: a bar you can click and drag to choose a time.
//!
//! It is its own element because seeking needs the bar's own geometry — where
//! the pointer landed, as a fraction of the track — which only the element
//! knows, and only once it has been laid out.
//!
//! Deliberately left to right, like the transport buttons beside it: a
//! timeline is a picture of time passing, not of reading order, and every
//! player keeps the start of the recording on the left, Hebrew interface or
//! not.

use crate::ui;
use std::rc::Rc;

use gpui_kit::{
    App, Bounds, DispatchPhase, Element, ElementId, GlobalElementId, Hitbox, HitboxBehavior,
    InspectorElementId, IntoElement, LayoutId, MouseButton, MouseDownEvent, MouseMoveEvent, Pixels,
    Style, Window, fill, point, px, rgb, size,
};

/// Height of the whole element; the track itself is drawn thinner and centred,
/// so there is something comfortable to aim at.
const HEIGHT: Pixels = px(16.);
const TRACK_HEIGHT: Pixels = px(6.);

type SeekHandler = Rc<dyn Fn(&f32, &mut Window, &mut App)>;

pub struct SeekBar {
    id: ElementId,
    /// How far playback has got, 0..=1.
    progress: f32,
    on_seek: Option<SeekHandler>,
}

/// A timeline showing `progress` (0..=1) of the recording.
pub fn seek_bar(id: impl Into<ElementId>, progress: f32) -> SeekBar {
    SeekBar {
        id: id.into(),
        progress: progress.clamp(0.0, 1.0),
        on_seek: None,
    }
}

impl SeekBar {
    /// Called with the fraction of the recording the user pointed at, on click
    /// and while dragging.
    pub fn on_seek(mut self, handler: impl Fn(&f32, &mut Window, &mut App) + 'static) -> Self {
        self.on_seek = Some(Rc::new(handler));
        self
    }
}

impl Element for SeekBar {
    type RequestLayoutState = ();
    type PrepaintState = Hitbox;

    fn id(&self) -> Option<ElementId> {
        Some(self.id.clone())
    }

    fn source_location(&self) -> Option<&'static core::panic::Location<'static>> {
        None
    }

    fn request_layout(
        &mut self,
        _id: Option<&GlobalElementId>,
        _inspector_id: Option<&InspectorElementId>,
        window: &mut Window,
        cx: &mut App,
    ) -> (LayoutId, Self::RequestLayoutState) {
        let mut style = Style::default();
        style.size.width = gpui_kit::relative(1.).into();
        style.size.height = HEIGHT.into();
        (window.request_layout(style, [], cx), ())
    }

    fn prepaint(
        &mut self,
        _id: Option<&GlobalElementId>,
        _inspector_id: Option<&InspectorElementId>,
        bounds: Bounds<Pixels>,
        _request_layout: &mut Self::RequestLayoutState,
        window: &mut Window,
        _cx: &mut App,
    ) -> Self::PrepaintState {
        window.insert_hitbox(bounds, HitboxBehavior::Normal)
    }

    fn paint(
        &mut self,
        _id: Option<&GlobalElementId>,
        _inspector_id: Option<&InspectorElementId>,
        bounds: Bounds<Pixels>,
        _request_layout: &mut Self::RequestLayoutState,
        hitbox: &mut Self::PrepaintState,
        window: &mut Window,
        _cx: &mut App,
    ) {
        let track = Bounds {
            origin: point(
                bounds.origin.x,
                bounds.origin.y + (HEIGHT - TRACK_HEIGHT) / 2.,
            ),
            size: size(bounds.size.width, TRACK_HEIGHT),
        };
        let played = Bounds {
            origin: track.origin,
            size: size(track.size.width * self.progress, TRACK_HEIGHT),
        };
        let radius = TRACK_HEIGHT / 2.;

        window.paint_quad(fill(track, ui::border()).corner_radii(radius));
        if played.size.width > px(0.) {
            window.paint_quad(fill(played, ui::accent()).corner_radii(radius));
        }
        // The thumb, so the exact position is visible on a thin track.
        let thumb_size = px(12.);
        let thumb = Bounds {
            origin: point(
                bounds.origin.x + (bounds.size.width - thumb_size) * self.progress,
                bounds.origin.y + (HEIGHT - thumb_size) / 2.,
            ),
            size: size(thumb_size, thumb_size),
        };
        window.paint_quad(fill(thumb, ui::accent()).corner_radii(thumb_size / 2.));

        let Some(on_seek) = self.on_seek.clone() else {
            return;
        };

        // Where along the track a pointer sits, as a fraction.
        let fraction = move |x: Pixels| -> f32 {
            if bounds.size.width <= px(0.) {
                return 0.0;
            }
            ((x - bounds.origin.x) / bounds.size.width).clamp(0.0, 1.0)
        };

        let hitbox = hitbox.clone();
        window.on_mouse_event({
            let on_seek = on_seek.clone();
            let hitbox = hitbox.clone();
            move |event: &MouseDownEvent, phase, window, cx| {
                if phase == DispatchPhase::Bubble
                    && event.button == MouseButton::Left
                    && hitbox.is_hovered(window)
                {
                    on_seek(&fraction(event.position.x), window, cx);
                }
            }
        });
        // Dragging: the button stays down while the pointer moves along the
        // bar, which is how anyone scrubs through a recording.
        window.on_mouse_event(move |event: &MouseMoveEvent, phase, window, cx| {
            if phase == DispatchPhase::Bubble
                && event.pressed_button == Some(MouseButton::Left)
                && hitbox.is_hovered(window)
            {
                on_seek(&fraction(event.position.x), window, cx);
            }
        });
    }
}

impl IntoElement for SeekBar {
    type Element = Self;

    fn into_element(self) -> Self::Element {
        self
    }
}
