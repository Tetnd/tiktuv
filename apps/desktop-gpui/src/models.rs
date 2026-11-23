//! Serde mirrors of the backend Pydantic contracts.
//!
//! These match `apps/backend/tiktuv/schemas.py` and its TypeScript mirror in
//! `packages/shared-types`. Field names are kept in snake_case exactly as the
//! backend serializes them, so `serde_json` round-trips them without mapping.

use serde::{Deserialize, Serialize};

// ---------------------------------------------------------------------------
// Enums
// ---------------------------------------------------------------------------
#[derive(Serialize, Deserialize, Clone, Copy, PartialEq, Eq, Debug)]
#[serde(rename_all = "snake_case")]
pub enum JobStatus {
    Queued,
    Running,
    Completed,
    Failed,
    Cancelled,
}

#[derive(Serialize, Deserialize, Clone, Copy, PartialEq, Eq, Debug, Default)]
#[serde(rename_all = "snake_case")]
pub enum JobStage {
    #[default]
    Pending,
    PreparingMedia,
    DownloadingModel,
    LoadingModel,
    Transcribing,
    Diarizing,
    Proofreading,
    Analyzing,
    GeneratingSubtitles,
    Exporting,
    Done,
}

#[derive(Serialize, Deserialize, Clone, Copy, PartialEq, Eq, Debug)]
pub enum Language {
    #[serde(rename = "he")]
    Hebrew,
    #[serde(rename = "en")]
    English,
    #[serde(rename = "auto")]
    Auto,
}

#[derive(Serialize, Deserialize, Clone, Copy, PartialEq, Eq, Debug)]
pub enum DevicePreference {
    #[serde(rename = "auto")]
    Auto,
    #[serde(rename = "cuda")]
    Cuda,
    #[serde(rename = "cpu")]
    Cpu,
}

#[derive(Serialize, Deserialize, Clone, Copy, PartialEq, Eq, Debug)]
pub enum QualityPreset {
    #[serde(rename = "fast")]
    Fast,
    #[serde(rename = "balanced")]
    Balanced,
    #[serde(rename = "accurate")]
    Accurate,
}

#[derive(Serialize, Deserialize, Clone, Copy, PartialEq, Eq, Debug)]
pub enum TimestampMode {
    #[serde(rename = "none")]
    None,
    #[serde(rename = "segment")]
    Segment,
    #[serde(rename = "word")]
    Word,
}

/// One exportable part of a job: the transcript, or a single analysis tab.
/// Each section is written as its own file, so a tab can be exported alone.
#[derive(Serialize, Deserialize, Clone, Copy, PartialEq, Eq, Debug, Default, Hash)]
#[serde(rename_all = "snake_case")]
pub enum ExportSection {
    #[default]
    Transcript,
    Summary,
    KeyPoints,
    ActionItems,
    NamedEntities,
    SpeakerSummaries,
    SpeakerQuotes,
}

impl ExportSection {
    /// Subtitles and timestamped text need a timeline, which only the
    /// transcript has — the backend skips the other pairs.
    pub fn supports(self, format: ExportFormat) -> bool {
        match self {
            Self::Transcript => true,
            _ => matches!(
                format,
                ExportFormat::Txt | ExportFormat::Docx | ExportFormat::Pdf | ExportFormat::Json
            ),
        }
    }
}

#[derive(Serialize, Deserialize, Clone, Copy, PartialEq, Eq, Debug, Default)]
#[serde(rename_all = "snake_case")]
pub enum ExportFormat {
    #[default]
    Txt,
    TxtTimestamped,
    Docx,
    Pdf,
    Srt,
    Vtt,
    Json,
}

#[derive(Serialize, Deserialize, Clone, Copy, PartialEq, Eq, Debug, Default)]
#[serde(rename_all = "snake_case")]
pub enum CheckStatus {
    #[default]
    Ok,
    Warning,
    Error,
}

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------
#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct TranscriptionSettings {
    pub language: Language,
    pub model_id: String,
    pub device: DevicePreference,
    pub quality: QualityPreset,
    pub timestamps: TimestampMode,
    pub batch_size: u32,
    pub use_fp16: bool,
    pub quantize_8bit: bool,
    pub subtitle_max_line_length: u32,
    pub subtitle_max_lines: u32,
    pub preserve_filler_words: bool,
    pub normalize_punctuation: bool,
    pub split_paragraphs: bool,
    pub detect_long_silences: bool,
    pub long_silence_seconds: f64,
    pub vocabulary_prompt: String,
    pub auto_download_model: bool,
    pub enable_diarization: bool,
    pub speaker_method: String,
    pub suggest_speaker_names: bool,
    pub expected_speakers: Option<u32>,
    pub enable_proofreading: bool,
    pub enable_analysis: bool,
    pub proofread_model: String,
    pub output_dir: Option<String>,
    pub export_formats: Vec<ExportFormat>,
}

impl Default for TranscriptionSettings {
    fn default() -> Self {
        Self {
            language: Language::Hebrew,
            model_id: "ivrit-ai/whisper-large-v3".to_string(),
            device: DevicePreference::Auto,
            quality: QualityPreset::Balanced,
            timestamps: TimestampMode::Segment,
            batch_size: 0,
            use_fp16: true,
            quantize_8bit: false,
            subtitle_max_line_length: 42,
            subtitle_max_lines: 2,
            preserve_filler_words: false,
            normalize_punctuation: true,
            split_paragraphs: true,
            detect_long_silences: true,
            long_silence_seconds: 2.5,
            vocabulary_prompt: String::new(),
            auto_download_model: true,
            enable_diarization: true,
            speaker_method: "auto".to_string(),
            suggest_speaker_names: true,
            expected_speakers: None,
            enable_proofreading: true,
            enable_analysis: true,
            proofread_model: String::new(),
            output_dir: None,
            export_formats: Vec::new(),
        }
    }
}

// ---------------------------------------------------------------------------
// Transcript
// ---------------------------------------------------------------------------
#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct Word {
    pub text: String,
    pub start: f64,
    pub end: f64,
    pub probability: Option<f64>,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct Segment {
    pub id: u32,
    pub start: f64,
    pub end: f64,
    pub text: String,
    #[serde(default)]
    pub words: Vec<Word>,
    pub speaker: Option<String>,
    #[serde(default)]
    pub edited: bool,
    #[serde(default)]
    pub boundary_merged: bool,
    /// True when DeepSeek corrected this segment during proofreading.
    #[serde(default)]
    pub proofread: bool,
    /// The raw ASR text, when proofreading or an edit replaced it.
    #[serde(default)]
    pub original_text: Option<String>,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct Transcript {
    pub job_id: String,
    pub language: String,
    pub model_id: String,
    pub duration: f64,
    #[serde(default)]
    pub segments: Vec<Segment>,
    #[serde(default)]
    pub speakers: std::collections::HashMap<String, String>,
    #[serde(default)]
    pub partial: bool,
    #[serde(default)]
    pub proofread_model: String,
    #[serde(default)]
    pub proofread_corrections: u32,
    pub created_at: Option<String>,
    pub updated_at: Option<String>,
}

/// Result of recognising the speakers of a stored transcript.
#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct IdentifySpeakersResponse {
    pub transcript: Transcript,
    #[serde(default)]
    pub method: String,
    #[serde(default)]
    pub speaker_count: u32,
    #[serde(default)]
    pub notes: Vec<String>,
}

// ---------------------------------------------------------------------------
// AI analysis (DeepSeek)
// ---------------------------------------------------------------------------
#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct ActionItem {
    pub task: String,
    pub assignee: Option<String>,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct NamedEntity {
    pub name: String,
    #[serde(default)]
    pub r#type: String,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct SpeakerSummary {
    pub speaker: String,
    pub summary: String,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct SpeakerQuote {
    pub speaker: String,
    #[serde(default)]
    pub quotes: Vec<String>,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct TranscriptAnalysis {
    pub job_id: String,
    #[serde(default)]
    pub summary: String,
    #[serde(default)]
    pub key_points: Vec<String>,
    #[serde(default)]
    pub action_items: Vec<ActionItem>,
    #[serde(default)]
    pub named_entities: Vec<NamedEntity>,
    #[serde(default)]
    pub speaker_summaries: Vec<SpeakerSummary>,
    #[serde(default)]
    pub speaker_quotes: Vec<SpeakerQuote>,
    #[serde(default)]
    pub model: String,
    pub generated_at: Option<String>,
}

// ---------------------------------------------------------------------------
// Media
// ---------------------------------------------------------------------------
#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct MediaInfo {
    pub path: String,
    pub filename: String,
    pub size_bytes: u64,
    pub duration: f64,
    pub container: String,
    pub audio_codec: Option<String>,
    pub video_codec: Option<String>,
    pub sample_rate: Option<u32>,
    pub channels: Option<u32>,
    pub bit_rate: Option<u64>,
    #[serde(default)]
    pub has_audio: bool,
    #[serde(default)]
    pub has_video: bool,
}

// ---------------------------------------------------------------------------
// Jobs
// ---------------------------------------------------------------------------
#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct JobProgress {
    pub stage: JobStage,
    #[serde(default)]
    pub percent: f64,
    #[serde(default)]
    pub processed_seconds: f64,
    #[serde(default)]
    pub total_seconds: f64,
    pub eta_seconds: Option<f64>,
    pub message: Option<String>,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct ErrorPayload {
    pub code: String,
    pub message: String,
    pub remedy: String,
    #[serde(default)]
    pub detail: String,
    #[serde(default)]
    pub context: serde_json::Value,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct JobSummary {
    pub id: String,
    pub filename: String,
    pub source_path: String,
    pub size_bytes: u64,
    pub duration: f64,
    pub status: JobStatus,
    #[serde(default)]
    pub progress: JobProgress,
    pub model_id: String,
    pub created_at: String,
    pub started_at: Option<String>,
    pub finished_at: Option<String>,
    pub last_opened_at: Option<String>,
    pub error: Option<ErrorPayload>,
    #[serde(default)]
    pub output_paths: std::collections::HashMap<String, String>,
    #[serde(default)]
    pub has_transcript: bool,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct JobDetail {
    #[serde(flatten)]
    pub summary: JobSummary,
    pub settings: TranscriptionSettings,
    pub media: Option<MediaInfo>,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct ImportedMedia {
    pub token: String,
    #[serde(default)]
    pub media: MediaInfo,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct CreateJobRequest {
    pub media_token: String,
    pub settings: TranscriptionSettings,
    pub autostart: bool,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct ExportResult {
    pub format: ExportFormat,
    pub path: String,
    pub size_bytes: u64,
    #[serde(default)]
    pub section: ExportSection,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct ExportResponse {
    pub job_id: String,
    #[serde(default)]
    pub results: Vec<ExportResult>,
}

// ---------------------------------------------------------------------------
// Models / system
// ---------------------------------------------------------------------------
#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct ModelInfo {
    pub id: String,
    pub label_he: String,
    #[serde(default)]
    pub hebrew_specialised: bool,
    #[serde(default)]
    pub approx_download_bytes: u64,
    #[serde(default)]
    pub downloaded: bool,
    pub cache_path: String,
    pub license: String,
    #[serde(default)]
    pub notes_he: String,
    #[serde(default)]
    pub is_cloud: bool,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct DeviceInfo {
    pub kind: String,
    pub name: String,
    pub index: Option<u32>,
    pub total_memory_bytes: Option<u64>,
    pub free_memory_bytes: Option<u64>,
    #[serde(default)]
    pub supports_fp16: bool,
    pub compute_capability: Option<String>,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct PreflightCheck {
    pub id: String,
    pub label_he: String,
    pub status: CheckStatus,
    #[serde(default)]
    pub detail: String,
    #[serde(default)]
    pub remedy: String,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct StorageUsage {
    pub data_dir: String,
    pub models_dir: String,
    pub temp_dir: String,
    pub temp_bytes: u64,
    pub models_bytes: u64,
    pub media_bytes: u64,
    pub transcripts_bytes: u64,
    pub exports_bytes: u64,
    pub free_disk_bytes: u64,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct SystemInfo {
    pub app_version: String,
    pub platform: String,
    pub python_version: String,
    pub torch_version: Option<String>,
    pub cuda_version: Option<String>,
    pub ffmpeg_version: Option<String>,
    #[serde(default)]
    pub devices: Vec<DeviceInfo>,
    #[serde(default)]
    pub checks: Vec<PreflightCheck>,
    pub storage: Option<StorageUsage>,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct ModelDownloadStatus {
    pub state: String,
    pub model_id: Option<String>,
    #[serde(default)]
    pub percent: f64,
    #[serde(default)]
    pub downloaded_bytes: u64,
    #[serde(default)]
    pub total_bytes: u64,
    #[serde(default)]
    pub files_done: u32,
    #[serde(default)]
    pub files_total: u32,
    #[serde(default)]
    pub current_file: String,
    pub eta_seconds: Option<f64>,
    pub error: Option<String>,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct UrlFetchStatus {
    pub state: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub percent: f64,
    #[serde(default)]
    pub downloaded_bytes: u64,
    #[serde(default)]
    pub total_bytes: u64,
    pub eta_seconds: Option<f64>,
    pub error: Option<String>,
    pub media: Option<MediaInfo>,
    pub media_token: Option<String>,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct SonioxStatus {
    #[serde(default)]
    pub configured: bool,
    #[serde(default)]
    pub masked_key: String,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct DeepSeekStatus {
    #[serde(default)]
    pub configured: bool,
    #[serde(default)]
    pub masked_key: String,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct DeepSeekBalanceItem {
    #[serde(default)]
    pub currency: String,
    #[serde(default)]
    pub total_balance: String,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct DeepSeekBalance {
    #[serde(default)]
    pub is_available: bool,
    #[serde(default)]
    pub balances: Vec<DeepSeekBalanceItem>,
}

/// Progress event delivered over SSE.
#[derive(Serialize, Deserialize, Clone, Debug, Default)]
pub struct ProgressEvent {
    pub job_id: String,
    pub status: Option<JobStatus>,
    pub stage: Option<JobStage>,
    pub percent: Option<f64>,
    pub processed_seconds: Option<f64>,
    pub total_seconds: Option<f64>,
    pub eta_seconds: Option<f64>,
    pub message: Option<String>,
    pub error: Option<ErrorPayload>,
}
