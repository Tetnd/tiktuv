//! Transcription settings panel, model picker and first-run model download.

use std::time::Duration;

use gpui_kit::base::StyledExt as _;
use gpui_kit::component::button::*;
use gpui_kit::component::radio::RadioGroup;
use gpui_kit::component::switch::Switch;
use gpui_kit::component::{ActiveTheme, Disableable as _};
use gpui_kit::prelude::FluentBuilder as _;
use gpui_kit::{
    AnyElement, Context, IntoElement, ParentElement as _, Styled as _, Window, div, relative, rgb,
};

use crate::app::{App, ToastTone};
use crate::format::*;
use crate::rtl_text::rtl_paragraph;
use crate::models::*;

impl App {
    // -- settings panel ----------------------------------------------------
    pub fn render_settings_panel(&self, cx: &mut Context<Self>) -> AnyElement {
        let open = self.settings_open;
        let body: AnyElement = if open { self.settings_body(cx) } else { div().into_any_element() };

        div()
            .rounded_lg()
            .border_1()
            .border_color(rgb(0xe5e7eb))
            .bg(cx.theme().background)
            .v_flex()
            .child(
                Button::new("toggle-settings")
                    .ghost()
                    .label(rtl(if open { "הגדרות תמלול — הסתר" } else { "הגדרות תמלול — הצג" }))
                    .on_click(cx.listener(|this, _, _, cx| {
                        this.settings_open = !this.settings_open;
                        cx.notify();
                    })),
            )
            .when(open, |this| {
                this.child(div().border_t_1().border_color(rgb(0xe5e7eb)).p_4().child(body))
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
        let diarization = self.settings.enable_diarization;
        let proofreading = self.settings.enable_proofreading;

        div()
            .v_flex()
            .gap_3()
            .child(field(
                "שפה",
                RadioGroup::new("language")
                    .children([rtl("עברית"), rtl("זיהוי אוטומטי"), rtl("אנגלית")])
                    .selected_index(Some(language_index(language)))
                    .on_change(cx.listener(|this, idx, _, cx| {
                        this.settings.language = language_from_index(*idx);
                        cx.notify();
                    }))
                    .into_any_element(),
            ))
            .child(field("מנוע תמלול", self.model_radio("model-setting", cx)))
            .child(field(
                "מעבד",
                RadioGroup::new("device")
                    .children([rtl("אוטומטי"), rtl("כרטיס מסך (GPU)"), rtl("מעבד (CPU)")])
                    .selected_index(Some(device_index(device)))
                    .on_change(cx.listener(|this, idx, _, cx| {
                        this.settings.device = device_from_index(*idx);
                        cx.notify();
                    }))
                    .into_any_element(),
            ))
            .child(field(
                "איכות",
                RadioGroup::new("quality")
                    .children([rtl("מהיר"), rtl("מאוזן"), rtl("דיוק מרבי")])
                    .selected_index(Some(quality_index(quality)))
                    .on_change(cx.listener(|this, idx, _, cx| {
                        this.settings.quality = quality_from_index(*idx);
                        cx.notify();
                    }))
                    .into_any_element(),
            ))
            .child(field(
                "חותמות זמן",
                RadioGroup::new("timestamps")
                    .children([rtl("ללא"), rtl("לפי מקטע"), rtl("לפי מילה")])
                    .selected_index(Some(timestamps_index(timestamps)))
                    .on_change(cx.listener(|this, idx, _, cx| {
                        this.settings.timestamps = timestamps_from_index(*idx);
                        cx.notify();
                    }))
                    .into_any_element(),
            ))
            .child(toggle(
                "fillers",
                "שמירת מילות מילוי",
                fillers,
                cx,
                |this, v| this.settings.preserve_filler_words = v,
            ))
            .child(toggle(
                "punctuation",
                "תיקון פיסוק ורווחים",
                punctuation,
                cx,
                |this, v| this.settings.normalize_punctuation = v,
            ))
            .child(toggle(
                "auto-download",
                "הורדת מודל אוטומטית",
                auto_download,
                cx,
                |this, v| this.settings.auto_download_model = v,
            ))
            .child(toggle(
                "diarization",
                "זיהוי דוברים (ניסיוני)",
                diarization,
                cx,
                |this, v| this.settings.enable_diarization = v,
            ))
            .child(toggle(
                "proofreading",
                "הגהה חכמה בעזרת DeepSeek (דורש מפתח API)",
                proofreading,
                cx,
                |this, v| this.settings.enable_proofreading = v,
            ))
            .into_any_element()
    }

    // -- model picker ------------------------------------------------------
    pub fn model_radio(&self, id: &'static str, cx: &mut Context<Self>) -> AnyElement {
        if self.models.is_empty() {
            return div()
                .text_sm()
                .text_color(rgb(0x6b7280))
                .child(rtl(&latin(&self.settings.model_id)))
                .into_any_element();
        }
        let labels: Vec<String> = self
            .models
            .iter()
            .map(|m| {
                let label = if !m.is_cloud && !m.downloaded {
                    format!("{} — טרם הורד", m.label_he)
                } else {
                    m.label_he.clone()
                };
                rtl(&label)
            })
            .collect();
        let selected = self.models.iter().position(|m| m.id == self.settings.model_id);
        let ids: Vec<String> = self.models.iter().map(|m| m.id.clone()).collect();

        RadioGroup::new(id)
            .children(labels)
            .selected_index(selected)
            .on_change(cx.listener(move |this: &mut App, idx: &usize, _: &mut Window, cx: &mut Context<App>| {
                if let Some(model_id) = ids.get(*idx) {
                    this.settings.model_id = model_id.clone();
                    cx.notify();
                }
            }))
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

        div()
            .rounded_lg()
            .border_1()
            .border_color(rgb(0xc7d2fe))
            .bg(rgb(0xeef2ff))
            .p_4()
            .v_flex()
            .gap_2()
            .child(
                div()
                    .text_base()
                    .child(rtl(if downloading { "מוריד את מודל התמלול…" } else { "נדרש מודל תמלול" })),
            )
            .child(
                div()
                    .text_sm()
                    .text_color(rgb(0x4b5563))
                    .child(rtl(&format!("{label} — {}", format_bytes(approx)))),
            )
            .child(
                div()
                    .text_xs()
                    .text_color(rgb(0x6b7280))
                    .child(rtl_paragraph(format!("נשמר בתיקייה: {}", latin(&cache)))),
            )
            .child(
                div()
                    .text_xs()
                    .text_color(rgb(0x6b7280))
                    .child(rtl_paragraph("זו הפעולה היחידה ביישום שמשתמשת באינטרנט. לאחר ההורדה הכול פועל מקומית.")),
            )
            .when(downloading, |this| {
                let p = status.as_ref().map(|s| s.percent).unwrap_or(0.0);
                this.child(
                    // Fills from the right, like the rest of the interface.
                    div()
                        .flex()
                        .flex_row_reverse()
                        .w_full()
                        .h_2()
                        .rounded_full()
                        .bg(rgb(0xe5e7eb))
                        .child(
                            div()
                                .h_full()
                                .rounded_full()
                                .bg(rgb(0x4f46e5))
                                .w(relative(p.clamp(0.0, 1.0) as f32)),
                        ),
                )
                .child(
                    div().text_xs().text_color(rgb(0x4b5563)).child(rtl(&format!(
                        "{} · {} מתוך {}",
                        format_percent(p),
                        format_bytes(status.as_ref().map(|s| s.downloaded_bytes).unwrap_or(0)),
                        format_bytes(status.as_ref().map(|s| s.total_bytes).unwrap_or(0))
                    ))),
                )
            })
            .child(if downloading {
                Button::new("cancel-dl")
                    .secondary()
                    .label(rtl("עצור"))
                    .on_click(cx.listener(|this, _, _, cx| this.cancel_model_download(cx)))
                    .into_any_element()
            } else {
                Button::new("start-dl")
                    .primary()
                    .label(rtl("הורדה"))
                    .on_click(cx.listener(move |this, _, _, cx| {
                        this.start_model_download(model_id.clone(), cx)
                    }))
                    .into_any_element()
            })
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

fn field(label: &'static str, element: AnyElement) -> AnyElement {
    div()
        .v_flex()
        .gap_1()
        .child(div().text_sm().text_color(rgb(0x4b5563)).child(rtl(&label)))
        .child(element)
        .into_any_element()
}

fn toggle(
    id: &'static str,
    label: &'static str,
    checked: bool,
    cx: &mut Context<App>,
    apply: impl Fn(&mut App, bool) + 'static,
) -> AnyElement {
    Switch::new(id)
        .label(rtl(label))
        .checked(checked)
        .on_change(cx.listener(move |this, value, _, cx| {
            apply(this, *value);
            cx.notify();
        }))
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
