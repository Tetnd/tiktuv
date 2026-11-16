//! Local job history: reopen, retry, delete records, delete generated files.

use gpui_kit::base::StyledExt as _;
use gpui_kit::component::button::*;
use gpui_kit::component::ActiveTheme;
use gpui_kit::prelude::FluentBuilder as _;
use gpui_kit::{
    AnyElement, Context, IntoElement, ParentElement as _, Styled as _, div, px, rgb,
};

use crate::app::{status_pill, App, ToastTone};
use crate::format::*;
use crate::rtl_text::rtl_paragraph;
use crate::models::*;

impl App {
    pub fn render_history(&self, cx: &mut Context<Self>) -> AnyElement {
        if self.jobs.is_empty() {
            return div()
                .p_10()
                .child(div().text_base().text_color(rgb(0x6b7280)).child(rtl("ההיסטוריה ריקה")))
                .into_any_element();
        }

        let mut rows: Vec<AnyElement> = Vec::new();
        for job in &self.jobs {
            rows.push(self.history_row(job, cx).into_any_element());
        }

        div()
            .w_full()
            .p_6()
            .v_flex()
            .gap_3()
            .child(div().text_lg().child(rtl("היסטוריית תמלולים")))
            .child(
                div()
                    .rounded_lg()
                    .border_1()
                    .border_color(rgb(0xe5e7eb))
                    .bg(cx.theme().background)
                    .v_flex()
                    .children(rows),
            )
            .child(
                div()
                    .text_xs()
                    .text_color(rgb(0x6b7280))
                    .child(rtl_paragraph("מחיקת רשומה מסירה את התמליל ואת עותק המדיה הפנימי, אך משאירה קבצי פלט שייצאתם. למחיקתם השתמשו באפשרות ״מחק קבצים״.")),
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

        // Output-file links (open with the OS default handler).
        let mut output_buttons: Vec<AnyElement> = Vec::new();
        for (format, path) in &outputs {
            let path2 = path.clone();
            output_buttons.push(
                Button::new(format!("open-{id}-{format}"))
                    .link()
                    .label(format.to_uppercase())
                    .on_click(cx.listener(move |this, _, _, _| this.open_file(path2.clone())))
                    .into_any_element(),
            );
        }

        let id_open = id.clone();
        let id_retry = id.clone();
        let id_outputs = id.clone();
        let id_delete = id.clone();

        div()
            .flex()
            .flex_row_reverse()
            .items_start()
            .gap_3()
            .px_4()
            .py_3()
            .border_b_1()
            .border_color(rgb(0xf3f4f6))
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
                            // Shortened in the middle before reordering, so the
                            // clip does not eat the start of the Hebrew name.
                            .child(div().truncate().text_sm().child(rtl(&truncate_middle(&filename, 48))))
                            .child(status_pill(status)),
                    )
                    .child(div().text_xs().text_color(rgb(0x9ca3af)).child(rtl(&latin(&truncate_middle(&source, 60)))))
                    .when(error.is_some(), |this| {
                        this.child(
                            div()
                                .text_xs()
                                .text_color(rgb(0xdc2626))
                                .child(rtl_paragraph(&error.clone().unwrap().message)),
                        )
                    }),
            )
            .child(
                div()
                    .w(px(120.))
                    .text_xs()
                    .text_color(rgb(0x6b7280))
                    .v_flex()
                    .child(rtl(&format_clock(duration)))
                    .child(rtl(&format_bytes(size))),
            )
            .child(
                div()
                    .w(px(150.))
                    .text_xs()
                    .text_color(rgb(0x6b7280))
                    .v_flex()
                    .child(rtl(&format!("נוצר: {}", format_date_time(&created))))
                    .child(rtl(&format!(
                        "נפתח: {}",
                        opened.as_deref().map(format_date_time).unwrap_or("—".to_string())
                    ))),
            )
            .child(
                div()
                    .w(px(140.))
                    .v_flex()
                    .gap_1()
                    .when(output_buttons.is_empty(), |this| {
                        this.child(div().text_xs().text_color(rgb(0x9ca3af)).child(rtl("—")))
                    })
                    .when(!output_buttons.is_empty(), |this| {
                        this.child(div().flex().flex_row_reverse().flex_wrap().gap_1().children(output_buttons))
                    }),
            )
            .child(
                div().flex().flex_row_reverse().flex_wrap().gap_1().when(has_transcript, |this| {
                    this.child(
                        Button::new(format!("open-{id_open}"))
                            .ghost()
                            .compact()
                            .label(rtl("פתיחה"))
                            .on_click(cx.listener(move |this, _, window, cx| {
                                this.open_transcript(id_open.clone(), window, cx)
                            })),
                    )
                })
                .when(matches!(status, JobStatus::Failed | JobStatus::Cancelled), |this| {
                    this.child(
                        Button::new(format!("retry-{id_retry}"))
                            .ghost()
                            .compact()
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
                        .label(rtl("מחק רשומה"))
                        .on_click(cx.listener(move |this, _, _, cx| {
                            this.delete_job(id_delete.clone(), cx)
                        })),
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
