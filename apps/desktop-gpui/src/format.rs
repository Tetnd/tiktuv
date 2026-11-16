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
    info.reorder_line(para, para.range.clone()).into_owned()
}

/// Left-to-right mark.
pub const LRM: char = '\u{200e}';
/// Right-to-left mark.
pub const RLM: char = '\u{200f}';

pub fn isolate(value: impl std::fmt::Display) -> String {
    format!("{LRM}{value}{LRM}")
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
    if exponent == 0 {
        format!("{} {}", isolate(rounded), SIZE_UNITS[exponent])
    } else {
        format!("{}.{} {}", isolate(rounded / 10), isolate(rounded % 10), SIZE_UNITS[exponent])
    }
}

pub fn format_percent(fraction: f64) -> String {
    let clamped = if fraction.is_finite() {
        fraction.clamp(0.0, 1.0)
    } else {
        0.0
    };
    format!("{}%", isolate((clamped * 100.0).round() as u64))
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
