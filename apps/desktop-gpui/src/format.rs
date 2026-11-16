//! Hebrew display formatting, mirroring `apps/frontend/src/lib/format.ts`.
//!
//! Direction is the text system's job (as `dir="rtl"` was in the web app); these
//! helpers only format values and, like the web originals, wrap Latin/digit runs
//! in left-to-right marks so a bidi renderer keeps them together inside Hebrew.

use crate::models::{ExportFormat, JobStage, JobStatus};

use unicode_bidi::{BidiInfo, Level};

/// Reorder text into visual order so that gpui's left-to-right renderer shows
/// Hebrew (and mixed Hebrew/Latin) correctly right-to-left. For pure Hebrew
/// this is a plain reversal; numbers and Latin runs are kept intact. Pairing
/// characters (parentheses, brackets…) are mirrored, as the bidi reorder alone
/// does not do that.
pub fn rtl(text: &str) -> String {
    if text.is_empty() {
        return String::new();
    }

    // Step 1: mirror pairing characters inside right-to-left runs.
    let info = BidiInfo::new(text, Some(Level::rtl()));
    let Some(para) = info.paragraphs.first() else {
        return text.to_string();
    };
    let levels = info.reordered_levels_per_char(para, para.range.clone());
    let mut mirrored = String::with_capacity(text.len());
    for (ch, level) in text.chars().zip(levels.iter()) {
        if level.is_rtl() {
            mirrored.push(unicode_bidi_mirroring::get_mirrored(ch).unwrap_or(ch));
        } else {
            mirrored.push(ch);
        }
    }

    // Step 2: reorder the (mirrored) text into visual order.
    let info = BidiInfo::new(&mirrored, Some(Level::rtl()));
    let Some(para) = info.paragraphs.first() else {
        return mirrored;
    };
    let visual = info.reorder_line(para, para.range.clone()).into_owned();

    // Step 3: drop the bidi controls. They were instructions for the two steps
    // above and have nothing left to say once the text is in visual order;
    // leaving them in only risks a renderer drawing a box for one.
    visual.chars().filter(|ch| !is_bidi_control(*ch)).collect()
}

/// The invisible characters that steer the bidi algorithm: the marks, the
/// isolates, and the deprecated embeddings and overrides.
fn is_bidi_control(ch: char) -> bool {
    matches!(
        ch,
        '\u{200e}' | '\u{200f}' | '\u{202a}'..='\u{202e}' | '\u{2066}'..='\u{2069}'
    )
}

/// Left-to-right isolate: opens a run the surrounding text cannot reach into,
/// and that cannot reach out of it.
pub const LRI: char = '\u{2066}';
/// Pop directional isolate: closes the run [`LRI`] opened.
pub const PDI: char = '\u{2069}';

/// Wraps a value so the bidi algorithm reads it as one left-to-right island.
///
/// An isolate, not a left-to-right *mark*. A mark is an ordinary strong
/// character: it holds its own run together, but two marked runs separated by
/// neutrals — "3.2 MB · 03:35 · model" — merge into a single left-to-right
/// island, and the three parts are then read in the wrong order. An isolate
/// keeps each value a sealed unit that the right-to-left text around it orders
/// as one piece, which is what `unicode-bidi: isolate` does in the web app.
pub fn isolate(value: impl std::fmt::Display) -> String {
    format!("{LRI}{value}{PDI}")
}

/// Keeps a technical value — a version, an identifier, a path, a masked key —
/// in one left-to-right piece inside Hebrew text.
///
/// Values that mix digits with Latin ("2.5.1+cpu", "8.1.2-full_build-www…")
/// are two bidi runs, not one: the digits are weak and take the paragraph's
/// direction, so the two halves end up swapped ("cpu+2.5.1"). Marks on both
/// sides make the whole value one left-to-right run. An empty value is left
/// alone, so nothing renders a stray mark.
pub fn latin(value: &str) -> String {
    if value.is_empty() {
        String::new()
    } else {
        isolate(value)
    }
}

/// `MM:SS` or `H:MM:SS`.
pub fn format_clock(seconds: f64) -> String {
    let total = if seconds.is_finite() && seconds >= 0.0 {
        seconds as u64
    } else {
        0
    };
    let h = total / 3600;
    let m = (total % 3600) / 60;
    let s = total % 60;
    let clock = if h > 0 {
        format!("{}:{:02}:{:02}", h, m, s)
    } else {
        format!("{:02}:{:02}", m, s)
    };
    isolate(clock)
}

/// Duration in Hebrew words, for summaries.
pub fn format_duration(seconds: f64) -> String {
    if !seconds.is_finite() || seconds <= 0.0 {
        return "לא ידוע".to_string();
    }
    let total = seconds.round() as u64;
    let hours = total / 3600;
    let minutes = (total % 3600) / 60;
    let secs = total % 60;

    let mut parts: Vec<String> = Vec::new();
    if hours > 0 {
        parts.push(if hours == 1 {
            "שעה".to_string()
        } else {
            format!("{} שעות", isolate(hours))
        });
    }
    if minutes > 0 {
        parts.push(if minutes == 1 {
            "דקה".to_string()
        } else {
            format!("{} דקות", isolate(minutes))
        });
    }
    if hours == 0 && secs > 0 {
        parts.push(if secs == 1 {
            "שנייה".to_string()
        } else {
            format!("{} שניות", isolate(secs))
        });
    }
    if parts.is_empty() {
        "פחות משנייה".to_string()
    } else {
        parts.join(" ו-")
    }
}

const SIZE_UNITS: [&str; 5] = ["בייט", "ק\"ב", "מ\"ב", "ג\"ב", "ט\"ב"];

pub fn format_bytes(bytes: u64) -> String {
    if bytes == 0 {
        return format!("{} בייט", isolate(0));
    }
    let exponent = ((bytes as f64).log2() / 10.0).floor() as usize;
    let exponent = exponent.min(SIZE_UNITS.len() - 1);
    let value = bytes as f64 / 1024f64.powi(exponent as i32);
    let rounded = if exponent == 0 {
        value.round() as u64
    } else {
        (value * 10.0).round() as u64
    };
    // One isolate around the whole number. Isolating the two halves
    // separately leaves the decimal point between two sealed islands, and the
    // right-to-left text around them then orders the islands — "3.2" becomes
    // "2.3".
    if exponent == 0 {
        format!("{} {}", isolate(rounded), SIZE_UNITS[exponent])
    } else {
        format!(
            "{} {}",
            isolate(format!("{}.{}", rounded / 10, rounded % 10)),
            SIZE_UNITS[exponent]
        )
    }
}

pub fn format_percent(fraction: f64) -> String {
    let clamped = if fraction.is_finite() {
        fraction.clamp(0.0, 1.0)
    } else {
        0.0
    };
    // The sign belongs inside the isolate; left out, it is a neutral that
    // the surrounding Hebrew pulls to the other side of the number.
    isolate(format!("{}%", (clamped * 100.0).round() as u64))
}

pub fn format_eta(seconds: Option<f64>) -> String {
    match seconds {
        Some(s) if s.is_finite() => {
            if s < 5.0 {
                "עוד רגע".to_string()
            } else {
                format!("נותרו כ-{}", format_duration(s))
            }
        }
        _ => String::new(),
    }
}

/// Truncate for display without breaking a surrogate pair (char-safe in Rust).
pub fn truncate_middle(value: &str, max: usize) -> String {
    let chars: Vec<char> = value.chars().collect();
    if chars.len() <= max {
        return value.to_string();
    }
    let head = (max - 1).div_ceil(2);
    let tail = (max - 1) / 2;
    let head_s: String = chars[..head].iter().collect();
    let tail_s: String = chars[chars.len() - tail..].iter().collect();
    format!("{head_s}…{tail_s}")
}

/// An ISO-8601 timestamp trimmed to `YYYY-MM-DD HH:MM`.
pub fn format_date_time(iso: &str) -> String {
    let s = iso.trim();
    let cut = s.get(..16).unwrap_or(s);
    cut.replace('T', " ")
}

pub fn stage_label(stage: JobStage) -> &'static str {
    match stage {
        JobStage::Pending => "ממתין בתור",
        JobStage::PreparingMedia => "מכין את המדיה",
        JobStage::DownloadingModel => "מוריד את המודל",
        JobStage::LoadingModel => "טוען מודל",
        JobStage::Transcribing => "מתמלל",
        JobStage::Diarizing => "מזהה דוברים",
        JobStage::GeneratingSubtitles => "מייצר כתוביות",
        JobStage::Exporting => "מייצא",
        JobStage::Done => "הושלם",
    }
}

pub fn status_label(status: JobStatus) -> &'static str {
    match status {
        JobStatus::Queued => "בתור",
        JobStatus::Running => "פועל",
        JobStatus::Completed => "הושלם",
        JobStatus::Failed => "נכשל",
        JobStatus::Cancelled => "בוטל",
    }
}

pub const ALL_EXPORT_FORMATS: [ExportFormat; 7] = [
    ExportFormat::Txt,
    ExportFormat::TxtTimestamped,
    ExportFormat::Docx,
    ExportFormat::Pdf,
    ExportFormat::Srt,
    ExportFormat::Vtt,
    ExportFormat::Json,
];

pub fn format_label(format: ExportFormat) -> &'static str {
    match format {
        ExportFormat::Txt => "טקסט רגיל (TXT)",
        ExportFormat::TxtTimestamped => "טקסט עם חותמות זמן (TXT)",
        ExportFormat::Docx => "מסמך Word (DOCX)",
        ExportFormat::Pdf => "מסמך PDF",
        ExportFormat::Srt => "כתוביות SRT",
        ExportFormat::Vtt => "כתוביות VTT",
        ExportFormat::Json => "נתונים מובנים (JSON)",
    }
}

pub const ACCEPTED_EXTENSIONS: [&str; 11] = [
    "mp3", "mp4", "wav", "m4a", "mov", "mkv", "webm", "aac", "flac", "ogg", "opus",
];

pub fn file_extension(filename: &str) -> String {
    match filename.rfind('.') {
        Some(i) if i > 0 => filename[i + 1..].to_lowercase(),
        _ => String::new(),
    }
}

pub fn is_accepted_file(filename: &str) -> bool {
    let ext = file_extension(filename);
    ACCEPTED_EXTENSIONS.contains(&ext.as_str())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Reads a visual-order string the way a Hebrew reader does: right to
    /// left, so the pieces come out in the order they were meant to be read.
    fn pieces(visual: &str) -> Vec<String> {
        let mut parts: Vec<String> = visual.split(" · ").map(str::to_string).collect();
        parts.reverse();
        parts
    }

    #[test]
    fn a_number_stays_in_one_piece() {
        // 3.2 MiB. Isolating the halves separately used to render "2.3".
        let visual = rtl(&format_bytes(3_353_661));
        assert!(visual.contains("3.2"), "{visual}");
        assert!(!visual.contains("2.3"), "{visual}");
    }

    #[test]
    fn a_percentage_keeps_its_sign_on_the_right_side() {
        let visual = rtl(&format_percent(0.85));
        assert!(visual.contains("85%"), "{visual}");
    }

    #[test]
    fn separated_values_are_read_in_order() {
        let line = format!(
            "{} · {} · {}",
            format_bytes(3_353_661),
            format_clock(215.0),
            isolate("soniox/stt-async-v6")
        );
        let visual = rtl(&line);
        let read = pieces(&visual);
        assert_eq!(read.len(), 3, "{visual}");
        assert!(read[0].contains("3.2"), "{visual}");
        assert!(read[1].contains("03:35"), "{visual}");
        assert!(read[2].contains("soniox/stt-async-v6"), "{visual}");
    }

    #[test]
    fn a_mixed_version_is_not_split_in_half() {
        let visual = rtl(&format!("PyTorch: {}", isolate("2.5.1+cpu")));
        assert!(visual.contains("2.5.1+cpu"), "{visual}");
    }

    #[test]
    fn reordering_is_reversible() {
        // Text goes through `rtl` on the way to the screen and back again on
        // the way to the backend, so the pair has to be lossless.
        for text in [
            "שלום (עולם) שלום",
            "אנחנו לומדים C-Sharp במערכת .NET,",
            "הרצאת מבוא לקורס C# .NET (סי-שארפ).mp3",
        ] {
            assert_eq!(rtl(&rtl(text)), text, "{text}");
        }
    }

    #[test]
    fn brackets_are_mirrored_for_the_renderer() {
        // gpui draws each character as it is, so the character that lands on
        // the right of a Hebrew parenthetical must be the one that *looks*
        // like an opening bracket there.
        let visual = rtl("שלום (עולם) שלום");
        assert_eq!(visual, "םולש (םלוע) םולש");
    }

    #[test]
    fn bidi_controls_do_not_reach_the_renderer() {
        let visual = rtl(&format!("גרסה: {}", isolate("1.0.0")));
        assert!(!visual.contains(LRI), "{visual:?}");
        assert!(!visual.contains(PDI), "{visual:?}");
    }
}
