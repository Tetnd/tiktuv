//! The window frame: sidebar navigation, theme switch, toasts and the root
//! `Render` that puts the current screen next to them.

use gpui_kit::base::StyledExt as _;
use gpui_kit::component::scroll::ScrollableElement as _;
use gpui_kit::prelude::FluentBuilder as _;
use gpui_kit::{
    AnyElement, Context, InteractiveElement as _, IntoElement, ParentElement as _, Render,
    StatefulInteractiveElement as _, Styled as _, Window, div, px,
};

use crate::app::{App, Screen, Toast, ToastTone};
use crate::format::*;
use crate::models::JobStatus;
use crate::ui::{self, Ic, Tone};

const SIDEBAR_WIDTH: f32 = 232.0;

impl App {
    // -- theme -------------------------------------------------------------
    pub(crate) fn toggle_theme(&mut self, cx: &mut Context<Self>) {
        let dark = !ui::is_dark();
        ui::apply_theme(dark, cx);
        crate::backend::save_theme_preference(dark);
        cx.notify();
    }

    // -- sidebar -----------------------------------------------------------
    fn sidebar(&self, cx: &mut Context<Self>) -> impl IntoElement {
        let screen = self.screen;
        let active_jobs = self
            .jobs
            .iter()
            .filter(|j| matches!(j.status, JobStatus::Running | JobStatus::Queued))
            .count();
        let dark = ui::is_dark();
        let connected = self.connected;

        div()
            .w(px(SIDEBAR_WIDTH))
            .h_full()
            .flex_shrink_0()
            .v_flex()
            .bg(ui::surface())
            .border_l_1()
            .border_color(ui::border())
            // -- brand ----------------------------------------------------
            .child(
                ui::rtl_row()
                    .gap_3()
                    .px_5()
                    .pt_6()
                    .pb_5()
                    .child(
                        div()
                            .size_10()
                            .flex_shrink_0()
                            .rounded_xl()
                            .bg(ui::accent())
                            .shadow_sm()
                            .flex()
                            .items_center()
                            .justify_center()
                            .child(ui::icon(Ic::AudioLines, 22.0, gpui_kit::rgb(0xffffff))),
                    )
                    .child(
                        div()
                            .v_flex()
                            .child(
                                div()
                                    .text_xl()
                                    .font_bold()
                                    .text_color(ui::ink())
                                    .child(rtl("תכתוב")),
                            )
                            .child(
                                div()
                                    .text_xs()
                                    .text_color(ui::muted())
                                    .child(rtl("תמלול עברית מקומי")),
                            ),
                    ),
            )
            // -- navigation -----------------------------------------------
            .child(
                div()
                    .v_flex()
                    .gap_1()
                    .px_3()
                    .child(nav_item(
                        "nav-queue",
                        Ic::AudioLines,
                        "תמלול חדש",
                        matches!(screen, Screen::Queue),
                        (active_jobs > 0).then_some(active_jobs),
                        cx,
                        Screen::Queue,
                    ))
                    .child(nav_item(
                        "nav-history",
                        Ic::Clock,
                        "היסטוריה",
                        matches!(screen, Screen::History | Screen::Editor),
                        None,
                        cx,
                        Screen::History,
                    ))
                    .child(nav_item(
                        "nav-system",
                        Ic::Settings,
                        "מערכת ומודלים",
                        matches!(screen, Screen::System),
                        None,
                        cx,
                        Screen::System,
                    )),
            )
            .child(div().flex_1())
            // -- footer ---------------------------------------------------
            .child(
                div()
                    .v_flex()
                    .gap_2()
                    .p_3()
                    .child(
                        ui::rtl_row()
                            .id("theme-toggle")
                            .gap_3()
                            .px_3()
                            .h_9()
                            .rounded_lg()
                            .cursor_pointer()
                            .text_sm()
                            .text_color(ui::subtle())
                            .hover(|s| s.bg(ui::surface_alt()))
                            .on_click(cx.listener(|this, _, _, cx| this.toggle_theme(cx)))
                            .child(ui::icon(if dark { Ic::Sun } else { Ic::Moon }, 16.0, ui::muted()))
                            .child(rtl(if dark { "מצב בהיר" } else { "מצב כהה" })),
                    )
                    .child(
                        ui::rtl_row()
                            .gap_2()
                            .px_3()
                            .py_2p5()
                            .rounded_lg()
                            .bg(ui::surface_alt())
                            .text_xs()
                            .text_color(ui::muted())
                            .child(
                                div()
                                    .size_2()
                                    .flex_shrink_0()
                                    .rounded_full()
                                    .bg(if connected { ui::success() } else { ui::faint() }),
                            )
                            .child(rtl(if connected {
                                "מחובר לשירות המקומי"
                            } else {
                                "מתחבר לשירות…"
                            })),
                    ),
            )
    }

    // -- toasts ------------------------------------------------------------
    fn toast_card(&self, toast: &Toast, cx: &mut Context<Self>) -> impl IntoElement {
        let id = toast.id;
        let title = toast.title.clone();
        let detail = toast.detail.clone();
        let (tone, ic) = match toast.tone {
            ToastTone::Success => (Tone::Success, Ic::CircleCheck),
            ToastTone::Error => (Tone::Danger, Ic::CircleAlert),
            ToastTone::Info => (Tone::Accent, Ic::Info),
        };
        let (fg, _) = tone.colors();
        ui::rtl_row()
            .id(("toast", id))
            .items_start()
            .gap_3()
            .p_3p5()
            .rounded_xl()
            .border_1()
            .border_color(ui::border())
            .bg(ui::surface())
            .shadow_lg()
            .child(div().pt_0p5().child(ui::icon(ic, 18.0, fg)))
            .child(
                div()
                    .flex_1()
                    .min_w_0()
                    .v_flex()
                    .gap_0p5()
                    .child(
                        div()
                            .text_sm()
                            .font_medium()
                            .text_color(ui::ink())
                            .child(crate::rtl_text::rtl_paragraph(&title)),
                    )
                    .when_some(detail, |this, detail| {
                        this.child(
                            div()
                                .text_xs()
                                .text_color(ui::muted())
                                .child(crate::rtl_text::rtl_paragraph(detail)),
                        )
                    }),
            )
            .child(
                div()
                    .id(("toast-close", id))
                    .size_6()
                    .flex_shrink_0()
                    .rounded_md()
                    .flex()
                    .items_center()
                    .justify_center()
                    .cursor_pointer()
                    .hover(|s| s.bg(ui::surface_alt()))
                    .on_click(cx.listener(move |this, _, _, cx| this.dismiss_toast(id, cx)))
                    .child(ui::icon(Ic::X, 14.0, ui::muted())),
            )
    }

    fn toast_layer(&self, cx: &mut Context<Self>) -> impl IntoElement {
        if self.toasts.is_empty() {
            return div();
        }
        let toasts: Vec<AnyElement> = self
            .toasts
            .iter()
            .map(|t| self.toast_card(t, cx).into_any_element())
            .collect();
        // Bottom-left: the corner farthest from the sidebar and the reading
        // start of a right-to-left page.
        div()
            .absolute()
            .bottom_0()
            .left_0()
            .p_5()
            .w(px(380.))
            .v_flex()
            .gap_2()
            .children(toasts)
    }
}

impl Render for App {
    fn render(&mut self, window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let sidebar = self.sidebar(cx).into_any_element();
        let wide = matches!(self.screen, Screen::Editor);
        let content: AnyElement = match self.screen {
            Screen::Queue => self.queue_screen(cx).into_any_element(),
            Screen::History => self.render_history(cx),
            Screen::Editor => self.render_editor(window, cx),
            Screen::System => self.render_system(cx),
        };
        let toasts = self.toast_layer(cx).into_any_element();
        let overlays = self.render_overlays(cx).into_any_element();

        div()
            .size_full()
            .relative()
            .flex()
            // The sidebar is the first child and so sits on the right.
            .flex_row_reverse()
            .bg(ui::page())
            .text_color(ui::ink())
            .text_right()
            .child(sidebar)
            .child(
                div()
                    .flex_1()
                    .min_w_0()
                    .h_full()
                    .v_flex()
                    // The scrolled content is a flex column (its height is
                    // auto with a viewport-height minimum), so a screen can
                    // `flex_1` to fill the page.
                    .child(
                        div().flex_1().min_h_0().v_flex().overflow_y_scrollbar().child(
                            div()
                                .w_full()
                                .flex_1()
                                .max_w(px(if wide { 1240.0 } else { 1040.0 }))
                                .mx_auto()
                                .v_flex()
                                .child(content),
                        ),
                    ),
            )
            .child(toasts)
            .child(overlays)
    }
}

fn nav_item(
    id: &'static str,
    ic: Ic,
    label: &'static str,
    active: bool,
    badge: Option<usize>,
    cx: &mut Context<App>,
    screen: Screen,
) -> impl IntoElement {
    let fg = if active { ui::accent_strong() } else { ui::subtle() };
    ui::rtl_row()
        .id(id)
        .gap_3()
        .px_3()
        .h_10()
        .rounded_lg()
        .cursor_pointer()
        .text_sm()
        .font_medium()
        .text_color(fg)
        .when(active, |this| this.bg(ui::accent_bg()))
        .when(!active, |this| this.hover(|s| s.bg(ui::surface_alt())))
        .on_click(cx.listener(move |this, _, _, cx| this.set_screen(screen, cx)))
        .child(ui::icon(ic, 18.0, if active { ui::accent() } else { ui::muted() }))
        .child(div().flex_1().child(rtl(label)))
        .when_some(badge, |this, n| {
            this.child(
                div()
                    .min_w_5()
                    .h_5()
                    .px_1p5()
                    .rounded_full()
                    .bg(ui::accent())
                    .text_color(gpui_kit::rgb(0xffffff))
                    .text_xs()
                    .font_semibold()
                    .flex()
                    .items_center()
                    .justify_center()
                    .child(n.to_string()),
            )
        })
}
