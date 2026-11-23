//! The design system: palette, icons and the small building blocks every
//! screen is made from.
//!
//! Colours are functions rather than constants because the interface has a
//! light and a dark mode; they read one global flag, so any element can ask for
//! "the muted text colour" without carrying the theme around.

use std::sync::atomic::{AtomicBool, Ordering};

use gpui_kit::base::StyledExt as _;
use gpui_kit::component::Icon;
use gpui_kit::prelude::FluentBuilder as _;
use gpui_kit::{
    AnyElement, Div, IntoElement, ParentElement as _, Rgba, Styled as _, div, px, rgb,
};

use crate::format::rtl;

static DARK: AtomicBool = AtomicBool::new(false);

pub fn set_dark(dark: bool) {
    DARK.store(dark, Ordering::Relaxed);
}

pub fn is_dark() -> bool {
    DARK.load(Ordering::Relaxed)
}

fn pick(light: u32, dark: u32) -> Rgba {
    rgb(if is_dark() { dark } else { light })
}

// -- palette -----------------------------------------------------------------
/// The page behind the cards.
pub fn page() -> Rgba {
    pick(0xf5f6fa, 0x0e1016)
}
/// Cards, the sidebar, dialogs.
pub fn surface() -> Rgba {
    pick(0xffffff, 0x171a22)
}
/// Inset areas: segmented-control tracks, hovered rows, table headers.
pub fn surface_alt() -> Rgba {
    pick(0xf0f2f8, 0x1f2330)
}
pub fn border() -> Rgba {
    pick(0xe3e6ef, 0x2a2f3d)
}
pub fn border_strong() -> Rgba {
    pick(0xcfd4e1, 0x3a4152)
}
pub fn divider() -> Rgba {
    pick(0xeceff6, 0x222733)
}
/// Main text on cards.
pub fn ink() -> Rgba {
    pick(0x151824, 0xeef0f6)
}
/// Secondary text.
pub fn subtle() -> Rgba {
    pick(0x475066, 0xc3c9d8)
}
/// Helper text and metadata.
pub fn muted() -> Rgba {
    pick(0x667085, 0x98a1b5)
}
/// Placeholders, disabled.
pub fn faint() -> Rgba {
    pick(0x98a2b3, 0x6c7488)
}

pub fn accent() -> Rgba {
    pick(0x4f46e5, 0x8b8cf8)
}
pub fn accent_strong() -> Rgba {
    pick(0x4338ca, 0xa5a6fa)
}
pub fn accent_bg() -> Rgba {
    pick(0xeef0ff, 0x23264a)
}
pub fn accent_border() -> Rgba {
    pick(0xc7cbfb, 0x3a3f87)
}
pub fn success() -> Rgba {
    pick(0x059669, 0x34d399)
}
pub fn success_bg() -> Rgba {
    pick(0xe8faf2, 0x12302a)
}
pub fn success_border() -> Rgba {
    pick(0xa7ebd0, 0x1f5a49)
}
pub fn warning() -> Rgba {
    pick(0xb45309, 0xfbbf24)
}
pub fn warning_bg() -> Rgba {
    pick(0xfff7e6, 0x33290f)
}
pub fn warning_border() -> Rgba {
    pick(0xfbdca0, 0x5c4a17)
}
pub fn danger() -> Rgba {
    pick(0xdc2626, 0xf87171)
}
pub fn danger_text() -> Rgba {
    pick(0x991b1b, 0xfca5a5)
}
pub fn danger_bg() -> Rgba {
    pick(0xfef2f2, 0x3a1a1d)
}
pub fn danger_border() -> Rgba {
    pick(0xfecaca, 0x6b2327)
}
pub fn info() -> Rgba {
    pick(0x2563eb, 0x60a5fa)
}
pub fn info_bg() -> Rgba {
    pick(0xeff6ff, 0x16263f)
}
pub fn highlight_bg() -> Rgba {
    pick(0xfffbeb, 0x3a3114)
}
pub fn scrim() -> gpui_kit::Hsla {
    gpui_kit::Hsla::from(rgb(0x05060a)).opacity(if is_dark() { 0.62 } else { 0.42 })
}

// -- icons -------------------------------------------------------------------
macro_rules! icons {
    ($($name:ident => $file:literal,)*) => {
        #[derive(Clone, Copy)]
        pub enum Ic { $($name,)* }
        fn svg_bytes(ic: Ic) -> &'static [u8] {
            match ic {
                $(Ic::$name => include_bytes!(concat!("../assets/icons/", $file, ".svg")),)*
            }
        }
    };
}

icons! {
    AudioLines => "audio-lines",
    Upload => "upload",
    Clock => "clock",
    FileText => "file-text",
    Settings => "settings",
    Cpu => "cpu",
    HardDrive => "hard-drive",
    Sparkles => "sparkles",
    Link => "link",
    Download => "download",
    Trash => "trash",
    Play => "play",
    Pause => "pause",
    Copy => "copy",
    Check => "check",
    Search => "search",
    Undo => "undo-2",
    Redo => "redo-2",
    X => "x",
    Pencil => "pencil",
    Users => "users",
    Languages => "languages",
    ShieldCheck => "shield-check",
    Lock => "lock",
    CircleCheck => "circle-check",
    CircleAlert => "circle-alert",
    CircleX => "circle-x",
    TriangleAlert => "triangle-alert",
    Info => "info",
    Moon => "moon",
    Sun => "sun",
    FolderOpen => "folder-open",
    ExternalLink => "external-link",
    Refresh => "refresh-cw",
    Plus => "plus",
    Mic => "mic",
    ChevronDown => "chevron-down",
    ChevronLeft => "chevron-left",
    ChevronRight => "chevron-right",
    Retry => "rotate-ccw",
    SkipBack => "skip-back",
    SkipForward => "skip-forward",
    Gauge => "gauge",
    Wifi => "wifi",
    WifiOff => "wifi-off",
    Cloud => "cloud",
    ListChecks => "list-checks",
    Quote => "quote",
    Tag => "tag",
    Save => "save",
    Captions => "captions",
    Replace => "replace",
    FileDown => "file-down",
    Database => "database",
    Sliders => "sliders-horizontal",
    Wand => "wand-sparkles",
    Eraser => "eraser",
    User => "user-round",
    Film => "film",
    Music => "music",
    Clapperboard => "clapperboard",
    FileMusic => "file-music",
}

/// An icon of `size` pixels in `color`.
pub fn icon(ic: Ic, size: f32, color: Rgba) -> Icon {
    Icon::default()
        .data(svg_bytes(ic))
        .size(px(size))
        .text_color(color)
}

/// Same, as an element.
pub fn ic(ic_: Ic, size: f32, color: Rgba) -> AnyElement {
    icon(ic_, size, color).into_any_element()
}

// -- building blocks ---------------------------------------------------------
/// A row that lays out right to left: its first child sits on the right.
pub fn rtl_row() -> Div {
    div().flex().flex_row_reverse().items_center()
}

/// A raised container.
pub fn card() -> Div {
    div()
        .rounded_xl()
        .border_1()
        .border_color(border())
        .bg(surface())
        .shadow_sm()
        .v_flex()
}

/// A card with the standard inner padding.
pub fn pad_card() -> Div {
    card().p_5().gap_4()
}

/// Rounded square holding an icon, used to lead card headers.
pub fn icon_tile(ic_: Ic, tone: Tone) -> Div {
    let (fg, bg) = tone.colors();
    div()
        .size_9()
        .flex_shrink_0()
        .rounded_lg()
        .bg(bg)
        .flex()
        .items_center()
        .justify_center()
        .child(icon(ic_, 18.0, fg))
}

/// Title (+ optional subtitle) with a leading icon tile, for card headers.
pub fn section_header(ic_: Ic, tone: Tone, title: &str, subtitle: Option<&str>) -> Div {
    rtl_row()
        .gap_3()
        .child(icon_tile(ic_, tone))
        .child(
            div()
                .min_w_0()
                .v_flex()
                .child(div().text_base().font_semibold().text_color(ink()).child(rtl(title)))
                .when_some(subtitle, |this, sub| {
                    this.child(div().text_xs().text_color(muted()).child(rtl(sub)))
                }),
        )
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Tone {
    Neutral,
    Accent,
    Success,
    Warning,
    Danger,
    Info,
}

impl Tone {
    /// `(foreground, background)`.
    pub fn colors(self) -> (Rgba, Rgba) {
        match self {
            Tone::Neutral => (muted(), surface_alt()),
            Tone::Accent => (accent(), accent_bg()),
            Tone::Success => (success(), success_bg()),
            Tone::Warning => (warning(), warning_bg()),
            Tone::Danger => (danger(), danger_bg()),
            Tone::Info => (info(), info_bg()),
        }
    }

    pub fn border(self) -> Rgba {
        match self {
            Tone::Neutral => border(),
            Tone::Accent => accent_border(),
            Tone::Success => success_border(),
            Tone::Warning => warning_border(),
            Tone::Danger => danger_border(),
            Tone::Info => accent_border(),
        }
    }
}

/// A small rounded status label.
pub fn pill(label: &str, tone: Tone) -> Div {
    let (fg, bg) = tone.colors();
    div()
        .px_2p5()
        .py_0p5()
        .rounded_full()
        .bg(bg)
        .text_color(fg)
        .text_xs()
        .font_medium()
        .whitespace_nowrap()
        .child(rtl(label))
}

/// A pill with a leading icon.
pub fn icon_pill(ic_: Ic, label: &str, tone: Tone) -> Div {
    let (fg, bg) = tone.colors();
    rtl_row()
        .gap_1p5()
        .px_2p5()
        .py_0p5()
        .rounded_full()
        .bg(bg)
        .text_color(fg)
        .text_xs()
        .font_medium()
        .whitespace_nowrap()
        .child(icon(ic_, 12.0, fg))
        .child(rtl(label))
}

/// A bordered notice with an icon, for banners and inline errors.
pub fn notice(ic_: Ic, tone: Tone) -> Div {
    let (fg, bg) = tone.colors();
    rtl_row()
        .items_start()
        .gap_3()
        .p_3p5()
        .rounded_lg()
        .border_1()
        .border_color(tone.border())
        .bg(bg)
        .child(div().pt_0p5().child(icon(ic_, 16.0, fg)))
}

/// Centered placeholder for an empty list.
pub fn empty_state(ic_: Ic, title: &str, body: &str) -> Div {
    div()
        .w_full()
        .py_10()
        .v_flex()
        .items_center()
        .gap_2()
        .child(
            div()
                .size_12()
                .rounded_full()
                .bg(surface_alt())
                .flex()
                .items_center()
                .justify_center()
                .child(icon(ic_, 22.0, faint())),
        )
        .child(div().text_base().font_semibold().text_color(ink()).child(rtl(title)))
        .child(
            div()
                .max_w(px(420.))
                .text_sm()
                .text_color(muted())
                .child(crate::rtl_text::rtl_paragraph(body)),
        )
}

/// Page title block.
pub fn page_title(title: &str, subtitle: &str) -> Div {
    div()
        .v_flex()
        .gap_1()
        .child(div().text_2xl().font_bold().text_color(ink()).child(rtl(title)))
        .child(div().text_sm().text_color(muted()).child(rtl(subtitle)))
}

/// A thin determinate progress bar that fills from the right.
pub fn progress_bar(fraction: f32, tone: Tone) -> Div {
    let (fg, _) = tone.colors();
    div()
        .flex()
        .flex_row_reverse()
        .w_full()
        .h_1p5()
        .rounded_full()
        .bg(surface_alt())
        .overflow_hidden()
        .child(
            div()
                .h_full()
                .rounded_full()
                .bg(fg)
                .w(gpui_kit::relative(fraction.clamp(0.0, 1.0))),
        )
}

// -- theme integration --------------------------------------------------------
fn hsla(color: Rgba) -> gpui_kit::Hsla {
    gpui_kit::Hsla::from(color)
}

/// Switches the component library to light or dark and re-skins its palette
/// with ours, so its buttons, switches and inputs match the rest of the app.
pub fn apply_theme(dark: bool, cx: &mut gpui_kit::App) {
    use gpui_kit::component::{Theme, ThemeMode};
    set_dark(dark);
    Theme::change(if dark { ThemeMode::Dark } else { ThemeMode::Light }, None, cx);
    Theme::update(cx, |theme| {
        let white = rgb(0xffffff);
        let c = &mut theme.colors;
        c.background = hsla(page());
        c.foreground = hsla(ink());
        c.primary = hsla(if dark { rgb(0x6366f1) } else { accent() });
        c.primary_hover = hsla(if dark { rgb(0x7c7ff5) } else { rgb(0x4338ca) });
        c.primary_active = hsla(if dark { rgb(0x5254e0) } else { rgb(0x3730a3) });
        c.primary_foreground = hsla(white);
        c.ring = hsla(accent());
        c.border = hsla(border());
        c.input = hsla(border_strong());
        c.secondary = hsla(surface_alt());
        c.secondary_hover = hsla(if dark { rgb(0x272c3b) } else { rgb(0xe6e9f3) });
        c.secondary_active = hsla(if dark { rgb(0x2f3547) } else { rgb(0xdadfed) });
        c.secondary_foreground = hsla(ink());
        c.muted = hsla(surface_alt());
        c.muted_foreground = hsla(muted());
        c.accent = hsla(surface_alt());
        c.accent_foreground = hsla(ink());
        c.popover = hsla(surface());
        c.popover_foreground = hsla(ink());
        c.danger = hsla(if dark { rgb(0xdc2626) } else { danger() });
        c.danger_foreground = hsla(white);

        // Buttons read their own colour group rather than `primary`.
        let primary = if dark { rgb(0x6366f1) } else { accent() };
        let primary_hover = if dark { rgb(0x7c7ff5) } else { rgb(0x4338ca) };
        let primary_active = if dark { rgb(0x5254e0) } else { rgb(0x3730a3) };
        c.button_primary = hsla(primary);
        c.button_primary_hover = hsla(primary_hover);
        c.button_primary_active = hsla(primary_active);
        c.button_primary_foreground = hsla(white);
        let sec = surface_alt();
        let sec_hover = if dark { rgb(0x272c3b) } else { rgb(0xe6e9f3) };
        let sec_active = if dark { rgb(0x2f3547) } else { rgb(0xdadfed) };
        c.button_secondary = hsla(sec);
        c.button_secondary_hover = hsla(sec_hover);
        c.button_secondary_active = hsla(sec_active);
        c.button_secondary_foreground = hsla(ink());
        c.button = hsla(sec);
        c.button_hover = hsla(sec_hover);
        c.button_active = hsla(sec_active);
        c.button_foreground = hsla(ink());
        c.button_danger = hsla(if dark { rgb(0xdc2626) } else { danger() });
        c.button_danger_foreground = hsla(white);
        c.switch = hsla(primary);
        c.switch_thumb = hsla(white);
        c.progress_bar = hsla(primary);
        c.slider_bar = hsla(primary);
        c.tab_active = hsla(surface());
    });
}
