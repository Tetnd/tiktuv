//! The application root view: navigation shell, shared state, and the screens.
//!
//! One `App` entity owns all state (a single-window app talking to one local
//! backend), renders the current screen inline, and reacts to live updates
//! pulled from the backend on a short poll.

use std::path::PathBuf;
use std::time::Duration;

use gpui_kit::base::StyledExt as _;
use gpui_kit::component::button::*;
use gpui_kit::component::input::InputState;
use gpui_kit::component::scroll::ScrollableElement as _;
use gpui_kit::component::{ActiveTheme, Disableable as _, Selectable as _};
use gpui_kit::prelude::FluentBuilder as _;
use gpui_kit::{
    AnyElement, AppContext as _, Context, Entity, IntoElement, ParentElement as _, Render,
    Styled as _, Window, div, px, relative, rgb,
};

use crate::api::{Api, ApiError};
use crate::backend::BackendProcess;
use crate::editor::EditorState;
use crate::format::*;
use crate::rtl_text::rtl_paragraph;
use crate::models::*;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Screen {
    Queue,
    History,
    Editor,
    System,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum PendingStatus {
    Importing,
    Ready,
    Error,
}

struct PendingFile {
    local_id: u64,
    filename: String,
    size_bytes: u64,
    duration_seconds: Option<f64>,
    media_token: Option<String>,
    status: PendingStatus,
    error: Option<String>,
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) enum ToastTone {
    Info,
    Success,
    Error,
}

struct Toast {
    id: u64,
    tone: ToastTone,
    title: String,
    detail: Option<String>,
}

pub struct App {
    pub(crate) api: Api,
    pub(crate) backend: Option<BackendProcess>,
    pub(crate) screen: Screen,
    pub(crate) connected: bool,
    pub(crate) settings: TranscriptionSettings,
    pub(crate) pending: Vec<PendingFile>,
    pub(crate) jobs: Vec<JobSummary>,
    pub(crate) models: Vec<ModelInfo>,
    pub(crate) system: Option<SystemInfo>,
    pub(crate) editor: Option<EditorState>,
    pub(crate) editor_loading: bool,
    pub(crate) toasts: Vec<Toast>,
    pub(crate) next_id: u64,
    pub(crate) settings_open: bool,
    pub(crate) model_download: Option<ModelDownloadStatus>,
    pub(crate) soniox_key: Entity<InputState>,
    pub(crate) deepseek_key: Entity<InputState>,
    pub(crate) soniox_status: Option<SonioxStatus>,
    pub(crate) deepseek_status: Option<DeepSeekStatus>,
    pub(crate) export_open: bool,
    pub(crate) export_formats: Vec<ExportFormat>,
    pub(crate) export_sections: Vec<ExportSection>,
    pub(crate) export_dir: Option<String>,
    pub(crate) export_busy: bool,
    pub(crate) zoom_open: bool,
    pub(crate) zoom_url: Entity<InputState>,
    pub(crate) zoom_passcode: Entity<InputState>,
    pub(crate) zoom_needs_passcode: bool,
    pub(crate) zoom_status: Option<UrlFetchStatus>,
    pub(crate) zoom_starting: bool,
    pub(crate) zoom_error: Option<String>,
    /// The proofreading-corrections review dialog is open.
    pub(crate) corrections_open: bool,
}

impl App {
    pub fn new(api: Api, backend: BackendProcess, window: &mut Window, cx: &mut Context<Self>) -> Self {
        let soniox_key = cx.new(|cx| InputState::new(window, cx).placeholder(rtl("Soniox API key")));
        let deepseek_key = cx.new(|cx| InputState::new(window, cx).placeholder(rtl("DeepSeek API key")));
        let zoom_url = cx.new(|cx| {
            InputState::new(window, cx).placeholder(rtl("https://us06web.zoom.us/rec/share/…"))
        });
        let zoom_passcode = cx.new(|cx| InputState::new(window, cx).placeholder(rtl("סיסמת ההקלטה")));
        let this = Self {
            api,
            backend: Some(backend),
            screen: Screen::Queue,
            connected: false,
            settings: TranscriptionSettings::default(),
            pending: Vec::new(),
            jobs: Vec::new(),
            models: Vec::new(),
            system: None,
            editor: None,
            editor_loading: false,
            toasts: Vec::new(),
            next_id: 1,
            settings_open: false,
            model_download: None,
            soniox_key,
            deepseek_key,
            soniox_status: None,
            deepseek_status: None,
            export_open: false,
            export_formats: Vec::new(),
            export_sections: Vec::new(),
            export_dir: None,
            export_busy: false,
            zoom_open: false,
            zoom_url,
            zoom_passcode,
            zoom_needs_passcode: false,
            zoom_status: None,
            zoom_starting: false,
            zoom_error: None,
            corrections_open: false,
        };

        this.refresh_jobs(cx);
        this.refresh_models(cx);
        this.start_polling(cx);
        this.start_position_tick(cx);

        this
    }

    // -- data loading ------------------------------------------------------
    pub(crate) fn refresh_jobs(&self, cx: &mut Context<Self>) {
        let api = self.api.clone();
        cx.spawn(async move |this, cx| {
            let result = api.list_jobs().await;
            let _ = this.update(cx, |this, cx| {
                match result {
                    Ok(jobs) => {
                        this.jobs = jobs;
                        this.connected = true;
                    }
                    Err(_) => this.connected = false,
                }
                cx.notify();
            });
        })
        .detach();
    }

    pub(crate) fn refresh_models(&self, cx: &mut Context<Self>) {
        let api = self.api.clone();
        cx.spawn(async move |this, cx| {
            if let Ok(models) = api.models().await {
                let _ = this.update(cx, |this, cx| {
                    this.models = models;
                    cx.notify();
                });
            }
        })
        .detach();
    }

    /// Poll `/api/jobs` on a Tokio timer and push fresh lists into the UI.
    fn start_polling(&self, cx: &mut Context<Self>) {
        let api = self.api.clone();
        let rt = self.api.rt().clone();
        let (tx, rx) = async_channel::unbounded::<Vec<JobSummary>>();

        rt.spawn(async move {
            loop {
                tokio::time::sleep(Duration::from_secs(3)).await;
                match api.list_jobs().await {
                    Ok(jobs) => {
                        if tx.send(jobs).await.is_err() {
                            break;
                        }
                    }
                    Err(_) => { /* connection briefly down; try again */ }
                }
            }
        });

        cx.spawn(async move |this, cx| {
            while let Ok(jobs) = rx.recv().await {
                let _ = this.update(cx, |this, cx| {
                    this.jobs = jobs;
                    this.connected = true;
                    cx.notify();
                });
            }
        })
        .detach();
    }

    // -- toasts ------------------------------------------------------------
    pub(crate) fn toast(&mut self, tone: ToastTone, title: impl Into<String>, detail: Option<String>) {
        let id = self.next_id;
        self.next_id += 1;
        self.toasts.push(Toast {
            id,
            tone,
            title: title.into(),
            detail,
        });
        if self.toasts.len() > 4 {
            self.toasts.remove(0);
        }
    }

    fn dismiss_toast(&mut self, id: u64, cx: &mut Context<Self>) {
        self.toasts.retain(|t| t.id != id);
        cx.notify();
    }

    // -- navigation --------------------------------------------------------
    fn set_screen(&mut self, screen: Screen, cx: &mut Context<Self>) {
        self.screen = screen;
        if screen == Screen::System && self.system.is_none() {
            self.refresh_system(cx);
        }
        cx.notify();
    }

    // -- actions -----------------------------------------------------------
    fn choose_files(&self, cx: &mut Context<Self>) {
        let api = self.api.clone();
        let rt = self.api.rt().clone();
        cx.spawn(async move |this, cx| {
            let (tx, rx) = async_channel::bounded::<Option<Vec<PathBuf>>>(1);
            rt.spawn_blocking(move || {
                let paths = rfd::FileDialog::new()
                    .set_title("בחירת קבצים לתמלול")
                    .add_filter("מדיה", &ACCEPTED_EXTENSIONS)
                    .pick_files();
                let _ = tx.send_blocking(paths);
            });

            let Some(paths) = rx.recv().await.ok().flatten() else {
                return;
            };
            if paths.is_empty() {
                return;
            }

            let mut results: Vec<(String, Result<ImportedMedia, ApiError>)> = Vec::new();
            for path in &paths {
                let name = path
                    .file_name()
                    .map(|n| n.to_string_lossy().into_owned())
                    .unwrap_or_else(|| path.to_string_lossy().into_owned());
                let res = api.import_media(&path.to_string_lossy()).await;
                results.push((name, res));
            }

            let _ = this.update(cx, |this, cx| {
                for (name, res) in results {
                    let id = this.next_id;
                    this.next_id += 1;
                    match res {
                        Ok(m) => this.pending.push(PendingFile {
                            local_id: id,
                            filename: m.media.filename.clone(),
                            size_bytes: m.media.size_bytes,
                            duration_seconds: Some(m.media.duration),
                            media_token: Some(m.token),
                            status: PendingStatus::Ready,
                            error: None,
                        }),
                        Err(e) => this.pending.push(PendingFile {
                            local_id: id,
                            filename: name,
                            size_bytes: 0,
                            duration_seconds: None,
                            media_token: None,
                            status: PendingStatus::Error,
                            error: Some(e.message),
                        }),
                    }
                }
                cx.notify();
            });
        })
        .detach();
    }

    fn start_all(&self, cx: &mut Context<Self>) {
        let api = self.api.clone();
        let settings = self.settings.clone();
        let ready: Vec<(u64, String)> = self
            .pending
            .iter()
            .filter(|p| p.status == PendingStatus::Ready)
            .filter_map(|p| p.media_token.clone().map(|t| (p.local_id, t)))
            .collect();
        if ready.is_empty() {
            return;
        }

        cx.spawn(async move |this, cx| {
            let mut failures: Vec<String> = Vec::new();
            for (_, token) in &ready {
                if let Err(e) = api.create_job(token, &settings, true).await {
                    failures.push(e.message);
                }
            }
            let jobs = api.list_jobs().await.unwrap_or_default();
            let _ = this.update(cx, |this, cx| {
                let started: Vec<u64> = ready.iter().map(|(id, _)| *id).collect();
                this.pending.retain(|p| !started.contains(&p.local_id));
                this.jobs = jobs;
                for message in failures {
                    this.toast(ToastTone::Error, message, None);
                }
                cx.notify();
            });
        })
        .detach();
    }

    fn cancel_job(&self, id: String, cx: &mut Context<Self>) {
        let api = self.api.clone();
        cx.spawn(async move |this, cx| {
            let _ = api.cancel_job(&id).await;
            let jobs = api.list_jobs().await.unwrap_or_default();
            let _ = this.update(cx, |this, cx| {
                this.jobs = jobs;
                cx.notify();
            });
        })
        .detach();
    }

    pub(crate) fn retry_job(&self, id: String, cx: &mut Context<Self>) {
        let api = self.api.clone();
        cx.spawn(async move |this, cx| {
            let _ = api.retry_job(&id).await;
            let jobs = api.list_jobs().await.unwrap_or_default();
            let _ = this.update(cx, |this, cx| {
                this.jobs = jobs;
                cx.notify();
            });
        })
        .detach();
    }

    pub(crate) fn delete_job(&self, id: String, cx: &mut Context<Self>) {
        let api = self.api.clone();
        cx.spawn(async move |this, cx| {
            let _ = api.delete_job(&id, false).await;
            let jobs = api.list_jobs().await.unwrap_or_default();
            let _ = this.update(cx, |this, cx| {
                this.jobs = jobs;
                cx.notify();
            });
        })
        .detach();
    }

    fn clear_completed(&self, cx: &mut Context<Self>) {
        let api = self.api.clone();
        cx.spawn(async move |this, cx| {
            let removed = api.clear_completed().await.unwrap_or(0);
            let jobs = api.list_jobs().await.unwrap_or_default();
            let _ = this.update(cx, |this, cx| {
                this.jobs = jobs;
                this.toast(
                    ToastTone::Success,
                    format!("הוסרו {removed} עבודות שהסתיימו."),
                    None,
                );
                cx.notify();
            });
        })
        .detach();
    }

    // -- rendering helpers -------------------------------------------------
    fn header(&self, cx: &mut Context<Self>) -> impl IntoElement {
        let screen = self.screen;
        let connected = self.connected;
        div()
            .flex()
            .flex_row_reverse()
            .items_center()
            .gap_4()
            .px_6()
            .py_3()
            .border_b_1()
            .border_color(rgb(0xe5e7eb))
            .bg(cx.theme().background)
            .child(
                div()
                    .flex()
                    .flex_row_reverse()
                    .items_baseline()
                    .gap_2()
                    .child(div().text_lg().child(rtl("תכתוב")))
                    .child(div().text_xs().text_color(rgb(0x6b7280)).child(rtl("תמלול עברית מקומי"))),
            )
            // All three tabs belong to one group: as siblings of the header row
            // they picked up its gap and spread out as if unrelated.
            .child(
                div()
                    .flex()
                    .flex_row_reverse()
                    .gap_1()
                    .child(nav_item("queue", "תמלול", screen == Screen::Queue, cx, Screen::Queue))
                    .child(nav_item(
                        "history",
                        "היסטוריה",
                        screen == Screen::History,
                        cx,
                        Screen::History,
                    ))
                    .child(nav_item(
                        "system",
                        "מערכת ומודלים",
                        screen == Screen::System,
                        cx,
                        Screen::System,
                    )),
            )
            .child(div().flex_1())
            .child(
                div()
                    .flex()
                    .flex_row_reverse()
                    .items_center()
                    .gap_2()
                    .text_xs()
                    .text_color(rgb(0x6b7280))
                    .child(
                        div()
                            .size_2()
                            .rounded_full()
                            .bg(if connected { rgb(0x10b981) } else { rgb(0xd1d5db) }),
                    )
                    .child(rtl(if connected { "מחובר לשירות המקומי" } else { "מתחבר…" })),
            )
    }

    fn placeholder_screen(&self, title: &'static str, _cx: &mut Context<Self>) -> impl IntoElement {
        div()
            .w_full()
            .p_10()
            .v_flex()
            .items_center()
            .gap_2()
            .child(div().text_lg().child(rtl(title)))
            .child(div().text_sm().text_color(rgb(0x6b7280)).child(rtl("מסך זה יושלם בשלב הבא.")))
    }

    fn upload_zone(&self, cx: &mut Context<Self>) -> impl IntoElement {
        let selected = self.models.iter().find(|m| m.id == self.settings.model_id);
        let using_cloud = selected.map(|m| m.is_cloud).unwrap_or(false);

        div()
            .rounded_2xl()
            .border_2()
            .border_dashed()
            .border_color(rgb(0xd1d5db))
            .p_10()
            .v_flex()
            .items_center()
            .gap_2()
            .bg(cx.theme().background)
            .child(div().text_lg().child(rtl("בחירת קבצים לתמלול")))
            .child(
                div()
                    .text_sm()
                    .text_color(rgb(0x6b7280))
                    .child(rtl(&format!("נתמכים: {}", ACCEPTED_EXTENSIONS.join(", ").to_uppercase()))),
            )
            .child(
                div()
                    .flex()
                    .flex_row_reverse()
                    .items_center()
                    .gap_2()
                    .text_sm()
                    .text_color(rgb(0x6b7280))
                    .child(rtl("מנוע תמלול:"))
                    .child(self.model_radio("model-picker", cx)),
            )
            .child(
                div()
                    .text_sm()
                    .text_color(rgb(0x6b7280))
                    .child(rtl_paragraph(if using_cloud {
                        "האודיו יישלח לשרתים של Soniox לצורך תמלול."
                    } else {
                        "הקבצים מעובדים על המחשב שלכם בלבד ואינם נשלחים לשום מקום."
                    })),
            )
            .child(
                div().flex().flex_row_reverse().gap_2().mt_2()
                    .child(
                        Button::new("choose-files")
                            .primary()
                            .label(rtl("בחירת קבצים"))
                            .on_click(cx.listener(|this, _, _, cx| this.choose_files(cx))),
                    )
                    .child(
                        Button::new("open-zoom")
                            .secondary()
                            .label(rtl("תמלול מקישור Zoom"))
                            .on_click(cx.listener(|this, _, _, cx| this.open_zoom(cx))),
                    ),
            )
    }

    fn pending_row(&self, f: &PendingFile, cx: &mut Context<Self>) -> impl IntoElement {
        let id = f.local_id;
        let filename = f.filename.clone();
        let mut meta = String::new();
        if f.size_bytes > 0 {
            meta.push_str(&format_bytes(f.size_bytes));
        }
        if let Some(d) = f.duration_seconds {
            if !meta.is_empty() {
                meta.push_str(" · ");
            }
            meta.push_str(&format_clock(d));
        }
        let status_text = match f.status {
            PendingStatus::Importing => "בודק קובץ…",
            PendingStatus::Ready => "מוכן",
            PendingStatus::Error => "שגיאה",
        };
        let status_color = if f.status == PendingStatus::Error {
            rgb(0xdc2626)
        } else {
            rgb(0x6b7280)
        };
        let error = f.error.clone();

        div()
            .flex()
            .flex_row_reverse()
            .items_center()
            .gap_3()
            .py_2()
            .child(
                div()
                    .flex_1()
                    .min_w_0()
                    .v_flex()
                    .child(div().truncate().text_sm().child(rtl(&truncate_middle(&filename, 60))))
                    .child(div().text_xs().text_color(rgb(0x6b7280)).child(rtl(&meta)))
                    .when(error.is_some(), |this| {
                        this.child(
                            div()
                                .text_xs()
                                .text_color(rgb(0xdc2626))
                                .child(rtl_paragraph(error.clone().unwrap_or_default())),
                        )
                    }),
            )
            .child(
                div()
                    .px_2()
                    .py_0p5()
                    .rounded_full()
                    .bg(status_color.opacity(0.12))
                    .text_color(status_color)
                    .text_xs()
                    .whitespace_nowrap()
                    .child(rtl(&status_text)),
            )
            .child(
                Button::new(format!("remove-pending-{id}"))
                    .ghost()
                    .label(rtl("הסר"))
                    .on_click(cx.listener(move |this, _, _, cx| {
                        this.pending.retain(|p| p.local_id != id);
                        cx.notify();
                    })),
            )
    }

    fn pending_list(&self, cx: &mut Context<Self>) -> impl IntoElement {
        if self.pending.is_empty() {
            return div();
        }
        let ready_count = self
            .pending
            .iter()
            .filter(|p| p.status == PendingStatus::Ready)
            .count();
        let rows: Vec<AnyElement> = self
            .pending
            .iter()
            .map(|f| self.pending_row(f, cx).into_any_element())
            .collect();

        div()
            .rounded_lg()
            .border_1()
            .border_color(rgb(0xe5e7eb))
            .p_4()
            .v_flex()
            .gap_2()
            .bg(cx.theme().background)
            .child(
                div()
                    .flex()
                    .flex_row_reverse()
                    .items_center()
                    .justify_between()
                    .child(div().text_base().child(rtl(&format!("קבצים ממתינים ({})", self.pending.len()))))
                    // Both buttons in one group: `justify_between` was pushing
                    // them to opposite ends of the row instead.
                    .child(
                        div()
                            .flex()
                            .flex_row_reverse()
                            .gap_2()
                            .child(
                                Button::new("clear-pending")
                                    .ghost()
                                    .label(rtl("נקה הכול"))
                                    .on_click(cx.listener(|this, _, _, cx| {
                                        this.pending.clear();
                                        cx.notify();
                                    })),
                            )
                            .child(
                                Button::new("start-all")
                                    .primary()
                                    .label(rtl(&format!("התחל תמלול ({ready_count})")))
                                    .disabled(ready_count == 0)
                                    .on_click(cx.listener(|this, _, _, cx| this.start_all(cx))),
                            ),
                    ),
            )
            .child(div().v_flex().children(rows))
    }

    fn job_row(&self, job: &JobSummary, cx: &mut Context<Self>) -> impl IntoElement {
        let id = job.id.clone();
        let filename = job.filename.clone();
        let model_id = job.model_id.clone();
        let size = job.size_bytes;
        let duration = job.duration;
        let status = job.status;
        let is_active = matches!(status, JobStatus::Running | JobStatus::Queued);
        let has_transcript = job.has_transcript;
        let stage = job.progress.stage;
        let percent = job.progress.percent;
        let eta = job.progress.eta_seconds;
        let error = job.error.clone();

        let id_open = id.clone();
        let id_cancel = id.clone();
        let id_retry = id.clone();
        let id_delete = id.clone();

        div()
            .py_4()
            .flex()
            .flex_row_reverse()
            .items_start()
            .gap_3()
            .child(
                div()
                    .flex_1()
                    .min_w_0()
                    .v_flex()
                    .child(
                        div()
                            .flex()
                            .flex_row_reverse()
                            .items_center()
                            .gap_2()
                            // Shortened in the middle *before* reordering:
                            // `truncate` clips the end of the laid-out line,
                            // which for reordered Hebrew is the start of the
                            // name — the part worth keeping.
                            .child(div().truncate().text_sm().child(rtl(&truncate_middle(&filename, 56))))
                            .child(status_pill(status)),
                    )
                    .child(
                        div()
                            .text_xs()
                            .text_color(rgb(0x6b7280))
                            // The model id is a Latin path-like token: isolated
                            // so bidi keeps it in one piece inside the Hebrew.
                            .child(rtl(&format!(
                                "{} · {} · {}",
                                format_bytes(size),
                                format_clock(duration),
                                isolate(&model_id)
                            ))),
                    )
                    .when(is_active, |this| this.child(progress_block(percent, stage, eta)))
                    .when(error.is_some(), |this| {
                        this.child(error_block(error.clone().unwrap()))
                    }),
            )
            .child(
                div().flex().flex_row_reverse().gap_2().when(has_transcript, |this| {
                    this.child(
                        Button::new(format!("open-{id_open}"))
                            .secondary()
                            .label(rtl("פתיחת התמליל"))
                            .on_click(cx.listener(move |this, _, window, cx| {
                                this.open_transcript(id_open.clone(), window, cx)
                            })),
                    )
                })
                .when(is_active, |this| {
                    this.child(
                        Button::new(format!("cancel-{id_cancel}"))
                            .secondary()
                            .label(rtl("ביטול"))
                            .on_click(cx.listener(move |this, _, _, cx| {
                                this.cancel_job(id_cancel.clone(), cx)
                            })),
                    )
                })
                .when(matches!(status, JobStatus::Failed | JobStatus::Cancelled), |this| {
                    this.child(
                        Button::new(format!("retry-{id_retry}"))
                            .secondary()
                            .label(rtl("נסה שוב"))
                            .on_click(cx.listener(move |this, _, _, cx| {
                                this.retry_job(id_retry.clone(), cx)
                            })),
                    )
                })
                .when(!is_active, |this| {
                    this.child(
                        Button::new(format!("delete-{id_delete}"))
                            .danger()
                            .label(rtl("מחיקה"))
                            .on_click(cx.listener(move |this, _, _, cx| {
                                this.delete_job(id_delete.clone(), cx)
                            })),
                    )
                }),
            )
    }

    fn jobs_list(&self, cx: &mut Context<Self>) -> impl IntoElement {
        let finished = self
            .jobs
            .iter()
            .filter(|j| {
                matches!(
                    j.status,
                    JobStatus::Completed | JobStatus::Failed | JobStatus::Cancelled
                )
            })
            .count();
        let rows: Vec<AnyElement> = self
            .jobs
            .iter()
            .map(|j| self.job_row(j, cx).into_any_element())
            .collect();

        div()
            .rounded_lg()
            .border_1()
            .border_color(rgb(0xe5e7eb))
            .p_4()
            .v_flex()
            .gap_2()
            .bg(cx.theme().background)
            .child(
                div()
                    .flex()
                    .flex_row_reverse()
                    .items_center()
                    .justify_between()
                    .child(div().text_base().child(rtl("עבודות")))
                    .when(finished > 0, |this| {
                        this.child(
                            Button::new("clear-completed")
                                .ghost()
                                .label(rtl("ניקוי עבודות שהסתיימו"))
                                .on_click(cx.listener(|this, _, _, cx| this.clear_completed(cx))),
                        )
                    }),
            )
            .when(self.jobs.is_empty(), |this| {
                this.child(
                    div()
                        .p_6()
                        .v_flex()
                        .items_center()
                        .gap_1()
                        .child(div().text_base().child(rtl("אין עדיין עבודות")))
                        .child(
                            div()
                                .text_sm()
                                .text_color(rgb(0x6b7280))
                                .child(rtl_paragraph("בחרו קובץ אודיו או וידאו כדי להתחיל בתמלול.")),
                        ),
                )
            })
            .when(!self.jobs.is_empty(), |this| this.child(div().v_flex().children(rows)))
    }

    fn queue_screen(&self, cx: &mut Context<Self>) -> impl IntoElement {
        let banner = self.model_banner(cx).into_any_element();
        let upload = self.upload_zone(cx).into_any_element();
        let pending = self.pending_list(cx).into_any_element();
        let settings = self.render_settings_panel(cx).into_any_element();
        let jobs = self.jobs_list(cx).into_any_element();
        div()
            .w_full()
            .p_6()
            .v_flex()
            .gap_6()
            .child(banner)
            .child(upload)
            .child(pending)
            .child(settings)
            .child(jobs)
    }

    fn toast_card(&self, toast: &Toast, cx: &mut Context<Self>) -> impl IntoElement {
        let id = toast.id;
        let title = toast.title.clone();
        let detail = toast.detail.clone();
        let (bg, border) = match toast.tone {
            ToastTone::Success => (rgb(0xecfdf5), rgb(0x059669)),
            ToastTone::Error => (rgb(0xfef2f2), rgb(0xdc2626)),
            ToastTone::Info => (rgb(0xeff6ff), rgb(0x3b82f6)),
        };
        div()
            .rounded_lg()
            .border_1()
            .border_color(border)
            .bg(bg)
            .p_3()
            .v_flex()
            .gap_1()
            .child(
                div()
                    .flex()
                    .flex_row_reverse()
                    .items_center()
                    .justify_between()
                    .gap_2()
                    .child(div().min_w_0().flex_1().text_sm().child(rtl_paragraph(&title)))
                    .child(
                        Button::new(format!("toast-close-{id}"))
                            .ghost()
                            .label(rtl("✕"))
                            .on_click(cx.listener(move |this, _, _, cx| this.dismiss_toast(id, cx))),
                    ),
            )
            .when(detail.is_some(), |this| {
                this.child(
                    div()
                        .text_xs()
                        .text_color(rgb(0x6b7280))
                        .child(rtl_paragraph(detail.clone().unwrap_or_default())),
                )
            })
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
        div()
            .absolute()
            .bottom_0()
            .right_0()
            .p_4()
            .w(px(320.))
            .v_flex()
            .gap_2()
            .children(toasts)
    }
}

impl Render for App {
    fn render(&mut self, window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let header = self.header(cx).into_any_element();
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
            .v_flex()
            .bg(cx.theme().background)
            .text_color(cx.theme().foreground)
            .text_right()
            .child(header)
            // The scrolled content is a flex column (its height is auto with
            // a viewport-height minimum), so a screen can `flex_1` to fill
            // the page — a percentage height would not resolve here.
            .child(div().flex_1().min_h_0().v_flex().overflow_y_scrollbar().child(content))
            .child(toasts)
            .child(overlays)
    }
}

// -- free helpers ----------------------------------------------------------
fn nav_item(
    id: &'static str,
    label: &'static str,
    active: bool,
    cx: &mut Context<App>,
    screen: Screen,
) -> impl IntoElement {
    Button::new(id)
        .ghost()
        .selected(active)
        .label(rtl(label))
        .on_click(cx.listener(move |this, _, _, cx| this.set_screen(screen, cx)))
}

pub(crate) fn status_pill(status: JobStatus) -> impl IntoElement {
    let (label, color) = match status {
        JobStatus::Queued => ("בתור", rgb(0x6b7280)),
        JobStatus::Running => ("פועל", rgb(0x2563eb)),
        JobStatus::Completed => ("הושלם", rgb(0x059669)),
        JobStatus::Failed => ("נכשל", rgb(0xdc2626)),
        JobStatus::Cancelled => ("בוטל", rgb(0x6b7280)),
    };
    div()
        .px_2()
        .py_0p5()
        .rounded_full()
        .bg(color.opacity(0.12))
        .text_color(color)
        .text_xs()
        .whitespace_nowrap()
        .child(rtl(&label))
}

fn progress_block(percent: f64, stage: JobStage, eta: Option<f64>) -> impl IntoElement {
    let p = percent.clamp(0.0, 1.0);
    div()
        .w_full()
        .mt_2()
        .v_flex()
        .gap_1()
        .child(
            // Reversed, so the bar fills from the right like every other
            // right-to-left progress indicator. A plain block child is
            // anchored to the left edge and filled the wrong way.
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
                        .w(relative(p as f32)),
                ),
        )
        .child(
            div()
                .flex()
                .flex_row_reverse()
                .gap_2()
                .text_xs()
                .text_color(rgb(0x6b7280))
                .child(rtl(&format_percent(percent)))
                .child(rtl("·"))
                .child(rtl(&stage_label(stage)))
                .when(eta.is_some(), |this| {
                    this.child(rtl("·")).child(rtl(&format_eta(eta)))
                }),
        )
}

fn error_block(error: ErrorPayload) -> impl IntoElement {
    div()
        .mt_2()
        .rounded_lg()
        .border_1()
        .border_color(rgb(0xfecaca))
        .bg(rgb(0xfef2f2))
        .p_3()
        .v_flex()
        .gap_1()
        .child(div().text_sm().text_color(rgb(0x991b1b)).child(rtl_paragraph(&error.message)))
        .child(div().text_xs().text_color(rgb(0xb91c1c)).child(rtl_paragraph(&error.remedy)))
        .when(!error.detail.is_empty(), |this| {
            this.child(div().text_xs().text_color(rgb(0xef4444)).child(rtl_paragraph(&error.detail)))
        })
}
