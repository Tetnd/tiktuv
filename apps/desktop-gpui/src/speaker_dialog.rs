//! "זיהוי דוברים": recognise the speakers of the open transcript.
//!
//! Two methods, chosen knowingly because they send different things away:
//! Soniox listens to the recording (accurate, uploads the audio); DeepSeek reads
//! the transcript text (no audio leaves the machine, less certain when voices
//! are similar and replies are short).

use gpui_kit::base::StyledExt as _;
use gpui_kit::component::button::*;
use gpui_kit::component::Disableable as _;
use gpui_kit::prelude::FluentBuilder as _;
use gpui_kit::{
    AnyElement, Context, InteractiveElement as _, IntoElement, ParentElement as _,
    StatefulInteractiveElement as _, Styled as _, div, px,
};

use crate::app::{App, ToastTone};
use crate::dialogs::modal;
use crate::format::*;
use crate::rtl_text::rtl_paragraph;
use crate::settings::segmented;
use crate::ui::{self, Ic, Tone};

const METHODS: [&str; 2] = ["soniox", "deepseek"];

impl App {
    pub(crate) fn open_speakers(&mut self, cx: &mut Context<Self>) {
        // Pick the privacy-preserving option when both are available.
        let soniox = self.soniox_status.as_ref().map(|s| s.configured).unwrap_or(false);
        let deepseek = self.deepseek_status.as_ref().map(|s| s.configured).unwrap_or(false);
        self.speakers_method = if deepseek || !soniox { 1 } else { 0 };
        self.speakers_busy = false;
        self.speakers_open = true;
        if self.soniox_status.is_none() || self.deepseek_status.is_none() {
            self.refresh_system(cx);
        }
        cx.notify();
    }

    /// Look for names of speakers who are still numbered, without re-recognising.
    pub(crate) fn find_speaker_names(&mut self, window: &mut gpui_kit::Window, cx: &mut Context<Self>) {
        let Some(e) = &self.editor else { return };
        if e.transcript.partial {
            return;
        }
        let job_id = e.job_id.clone();
        let segments = e.segments.clone();
        let speakers = e.speakers.clone();
        self.toast(ToastTone::Info, "מחפש שמות של הדוברים…".to_string(), None);
        cx.notify();
        let api = self.api.clone();
        cx.spawn_in(window, async move |this, cx| {
            // Names typed a moment ago are saved first, so they are seen as
            // already chosen and never overwritten.
            let saved = api.save_transcript(&job_id, &segments, &speakers).await;
            let result = match saved {
                Ok(_) => api.find_speaker_names(&job_id).await,
                Err(err) => Err(err),
            };
            let _ = this.update_in(cx, |this, window, cx| {
                match result {
                    Ok(response) => {
                        this.open_transcript(job_id.clone(), window, cx);
                        this.toast(
                            ToastTone::Success,
                            response
                                .notes
                                .first()
                                .cloned()
                                .unwrap_or_else(|| "החיפוש הסתיים.".to_string()),
                            None,
                        );
                    }
                    Err(err) => this.toast(ToastTone::Error, err.message, Some(err.remedy)),
                }
                cx.notify();
            });
        })
        .detach();
    }

    fn close_speakers(&mut self, cx: &mut Context<Self>) {
        if self.speakers_busy {
            return;
        }
        self.speakers_open = false;
        cx.notify();
    }

    fn run_identify_speakers(
        &mut self,
        window: &mut gpui_kit::Window,
        cx: &mut Context<Self>,
    ) {
        let Some(e) = &self.editor else { return };
        if self.speakers_busy || e.transcript.partial {
            return;
        }
        let job_id = e.job_id.clone();
        let segments = e.segments.clone();
        let speakers = e.speakers.clone();
        let method = METHODS[self.speakers_method.min(1)].to_string();
        let names = self.speakers_names;
        let expected = if self.speakers_method == 1 && self.speakers_expected > 0 {
            Some(self.speakers_expected as u32 + 1)
        } else {
            None
        };
        self.speakers_busy = true;
        cx.notify();

        let api = self.api.clone();
        cx.spawn_in(window, async move |this, cx| {
            // Write the user's edits first, so identification works on what
            // they see and nothing typed since the last autosave is lost.
            let saved = api.save_transcript(&job_id, &segments, &speakers).await;
            let result = match saved {
                Ok(_) => api.identify_speakers(&job_id, &method, names, expected).await,
                Err(err) => Err(err),
            };
            let _ = this.update_in(cx, |this, window, cx| {
                this.speakers_busy = false;
                match result {
                    Ok(response) => {
                        this.speakers_open = false;
                        // Reopen the transcript so the editor shows the new
                        // lines and the speaker names fields are rebuilt.
                        this.open_transcript(job_id.clone(), window, cx);
                        let detail = if response.notes.is_empty() {
                            None
                        } else {
                            Some(response.notes.join(" · "))
                        };
                        this.toast(
                            ToastTone::Success,
                            if response.speaker_count == 1 {
                                "זוהה דובר אחד בתמליל.".to_string()
                            } else {
                                format!("זוהו {} דוברים בתמליל.", response.speaker_count)
                            },
                            detail,
                        );
                    }
                    Err(err) => this.toast(ToastTone::Error, err.message, Some(err.remedy)),
                }
                cx.notify();
            });
        })
        .detach();
    }

    pub(crate) fn speakers_overlay(&self, cx: &mut Context<Self>) -> AnyElement {
        let soniox_ok = self.soniox_status.as_ref().map(|s| s.configured).unwrap_or(false);
        let deepseek_ok = self.deepseek_status.as_ref().map(|s| s.configured).unwrap_or(false);
        let method = self.speakers_method.min(1);
        let busy = self.speakers_busy;
        let key_missing = if method == 0 { !soniox_ok } else { !deepseek_ok };
        let can_name = deepseek_ok;
        let names_on = self.speakers_names && can_name;

        let option = |index: usize,
                      ic: Ic,
                      title: &'static str,
                      sends: &'static str,
                      body: &'static str,
                      ok: bool,
                      cx: &mut Context<Self>| {
            method_card(("speaker-method", index), method == index, ic, title, sends, body, ok)
                .on_click(cx.listener(move |this, _, _, cx| {
                    if !this.speakers_busy {
                        this.speakers_method = index;
                        cx.notify();
                    }
                }))
        };

        let soniox_card = option(
            0,
            Ic::Mic,
            "לפי הקול — Soniox",
            "נשלחת ההקלטה לשרתי Soniox",
            "הכי מדויק. מפריד בין הקולות עצמם, ולכן עובד גם כשהתשובות קצרות או כשהדוברים דומים בתוכן.",
            soniox_ok,
            cx,
        );
        let deepseek_card = option(
            1,
            Ic::Sparkles,
            "לפי התוכן — DeepSeek",
            "נשלח רק טקסט התמליל",
            "קורא את השיחה ומבין מי שואל ומי עונה. טוב לראיונות, שיחות וישיבות עם תפקידים ברורים; פחות מדויק כשכמה דוברים מחליפים תשובות קצרות.",
            deepseek_ok,
            cx,
        );

        let expected_control = segmented(
            "speakers-expected",
            &["לא יודע", "2", "3", "4", "5"],
            self.speakers_expected,
            cx,
            |this, idx| this.speakers_expected = idx,
        );

        modal(
            cx,
            div()
                .w(px(560.))
                .v_flex()
                .gap_4()
                .child(ui::section_header(
                    Ic::Users,
                    Tone::Accent,
                    "זיהוי דוברים",
                    Some("קובע מי אמר מה ומסמן כל שורה בתמליל"),
                ))
                .child(div().v_flex().gap_2().child(soniox_card).child(deepseek_card))
                .when(method == 1, |this| {
                    this.child(
                        ui::rtl_row()
                            .justify_between()
                            .gap_3()
                            .child(
                                div()
                                    .v_flex()
                                    .child(
                                        div()
                                            .text_sm()
                                            .font_medium()
                                            .text_color(ui::ink())
                                            .child(rtl("כמה אנשים מדברים?")),
                                    )
                                    .child(
                                        div()
                                            .text_xs()
                                            .text_color(ui::muted())
                                            .child(rtl("אם ידוע, זה משפר את הדיוק")),
                                    ),
                            )
                            .child(expected_control),
                    )
                })
                .child(
                    div()
                        .v_flex()
                        .gap_0p5()
                        .child(
                            gpui_kit::component::switch::Switch::new("speakers-names")
                                .label(rtl("חיפוש שמות הדוברים"))
                                .checked(names_on)
                                .disabled(!can_name)
                                .on_change(cx.listener(|this, value, _, cx| {
                                    this.speakers_names = *value;
                                    cx.notify();
                                })),
                        )
                        .child(
                            div().pr_11().text_xs().text_color(ui::muted()).child(rtl_paragraph(
                                if can_name {
                                    "מוסיף שם אמיתי רק כשהוא נאמר בבירור בהקלטה (״אני דנה״), עם ציטוט כראיה. אפשר לשנות כל שם אחר כך."
                                } else {
                                    "דורש מפתח DeepSeek."
                                },
                            )),
                        ),
                )
                .child(
                    ui::notice(Ic::Info, Tone::Neutral).child(
                        div().flex_1().min_w_0().text_xs().text_color(ui::subtle()).child(
                            rtl_paragraph(
                                "הפעולה מחליפה את חלוקת הדוברים הקיימת, ועשויה לפצל שורות שבהן מדברים כמה אנשים. טקסט התמליל לא משתנה.",
                            ),
                        ),
                    ),
                )
                .when(key_missing, |this| {
                    this.child(
                        ui::notice(Ic::TriangleAlert, Tone::Warning).child(
                            div()
                                .flex_1()
                                .min_w_0()
                                .text_xs()
                                .text_color(ui::warning())
                                .child(rtl_paragraph(
                                    "השיטה שנבחרה דורשת מפתח API. אפשר להוסיף אותו במסך ״מערכת ומודלים״.",
                                )),
                        ),
                    )
                })
                .child(
                    ui::rtl_row()
                        .gap_2()
                        .child(
                            Button::new("run-speakers")
                                .primary()
                                .icon(ui::icon(Ic::Users, 15.0, gpui_kit::rgb(0xffffff)))
                                .label(rtl(if busy { "מזהה דוברים…" } else { "זהה דוברים" }))
                                .disabled(busy || key_missing)
                                .on_click(cx.listener(|this, _, window, cx| {
                                    this.run_identify_speakers(window, cx)
                                })),
                        )
                        .child(
                            Button::new("cancel-speakers")
                                .ghost()
                                .label(rtl("ביטול"))
                                .disabled(busy)
                                .on_click(cx.listener(|this, _, _, cx| this.close_speakers(cx))),
                        )
                        .when(busy, |this| {
                            this.child(
                                div().text_xs().text_color(ui::muted()).child(rtl(
                                    "זה עשוי לקחת דקה או יותר, תלוי באורך ההקלטה…",
                                )),
                            )
                        }),
                ),
        )
    }
}

/// One selectable way of recognising speakers: what it is, what it sends away,
/// and whether its key is set. The caller attaches the click.
pub(crate) fn method_card(
    id: (&'static str, usize),
    selected: bool,
    ic: Ic,
    title: &'static str,
    sends: &'static str,
    body: &'static str,
    ok: bool,
) -> gpui_kit::Stateful<gpui_kit::Div> {
    ui::rtl_row()
        .id(id)
        .items_start()
        .gap_3()
        .p_4()
        .rounded_xl()
        .border_1()
        .cursor_pointer()
        .when(selected, |this| this.border_color(ui::accent()).bg(ui::accent_bg()))
        .when(!selected, |this| {
            this.border_color(ui::border()).hover(|s| s.bg(ui::surface_alt()))
        })
        .child(ui::icon_tile(ic, if selected { Tone::Accent } else { Tone::Neutral }))
        .child(
            div()
                .flex_1()
                .min_w_0()
                .v_flex()
                .gap_1()
                .child(
                    ui::rtl_row()
                        .gap_2()
                        .child(
                            div()
                                .text_sm()
                                .font_semibold()
                                .text_color(ui::ink())
                                .child(rtl(title)),
                        )
                        .child(if ok {
                            ui::pill("מפתח מוגדר", Tone::Success)
                        } else {
                            ui::pill("חסר מפתח API", Tone::Warning)
                        }),
                )
                .child(div().text_xs().text_color(ui::muted()).child(rtl_paragraph(body)))
                .child(
                    ui::rtl_row()
                        .gap_1p5()
                        .text_xs()
                        .text_color(ui::subtle())
                        .child(ui::icon(Ic::Lock, 12.0, ui::faint()))
                        .child(rtl(sends)),
                ),
        )
        .child(ui::icon(
            if selected { Ic::CircleCheck } else { Ic::Plus },
            18.0,
            if selected { ui::accent() } else { ui::faint() },
        ))
}

impl App {
    /// "זיהוי דוברים" on the new-transcription screen: the same choices as the
    /// editor's dialog, made once, up front, and applied to the whole recording
    /// when it finishes transcribing.
    pub(crate) fn speakers_setup_panel(&self, cx: &mut Context<Self>) -> AnyElement {
        let on = self.settings.enable_diarization;
        let soniox_ok = self.soniox_status.as_ref().map(|s| s.configured).unwrap_or(false);
        let deepseek_ok = self.deepseek_status.as_ref().map(|s| s.configured).unwrap_or(false);
        // "auto" never uploads audio, so it reads as the text method.
        let by_voice = self.settings.speaker_method == "soniox";
        let engine_is_soniox = self
            .models
            .iter()
            .find(|m| m.id == self.settings.model_id)
            .map(|m| m.is_cloud)
            .unwrap_or(false);

        let summary = if !on {
            "כבוי — התמליל יישמר בלי חלוקה לדוברים".to_string()
        } else if engine_is_soniox {
            "Soniox מפריד את הדוברים לפי הקול כבר בתמלול".to_string()
        } else if by_voice {
            "לפי הקול (Soniox) — בסוף התמלול".to_string()
        } else {
            "לפי התוכן (DeepSeek) — בסוף התמלול".to_string()
        };

        let expected = self.settings.expected_speakers.map(|n| (n.max(2) - 1) as usize).unwrap_or(0);
        let expected_control = segmented(
            "setup-expected",
            &["לא יודע", "2", "3", "4", "5"],
            expected.min(4),
            cx,
            |this, idx| {
                this.settings.expected_speakers = if idx == 0 { None } else { Some(idx as u32 + 1) };
            },
        );

        let soniox_card = method_card(
            ("setup-method", 0),
            by_voice,
            Ic::Mic,
            "לפי הקול — Soniox",
            "נשלחת ההקלטה לשרתי Soniox",
            "הכי מדויק. מפריד בין הקולות עצמם, ולכן עובד גם כשהתשובות קצרות או כשהדוברים דומים בתוכן.",
            soniox_ok,
        )
        .on_click(cx.listener(|this, _, _, cx| {
            this.settings.speaker_method = "soniox".to_string();
            cx.notify();
        }));
        let deepseek_card = method_card(
            ("setup-method", 1),
            !by_voice,
            Ic::Sparkles,
            "לפי התוכן — DeepSeek",
            "נשלח רק טקסט התמליל",
            "קורא את השיחה ומבין מי שואל ומי עונה. טוב לראיונות, שיחות וישיבות עם תפקידים ברורים; פחות מדויק כשכמה דוברים מחליפים תשובות קצרות.",
            deepseek_ok,
        )
        .on_click(cx.listener(|this, _, _, cx| {
            this.settings.speaker_method = "deepseek".to_string();
            cx.notify();
        }));

        ui::pad_card()
            .child(
                ui::rtl_row()
                    .justify_between()
                    .gap_3()
                    .child(ui::section_header(Ic::Users, Tone::Accent, "זיהוי דוברים", Some(&summary)))
                    .child(
                        gpui_kit::component::switch::Switch::new("setup-speakers-on")
                            .checked(on)
                            .on_change(cx.listener(|this, value, _, cx| {
                                this.settings.enable_diarization = *value;
                                cx.notify();
                            })),
                    ),
            )
            .when(on && !engine_is_soniox, |this| {
                this.child(div().v_flex().gap_2().child(soniox_card).child(deepseek_card))
                    .when(!by_voice, |this| {
                        this.child(
                            ui::rtl_row()
                                .justify_between()
                                .gap_3()
                                .child(
                                    div()
                                        .v_flex()
                                        .child(
                                            div()
                                                .text_sm()
                                                .font_medium()
                                                .text_color(ui::ink())
                                                .child(rtl("כמה אנשים מדברים?")),
                                        )
                                        .child(
                                            div()
                                                .text_xs()
                                                .text_color(ui::muted())
                                                .child(rtl("אם ידוע, זה משפר את הדיוק")),
                                        ),
                                )
                                .child(expected_control),
                        )
                    })
            })
            .when(on, |this| {
                this.child(
                    div()
                        .v_flex()
                        .gap_0p5()
                        .child(
                            gpui_kit::component::switch::Switch::new("setup-speaker-names")
                                .label(rtl("חיפוש שמות הדוברים"))
                                .checked(self.settings.suggest_speaker_names && deepseek_ok)
                                .disabled(!deepseek_ok)
                                .on_change(cx.listener(|this, value, _, cx| {
                                    this.settings.suggest_speaker_names = *value;
                                    cx.notify();
                                })),
                        )
                        .child(
                            div().pr_11().text_xs().text_color(ui::muted()).child(rtl_paragraph(
                                if deepseek_ok {
                                    "מוסיף שם אמיתי במקום ״דובר 1״ רק כשהוא נאמר בבירור בהקלטה (״אני דנה״), עם ציטוט כראיה."
                                } else {
                                    "דורש מפתח DeepSeek."
                                },
                            )),
                        ),
                )
            })
            .into_any_element()
    }
}
