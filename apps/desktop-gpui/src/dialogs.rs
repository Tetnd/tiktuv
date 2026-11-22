//! Modal dialogs rendered as overlays: the export format picker, Zoom import,
//! and the proofreading-corrections review.

use std::time::Duration;

use gpui_kit::base::StyledExt as _;
use gpui_kit::component::button::*;
use gpui_kit::component::checkbox::Checkbox;
use gpui_kit::component::input::Input;
use gpui_kit::component::scroll::ScrollableElement as _;
use gpui_kit::component::{ActiveTheme, Disableable as _};
use gpui_kit::prelude::FluentBuilder as _;
use gpui_kit::{
    AnyElement, Context, IntoElement, ParentElement as _, Styled as _, Window, div, px, relative,
    rgb,
};

use crate::app::{App, ToastTone};
use crate::diff::{DiffKind, DiffRun, word_diff};
use crate::editor::section_for_tab;
use crate::format::*;
use crate::models::*;
use crate::rtl_text::rtl_paragraph;

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
        let corrections = if self.corrections_open {
            self.corrections_overlay(cx)
        } else {
            div().into_any_element()
        };
        div()
            .absolute()
            .inset_0()
            .child(export)
            .child(zoom)
            .child(corrections)
            .into_any_element()
    }

    // -- export dialog ----------------------------------------------------
    /// Open the dialog on whatever the editor is showing: the transcript, or
    /// the analysis tab the user is reading.
    pub fn open_export(&mut self, cx: &mut Context<Self>) {
        let section = self
            .editor
            .as_ref()
            .and_then(|e| e.analysis_tab)
            .map(section_for_tab)
            .unwrap_or(ExportSection::Transcript);
        self.open_export_for(section, cx);
    }

    /// Open the dialog with one section pre-selected.
    pub fn open_export_for(&mut self, section: ExportSection, cx: &mut Context<Self>) {
        // Exports read the analysis back from disk, so an edit still inside
        // the autosave debounce would be missing from the file.
        self.flush_entity_save(cx);
        self.flush_speaker_summaries_save(cx);
        self.flush_key_points_save(cx);
        self.flush_quotes_save(cx);
        self.flush_action_items_save(cx);
        self.flush_summary_save(cx);
        self.export_sections = vec![section];
        self.export_formats = [
            ExportFormat::Txt,
            ExportFormat::Docx,
            ExportFormat::Pdf,
            ExportFormat::Srt,
        ]
        .into_iter()
        .filter(|f| section.supports(*f))
        .collect();
        self.export_dir = None;
        self.export_busy = false;
        self.export_open = true;
        cx.notify();
    }

    /// Is this format usable by at least one selected section?
    fn format_applies(&self, format: ExportFormat) -> bool {
        self.export_sections.is_empty()
            || self.export_sections.iter().any(|s| s.supports(format))
    }

    fn export_overlay(&self, cx: &mut Context<Self>) -> AnyElement {
        let has_analysis = self
            .editor
            .as_ref()
            .map(|e| e.analysis.is_some())
            .unwrap_or(false);

        let mut sections: Vec<AnyElement> = Vec::new();
        for section in ALL_EXPORT_SECTIONS {
            let checked = self.export_sections.contains(&section);
            // The analysis tabs can only be exported once DeepSeek has run.
            let locked = section != ExportSection::Transcript && !has_analysis;
            sections.push(
                Checkbox::new(format!("export-section-{section:?}"))
                    .label(rtl(section_label(section)))
                    .checked(checked)
                    .disabled(locked)
                    .on_change(cx.listener(move |this, value, _, cx| {
                        this.set_export_section(section, *value, cx)
                    }))
                    .into_any_element(),
            );
        }

        let mut checkboxes: Vec<AnyElement> = Vec::new();
        for format in ALL_EXPORT_FORMATS {
            let checked = self.export_formats.contains(&format);
            let applies = self.format_applies(format);
            checkboxes.push(
                Checkbox::new(format!("export-fmt-{format:?}"))
                    .label(rtl(format_label(format)))
                    .checked(checked && applies)
                    .disabled(!applies)
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
                .child(div().text_lg().child(rtl("ייצוא")))
                .child(
                    div()
                        .text_sm()
                        .text_color(rgb(0x6b7280))
                        .child(rtl_paragraph("כל לשונית נשמרת בקובץ נפרד. קובצי DOCX ו-PDF נוצרים עם פסקאות מימין לשמאל ופונט התומך בעברית.")),
                )
                .child(
                    div()
                        .flex()
                        .flex_row_reverse()
                        .items_center()
                        .justify_between()
                        .child(div().text_sm().child(rtl("לשוניות לייצוא")))
                        .child(
                            div()
                                .flex()
                                .flex_row_reverse()
                                .gap_1()
                                .child(
                                    Button::new("export-all-sections")
                                        .ghost()
                                        .compact()
                                        .label(rtl("בחר הכול"))
                                        .disabled(!has_analysis)
                                        .on_click(cx.listener(|this, _, _, cx| {
                                            this.select_all_export_sections(cx)
                                        })),
                                )
                                .child(
                                    Button::new("export-only-current")
                                        .ghost()
                                        .compact()
                                        .label(rtl("רק הלשונית הנוכחית"))
                                        .on_click(cx.listener(|this, _, _, cx| {
                                            this.select_current_export_section(cx)
                                        })),
                                ),
                        ),
                )
                .child(div().v_flex().gap_2().children(sections))
                .when(!has_analysis, |this| {
                    this.child(
                        div()
                            .text_xs()
                            .text_color(rgb(0x6b7280))
                            .child(rtl_paragraph("לשוניות הניתוח יהיו זמינות לייצוא אחרי שהניתוח יופק.")),
                    )
                })
                .child(div().text_sm().child(rtl("פורמטים")))
                .child(div().v_flex().gap_2().children(checkboxes))
                .when(
                    !self.export_sections.is_empty()
                        && !self.export_sections.contains(&ExportSection::Transcript),
                    |this| {
                        this.child(
                            div()
                                .text_xs()
                                .text_color(rgb(0x6b7280))
                                .child(rtl_paragraph("כתוביות וחותמות זמן זמינות לתמלול בלבד.")),
                        )
                    },
                )
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
                                .disabled(
                                    self.export_formats.is_empty()
                                        || self.export_sections.is_empty()
                                        || self.export_busy,
                                )
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

    fn set_export_section(
        &mut self,
        section: ExportSection,
        enabled: bool,
        cx: &mut Context<Self>,
    ) {
        if enabled {
            if !self.export_sections.contains(&section) {
                self.export_sections.push(section);
            }
        } else {
            self.export_sections.retain(|s| *s != section);
        }
        cx.notify();
    }

    fn select_all_export_sections(&mut self, cx: &mut Context<Self>) {
        self.export_sections = ALL_EXPORT_SECTIONS.to_vec();
        cx.notify();
    }

    fn select_current_export_section(&mut self, cx: &mut Context<Self>) {
        let section = self
            .editor
            .as_ref()
            .and_then(|e| e.analysis_tab)
            .map(section_for_tab)
            .unwrap_or(ExportSection::Transcript);
        self.export_sections = vec![section];
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
        if self.export_formats.is_empty() || self.export_sections.is_empty() {
            return;
        }
        let sections = self.export_sections.clone();
        // Only send formats at least one chosen section can produce, so the
        // request never carries a pair the backend would have to drop.
        let formats: Vec<ExportFormat> = self
            .export_formats
            .iter()
            .copied()
            .filter(|f| sections.iter().any(|s| s.supports(*f)))
            .collect();
        if formats.is_empty() {
            return;
        }
        let dir = self.export_dir.clone();
        let api = self.api.clone();
        self.export_busy = true;
        cx.notify();
        cx.spawn(async move |this, cx| {
            let result = api
                .export_job(&job_id, &formats, &sections, dir.as_deref())
                .await;
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
    // -- proofreading corrections dialog ----------------------------------
    /// Open the review of everything DeepSeek corrected in the open transcript.
    pub(crate) fn open_corrections(&mut self, cx: &mut Context<Self>) {
        self.corrections_open = true;
        cx.notify();
    }

    /// Close the review dialog, keeping the corrections themselves.
    pub(crate) fn close_corrections(&mut self, cx: &mut Context<Self>) {
        self.corrections_open = false;
        cx.notify();
    }

    /// One row per corrected segment: what was removed, what replaced it, and
    /// a jump that plays it in the transcript.
    fn corrections_overlay(&self, cx: &mut Context<Self>) -> AnyElement {
        let Some(editor) = &self.editor else {
            return div().into_any_element();
        };

        let mut rows: Vec<AnyElement> = Vec::new();
        let mut count = 0usize;
        for (index, segment) in editor.segments.iter().enumerate() {
            if !segment.proofread {
                continue;
            }
            count += 1;
            let start = segment.start;
            let speaker_label = segment.speaker.as_ref().map(|key| {
                editor
                    .speakers
                    .get(key)
                    .cloned()
                    .unwrap_or_else(|| key.clone())
            });
            // The raw ASR text is kept for exactly this screen. Transcripts
            // saved before that existed have no original to diff against, and
            // then there is nothing honest to show as removed or added.
            let runs: Vec<DiffRun> = match &segment.original_text {
                Some(original) => word_diff(original, &segment.text),
                None => Vec::new(),
            };
            let original = segment.original_text.clone().unwrap_or_default();
            let corrected = segment.text.clone();

            rows.push(
                div()
                    .rounded_lg()
                    .border_1()
                    .border_color(rgb(0xe5e7eb))
                    .p_3()
                    .v_flex()
                    .gap_1()
                    .child(
                        div()
                            .flex()
                            .flex_row_reverse()
                            .items_center()
                            .gap_2()
                            .child(
                                div()
                                    .flex()
                                    .flex_row_reverse()
                                    .items_center()
                                    .gap_2()
                                    .text_xs()
                                    .text_color(rgb(0x6b7280))
                                    .child(rtl(&format_clock(start)))
                                    .when(speaker_label.is_some(), |this| {
                                        this.child(
                                            div().text_xs().text_color(rgb(0x4f46e5)).child(rtl(
                                                &speaker_label.clone().unwrap_or_default(),
                                            )),
                                        )
                                    }),
                            )
                            .child(div().flex_1())
                            .child(
                                Button::new(format!("corrections-seek-{index}"))
                                    .ghost()
                                    .compact()
                                    .label(rtl("דילוג למקטע"))
                                    .on_click(cx.listener(move |this, _, _, cx| {
                                        this.seek_to(start, cx);
                                        this.corrections_open = false;
                                        cx.notify();
                                    })),
                            ),
                    )
                    .when(!runs.is_empty(), |this| this.child(changed_words(runs)))
                    .when(!original.is_empty(), |this| {
                        this.child(
                            div()
                                .flex()
                                .flex_row_reverse()
                                .items_start()
                                .gap_2()
                                .child(
                                    div()
                                        .text_xs()
                                        .text_color(rgb(0xdc2626))
                                        .whitespace_nowrap()
                                        .child(rtl("לפני:")),
                                )
                                .child(
                                    div()
                                        .flex_1()
                                        .min_w_0()
                                        .text_xs()
                                        .text_color(rgb(0x991b1b))
                                        .line_through()
                                        .child(rtl_paragraph(&original)),
                                ),
                        )
                    })
                    .child(
                        div()
                            .flex()
                            .flex_row_reverse()
                            .items_start()
                            .gap_2()
                            .child(
                                div()
                                    .text_xs()
                                    .text_color(rgb(0x059669))
                                    .whitespace_nowrap()
                                    .child(rtl("אחרי:")),
                            )
                            .child(
                                div()
                                    .flex_1()
                                    .min_w_0()
                                    .text_xs()
                                    .text_color(rgb(0x065f46))
                                    .child(rtl_paragraph(&corrected)),
                            ),
                    )
                    .into_any_element(),
            );
        }

        modal(
            cx,
            div()
                .w(px(560.))
                .v_flex()
                .gap_3()
                .child(
                    div()
                        .text_lg()
                        .child(rtl(&format!("תיקוני ההגהה ({count})"))),
                )
                .child(
                    div()
                        .text_sm()
                        .text_color(rgb(0x6b7280))
                        .child(rtl_paragraph(
                            "מה ש-DeepSeek שינה בכל מקטע: אדום עם קו חוצה — הוסר, ירוק — נוסף במקומו. \"דילוג למקטע\" מנגן אותו בתמליל.",
                        )),
                )
                .when(count == 0, |this| {
                    this.child(
                        div()
                            .text_sm()
                            .text_color(rgb(0x6b7280))
                            .child(rtl_paragraph("אין מקטעים שתוקנו בהגהה בתמליל הנוכחי.")),
                    )
                })
                .child(
                    div()
                        .max_h(px(420.))
                        .overflow_y_scrollbar()
                        .v_flex()
                        .gap_2()
                        .children(rows),
                )
                .child(
                    div().flex().flex_row_reverse().gap_2().child(
                        Button::new("close-corrections")
                            .ghost()
                            .label(rtl("סגירה"))
                            .on_click(cx.listener(|this, _, _, cx| this.close_corrections(cx))),
                    ),
                ),
        )
    }
}

/// The changed words of one correction as chips: removed words struck through
/// in red, added words in green. Unchanged words are skipped, so this line is
/// short — the context is in the "לפני"/"אחרי" lines around it.
fn changed_words(runs: Vec<DiffRun>) -> AnyElement {
    let mut chips: Vec<AnyElement> = Vec::new();
    for run in runs {
        if run.kind == DiffKind::Equal {
            continue;
        }
        let removed = run.kind == DiffKind::Removed;
        for word in run.text.split_whitespace() {
            chips.push(
                div()
                    .px_1()
                    .rounded_md()
                    .whitespace_nowrap()
                    .bg(if removed {
                        rgb(0xdc2626).opacity(0.08)
                    } else {
                        rgb(0x059669).opacity(0.08)
                    })
                    .text_color(if removed {
                        rgb(0xdc2626)
                    } else {
                        rgb(0x059669)
                    })
                    .when(removed, |this| this.line_through())
                    .child(rtl(word))
                    .into_any_element(),
            );
        }
    }
    // Children in logical order inside a reversed row: the removed words sit
    // to the right of their replacements, as they would in a diff.
    div()
        .flex()
        .flex_row_reverse()
        .flex_wrap()
        .items_center()
        .gap_1()
        .text_sm()
        .children(chips)
        .into_any_element()
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
