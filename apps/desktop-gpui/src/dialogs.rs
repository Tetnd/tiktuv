//! Modal dialogs rendered as overlays: export format picker and Zoom import.

use std::time::Duration;

use gpui_kit::base::StyledExt as _;
use gpui_kit::component::button::*;
use gpui_kit::component::checkbox::Checkbox;
use gpui_kit::component::input::Input;
use gpui_kit::component::{ActiveTheme, Disableable as _};
use gpui_kit::prelude::FluentBuilder as _;
use gpui_kit::{
    AnyElement, Context, IntoElement, ParentElement as _, Styled as _, Window, div, px, relative,
    rgb,
};

use crate::app::{App, ToastTone};
use crate::format::*;
use crate::rtl_text::rtl_paragraph;
use crate::models::*;

enum ZoomMsg {
    Status(UrlFetchStatus),
    Error {
        message: String,
        remedy: String,
        needs_passcode: bool,
    },
    Done(Vec<JobSummary>),
}

impl App {
    // -- overlay ----------------------------------------------------------
    pub fn render_overlays(&self, cx: &mut Context<Self>) -> AnyElement {
        let export = if self.export_open {
            self.export_overlay(cx)
        } else {
            div().into_any_element()
        };
        let zoom = if self.zoom_open {
            self.zoom_overlay(cx)
        } else {
            div().into_any_element()
        };
        div()
            .absolute()
            .inset_0()
            .child(export)
            .child(zoom)
            .into_any_element()
    }

    // -- export dialog ----------------------------------------------------
    pub fn open_export(&mut self, cx: &mut Context<Self>) {
        self.export_formats = vec![
            ExportFormat::Txt,
            ExportFormat::Docx,
            ExportFormat::Pdf,
            ExportFormat::Srt,
        ];
        self.export_dir = None;
        self.export_busy = false;
        self.export_open = true;
        cx.notify();
    }

    fn export_overlay(&self, cx: &mut Context<Self>) -> AnyElement {
        let mut checkboxes: Vec<AnyElement> = Vec::new();
        for format in ALL_EXPORT_FORMATS {
            let checked = self.export_formats.contains(&format);
            checkboxes.push(
                Checkbox::new(format!("export-fmt-{format:?}"))
                    .label(rtl(format_label(format)))
                    .checked(checked)
                    .on_change(cx.listener(move |this, value, _, cx| {
                        this.set_export_format(format, *value, cx)
                    }))
                    .into_any_element(),
            );
        }
        let dir_label = self.export_dir.clone().unwrap_or_else(|| "ברירת מחדל: תיקיית הייצוא".to_string());

        modal(
            cx,
            div()
                .w(px(480.))
                .v_flex()
                .gap_3()
                .child(div().text_lg().child(rtl("ייצוא התמליל")))
                .child(
                    div()
                        .text_sm()
                        .text_color(rgb(0x6b7280))
                        .child(rtl_paragraph("קובצי DOCX ו-PDF נוצרים עם פסקאות מימין לשמאל ופונט התומך בעברית.")),
                )
                .child(div().v_flex().gap_2().children(checkboxes))
                .child(
                    div()
                        .flex()
                        .flex_row_reverse()
                        .items_center()
                        .gap_2()
                        .child(Button::new("pick-export-dir").ghost().label(rtl("בחירת תיקייה")).on_click(cx.listener(|this, _, _, cx| this.pick_export_dir(cx))))
                        .child(div().flex_1().min_w_0().truncate().text_xs().text_color(rgb(0x6b7280)).child(rtl(&dir_label))),
                )
                .child(
                    div()
                        .flex()
                        .flex_row_reverse()
                        .gap_2()
                        .child(
                            Button::new("submit-export")
                                .primary()
                                .label(rtl(if self.export_busy { "מייצא…" } else { "ייצוא" }))
                                .disabled(self.export_formats.is_empty() || self.export_busy)
                                .on_click(cx.listener(|this, _, _, cx| this.submit_export(cx))),
                        )
                        .child(
                            Button::new("cancel-export")
                                .ghost()
                                .label(rtl("ביטול"))
                                .on_click(cx.listener(|this, _, _, cx| this.close_export(cx))),
                        ),
                ),
        )
    }

    fn set_export_format(&mut self, format: ExportFormat, enabled: bool, cx: &mut Context<Self>) {
        if enabled {
            if !self.export_formats.contains(&format) {
                self.export_formats.push(format);
            }
        } else {
            self.export_formats.retain(|f| *f != format);
        }
        cx.notify();
    }

    fn close_export(&mut self, cx: &mut Context<Self>) {
        self.export_open = false;
        cx.notify();
    }

    fn pick_export_dir(&mut self, cx: &mut Context<Self>) {
        let rt = self.api.rt().clone();
        cx.spawn(async move |this, cx| {
            let (tx, rx) = async_channel::bounded::<Option<std::path::PathBuf>>(1);
            rt.spawn_blocking(move || {
                let dir = rfd::FileDialog::new()
                    .set_title("בחירת תיקיית יעד")
                    .pick_folder();
                let _ = tx.send_blocking(dir);
            });
            if let Ok(Some(dir)) = rx.recv().await {
                let _ = this.update(cx, |this, cx| {
                    this.export_dir = Some(dir.to_string_lossy().into_owned());
                    cx.notify();
                });
            }
        })
        .detach();
    }

    fn submit_export(&mut self, cx: &mut Context<Self>) {
        let Some(job_id) = self.editor.as_ref().map(|e| e.job_id.clone()) else {
            return;
        };
        if self.export_formats.is_empty() {
            return;
        }
        let formats = self.export_formats.clone();
        let dir = self.export_dir.clone();
        let api = self.api.clone();
        self.export_busy = true;
        cx.notify();
        cx.spawn(async move |this, cx| {
            let result = api.export_job(&job_id, &formats, dir.as_deref()).await;
            let _ = this.update(cx, |this, cx| {
                this.export_busy = false;
                match result {
                    Ok(r) => {
                        this.export_open = false;
                        this.toast(
                            ToastTone::Success,
                            format!("יוצאו {} קבצים.", r.results.len()),
                            r.results.first().map(|x| x.path.clone()),
                        );
                    }
                    Err(e) => this.toast(ToastTone::Error, e.message, Some(e.remedy)),
                }
                cx.notify();
            });
        })
        .detach();
    }

    // -- zoom import dialog ----------------------------------------------
    pub fn open_zoom(&mut self, cx: &mut Context<Self>) {
        self.zoom_open = true;
        self.zoom_error = None;
        self.zoom_needs_passcode = false;
        self.zoom_starting = false;
        cx.notify();
    }

    fn zoom_overlay(&self, cx: &mut Context<Self>) -> AnyElement {
        let running = self
            .zoom_status
            .as_ref()
            .map(|s| s.state == "resolving" || s.state == "downloading")
            .unwrap_or(false);
        let url_state = self.zoom_url.clone();
        let passcode_state = self.zoom_passcode.clone();
        let status = self.zoom_status.clone();
        let error = self.zoom_error.clone();

        modal(
            cx,
            div()
                .w(px(560.))
                .v_flex()
                .gap_3()
                .child(div().text_lg().child(rtl("תמלול מקישור Zoom")))
                .child(
                    div()
                        .text_sm()
                        .text_color(rgb(0x6b7280))
                        .child(rtl_paragraph("הדביקו קישור להקלטת ענן. ההקלטה תורד למחשב שלכם ותתומלל מקומית.")),
                )
                .child(Input::new(&url_state))
                .when(self.zoom_needs_passcode, |this| {
                    this.child(Input::new(&passcode_state))
                })
                .when(running, |this| {
                    let p = status.as_ref().map(|s| s.percent).unwrap_or(0.0);
                    this.child(
                        div()
                            .rounded_lg()
                            .border_1()
                            .border_color(rgb(0xc7d2fe))
                            .bg(rgb(0xeef2ff))
                            .p_3()
                            .v_flex()
                            .gap_2()
                            .child(div().text_sm().child(rtl(if status.as_ref().map(|s| s.state.as_str()).unwrap_or("") == "resolving" { "מאתר את ההקלטה…" } else { "מוריד את ההקלטה…" })))
                            .when(status.as_ref().map(|s| !s.title.is_empty()).unwrap_or(false), |this| {
                                this.child(div().text_xs().text_color(rgb(0x4b5563)).child(rtl(&status.as_ref().unwrap().title)))
                            })
                            .child(
                                // Fills from the right, like the rest of the
                                // interface.
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
                            .child(div().text_xs().text_color(rgb(0x4b5563)).child(rtl(&format!(
                                "{} · {} מתוך {}",
                                format_percent(p),
                                format_bytes(status.as_ref().map(|s| s.downloaded_bytes).unwrap_or(0)),
                                format_bytes(status.as_ref().map(|s| s.total_bytes).unwrap_or(0))
                            )))),
                    )
                })
                .when(error.is_some(), |this| {
                    this.child(div().text_sm().text_color(rgb(0xdc2626)).child(rtl_paragraph(error.clone().unwrap_or_default())))
                })
                .child(
                    div()
                        .text_xs()
                        .text_color(rgb(0x9ca3af))
                        .child(rtl_paragraph("ההורדה מתבצעת ישירות מ-Zoom אל המחשב שלכם. השתמשו רק בהקלטות שיש לכם הרשאה אליהן.")),
                )
                .child(
                    div()
                        .flex()
                        .flex_row_reverse()
                        .gap_2()
                        .when(running, |this| {
                            this.child(
                                Button::new("cancel-zoom")
                                    .secondary()
                                    .label(rtl("עצור"))
                                    .on_click(cx.listener(|this, _, _, cx| this.cancel_zoom(cx))),
                            )
                        })
                        .when(!running, |this| {
                            this.child(
                                Button::new("start-zoom")
                                    .primary()
                                    .label(rtl(if self.zoom_starting { "מתחיל…" } else { "הורד ותמלל" }))
                                    .disabled(self.zoom_starting)
                                    .on_click(cx.listener(|this, _, _, cx| this.start_zoom(cx))),
                            )
                        })
                        .child(
                            Button::new("close-zoom")
                                .ghost()
                                .label(rtl("סגירה"))
                                .disabled(running)
                                .on_click(cx.listener(|this, _, _, cx| this.close_zoom(cx))),
                        ),
                ),
        )
    }

    fn close_zoom(&mut self, cx: &mut Context<Self>) {
        self.zoom_open = false;
        cx.notify();
    }

    fn cancel_zoom(&self, cx: &mut Context<Self>) {
        let api = self.api.clone();
        cx.spawn(async move |this, cx| {
            if let Ok(status) = api.cancel_url_import().await {
                let _ = this.update(cx, |this, cx| {
                    this.zoom_status = Some(status);
                    cx.notify();
                });
            }
        })
        .detach();
    }

    fn start_zoom(&mut self, cx: &mut Context<Self>) {
        let url = self.zoom_url.read(cx).value().to_string();
        let passcode = self.zoom_passcode.read(cx).value().to_string();
        let url = url.trim().to_string();
        if !looks_like_zoom_link(&url) {
            self.zoom_error = Some("הדביקו קישור להקלטת Zoom (rec/share או rec/play).".to_string());
            cx.notify();
            return;
        }
        let passcode_opt = if passcode.trim().is_empty() {
            None
        } else {
            Some(passcode.trim().to_string())
        };
        self.zoom_starting = true;
        self.zoom_error = None;
        cx.notify();

        let api = self.api.clone();
        let rt = self.api.rt().clone();
        let settings = self.settings.clone();
        let (tx, rx) = async_channel::unbounded::<ZoomMsg>();

        rt.spawn(async move {
            match api.import_from_url(&url, passcode_opt.as_deref()).await {
                Err(e) => {
                    let needs_passcode = e.detail.to_lowercase().contains("passcode")
                        || e.code.to_lowercase().contains("passcode")
                        || e.message.contains("סיסמה");
                    let _ = tx
                        .send(ZoomMsg::Error {
                            message: e.message,
                            remedy: e.remedy,
                            needs_passcode,
                        })
                        .await;
                }
                Ok(mut status) => {
                    loop {
                        let state = status.state.clone();
                        let is_done = matches!(state.as_str(), "ready" | "failed" | "cancelled");
                        if tx.send(ZoomMsg::Status(status.clone())).await.is_err() {
                            break;
                        }
                        if is_done {
                            break;
                        }
                        tokio::time::sleep(Duration::from_millis(700)).await;
                        match api.url_import_status().await {
                            Ok(s) => status = s,
                            Err(_) => break,
                        }
                    }
                    if status.state == "ready" {
                        if let Some(token) = status.media_token.clone() {
                            let _ = api.create_job(&token, &settings, true).await;
                            let jobs = api.list_jobs().await.unwrap_or_default();
                            let _ = tx.send(ZoomMsg::Done(jobs)).await;
                        }
                    }
                }
            }
        });

        cx.spawn(async move |this, cx| {
            while let Ok(msg) = rx.recv().await {
                match msg {
                    ZoomMsg::Status(s) => {
                        let _ = this.update(cx, |this, cx| {
                            this.zoom_status = Some(s);
                            this.zoom_starting = false;
                            cx.notify();
                        });
                    }
                    ZoomMsg::Error {
                        message,
                        remedy,
                        needs_passcode,
                    } => {
                        let _ = this.update(cx, |this, cx| {
                            this.zoom_starting = false;
                            this.zoom_needs_passcode = needs_passcode;
                            this.zoom_error = Some(format!("{message} {remedy}"));
                            cx.notify();
                        });
                    }
                    ZoomMsg::Done(jobs) => {
                        let _ = this.update(cx, |this, cx| {
                            this.jobs = jobs;
                            this.zoom_open = false;
                            this.zoom_status = None;
                            this.zoom_starting = false;
                            this.toast(
                                ToastTone::Success,
                                "ההקלטה הורדה והתמלול התחיל.".to_string(),
                                None,
                            );
                            cx.notify();
                        });
                    }
                }
            }
        })
        .detach();
    }
}

fn modal(cx: &mut Context<App>, content: impl IntoElement) -> AnyElement {
    div()
        .absolute()
        .inset_0()
        .bg(rgb(0x000000).opacity(0.4))
        .flex()
        .flex_row_reverse()
        .items_center()
        .justify_center()
        .p_4()
        .child(
            div()
                .rounded_lg()
                .bg(cx.theme().background)
                .p_5()
                .child(content),
        )
        .into_any_element()
}

fn looks_like_zoom_link(value: &str) -> bool {
    let v = value.trim().to_lowercase();
    let Some(rest) = v
        .strip_prefix("http://")
        .or_else(|| v.strip_prefix("https://"))
    else {
        return false;
    };
    let Some((host, path)) = rest.split_once('/') else {
        return false;
    };
    if !(host == "zoom.us" || host.ends_with(".zoom.us")) {
        return false;
    }
    let path = path.split('?').next().unwrap_or(path);
    path == "rec/play"
        || path.starts_with("rec/play/")
        || path == "rec/share"
        || path.starts_with("rec/share/")
}
