//! The "new transcription" screen: pick files, review them, watch the jobs run.

use gpui_kit::base::StyledExt as _;
use gpui_kit::component::button::*;
use gpui_kit::component::Disableable as _;
use gpui_kit::prelude::FluentBuilder as _;
use gpui_kit::{
    AnyElement, Context, InteractiveElement as _, IntoElement, ParentElement as _,
    StatefulInteractiveElement as _, Styled as _, Window, div, px,
};

use crate::app::{App, PendingFile, PendingStatus};
use crate::format::*;
use crate::models::*;
use crate::rtl_text::rtl_paragraph;
use crate::ui::{self, Ic, Tone};

impl App {
    // -- upload hero ---------------------------------------------------------
    fn upload_zone(&self, cx: &mut Context<Self>) -> impl IntoElement {
        let selected = self.models.iter().find(|m| m.id == self.settings.model_id);
        let using_cloud = selected.map(|m| m.is_cloud).unwrap_or(false);

        let formats: Vec<AnyElement> = ACCEPTED_EXTENSIONS
            .iter()
            .map(|ext| {
                div()
                    .px_2()
                    .py_0p5()
                    .rounded_md()
                    .bg(ui::surface_alt())
                    .text_xs()
                    .font_medium()
                    .text_color(ui::muted())
                    .child(ext.to_uppercase())
                    .into_any_element()
            })
            .collect();

        div()
            .w_full()
            .rounded_2xl()
            .border_2()
            .border_dashed()
            .border_color(ui::accent_border())
            .bg(ui::surface())
            .shadow_sm()
            .px_8()
            .py_8()
            .v_flex()
            .items_center()
            .gap_4()
            .child(
                div()
                    .size_16()
                    .rounded_full()
                    .bg(ui::accent_bg())
                    .flex()
                    .items_center()
                    .justify_center()
                    .child(ui::icon(Ic::Upload, 28.0, ui::accent())),
            )
            .child(
                div()
                    .v_flex()
                    .items_center()
                    .gap_1()
                    .child(
                        div()
                            .text_xl()
                            .font_semibold()
                            .text_color(ui::ink())
                            .child(rtl("בחירת קבצים לתמלול")),
                    )
                    .child(
                        div()
                            .text_sm()
                            .text_color(ui::muted())
                            .child(rtl("קובצי אודיו או וידאו — אפשר לבחור כמה בבת אחת")),
                    ),
            )
            .child(
                div()
                    .flex()
                    .flex_row_reverse()
                    .flex_wrap()
                    .justify_center()
                    .gap_1p5()
                    .max_w(px(560.))
                    .children(formats),
            )
            .child(
                ui::rtl_row()
                    .gap_3()
                    .mt_1()
                    .child(
                        Button::new("choose-files")
                            .primary()
                            .icon(ui::icon(Ic::Upload, 16.0, gpui_kit::rgb(0xffffff)))
                            .label(rtl("בחירת קבצים"))
                            .on_click(cx.listener(|this, _, _, cx| this.choose_files(cx))),
                    )
                    .child(
                        Button::new("open-zoom")
                            .secondary()
                            .icon(ui::icon(Ic::Link, 16.0, ui::subtle()))
                            .label(rtl("תמלול מקישור Zoom"))
                            .on_click(cx.listener(|this, _, _, cx| this.open_zoom(cx))),
                    ),
            )
            .child(div().w_full().h_px().bg(ui::divider()))
            .child(
                div()
                    .w_full()
                    .v_flex()
                    .items_center()
                    .gap_2p5()
                    .child(
                        div()
                            .text_xs()
                            .font_medium()
                            .text_color(ui::muted())
                            .child(rtl("מנוע תמלול")),
                    )
                    .child(self.model_chips(cx))
                    .child(
                        ui::rtl_row()
                            .gap_2()
                            .text_xs()
                            .text_color(if using_cloud { ui::warning() } else { ui::muted() })
                            .child(ui::icon(
                                if using_cloud { Ic::Cloud } else { Ic::ShieldCheck },
                                14.0,
                                if using_cloud { ui::warning() } else { ui::success() },
                            ))
                            .child(rtl(if using_cloud {
                                "האודיו יישלח לשרתים של Soniox לצורך תמלול."
                            } else {
                                "הקבצים מעובדים על המחשב שלכם בלבד ואינם נשלחים לשום מקום."
                            })),
                    ),
            )
    }

    /// The engine picker as a row of selectable chips, centred.
    pub(crate) fn model_chips(&self, cx: &mut Context<Self>) -> AnyElement {
        self.model_chips_aligned(true, cx)
    }

    pub(crate) fn model_chips_aligned(&self, centered: bool, cx: &mut Context<Self>) -> AnyElement {
        if self.models.is_empty() {
            return div()
                .text_sm()
                .text_color(ui::muted())
                .child(rtl(&latin(&self.settings.model_id)))
                .into_any_element();
        }
        let mut chips: Vec<AnyElement> = Vec::new();
        for (index, model) in self.models.iter().enumerate() {
            let selected = model.id == self.settings.model_id;
            let id = model.id.clone();
            let needs_download = !model.is_cloud && !model.downloaded;
            chips.push(
                ui::rtl_row()
                    .id((if centered { "model-chip-hero" } else { "model-chip-settings" }, index))
                    .gap_2()
                    .px_3()
                    .h_9()
                    .rounded_full()
                    .border_1()
                    .cursor_pointer()
                    .text_sm()
                    .when(selected, |this| {
                        this.border_color(ui::accent())
                            .bg(ui::accent_bg())
                            .text_color(ui::accent_strong())
                            .font_medium()
                    })
                    .when(!selected, |this| {
                        this.border_color(ui::border())
                            .bg(ui::surface())
                            .text_color(ui::subtle())
                            .hover(|s| s.bg(ui::surface_alt()))
                    })
                    .on_click(cx.listener(move |this, _, _, cx| {
                        this.settings.model_id = id.clone();
                        cx.notify();
                    }))
                    .child(ui::icon(
                        if selected { Ic::CircleCheck } else if model.is_cloud { Ic::Cloud } else { Ic::Cpu },
                        15.0,
                        if selected { ui::accent() } else { ui::faint() },
                    ))
                    .child(rtl(&model.label_he))
                    .when(needs_download, |this| {
                        this.child(
                            div()
                                .px_1p5()
                                .rounded_md()
                                .bg(ui::warning_bg())
                                .text_xs()
                                .text_color(ui::warning())
                                .child(rtl("טרם הורד")),
                        )
                    })
                    .into_any_element(),
            );
        }
        div()
            .flex()
            .flex_row_reverse()
            .flex_wrap()
            .when(centered, |this| this.justify_center())
            .gap_2()
            .children(chips)
            .into_any_element()
    }

    // -- pending files ---------------------------------------------------------
    fn pending_row(&self, f: &PendingFile, cx: &mut Context<Self>) -> impl IntoElement {
        let id = f.local_id;
        let filename = f.filename.clone();
        let mut meta: Vec<String> = Vec::new();
        if f.size_bytes > 0 {
            meta.push(format_bytes(f.size_bytes));
        }
        if let Some(d) = f.duration_seconds {
            meta.push(format_clock(d));
        }
        let (status_text, tone, ic) = match f.status {
            PendingStatus::Importing => ("בודק קובץ…", Tone::Neutral, Ic::Refresh),
            PendingStatus::Ready => ("מוכן", Tone::Success, Ic::CircleCheck),
            PendingStatus::Error => ("שגיאה", Tone::Danger, Ic::CircleAlert),
        };
        let error = f.error.clone();

        ui::rtl_row()
            .gap_3()
            .py_3()
            .child(ui::icon_tile(Ic::FileMusic, Tone::Accent))
            .child(
                div()
                    .flex_1()
                    .min_w_0()
                    .v_flex()
                    .child(
                        div()
                            .truncate()
                            .text_sm()
                            .font_medium()
                            .text_color(ui::ink())
                            .child(rtl(&truncate_middle(&filename, 60))),
                    )
                    .when(!meta.is_empty(), |this| {
                        this.child(
                            div()
                                .text_xs()
                                .text_color(ui::muted())
                                .child(rtl(&meta.join(" · "))),
                        )
                    })
                    .when_some(error, |this, error| {
                        this.child(
                            div()
                                .text_xs()
                                .text_color(ui::danger())
                                .child(rtl_paragraph(error)),
                        )
                    }),
            )
            .child(ui::icon_pill(ic, status_text, tone))
            .child(
                Button::new(format!("remove-pending-{id}"))
                    .ghost()
                    .compact()
                    .icon(ui::icon(Ic::X, 16.0, ui::muted()))
                    .tooltip(rtl("הסרה"))
                    .on_click(cx.listener(move |this, _, _, cx| {
                        this.pending.retain(|p| p.local_id != id);
                        cx.notify();
                    })),
            )
    }

    fn pending_list(&self, cx: &mut Context<Self>) -> AnyElement {
        if self.pending.is_empty() {
            return div().into_any_element();
        }
        let ready_count = self
            .pending
            .iter()
            .filter(|p| p.status == PendingStatus::Ready)
            .count();
        let mut rows: Vec<AnyElement> = Vec::new();
        for (i, f) in self.pending.iter().enumerate() {
            rows.push(
                div()
                    .when(i > 0, |this| this.border_t_1().border_color(ui::divider()))
                    .child(self.pending_row(f, cx))
                    .into_any_element(),
            );
        }

        ui::pad_card()
            .child(
                ui::rtl_row()
                    .justify_between()
                    .child(ui::section_header(
                        Ic::ListChecks,
                        Tone::Accent,
                        &format!("קבצים ממתינים ({})", self.pending.len()),
                        Some("בדקו את הרשימה והתחילו בתמלול"),
                    ))
                    .child(
                        ui::rtl_row()
                            .gap_2()
                            .child(
                                Button::new("start-all")
                                    .primary()
                                    .icon(ui::icon(Ic::Play, 16.0, gpui_kit::rgb(0xffffff)))
                                    .label(rtl(&format!("התחל תמלול ({ready_count})")))
                                    .disabled(ready_count == 0)
                                    .on_click(cx.listener(|this, _, _, cx| this.start_all(cx))),
                            )
                            .child(
                                Button::new("clear-pending")
                                    .ghost()
                                    .label(rtl("נקה הכול"))
                                    .on_click(cx.listener(|this, _, _, cx| {
                                        this.pending.clear();
                                        cx.notify();
                                    })),
                            ),
                    ),
            )
            .child(div().v_flex().children(rows))
            .into_any_element()
    }

    // -- jobs ----------------------------------------------------------------
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

        let (tile_ic, tile_tone) = match status {
            JobStatus::Queued => (Ic::Clock, Tone::Neutral),
            JobStatus::Running => (Ic::Refresh, Tone::Accent),
            JobStatus::Completed => (Ic::CircleCheck, Tone::Success),
            JobStatus::Failed => (Ic::CircleAlert, Tone::Danger),
            JobStatus::Cancelled => (Ic::CircleX, Tone::Neutral),
        };

        ui::rtl_row()
            .items_start()
            .gap_3()
            .py_4()
            .child(ui::icon_tile(tile_ic, tile_tone))
            .child(
                div()
                    .flex_1()
                    .min_w_0()
                    .v_flex()
                    .gap_0p5()
                    .child(
                        ui::rtl_row()
                            .gap_2()
                            // Shortened in the middle *before* reordering:
                            // `truncate` clips the end of the laid-out line,
                            // which for reordered Hebrew is the start of the
                            // name — the part worth keeping.
                            .child(
                                div()
                                    .truncate()
                                    .text_sm()
                                    .font_semibold()
                                    .text_color(ui::ink())
                                    .child(rtl(&truncate_middle(&filename, 56))),
                            )
                            .child(status_pill(status)),
                    )
                    .child(
                        div()
                            .text_xs()
                            .text_color(ui::muted())
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
                    .when_some(error, |this, error| this.child(error_block(error))),
            )
            .child(
                ui::rtl_row()
                    .gap_2()
                    .when(has_transcript, |this| {
                        this.child(
                            Button::new(format!("open-{id_open}"))
                                .primary()
                                .compact()
                                .icon(ui::icon(Ic::FileText, 15.0, gpui_kit::rgb(0xffffff)))
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
                                .compact()
                                .icon(ui::icon(Ic::X, 15.0, ui::subtle()))
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
                                .compact()
                                .icon(ui::icon(Ic::Retry, 15.0, ui::subtle()))
                                .label(rtl("נסה שוב"))
                                .on_click(cx.listener(move |this, _, _, cx| {
                                    this.retry_job(id_retry.clone(), cx)
                                })),
                        )
                    })
                    .when(!is_active, |this| {
                        this.child(
                            Button::new(format!("delete-{id_delete}"))
                                .ghost()
                                .compact()
                                .icon(ui::icon(Ic::Trash, 16.0, ui::danger()))
                                .tooltip(rtl("מחיקה"))
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
        let active = self
            .jobs
            .iter()
            .filter(|j| matches!(j.status, JobStatus::Running | JobStatus::Queued))
            .count();
        let mut rows: Vec<AnyElement> = Vec::new();
        for (i, j) in self.jobs.iter().enumerate() {
            rows.push(
                div()
                    .when(i > 0, |this| this.border_t_1().border_color(ui::divider()))
                    .child(self.job_row(j, cx))
                    .into_any_element(),
            );
        }
        let subtitle = if self.jobs.is_empty() {
            "אין עבודות כרגע".to_string()
        } else if active > 0 {
            format!("{active} פעילות · {finished} הסתיימו")
        } else {
            format!("{finished} עבודות הסתיימו")
        };

        ui::pad_card()
            .child(
                ui::rtl_row()
                    .justify_between()
                    .child(ui::section_header(Ic::AudioLines, Tone::Neutral, "עבודות", Some(&subtitle)))
                    .when(finished > 0, |this| {
                        this.child(
                            Button::new("clear-completed")
                                .ghost()
                                .compact()
                                .icon(ui::icon(Ic::Eraser, 15.0, ui::muted()))
                                .label(rtl("ניקוי עבודות שהסתיימו"))
                                .on_click(cx.listener(|this, _, _, cx| this.clear_completed(cx))),
                        )
                    }),
            )
            .when(self.jobs.is_empty(), |this| {
                this.child(ui::empty_state(
                    Ic::Mic,
                    "אין עדיין עבודות",
                    "בחרו קובץ אודיו או וידאו כדי להתחיל בתמלול.",
                ))
            })
            .when(!self.jobs.is_empty(), |this| this.child(div().v_flex().children(rows)))
    }

    pub(crate) fn queue_screen(&self, cx: &mut Context<Self>) -> impl IntoElement {
        let banner = self.model_banner(cx);
        let upload = self.upload_zone(cx).into_any_element();
        let pending = self.pending_list(cx);
        let speakers = self.speakers_setup_panel(cx);
        let settings = self.render_settings_panel(cx);
        let jobs = self.jobs_list(cx).into_any_element();
        div()
            .w_full()
            .p_8()
            .v_flex()
            .gap_6()
            .child(ui::page_title(
                "תמלול חדש",
                "תמלול עברית מדויק, מקומי ופרטי — בלי להעלות דבר לשום מקום",
            ))
            .child(banner)
            .child(upload)
            .child(pending)
            .child(speakers)
            .child(settings)
            .child(jobs)
    }
}

pub(crate) fn status_pill(status: JobStatus) -> impl IntoElement {
    let (label, tone) = match status {
        JobStatus::Queued => ("בתור", Tone::Neutral),
        JobStatus::Running => ("פועל", Tone::Accent),
        JobStatus::Completed => ("הושלם", Tone::Success),
        JobStatus::Failed => ("נכשל", Tone::Danger),
        JobStatus::Cancelled => ("בוטל", Tone::Neutral),
    };
    ui::pill(label, tone)
}

fn progress_block(percent: f64, stage: JobStage, eta: Option<f64>) -> impl IntoElement {
    let p = percent.clamp(0.0, 1.0);
    div()
        .w_full()
        .mt_2()
        .v_flex()
        .gap_1p5()
        // Reversed fill, so the bar grows from the right like every other
        // right-to-left progress indicator.
        .child(ui::progress_bar(p as f32, Tone::Accent))
        .child(
            ui::rtl_row()
                .gap_2()
                .text_xs()
                .text_color(ui::muted())
                .child(
                    div()
                        .font_semibold()
                        .text_color(ui::accent_strong())
                        .child(rtl(&format_percent(percent))),
                )
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
        .child(
            ui::notice(Ic::CircleAlert, Tone::Danger).child(
                div()
                    .flex_1()
                    .min_w_0()
                    .v_flex()
                    .gap_0p5()
                    .child(
                        div()
                            .text_sm()
                            .font_medium()
                            .text_color(ui::danger_text())
                            .child(rtl_paragraph(&error.message)),
                    )
                    .child(
                        div()
                            .text_xs()
                            .text_color(ui::danger_text())
                            .child(rtl_paragraph(&error.remedy)),
                    )
                    .when(!error.detail.is_empty(), |this| {
                        this.child(
                            div()
                                .text_xs()
                                .text_color(ui::muted())
                                .child(rtl_paragraph(&error.detail)),
                        )
                    }),
            ),
        )
}

#[allow(dead_code)]
fn _unused(_: &mut Window) {}
