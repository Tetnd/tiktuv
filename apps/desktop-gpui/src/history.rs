//! Local job history: reopen, retry, delete records, delete generated files.

use gpui_kit::base::StyledExt as _;
use gpui_kit::component::button::*;
use gpui_kit::prelude::FluentBuilder as _;
use gpui_kit::{
    AnyElement, Context, InteractiveElement as _, IntoElement, ParentElement as _,
    StatefulInteractiveElement as _, Styled as _, div,
};

use crate::app::{App, ToastTone};
use crate::format::*;
use crate::models::*;
use crate::queue::status_pill;
use crate::rtl_text::rtl_paragraph;
use crate::ui::{self, Ic, Tone};

impl App {
    pub fn render_history(&self, cx: &mut Context<Self>) -> AnyElement {
        let completed = self
            .jobs
            .iter()
            .filter(|j| j.status == JobStatus::Completed)
            .count();
        let total_duration: f64 = self.jobs.iter().map(|j| j.duration).sum();

        let mut rows: Vec<AnyElement> = Vec::new();
        for (i, job) in self.jobs.iter().enumerate() {
            rows.push(
                div()
                    .when(i > 0, |this| this.border_t_1().border_color(ui::divider()))
                    .child(self.history_row(job, cx))
                    .into_any_element(),
            );
        }

        div()
            .w_full()
            .p_8()
            .v_flex()
            .gap_6()
            .child(ui::page_title(
                "היסטוריית תמלולים",
                "כל התמלולים נשמרים אצלכם במחשב — פתחו, ערכו וייצאו מחדש בכל עת",
            ))
            .when(!self.jobs.is_empty(), |this| {
                this.child(
                    ui::rtl_row()
                        .gap_3()
                        .child(stat_tile(Ic::FileText, Tone::Accent, "תמלולים", self.jobs.len().to_string()))
                        .child(stat_tile(Ic::CircleCheck, Tone::Success, "הושלמו", completed.to_string()))
                        .child(stat_tile(
                            Ic::Clock,
                            Tone::Neutral,
                            "זמן הקלטה כולל",
                            format_clock(total_duration),
                        )),
                )
            })
            .child(if self.jobs.is_empty() {
                ui::pad_card()
                    .child(ui::empty_state(
                        Ic::Clock,
                        "ההיסטוריה ריקה",
                        "תמלולים שתבצעו יופיעו כאן, ותוכלו לפתוח אותם שוב בכל זמן.",
                    ))
                    .into_any_element()
            } else {
                ui::card().px_5().py_1().children(rows).into_any_element()
            })
            .child(
                ui::rtl_row().gap_2().items_start().child(ui::icon(Ic::Info, 14.0, ui::faint())).child(
                    div().flex_1().min_w_0().text_xs().text_color(ui::muted()).child(rtl_paragraph(
                        "מחיקת רשומה מסירה את התמליל ואת עותק המדיה הפנימי, אך משאירה קבצי פלט שייצאתם. למחיקתם השתמשו באפשרות \"מחק קבצים\".",
                    )),
                ),
            )
            .into_any_element()
    }

    fn history_row(&self, job: &JobSummary, cx: &mut Context<Self>) -> impl IntoElement {
        let id = job.id.clone();
        let filename = job.filename.clone();
        let source = job.source_path.clone();
        let status = job.status;
        let duration = job.duration;
        let size = job.size_bytes;
        let created = job.created_at.clone();
        let opened = job.last_opened_at.clone();
        let has_transcript = job.has_transcript;
        let error = job.error.clone();
        let outputs: Vec<(String, String)> = job
            .output_paths
            .iter()
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect();

        let (tile_ic, tile_tone) = match status {
            JobStatus::Queued => (Ic::Clock, Tone::Neutral),
            JobStatus::Running => (Ic::Refresh, Tone::Accent),
            JobStatus::Completed => (Ic::FileText, Tone::Accent),
            JobStatus::Failed => (Ic::CircleAlert, Tone::Danger),
            JobStatus::Cancelled => (Ic::CircleX, Tone::Neutral),
        };

        // Output-file links (open with the OS default handler).
        let mut output_chips: Vec<AnyElement> = Vec::new();
        for (format, path) in &outputs {
            let path2 = path.clone();
            output_chips.push(
                ui::rtl_row()
                    .id(format!("open-{id}-{format}"))
                    .gap_1p5()
                    .px_2()
                    .h_6()
                    .rounded_md()
                    .border_1()
                    .border_color(ui::border())
                    .cursor_pointer()
                    .text_xs()
                    .font_medium()
                    .text_color(ui::subtle())
                    .hover(|s| s.bg(ui::surface_alt()))
                    .on_click(cx.listener(move |this, _, _, _| this.open_file(path2.clone())))
                    .child(ui::icon(Ic::ExternalLink, 12.0, ui::muted()))
                    .child(format.to_uppercase())
                    .into_any_element(),
            );
        }

        let id_open = id.clone();
        let id_retry = id.clone();
        let id_outputs = id.clone();
        let id_delete = id.clone();

        ui::rtl_row()
            .items_start()
            .gap_4()
            .py_4()
            .child(ui::icon_tile(tile_ic, tile_tone))
            .child(
                div()
                    .flex_1()
                    .min_w_0()
                    .v_flex()
                    .gap_1()
                    .child(
                        ui::rtl_row()
                            .gap_2()
                            // Shortened in the middle before reordering, so the
                            // clip does not eat the start of the Hebrew name.
                            .child(
                                div()
                                    .truncate()
                                    .text_sm()
                                    .font_semibold()
                                    .text_color(ui::ink())
                                    .child(rtl(&truncate_middle(&filename, 48))),
                            )
                            .child(status_pill(status)),
                    )
                    .child(
                        ui::rtl_row()
                            .gap_3()
                            .flex_wrap()
                            .text_xs()
                            .text_color(ui::muted())
                            .child(meta_item(Ic::Clock, format_clock(duration)))
                            .child(meta_item(Ic::HardDrive, format_bytes(size)))
                            .child(meta_item(Ic::Plus, format!("נוצר {}", format_date_time(&created))))
                            .when_some(opened, |this, opened| {
                                this.child(meta_item(
                                    Ic::FolderOpen,
                                    format!("נפתח {}", format_date_time(&opened)),
                                ))
                            }),
                    )
                    .child(
                        div()
                            .truncate()
                            .text_xs()
                            .text_color(ui::faint())
                            .child(rtl(&latin(&truncate_middle(&source, 60)))),
                    )
                    .when(!output_chips.is_empty(), |this| {
                        this.child(
                            ui::rtl_row()
                                .flex_wrap()
                                .gap_1p5()
                                .pt_1()
                                .child(div().text_xs().text_color(ui::muted()).child(rtl("קבצי פלט:")))
                                .children(output_chips),
                        )
                    })
                    .when_some(error, |this, error| {
                        this.child(
                            div()
                                .text_xs()
                                .text_color(ui::danger())
                                .child(rtl_paragraph(&error.message)),
                        )
                    })
                    .child(
                        ui::rtl_row()
                            .gap_1p5()
                            .pt_2()
                            .flex_wrap()
                            .when(has_transcript, |this| {
                                this.child(
                                    Button::new(format!("open-{id_open}"))
                                        .primary()
                                        .compact()
                                        .icon(ui::icon(Ic::FileText, 15.0, gpui_kit::rgb(0xffffff)))
                                        .label(rtl("פתיחה"))
                                        .on_click(cx.listener(move |this, _, window, cx| {
                                            this.open_transcript(id_open.clone(), window, cx)
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
                            .when(!outputs.is_empty(), |this| {
                                this.child(
                                    Button::new(format!("del-out-{id_outputs}"))
                                        .ghost()
                                        .compact()
                                        .label(rtl("מחק קבצים"))
                                        .on_click(cx.listener(move |this, _, _, cx| {
                                            this.delete_outputs(id_outputs.clone(), cx)
                                        })),
                                )
                            })
                            .child(
                                Button::new(format!("del-{id_delete}"))
                                    .ghost()
                                    .compact()
                                    .icon(ui::icon(Ic::Trash, 15.0, ui::danger()))
                                    .label(rtl("מחק רשומה"))
                                    .on_click(cx.listener(move |this, _, _, cx| {
                                        this.delete_job(id_delete.clone(), cx)
                                    })),
                            ),
                    ),
            )
    }

    fn delete_outputs(&self, id: String, cx: &mut Context<Self>) {
        let api = self.api.clone();
        cx.spawn(async move |this, cx| {
            let removed = api.delete_job_outputs(&id).await.unwrap_or(0);
            let jobs = api.list_jobs().await.unwrap_or_default();
            let _ = this.update(cx, |this, cx| {
                this.jobs = jobs;
                this.toast(
                    ToastTone::Success,
                    format!("נמחקו {removed} קבצי פלט."),
                    None,
                );
                cx.notify();
            });
        })
        .detach();
    }

    fn open_file(&self, path: String) {
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            let _ = std::process::Command::new("cmd")
                .args(["/C", "start", "", &path])
                .creation_flags(0x0800_0000) // CREATE_NO_WINDOW
                .spawn();
        }
        #[cfg(not(windows))]
        {
            let _ = std::process::Command::new("xdg-open").arg(&path).spawn();
        }
    }
}

fn meta_item(ic: Ic, text: String) -> impl IntoElement {
    ui::rtl_row()
        .gap_1()
        .child(ui::icon(ic, 12.0, ui::faint()))
        .child(rtl(&text))
}

fn stat_tile(ic: Ic, tone: Tone, label: &str, value: String) -> impl IntoElement {
    ui::card()
        .flex_1()
        .min_w_0()
        .p_4()
        .child(
            ui::rtl_row()
                .gap_3()
                .child(ui::icon_tile(ic, tone))
                .child(
                    div()
                        .flex_1()
                        .min_w_0()
                        .v_flex()
                        .child(div().text_base().font_bold().text_color(ui::ink()).child(rtl(&value)))
                        .child(div().text_xs().text_color(ui::muted()).child(rtl(label))),
                ),
        )
}
