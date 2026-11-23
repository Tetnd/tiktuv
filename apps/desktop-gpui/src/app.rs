//! The application root view: navigation shell, shared state, and the screens.
//!
//! One `App` entity owns all state (a single-window app talking to one local
//! backend), renders the current screen inline, and reacts to live updates
//! pulled from the backend on a short poll.

use std::path::PathBuf;
use std::time::Duration;

use gpui_kit::base::StyledExt as _;
use gpui_kit::component::button::*;
use crate::rtl_field::RtlField;
use gpui_kit::component::scroll::ScrollableElement as _;
use gpui_kit::component::{ActiveTheme, Disableable as _, Selectable as _};
use gpui_kit::prelude::FluentBuilder as _;
use gpui_kit::{
    AnyElement, AppContext as _, Context, Entity, IntoElement, ParentElement as _, Render,
    Styled as _, Window, div, px, relative, rgb,
};

use crate::api::{Api, ApiError};
use crate::backend::{BackendError, BackendProcess};
use crate::editor::EditorState;
use crate::format::*;
use crate::ui;
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
pub(crate) enum PendingStatus {
    Importing,
    Ready,
    Error,
}

pub(crate) struct PendingFile {
    pub(crate) local_id: u64,
    pub(crate) filename: String,
    pub(crate) size_bytes: u64,
    pub(crate) duration_seconds: Option<f64>,
    pub(crate) media_token: Option<String>,
    pub(crate) status: PendingStatus,
    pub(crate) error: Option<String>,
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) enum ToastTone {
    Info,
    Success,
    Error,
}

pub(crate) struct Toast {
    pub(crate) id: u64,
    pub(crate) tone: ToastTone,
    pub(crate) title: String,
    pub(crate) detail: Option<String>,
    pub(crate) created: std::time::Instant,
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
    pub(crate) soniox_key: Entity<RtlField>,
    pub(crate) deepseek_key: Entity<RtlField>,
    pub(crate) soniox_status: Option<SonioxStatus>,
    pub(crate) deepseek_status: Option<DeepSeekStatus>,
    /// `None` = not loaded / unavailable.
    pub(crate) deepseek_balance: Option<DeepSeekBalance>,
    pub(crate) export_open: bool,
    pub(crate) export_formats: Vec<ExportFormat>,
    pub(crate) export_sections: Vec<ExportSection>,
    pub(crate) export_dir: Option<String>,
    pub(crate) export_busy: bool,
    pub(crate) speakers_open: bool,
    /// 0 = Soniox (by voice), 1 = DeepSeek (by content).
    pub(crate) speakers_method: usize,
    pub(crate) speakers_names: bool,
    /// 0 = let the model decide, otherwise the number of people.
    pub(crate) speakers_expected: usize,
    pub(crate) speakers_busy: bool,
    pub(crate) zoom_open: bool,
    pub(crate) zoom_url: Entity<RtlField>,
    pub(crate) zoom_passcode: Entity<RtlField>,
    pub(crate) zoom_needs_passcode: bool,
    pub(crate) zoom_status: Option<UrlFetchStatus>,
    pub(crate) zoom_starting: bool,
    pub(crate) zoom_error: Option<String>,
    /// The proofreading-corrections review dialog is open.
    pub(crate) corrections_open: bool,
}

impl App {
    pub fn new(
        api: Api,
        backend_rx: async_channel::Receiver<Result<BackendProcess, BackendError>>,
        window: &mut Window, cx: &mut Context<Self>) -> Self {
        let soniox_key = cx.new(|cx| RtlField::new(window, cx).placeholder("Soniox API key").ltr(true));
        let deepseek_key = cx.new(|cx| RtlField::new(window, cx).placeholder("DeepSeek API key").ltr(true));
        let zoom_url = cx.new(|cx| {
            RtlField::new(window, cx)
                .placeholder("https://us06web.zoom.us/rec/share/…")
                .ltr(true)
        });
        let zoom_passcode = cx.new(|cx| RtlField::new(window, cx).placeholder("סיסמת ההקלטה"));
        let this = Self {
            api,
            backend: None,
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
            deepseek_balance: None,
            export_open: false,
            export_formats: Vec::new(),
            export_sections: Vec::new(),
            export_dir: None,
            export_busy: false,
            speakers_open: false,
            speakers_method: 1,
            speakers_names: true,
            speakers_expected: 0,
            speakers_busy: false,
            zoom_open: false,
            zoom_url,
            zoom_passcode,
            zoom_needs_passcode: false,
            zoom_status: None,
            zoom_starting: false,
            zoom_error: None,
            corrections_open: false,
        };

        // The backend starts on another thread; wire it up when it is ready.
        cx.spawn(async move |this, cx| {
            let result = backend_rx.recv().await;
            let _ = this.update(cx, |this, cx| {
                match result {
                    Ok(Ok(backend)) => {
                        this.api
                            .connect(backend.handshake.base_url(), backend.handshake.token.clone());
                        this.backend = Some(backend);
                        this.refresh_jobs(cx);
                        this.refresh_models(cx);
                        // Which keys are set decides what the speaker options say.
                        this.refresh_system(cx);
                    }
                    Ok(Err(error)) => this.toast(
                        ToastTone::Error,
                        "השירות המקומי לא עלה",
                        Some(error.to_string()),
                    ),
                    Err(_) => {}
                }
                cx.notify();
            });
        })
        .detach();
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
                    // Notices fade on their own so they never pile up.
                    this.toasts
                        .retain(|t| t.created.elapsed() < std::time::Duration::from_secs(9));
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
            created: std::time::Instant::now(),
        });
        if self.toasts.len() > 4 {
            self.toasts.remove(0);
        }
    }

    pub(crate) fn dismiss_toast(&mut self, id: u64, cx: &mut Context<Self>) {
        self.toasts.retain(|t| t.id != id);
        cx.notify();
    }

    // -- navigation --------------------------------------------------------
    pub(crate) fn set_screen(&mut self, screen: Screen, cx: &mut Context<Self>) {
        self.screen = screen;
        if screen == Screen::System && self.system.is_none() {
            self.refresh_system(cx);
        }
        cx.notify();
    }

    // -- actions -----------------------------------------------------------
    pub(crate) fn choose_files(&self, cx: &mut Context<Self>) {
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

    pub(crate) fn start_all(&self, cx: &mut Context<Self>) {
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

    pub(crate) fn cancel_job(&self, id: String, cx: &mut Context<Self>) {
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

    pub(crate) fn clear_completed(&self, cx: &mut Context<Self>) {
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
}
