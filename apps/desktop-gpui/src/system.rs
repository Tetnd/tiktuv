//! System / models / storage screen: preflight checks, model management and
//! storage/privacy controls.

use gpui_kit::base::StyledExt as _;
use gpui_kit::component::button::*;
use gpui_kit::prelude::FluentBuilder as _;
use gpui_kit::{
    AnyElement, Context, IntoElement, ParentElement as _, Styled as _, div, px,
};

use crate::api::ApiError;
use crate::app::{App, ToastTone};
use crate::format::*;
use crate::ui::{self, Ic, Tone};
use crate::rtl_text::rtl_paragraph;
use crate::models::*;

impl App {
    pub fn refresh_system(&self, cx: &mut Context<Self>) {
        let api = self.api.clone();
        cx.spawn(async move |this, cx| {
            let system = api.system().await;
            let soniox = api.soniox_status().await;
            let deepseek = api.deepseek_status().await;
            let balance = match &deepseek {
                Ok(s) if s.configured => api.deepseek_balance().await.ok(),
                _ => None,
            };
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
                this.deepseek_balance = balance;
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
            .p_8()
            .v_flex()
            .gap_6()
            .child(ui::page_title(
                "מערכת ומודלים",
                "בדיקות סביבה, ניהול מודלים, מפתחות API ואחסון",
            ))
            .child(preflight)
            .child(models)
            .child(
                div()
                    .flex()
                    .flex_row_reverse()
                    .flex_wrap()
                    .gap_6()
                    .items_start()
                    .child(div().flex_1().min_w(px(420.)).child(soniox))
                    .child(div().flex_1().min_w(px(420.)).child(deepseek)),
            )
            .child(storage)
            .into_any_element()
    }

    fn preflight_panel(&self, cx: &mut Context<Self>) -> impl IntoElement {
        let card = ui::pad_card();

        let Some(system) = &self.system else {
            return card
                .child(ui::section_header(Ic::ShieldCheck, Tone::Accent, "בדיקת מערכת", None))
                .child(
                    div()
                        .text_sm()
                        .text_color(ui::muted())
                        .child(rtl("בודק את סביבת העבודה…")),
                );
        };

        let blocking = system
            .checks
            .iter()
            .filter(|c| c.status == CheckStatus::Error)
            .count();
        let warnings = system
            .checks
            .iter()
            .filter(|c| c.status == CheckStatus::Warning)
            .count();

        let mut rows: Vec<AnyElement> = Vec::new();
        for (i, check) in system.checks.iter().enumerate() {
            rows.push(
                div()
                    .when(i > 0, |this| this.border_t_1().border_color(ui::divider()))
                    .child(check_row(check))
                    .into_any_element(),
            );
        }

        let (header_tone, summary) = if blocking > 0 {
            (Tone::Danger, format!("{blocking} בעיות חוסמות תמלול"))
        } else if warnings > 0 {
            (Tone::Warning, format!("הכול תקין · {warnings} אזהרות"))
        } else {
            (Tone::Success, "הכול תקין והמערכת מוכנה לתמלול".to_string())
        };

        let versions: Vec<(&'static str, String)> = vec![
            ("גרסה", latin(&system.app_version)),
            ("מערכת", latin(&system.platform)),
            ("Python", latin(&system.python_version)),
            (
                "FFmpeg",
                system.ffmpeg_version.as_deref().map(latin).unwrap_or_else(|| "לא נמצא".into()),
            ),
            (
                "PyTorch",
                system.torch_version.as_deref().map(latin).unwrap_or_else(|| "לא מותקן".into()),
            ),
            (
                "CUDA",
                system.cuda_version.as_deref().map(latin).unwrap_or_else(|| "לא זמין".into()),
            ),
        ];
        // Version strings mix digits and Latin ("2.5.1+cpu",
        // "8.1.2-full_build-www.gyan.dev"). Without an isolate the bidi
        // algorithm treats the digits as a separate run and swaps the halves —
        // "cpu+2.5.1". `latin` keeps each value in one piece.
        let chips: Vec<AnyElement> = versions
            .into_iter()
            .map(|(k, v)| {
                ui::rtl_row()
                    .gap_1p5()
                    .px_2p5()
                    .py_1()
                    .rounded_md()
                    .bg(ui::surface_alt())
                    .text_xs()
                    .child(div().text_color(ui::muted()).child(rtl(&format!("{k}:"))))
                    .child(div().font_medium().text_color(ui::subtle()).child(rtl(&v)))
                    .into_any_element()
            })
            .collect();

        card.child(
            ui::rtl_row()
                .justify_between()
                .child(ui::section_header(
                    Ic::ShieldCheck,
                    header_tone,
                    "בדיקת מערכת",
                    Some(&summary),
                ))
                .child(
                    Button::new("refresh-system")
                        .secondary()
                        .compact()
                        .icon(ui::icon(Ic::Refresh, 15.0, ui::subtle()))
                        .label(rtl("רענון"))
                        .on_click(cx.listener(|this, _, _, cx| this.refresh_system(cx))),
                ),
        )
        .when(blocking > 0, |this| {
            this.child(
                ui::notice(Ic::CircleAlert, Tone::Danger).child(
                    div()
                        .text_sm()
                        .text_color(ui::danger_text())
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
                .gap_2()
                .children(chips),
        )
    }

    fn models_panel(&self, cx: &mut Context<Self>) -> impl IntoElement {
        let mut rows: Vec<AnyElement> = Vec::new();
        for (i, model) in self.models.iter().enumerate() {
            rows.push(
                div()
                    .when(i > 0, |this| this.border_t_1().border_color(ui::divider()))
                    .child(self.model_row(model, cx))
                    .into_any_element(),
            );
        }
        ui::pad_card()
            .child(ui::section_header(
                Ic::Cpu,
                Tone::Accent,
                "מודלים",
                Some("מודלי התמלול הזמינים והמקום שהם תופסים"),
            ))
            .when(self.models.is_empty(), |this| {
                this.child(
                    div()
                        .text_sm()
                        .text_color(ui::muted())
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
        let (tile, tone) = if model.is_cloud {
            (Ic::Cloud, Tone::Info)
        } else {
            (Ic::Cpu, Tone::Accent)
        };

        ui::rtl_row()
            .items_start()
            .gap_3()
            .py_4()
            .child(ui::icon_tile(tile, tone))
            .child(
                div()
                    .flex_1()
                    .min_w_0()
                    .v_flex()
                    .gap_0p5()
                    .child(
                        ui::rtl_row()
                            .gap_2()
                            .child(
                                div()
                                    .text_sm()
                                    .font_semibold()
                                    .text_color(ui::ink())
                                    .child(rtl(&model.label_he)),
                            )
                            .child(if model.downloaded {
                                ui::pill(if model.is_cloud { "מוכן" } else { "הורד" }, Tone::Success)
                            } else {
                                ui::pill("טרם הורד", Tone::Warning)
                            }),
                    )
                    .child(div().text_xs().text_color(ui::faint()).child(rtl(&latin(&model.id))))
                    .when(!model.notes_he.is_empty(), |this| {
                        this.child(
                            div()
                                .text_xs()
                                .text_color(ui::muted())
                                .child(rtl_paragraph(&model.notes_he)),
                        )
                    })
                    .child(
                        div()
                            .text_xs()
                            .text_color(ui::muted())
                            .child(rtl_paragraph(format!(
                                "גודל הורדה: כ-{} · רישיון: {}",
                                format_bytes(model.approx_download_bytes),
                                model.license
                            ))),
                    ),
            )
            .child(
                ui::rtl_row()
                    .gap_2()
                    .when(model.downloaded, |this| {
                        this.child(
                            Button::new(format!("del-model-{id_delete}"))
                                .ghost()
                                .compact()
                                .icon(ui::icon(Ic::Trash, 15.0, ui::danger()))
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
                                .compact()
                                .icon(ui::icon(Ic::Download, 15.0, ui::subtle()))
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
        let tiles: Vec<AnyElement> = [
            (Ic::Film, "מדיה", format_bytes(storage.media_bytes)),
            (Ic::FileText, "תמלילים", format_bytes(storage.transcripts_bytes)),
            (Ic::FileDown, "ייצוא", format_bytes(storage.exports_bytes)),
            (Ic::Eraser, "קבצים זמניים", format_bytes(storage.temp_bytes)),
            (Ic::Cpu, "מודלים", format_bytes(storage.models_bytes)),
            (Ic::HardDrive, "פנוי בדיסק", format_bytes(storage.free_disk_bytes)),
        ]
        .into_iter()
        .map(|(ic, label, value)| {
            ui::rtl_row()
                .w(px(190.))
                .flex_grow(1.0)
                .gap_3()
                .p_3()
                .rounded_lg()
                .bg(ui::surface_alt())
                .child(ui::icon(ic, 18.0, ui::muted()))
                .child(
                    div()
                        .v_flex()
                        .child(div().text_sm().font_semibold().text_color(ui::ink()).child(rtl(&value)))
                        .child(div().text_xs().text_color(ui::muted()).child(rtl(label))),
                )
                .into_any_element()
        })
        .collect();

        div().child(
            ui::pad_card()
                .child(ui::section_header(
                    Ic::HardDrive,
                    Tone::Neutral,
                    "אחסון ופרטיות",
                    Some("כל הנתונים נשמרים במחשב שלכם בלבד"),
                ))
                .child(
                    ui::notice(Ic::Lock, Tone::Success).child(
                        div().flex_1().text_sm().text_color(ui::subtle()).child(rtl_paragraph(
                            "אין העלאה של הקלטות ואין איסוף נתוני שימוש — למעט הורדת מודל שאתם יוזמים במפורש.",
                        )),
                    ),
                )
                .child(
                    div()
                        .flex()
                        .flex_row_reverse()
                        .flex_wrap()
                        .gap_2()
                        .children(tiles),
                )
                .child(
                    div()
                        .v_flex()
                        .gap_1()
                        .text_xs()
                        .text_color(ui::muted())
                        .child(rtl(&format!("תיקיית הנתונים: {}", latin(&storage.data_dir))))
                        .child(rtl(&format!("תיקיית המודלים: {}", latin(&storage.models_dir)))),
                )
                .child(
                    div().flex().flex_row_reverse().child(
                        Button::new("cleanup-temp")
                            .secondary()
                            .compact()
                            .icon(ui::icon(Ic::Eraser, 15.0, ui::subtle()))
                            .label(rtl("מחיקת קבצים זמניים"))
                            .on_click(cx.listener(|this, _, _, cx| this.cleanup_temp(cx))),
                    ),
                ),
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
        let (title, desc, ic, configured, masked, key_state) = match kind {
            KeyKind::Soniox => (
                "Soniox (תמלול בענן)",
                "תמלול עברי מדויק דרך שירות הענן של Soniox. האודיו נשלח לשרתים של Soniox.",
                Ic::Cloud,
                self.soniox_status.as_ref().map(|s| s.configured).unwrap_or(false),
                self.soniox_status.as_ref().map(|s| s.masked_key.clone()),
                self.soniox_key.clone(),
            ),
            KeyKind::DeepSeek => (
                "DeepSeek (ניתוח AI)",
                "מנוע ה-AI שמפיק את התקציר, נקודות המפתח, המשימות, הישויות וניתוח הדוברים. התמליל נשלח ל-DeepSeek רק בעת יצירת ניתוח.",
                Ic::Sparkles,
                self.deepseek_status.as_ref().map(|s| s.configured).unwrap_or(false),
                self.deepseek_status.as_ref().map(|s| s.masked_key.clone()),
                self.deepseek_key.clone(),
            ),
        };
        let save_id = if kind == KeyKind::Soniox { "save-soniox" } else { "save-deepseek" };
        let clear_id = if kind == KeyKind::Soniox { "clear-soniox" } else { "clear-deepseek" };

        ui::pad_card()
            .child(
                ui::rtl_row()
                    .justify_between()
                    .child(ui::section_header(
                        ic,
                        Tone::Accent,
                        title,
                        Some(if configured { "מפתח מוגדר" } else { "לא הוגדר מפתח" }),
                    ))
                    .child(if configured {
                        ui::icon_pill(Ic::CircleCheck, "פעיל", Tone::Success)
                    } else {
                        ui::pill("לא פעיל", Tone::Neutral)
                    }),
            )
            .child(
                div()
                    .text_sm()
                    .text_color(ui::muted())
                    .child(rtl_paragraph(desc)),
            )
            .when(configured && kind == KeyKind::DeepSeek, |this| {
                let text = match &self.deepseek_balance {
                    Some(b) if b.balances.is_empty() => "אין יתרה זמינה".to_string(),
                    Some(b) => {
                        let amounts: Vec<String> = b
                            .balances
                            .iter()
                            .map(|i| {
                                let sym = match i.currency.as_str() {
                                    "USD" => "$".to_string(),
                                    "CNY" => "¥".to_string(),
                                    other => format!("{other} "),
                                };
                                format!("{sym}{}", i.total_balance)
                            })
                            .collect();
                        let suffix = if b.is_available { "" } else { " (היתרה אינה מספיקה)" };
                        format!("{}{}", latin(&amounts.join(" + ")), suffix)
                    }
                    None => "לא זמינה כרגע".to_string(),
                };
                this.child(
                    ui::rtl_row()
                        .gap_3()
                        .p_3()
                        .rounded_lg()
                        .bg(ui::surface_alt())
                        .child(ui::icon(Ic::Gauge, 18.0, ui::muted()))
                        .child(
                            div()
                                .v_flex()
                                .child(div().text_xs().text_color(ui::muted()).child(rtl("יתרה בחשבון")))
                                .child(
                                    div()
                                        .text_base()
                                        .font_semibold()
                                        .text_color(ui::ink())
                                        .child(rtl(&text)),
                                ),
                        ),
                )
            })
            .when(configured, |this| {
                this.child(
                    ui::rtl_row()
                        .gap_3()
                        .justify_between()
                        .child(
                            ui::rtl_row()
                                .gap_2()
                                .text_sm()
                                .text_color(ui::subtle())
                                .child(ui::icon(Ic::Lock, 14.0, ui::muted()))
                                .child(rtl(&latin(&masked.unwrap_or_default()))),
                        )
                        .child(
                            Button::new(clear_id)
                                .ghost()
                                .compact()
                                .icon(ui::icon(Ic::Trash, 15.0, ui::danger()))
                                .label(rtl("הסרת מפתח"))
                                .on_click(cx.listener(move |this, _, _, cx| this.clear_api_key(kind, cx))),
                        ),
                )
            })
            .when(!configured, |this| {
                this.child(key_state.clone()).child(
                    div().flex().flex_row_reverse().child(
                        Button::new(save_id)
                            .primary()
                            .icon(ui::icon(Ic::Save, 15.0, gpui_kit::rgb(0xffffff)))
                            .label(rtl("שמירה"))
                            .on_click(cx.listener(move |this, _, _, cx| this.save_api_key(kind, cx))),
                    ),
                )
            })
            .child(
                ui::rtl_row()
                    .gap_1p5()
                    .text_xs()
                    .text_color(ui::faint())
                    .child(ui::icon(Ic::ShieldCheck, 12.0, ui::faint()))
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
    let (label, tone, ic) = match check.status {
        CheckStatus::Ok => ("תקין", Tone::Success, Ic::CircleCheck),
        CheckStatus::Warning => ("אזהרה", Tone::Warning, Ic::TriangleAlert),
        CheckStatus::Error => ("שגיאה", Tone::Danger, Ic::CircleAlert),
    };
    let (fg, _) = tone.colors();
    ui::rtl_row()
        .items_start()
        .gap_3()
        .py_3()
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
                        .child(rtl(&check.label_he)),
                )
                .when(!check.detail.is_empty(), |this| {
                    this.child(
                        div().text_xs().text_color(ui::muted()).child(rtl_paragraph(&check.detail)),
                    )
                })
                .when(!check.remedy.is_empty(), |this| {
                    this.child(
                        div().text_xs().text_color(ui::faint()).child(rtl_paragraph(&check.remedy)),
                    )
                }),
        )
        .child(ui::pill(label, tone))
}
