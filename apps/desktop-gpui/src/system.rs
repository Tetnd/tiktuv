//! System / models / storage screen: preflight checks, model management and
//! storage/privacy controls.

use gpui_kit::base::StyledExt as _;
use gpui_kit::component::button::*;
use gpui_kit::component::input::{Input, InputState};
use gpui_kit::component::ActiveTheme;
use gpui_kit::prelude::FluentBuilder as _;
use gpui_kit::{
    AnyElement, Context, IntoElement, ParentElement as _, Styled as _, div, rgb,
};

use crate::api::ApiError;
use crate::app::{App, ToastTone};
use crate::format::*;
use crate::rtl_text::rtl_paragraph;
use crate::models::*;

impl App {
    pub fn refresh_system(&self, cx: &mut Context<Self>) {
        let api = self.api.clone();
        cx.spawn(async move |this, cx| {
            let system = api.system().await;
            let soniox = api.soniox_status().await;
            let deepseek = api.deepseek_status().await;
            let _ = this.update(cx, |this, cx| {
                if let Ok(s) = system {
                    this.system = Some(s);
                }
                if let Ok(s) = soniox {
                    this.soniox_status = Some(s);
                }
                if let Ok(s) = deepseek {
                    this.deepseek_status = Some(s);
                }
                cx.notify();
            });
        })
        .detach();
    }

    pub fn render_system(&self, cx: &mut Context<Self>) -> AnyElement {
        let soniox = self.key_panel(KeyKind::Soniox, cx).into_any_element();
        let deepseek = self.key_panel(KeyKind::DeepSeek, cx).into_any_element();
        let preflight = self.preflight_panel(cx).into_any_element();
        let models = self.models_panel(cx).into_any_element();
        let storage = self.storage_panel(cx).into_any_element();
        div()
            .w_full()
            .p_6()
            .v_flex()
            .gap_4()
            .child(soniox)
            .child(deepseek)
            .child(preflight)
            .child(models)
            .child(storage)
            .into_any_element()
    }

    fn preflight_panel(&self, cx: &mut Context<Self>) -> impl IntoElement {
        let card = div()
            .rounded_lg()
            .border_1()
            .border_color(rgb(0xe5e7eb))
            .bg(cx.theme().background)
            .p_4()
            .v_flex()
            .gap_2();

        let Some(system) = &self.system else {
            return card.child(
                div()
                    .text_sm()
                    .text_color(rgb(0x6b7280))
                    .child(rtl("בודק את סביבת העבודה…")),
            );
        };

        let blocking = system
            .checks
            .iter()
            .filter(|c| c.status == CheckStatus::Error)
            .count();

        let mut rows: Vec<AnyElement> = Vec::new();
        for check in &system.checks {
            rows.push(check_row(check).into_any_element());
        }

        card.child(
            div()
                .flex()
                .flex_row_reverse()
                .items_center()
                .justify_between()
                .child(div().text_base().child(rtl("בדיקת מערכת")))
                .child(
                    Button::new("refresh-system")
                        .ghost()
                        .label(rtl("רענון"))
                        .on_click(cx.listener(|this, _, _, cx| this.refresh_system(cx))),
                ),
        )
        .when(blocking > 0, |this| {
            this.child(
                div()
                    .rounded_lg()
                    .border_1()
                    .border_color(rgb(0xfecaca))
                    .bg(rgb(0xfef2f2))
                    .p_3()
                    .child(
                        div()
                            .text_sm()
                            .text_color(rgb(0x991b1b))
                            .child(rtl(&format!("יש {blocking} בעיות שחוסמות תמלול."))),
                    ),
            )
        })
        .child(div().v_flex().children(rows))
        .child(
            div()
                .flex()
                .flex_row_reverse()
                .flex_wrap()
                .gap_3()
                .text_xs()
                .text_color(rgb(0x6b7280))
                // Version strings mix digits and Latin ("2.5.1+cpu",
                // "8.1.2-full_build-www.gyan.dev"). Without an isolate the bidi
                // algorithm treats the digits as a separate run and swaps the
                // halves — "cpu+2.5.1". `latin` keeps each value in one piece.
                .child(rtl(&format!("גרסה: {}", latin(&system.app_version))))
                .child(rtl(&format!("מערכת: {}", latin(&system.platform))))
                .child(rtl(&format!("Python: {}", latin(&system.python_version))))
                .child(rtl(&format!(
                    "FFmpeg: {}",
                    system
                        .ffmpeg_version
                        .as_deref()
                        .map(latin)
                        .unwrap_or_else(|| "לא נמצא".into())
                )))
                .child(rtl(&format!(
                    "PyTorch: {}",
                    system
                        .torch_version
                        .as_deref()
                        .map(latin)
                        .unwrap_or_else(|| "לא מותקן".into())
                )))
                .child(rtl(&format!(
                    "CUDA: {}",
                    system
                        .cuda_version
                        .as_deref()
                        .map(latin)
                        .unwrap_or_else(|| "לא זמין".into())
                ))),
        )
    }

    fn models_panel(&self, cx: &mut Context<Self>) -> impl IntoElement {
        let mut rows: Vec<AnyElement> = Vec::new();
        for model in &self.models {
            rows.push(self.model_row(model, cx).into_any_element());
        }
        div()
            .rounded_lg()
            .border_1()
            .border_color(rgb(0xe5e7eb))
            .bg(cx.theme().background)
            .p_4()
            .v_flex()
            .gap_2()
            .child(div().text_base().child(rtl("מודלים")))
            .when(self.models.is_empty(), |this| {
                this.child(
                    div()
                        .text_sm()
                        .text_color(rgb(0x6b7280))
                        .child(rtl("לא זוהו מודלים. בדקו את הגדרות המערכת.")),
                )
            })
            .when(!self.models.is_empty(), |this| {
                this.child(div().v_flex().children(rows))
            })
    }

    fn model_row(&self, model: &ModelInfo, cx: &mut Context<Self>) -> impl IntoElement {
        let id_download = model.id.clone();
        let id_delete = model.id.clone();

        div()
            .flex()
            .flex_row_reverse()
            .items_start()
            .gap_3()
            .py_2()
            .border_b_1()
            .border_color(rgb(0xf3f4f6))
            .child(
                div()
                    .flex_1()
                    .min_w_0()
                    .v_flex()
                    .child(div().text_sm().child(rtl(&model.label_he)))
                    .child(div().text_xs().text_color(rgb(0x9ca3af)).child(rtl(&latin(&model.id))))
                    .when(!model.notes_he.is_empty(), |this| {
                        this.child(
                            div()
                                .text_xs()
                                .text_color(rgb(0x6b7280))
                                .child(rtl_paragraph(&model.notes_he)),
                        )
                    })
                    .child(
                        div()
                            .text_xs()
                            .text_color(rgb(0x6b7280))
                            .child(rtl_paragraph(format!(
                                "גודל הורדה: כ-{} · רישיון: {}",
                                format_bytes(model.approx_download_bytes),
                                model.license
                            ))),
                    ),
            )
            .child(
                div()
                    .flex()
                    .flex_row_reverse()
                    .gap_2()
                    .when(model.downloaded, |this| {
                        this.child(
                            Button::new(format!("del-model-{id_delete}"))
                                .danger()
                                .label(rtl("מחיקה"))
                                .on_click(cx.listener(move |this, _, _, cx| {
                                    this.delete_model(id_delete.clone(), cx)
                                })),
                        )
                    })
                    .when(!model.downloaded, |this| {
                        this.child(
                            Button::new(format!("dl-model-{id_download}"))
                                .secondary()
                                .label(rtl("הורדה"))
                                .on_click(cx.listener(move |this, _, _, cx| {
                                    this.download_model(id_download.clone(), cx)
                                })),
                        )
                    }),
            )
    }

    fn storage_panel(&self, cx: &mut Context<Self>) -> impl IntoElement {
        let Some(storage) = self.system.as_ref().and_then(|s| s.storage.clone()) else {
            return div();
        };
        div()
            .rounded_lg()
            .border_1()
            .border_color(rgb(0xe5e7eb))
            .bg(cx.theme().background)
            .p_4()
            .v_flex()
            .gap_2()
            .child(div().text_base().child(rtl("אחסון ופרטיות")))
            .child(
                div()
                    .text_sm()
                    .text_color(rgb(0x6b7280))
                    .child(rtl_paragraph("כל הנתונים נשמרים במחשב שלכם בלבד. אין העלאה של הקלטות ואין איסוף נתוני שימוש — למעט הורדת מודל שאתם יוזמים במפורש.")),
            )
            .child(
                div()
                    .v_flex()
                    .gap_1()
                    .text_xs()
                    .text_color(rgb(0x6b7280))
                    .child(rtl(&format!("תיקיית הנתונים: {}", latin(&storage.data_dir))))
                    .child(rtl(&format!("תיקיית המודלים: {}", latin(&storage.models_dir))))
                    .child(rtl(&format!("מדיה: {}", format_bytes(storage.media_bytes))))
                    .child(rtl(&format!("תמלילים: {}", format_bytes(storage.transcripts_bytes))))
                    .child(rtl(&format!("ייצוא: {}", format_bytes(storage.exports_bytes))))
                    .child(rtl(&format!("קבצים זמניים: {}", format_bytes(storage.temp_bytes))))
                    .child(rtl(&format!("מודלים: {}", format_bytes(storage.models_bytes))))
                    .child(rtl(&format!("פנוי בדיסק: {}", format_bytes(storage.free_disk_bytes)))),
            )
            .child(
                Button::new("cleanup-temp")
                    .secondary()
                    .label(rtl("מחיקת קבצים זמניים"))
                    .on_click(cx.listener(|this, _, _, cx| this.cleanup_temp(cx))),
            )
    }

    fn download_model(&self, id: String, cx: &mut Context<Self>) {
        let api = self.api.clone();
        cx.spawn(async move |this, cx| {
            let result = api.download_model(&id).await;
            let _ = this.update(cx, |this, cx| {
                match result {
                    Ok(_) => this.toast(
                        ToastTone::Info,
                        "ההורדה החלה ברקע.".to_string(),
                        Some("רעננו כדי לראות את המצב.".to_string()),
                    ),
                    Err(e) => this.toast(
                        ToastTone::Error,
                        "לא ניתן היה להתחיל את ההורדה.".to_string(),
                        Some(e.message),
                    ),
                }
                cx.notify();
            });
        })
        .detach();
    }

    fn delete_model(&self, id: String, cx: &mut Context<Self>) {
        let api = self.api.clone();
        cx.spawn(async move |this, cx| {
            let result = api.delete_model(&id).await;
            let models = api.models().await.unwrap_or_default();
            let _ = this.update(cx, |this, cx| {
                this.models = models;
                match result {
                    Ok(bytes) => this.toast(
                        ToastTone::Success,
                        format!("שוחררו {}.", format_bytes(bytes)),
                        None,
                    ),
                    Err(e) => this.toast(
                        ToastTone::Error,
                        "מחיקת המודל נכשלה.".to_string(),
                        Some(e.message),
                    ),
                }
                cx.notify();
            });
        })
        .detach();
    }

    fn cleanup_temp(&self, cx: &mut Context<Self>) {
        let api = self.api.clone();
        cx.spawn(async move |this, cx| {
            let result = api.cleanup_temp().await;
            let _ = this.update(cx, |this, cx| {
                match result {
                    Ok(bytes) => this.toast(
                        ToastTone::Success,
                        format!("שוחררו {}.", format_bytes(bytes)),
                        None,
                    ),
                    Err(e) => this.toast(
                        ToastTone::Error,
                        "לא ניתן לנקות קבצים זמניים כרגע.".to_string(),
                        Some(e.message),
                    ),
                }
                cx.notify();
            });
        })
        .detach();
    }

    fn key_panel(&self, kind: KeyKind, cx: &mut Context<Self>) -> AnyElement {
        let (title, desc, configured, masked, key_state) = match kind {
            KeyKind::Soniox => (
                "Soniox (תמלול בענן)",
                "תמלול עברי מדויק דרך שירות הענן של Soniox. האודיו נשלח לשרתים של Soniox.",
                self.soniox_status.as_ref().map(|s| s.configured).unwrap_or(false),
                self.soniox_status.as_ref().map(|s| s.masked_key.clone()),
                self.soniox_key.clone(),
            ),
            KeyKind::DeepSeek => (
                "DeepSeek (ניתוח AI)",
                "מנוע ה-AI שמפיק את התקציר, נקודות המפתח, המשימות, הישויות וניתוח הדוברים. התמליל נשלח ל-DeepSeek רק בעת יצירת ניתוח.",
                self.deepseek_status.as_ref().map(|s| s.configured).unwrap_or(false),
                self.deepseek_status.as_ref().map(|s| s.masked_key.clone()),
                self.deepseek_key.clone(),
            ),
        };
        let save_id = if kind == KeyKind::Soniox { "save-soniox" } else { "save-deepseek" };
        let clear_id = if kind == KeyKind::Soniox { "clear-soniox" } else { "clear-deepseek" };

        div()
            .rounded_lg()
            .border_1()
            .border_color(rgb(0xe5e7eb))
            .bg(cx.theme().background)
            .p_4()
            .v_flex()
            .gap_2()
            .child(div().text_base().child(rtl(title)))
            .child(div().text_sm().text_color(rgb(0x6b7280)).child(rtl_paragraph(desc)))
            .when(configured, |this| {
                this.child(
                    div()
                        .flex()
                        .flex_row_reverse()
                        .items_center()
                        .gap_3()
                        .child(div().text_sm().child(rtl(&format!("מפתח מוגדר: {}", latin(&masked.unwrap_or_default())))))
                        .child(
                            Button::new(clear_id)
                                .ghost()
                                .label(rtl("הסרת מפתח"))
                                .on_click(cx.listener(move |this, _, _, cx| this.clear_api_key(kind, cx))),
                        ),
                )
            })
            .when(!configured, |this| {
                this.child(Input::new(&key_state)).child(
                    Button::new(save_id)
                        .primary()
                        .label(rtl("שמירה"))
                        .on_click(cx.listener(move |this, _, _, cx| this.save_api_key(kind, cx))),
                )
            })
            .child(
                div()
                    .text_xs()
                    .text_color(rgb(0x9ca3af))
                    .child(rtl("המפתח נשמר מקומית בתיקיית הנתונים של היישום בלבד.")),
            )
            .into_any_element()
    }

    fn save_api_key(&mut self, kind: KeyKind, cx: &mut Context<Self>) {
        let key = match kind {
            KeyKind::Soniox => self.soniox_key.read(cx).value().to_string(),
            KeyKind::DeepSeek => self.deepseek_key.read(cx).value().to_string(),
        };
        let key = key.trim().to_string();
        if key.is_empty() {
            return;
        }
        let api = self.api.clone();
        cx.spawn(async move |this, cx| {
            let result: Result<(), ApiError> = match kind {
                KeyKind::Soniox => api.set_soniox_key(&key).await.map(|_| ()),
                KeyKind::DeepSeek => api.set_deepseek_key(&key).await.map(|_| ()),
            };
            let _ = this.update(cx, |this, cx| {
                match result {
                    Ok(()) => {
                        this.toast(ToastTone::Success, "מפתח ה-API נשמר.".to_string(), None);
                        this.refresh_system(cx);
                    }
                    Err(e) => this.toast(
                        ToastTone::Error,
                        "שמירת המפתח נכשלה.".to_string(),
                        Some(e.message),
                    ),
                }
                cx.notify();
            });
        })
        .detach();
    }

    fn clear_api_key(&self, kind: KeyKind, cx: &mut Context<Self>) {
        let api = self.api.clone();
        cx.spawn(async move |this, cx| {
            let result: Result<(), ApiError> = match kind {
                KeyKind::Soniox => api.clear_soniox_key().await.map(|_| ()),
                KeyKind::DeepSeek => api.clear_deepseek_key().await.map(|_| ()),
            };
            let _ = this.update(cx, |this, cx| {
                match result {
                    Ok(()) => {
                        this.toast(ToastTone::Info, "מפתח ה-API הוסר.".to_string(), None);
                        this.refresh_system(cx);
                    }
                    Err(e) => this.toast(
                        ToastTone::Error,
                        "הסרת המפתח נכשלה.".to_string(),
                        Some(e.message),
                    ),
                }
                cx.notify();
            });
        })
        .detach();
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum KeyKind {
    Soniox,
    DeepSeek,
}

fn check_row(check: &PreflightCheck) -> impl IntoElement {
    let (label, color) = match check.status {
        CheckStatus::Ok => ("תקין", rgb(0x059669)),
        CheckStatus::Warning => ("אזהרה", rgb(0xd97706)),
        CheckStatus::Error => ("שגיאה", rgb(0xdc2626)),
    };
    div()
        .flex()
        .flex_row_reverse()
        .items_start()
        .gap_3()
        .py_2()
        .child(
            div()
                .px_2()
                .py_0p5()
                .rounded_full()
                .bg(color.opacity(0.12))
                .text_color(color)
                .text_xs()
                .whitespace_nowrap()
                .child(rtl(&label)),
        )
        .child(
            div()
                .flex_1()
                .min_w_0()
                .v_flex()
                .child(div().text_sm().child(rtl(&check.label_he)))
                .when(!check.detail.is_empty(), |this| {
                    this.child(
                        div().text_xs().text_color(rgb(0x6b7280)).child(rtl_paragraph(&check.detail)),
                    )
                })
                .when(!check.remedy.is_empty(), |this| {
                    this.child(
                        div().text_xs().text_color(rgb(0x9ca3af)).child(rtl_paragraph(&check.remedy)),
                    )
                }),
        )
}
