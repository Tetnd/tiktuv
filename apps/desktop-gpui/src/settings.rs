//! Transcription settings panel, model picker and first-run model download.

use std::rc::Rc;
use std::time::Duration;

use gpui_kit::base::StyledExt as _;
use gpui_kit::component::button::*;
use gpui_kit::component::switch::Switch;
use gpui_kit::component::{ActiveTheme, Disableable as _};
use gpui_kit::prelude::FluentBuilder as _;
use gpui_kit::{
    AnyElement, Context, InteractiveElement as _, IntoElement, ParentElement as _,
    StatefulInteractiveElement as _, Styled as _, div, px,
};

use crate::app::{App, ToastTone};
use crate::format::*;
use crate::ui::{self, Ic, Tone};
use crate::rtl_text::rtl_paragraph;
use crate::models::*;

impl App {
    // -- settings panel ----------------------------------------------------
    pub fn render_settings_panel(&self, cx: &mut Context<Self>) -> AnyElement {
        let open = self.settings_open;
        let summary = format!(
            "{} · {} · {}",
            ["עברית", "זיהוי אוטומטי", "אנגלית"][language_index(self.settings.language)],
            ["מעבד אוטומטי", "כרטיס מסך", "מעבד (CPU)"][device_index(self.settings.device)],
            ["מהיר", "מאוזן", "דיוק מרבי"][quality_index(self.settings.quality)],
        );

        ui::card()
            .child(
                ui::rtl_row()
                    .id("toggle-settings")
                    .gap_3()
                    .p_5()
                    .cursor_pointer()
                    .rounded_xl()
                    .hover(|s| s.bg(ui::surface_alt()))
                    .on_click(cx.listener(|this, _, _, cx| {
                        this.settings_open = !this.settings_open;
                        cx.notify();
                    }))
                    .child(div().flex_1().child(ui::section_header(
                        Ic::Sliders,
                        Tone::Neutral,
                        "הגדרות תמלול",
                        Some(&summary),
                    )))
                    .child(ui::icon(
                        if open { Ic::ChevronDown } else { Ic::ChevronLeft },
                        18.0,
                        ui::muted(),
                    )),
            )
            .when(open, |this| {
                this.child(
                    div()
                        .border_t_1()
                        .border_color(ui::divider())
                        .p_5()
                        .child(self.settings_body(cx)),
                )
            })
            .into_any_element()
    }

    fn settings_body(&self, cx: &mut Context<Self>) -> AnyElement {
        let language = self.settings.language;
        let device = self.settings.device;
        let quality = self.settings.quality;
        let timestamps = self.settings.timestamps;
        let fillers = self.settings.preserve_filler_words;
        let punctuation = self.settings.normalize_punctuation;
        let auto_download = self.settings.auto_download_model;
        let proofreading = self.settings.enable_proofreading;
        let analysis = self.settings.enable_analysis;

        div()
            .v_flex()
            .gap_5()
            .child(setting_row(
                "שפה",
                "השפה שמדברים בהקלטה",
                segmented(
                    "language",
                    &["עברית", "זיהוי אוטומטי", "אנגלית"],
                    language_index(language),
                    cx,
                    |this, idx| this.settings.language = language_from_index(idx),
                ),
            ))
            .child(setting_row(
                "מנוע תמלול",
                "המודל שיבצע את התמלול",
                self.model_chips_aligned(false, cx),
            ))
            .child(setting_row(
                "מעבד",
                "היכן לבצע את העיבוד",
                segmented(
                    "device",
                    &["אוטומטי", "כרטיס מסך (GPU)", "מעבד (CPU)"],
                    device_index(device),
                    cx,
                    |this, idx| this.settings.device = device_from_index(idx),
                ),
            ))
            .child(setting_row(
                "איכות",
                "מהירות מול דיוק",
                segmented(
                    "quality",
                    &["מהיר", "מאוזן", "דיוק מרבי"],
                    quality_index(quality),
                    cx,
                    |this, idx| this.settings.quality = quality_from_index(idx),
                ),
            ))
            .child(setting_row(
                "חותמות זמן",
                "תיעוד זמנים בתמליל",
                segmented(
                    "timestamps",
                    &["ללא", "לפי מקטע", "לפי מילה"],
                    timestamps_index(timestamps),
                    cx,
                    |this, idx| this.settings.timestamps = timestamps_from_index(idx),
                ),
            ))
            .child(div().h_px().bg(ui::divider()))
            .child(
                div()
                    .flex()
                    .flex_row_reverse()
                    .flex_wrap()
                    .gap_y_4()
                    .child(toggle(
                        "fillers",
                        "שמירת מילות מילוי",
                        "משאיר ״אה״, ״אממ״ וכדומה בתמליל",
                        fillers,
                        cx,
                        |this, v| this.settings.preserve_filler_words = v,
                    ))
                    .child(toggle(
                        "punctuation",
                        "תיקון פיסוק ורווחים",
                        "מסדר רווחים וסימני פיסוק אחרי התמלול",
                        punctuation,
                        cx,
                        |this, v| this.settings.normalize_punctuation = v,
                    ))
                    .child(toggle(
                        "auto-download",
                        "הורדת מודל אוטומטית",
                        "מוריד את המודל בשימוש ראשון אם הוא חסר",
                        auto_download,
                        cx,
                        |this, v| this.settings.auto_download_model = v,
                    ))
                    .child(toggle(
                        "analysis",
                        "ניתוח AI אוטומטי",
                        "תקציר, נקודות מפתח, משימות וציטוטים מוכנים עם סיום התמלול (DeepSeek)",
                        analysis,
                        cx,
                        |this, v| this.settings.enable_analysis = v,
                    ))
                    .child(toggle(
                        "proofreading",
                        "הגהה חכמה בעזרת DeepSeek",
                        "דורש מפתח API; התמליל נשלח ל-DeepSeek",
                        proofreading,
                        cx,
                        |this, v| this.settings.enable_proofreading = v,
                    )),
            )
            .into_any_element()
    }

    // -- model download banner --------------------------------------------
    pub fn model_banner(&self, cx: &mut Context<Self>) -> AnyElement {
        let Some(model) = self.models.iter().find(|m| m.id == self.settings.model_id) else {
            return div().into_any_element();
        };
        let downloading = self
            .model_download
            .as_ref()
            .map(|s| s.state == "downloading")
            .unwrap_or(false);
        if model.downloaded && !downloading {
            return div().into_any_element();
        }

        let label = model.label_he.clone();
        let approx = model.approx_download_bytes;
        let cache = model.cache_path.clone();
        let model_id = model.id.clone();
        let status = self.model_download.clone();

        ui::notice(Ic::Download, Tone::Accent)
            .p_5()
            .gap_4()
            .rounded_xl()
            .child(
                div()
                    .flex_1()
                    .min_w_0()
                    .v_flex()
                    .gap_2()
                    .child(
                        div()
                            .text_base()
                            .font_semibold()
                            .text_color(ui::ink())
                            .child(rtl(if downloading {
                                "מוריד את מודל התמלול…"
                            } else {
                                "נדרש מודל תמלול"
                            })),
                    )
                    .child(
                        div()
                            .text_sm()
                            .text_color(ui::subtle())
                            .child(rtl(&format!("{label} — {}", format_bytes(approx)))),
                    )
                    .child(
                        div()
                            .text_xs()
                            .text_color(ui::muted())
                            .child(rtl_paragraph(format!("נשמר בתיקייה: {}", latin(&cache)))),
                    )
                    .child(
                        div().text_xs().text_color(ui::muted()).child(rtl_paragraph(
                            "זו הפעולה היחידה ביישום שמשתמשת באינטרנט. לאחר ההורדה הכול פועל מקומית.",
                        )),
                    )
                    .when(downloading, |this| {
                        let p = status.as_ref().map(|s| s.percent).unwrap_or(0.0);
                        this.child(ui::progress_bar(p as f32, Tone::Accent)).child(
                            div().text_xs().text_color(ui::subtle()).child(rtl(&format!(
                                "{} · {} מתוך {}",
                                format_percent(p),
                                format_bytes(status.as_ref().map(|s| s.downloaded_bytes).unwrap_or(0)),
                                format_bytes(status.as_ref().map(|s| s.total_bytes).unwrap_or(0))
                            ))),
                        )
                    })
                    .child(div().pt_1().child(if downloading {
                        Button::new("cancel-dl")
                            .secondary()
                            .label(rtl("עצור"))
                            .on_click(cx.listener(|this, _, _, cx| this.cancel_model_download(cx)))
                            .into_any_element()
                    } else {
                        Button::new("start-dl")
                            .primary()
                            .icon(ui::icon(Ic::Download, 16.0, gpui_kit::rgb(0xffffff)))
                            .label(rtl("הורדה"))
                            .on_click(cx.listener(move |this, _, _, cx| {
                                this.start_model_download(model_id.clone(), cx)
                            }))
                            .into_any_element()
                    })),
            )
            .into_any_element()
    }

    pub fn start_model_download(&self, model_id: String, cx: &mut Context<Self>) {
        let api = self.api.clone();
        let rt = self.api.rt().clone();
        let (tx, rx) = async_channel::unbounded::<ModelDownloadStatus>();

        rt.spawn(async move {
            let _ = api.download_model(&model_id).await;
            loop {
                match api.download_status().await {
                    Ok(status) => {
                        let state = status.state.clone();
                        if tx.send(status).await.is_err() {
                            break;
                        }
                        if matches!(state.as_str(), "ready" | "failed" | "cancelled") {
                            break;
                        }
                    }
                    Err(_) => break,
                }
                tokio::time::sleep(Duration::from_millis(800)).await;
            }
        });

        cx.spawn(async move |this, cx| {
            while let Ok(status) = rx.recv().await {
                let done = matches!(status.state.as_str(), "ready" | "failed" | "cancelled");
                let _ = this.update(cx, |this, cx| {
                    this.model_download = Some(status);
                    cx.notify();
                    if done {
                        this.refresh_models(cx);
                        this.toast(
                            ToastTone::Success,
                            "המודל הורד והתמלול מוכן לשימוש.".to_string(),
                            None,
                        );
                    }
                });
            }
        })
        .detach();
    }

    pub fn cancel_model_download(&self, cx: &mut Context<Self>) {
        let api = self.api.clone();
        cx.spawn(async move |this, cx| {
            if let Ok(status) = api.cancel_download().await {
                let _ = this.update(cx, |this, cx| {
                    this.model_download = Some(status);
                    cx.notify();
                });
            }
        })
        .detach();
    }
}

/// A settings row: label and hint on the right, the control filling the rest.
fn setting_row(label: &'static str, hint: &'static str, control: AnyElement) -> AnyElement {
    ui::rtl_row()
        .items_start()
        .gap_6()
        .child(
            div()
                .w(px(170.))
                .flex_shrink_0()
                .v_flex()
                .child(div().text_sm().font_medium().text_color(ui::ink()).child(rtl(label)))
                .child(div().text_xs().text_color(ui::muted()).child(rtl(hint))),
        )
        .child(div().flex_1().min_w_0().flex().flex_row_reverse().child(control))
        .into_any_element()
}

/// A row of mutually exclusive options in a single rounded track.
pub(crate) fn segmented(
    id: &'static str,
    options: &[&'static str],
    selected: usize,
    cx: &mut Context<App>,
    apply: impl Fn(&mut App, usize) + 'static,
) -> AnyElement {
    let apply = Rc::new(apply);
    let mut items: Vec<AnyElement> = Vec::new();
    for (index, label) in options.iter().enumerate() {
        let on = index == selected;
        let apply = apply.clone();
        items.push(
            div()
                .id((id, index))
                .px_4()
                .h_8()
                .flex()
                .items_center()
                .justify_center()
                .rounded_md()
                .cursor_pointer()
                .text_sm()
                .whitespace_nowrap()
                .when(on, |this| {
                    this.bg(ui::surface())
                        .shadow_sm()
                        .font_semibold()
                        .text_color(ui::accent_strong())
                })
                .when(!on, |this| this.text_color(ui::muted()).hover(|s| s.text_color(ui::ink())))
                .on_click(cx.listener(move |this, _, _, cx| {
                    apply(this, index);
                    cx.notify();
                }))
                .child(rtl(label))
                .into_any_element(),
        );
    }
    div()
        .flex()
        .flex_row_reverse()
        .p_1()
        .gap_1()
        .rounded_lg()
        .bg(ui::surface_alt())
        .children(items)
        .into_any_element()
}

fn toggle(
    id: &'static str,
    label: &'static str,
    hint: &'static str,
    checked: bool,
    cx: &mut Context<App>,
    apply: impl Fn(&mut App, bool) + 'static,
) -> AnyElement {
    div()
        .w_1_2()
        .px_2()
        .v_flex()
        .gap_0p5()
        .child(
            Switch::new(id)
                .label(rtl(label))
                .checked(checked)
                .on_change(cx.listener(move |this, value, _, cx| {
                    apply(this, *value);
                    cx.notify();
                })),
        )
        .child(
            div()
                .pr_11()
                .text_xs()
                .text_color(ui::muted())
                .child(rtl(hint)),
        )
        .into_any_element()
}

fn language_index(l: Language) -> usize {
    match l {
        Language::Hebrew => 0,
        Language::Auto => 1,
        Language::English => 2,
    }
}

fn language_from_index(i: usize) -> Language {
    match i {
        0 => Language::Hebrew,
        1 => Language::Auto,
        _ => Language::English,
    }
}

fn device_index(d: DevicePreference) -> usize {
    match d {
        DevicePreference::Auto => 0,
        DevicePreference::Cuda => 1,
        DevicePreference::Cpu => 2,
    }
}

fn device_from_index(i: usize) -> DevicePreference {
    match i {
        0 => DevicePreference::Auto,
        1 => DevicePreference::Cuda,
        _ => DevicePreference::Cpu,
    }
}

fn quality_index(q: QualityPreset) -> usize {
    match q {
        QualityPreset::Fast => 0,
        QualityPreset::Balanced => 1,
        QualityPreset::Accurate => 2,
    }
}

fn quality_from_index(i: usize) -> QualityPreset {
    match i {
        0 => QualityPreset::Fast,
        1 => QualityPreset::Balanced,
        _ => QualityPreset::Accurate,
    }
}

fn timestamps_index(t: TimestampMode) -> usize {
    match t {
        TimestampMode::None => 0,
        TimestampMode::Segment => 1,
        TimestampMode::Word => 2,
    }
}

fn timestamps_from_index(i: usize) -> TimestampMode {
    match i {
        0 => TimestampMode::None,
        1 => TimestampMode::Segment,
        _ => TimestampMode::Word,
    }
}

fn speaker_method_index(method: &str) -> usize {
    match method {
        "soniox" => 1,
        "deepseek" => 2,
        _ => 0,
    }
}
