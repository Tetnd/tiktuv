/**
 * TypeScript mirror of the backend Pydantic contracts in
 * `apps/backend/tiktuv/schemas.py`.
 *
 * Keep the two in sync. `scripts/check-shared-types.ps1` compares the string
 * union members here against the Python enums and fails the build on drift.
 */

export const JOB_STATUSES = ['queued', 'running', 'completed', 'failed', 'cancelled'] as const;
export type JobStatus = (typeof JOB_STATUSES)[number];

export const JOB_STAGES = [
  'pending',
  'preparing_media',
  'downloading_model',
  'loading_model',
  'transcribing',
  'diarizing',
  'generating_subtitles',
  'exporting',
  'done',
] as const;
export type JobStage = (typeof JOB_STAGES)[number];

export const LANGUAGES = ['he', 'en', 'auto'] as const;
export type Language = (typeof LANGUAGES)[number];

export const DEVICE_PREFERENCES = ['auto', 'cuda', 'cpu'] as const;
export type DevicePreference = (typeof DEVICE_PREFERENCES)[number];

export const QUALITY_PRESETS = ['fast', 'balanced', 'accurate'] as const;
export type QualityPreset = (typeof QUALITY_PRESETS)[number];

export const TIMESTAMP_MODES = ['none', 'segment', 'word'] as const;
export type TimestampMode = (typeof TIMESTAMP_MODES)[number];

export const EXPORT_FORMATS = [
  'txt',
  'txt_timestamped',
  'docx',
  'pdf',
  'srt',
  'vtt',
  'json',
] as const;
export type ExportFormat = (typeof EXPORT_FORMATS)[number];

export const CHECK_STATUSES = ['ok', 'warning', 'error'] as const;
export type CheckStatus = (typeof CHECK_STATUSES)[number];

export const URL_FETCH_STATES = [
  'idle',
  'resolving',
  'downloading',
  'ready',
  'failed',
  'cancelled',
] as const;
export type UrlFetchState = (typeof URL_FETCH_STATES)[number];

export interface UrlFetchStatus {
  state: UrlFetchState;
  title: string;
  percent: number;
  downloaded_bytes: number;
  total_bytes: number;
  eta_seconds: number | null;
  error: string | null;
  media: MediaInfo | null;
  media_token: string | null;
}

export const MODEL_DOWNLOAD_STATES = [
  'idle',
  'downloading',
  'ready',
  'failed',
  'cancelled',
] as const;
export type ModelDownloadState = (typeof MODEL_DOWNLOAD_STATES)[number];

export interface ModelDownloadStatus {
  state: ModelDownloadState;
  model_id: string | null;
  percent: number;
  downloaded_bytes: number;
  total_bytes: number;
  files_done: number;
  files_total: number;
  current_file: string;
  eta_seconds: number | null;
  error: string | null;
}

export interface TranscriptionSettings {
  language: Language;
  model_id: string;
  device: DevicePreference;
  quality: QualityPreset;
  timestamps: TimestampMode;
  batch_size: number;
  use_fp16: boolean;
  quantize_8bit: boolean;
  subtitle_max_line_length: number;
  subtitle_max_lines: number;
  preserve_filler_words: boolean;
  normalize_punctuation: boolean;
  split_paragraphs: boolean;
  detect_long_silences: boolean;
  long_silence_seconds: number;
  vocabulary_prompt: string;
  auto_download_model: boolean;
  enable_diarization: boolean;
  output_dir: string | null;
  export_formats: ExportFormat[];
}

export interface Word {
  text: string;
  start: number;
  end: number;
  probability: number | null;
}

export interface Segment {
  id: number;
  start: number;
  end: number;
  text: string;
  words: Word[];
  speaker: string | null;
  edited: boolean;
  boundary_merged: boolean;
}

export interface Transcript {
  job_id: string;
  language: string;
  model_id: string;
  duration: number;
  segments: Segment[];
  speakers: Record<string, string>;
  partial: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface MediaInfo {
  path: string;
  filename: string;
  size_bytes: number;
  duration: number;
  container: string;
  audio_codec: string | null;
  video_codec: string | null;
  sample_rate: number | null;
  channels: number | null;
  bit_rate: number | null;
  has_audio: boolean;
  has_video: boolean;
}

export interface JobProgress {
  stage: JobStage;
  percent: number;
  processed_seconds: number;
  total_seconds: number;
  eta_seconds: number | null;
  message: string | null;
}

export interface ErrorPayload {
  code: string;
  message: string;
  remedy: string;
  detail: string;
  context: Record<string, unknown>;
}

export interface JobSummary {
  id: string;
  filename: string;
  source_path: string;
  size_bytes: number;
  duration: number;
  status: JobStatus;
  progress: JobProgress;
  model_id: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  last_opened_at: string | null;
  error: ErrorPayload | null;
  output_paths: Record<string, string>;
  has_transcript: boolean;
}

export interface JobDetail extends JobSummary {
  settings: TranscriptionSettings;
  media: MediaInfo | null;
}

export interface ImportedMedia {
  token: string;
  media: MediaInfo;
}

export interface ExportResult {
  format: ExportFormat;
  path: string;
  size_bytes: number;
}

export interface ExportResponse {
  job_id: string;
  results: ExportResult[];
}

export interface PreflightCheck {
  id: string;
  label_he: string;
  status: CheckStatus;
  detail: string;
  remedy: string;
}

export interface DeviceInfo {
  kind: 'cuda' | 'cpu';
  name: string;
  index: number | null;
  total_memory_bytes: number | null;
  free_memory_bytes: number | null;
  supports_fp16: boolean;
  compute_capability: string | null;
}

export interface ModelInfo {
  id: string;
  label_he: string;
  hebrew_specialised: boolean;
  approx_download_bytes: number;
  downloaded: boolean;
  cache_path: string;
  license: string;
  notes_he: string;
  is_cloud: boolean;
}

export interface StorageUsage {
  data_dir: string;
  models_dir: string;
  temp_dir: string;
  temp_bytes: number;
  models_bytes: number;
  media_bytes: number;
  transcripts_bytes: number;
  exports_bytes: number;
  free_disk_bytes: number;
}

export interface SystemInfo {
  app_version: string;
  platform: string;
  python_version: string;
  torch_version: string | null;
  cuda_version: string | null;
  ffmpeg_version: string | null;
  devices: DeviceInfo[];
  checks: PreflightCheck[];
  storage: StorageUsage | null;
}

export interface SonioxStatus {
  configured: boolean;
  masked_key: string;
}

export interface DeepSeekStatus {
  configured: boolean;
  masked_key: string;
}

export interface ActionItem {
  task: string;
  assignee: string | null;
}

export interface NamedEntity {
  name: string;
  type: string;
}

export interface SpeakerSummary {
  speaker: string;
  summary: string;
}

export interface SpeakerQuote {
  speaker: string;
  quotes: string[];
}

export interface TranscriptAnalysis {
  job_id: string;
  summary: string;
  key_points: string[];
  action_items: ActionItem[];
  named_entities: NamedEntity[];
  speaker_summaries: SpeakerSummary[];
  speaker_quotes: SpeakerQuote[];
  model: string;
  generated_at: string | null;
}

/** SSE event names emitted by the backend. */
export const EVENT_TYPES = [
  'job.created',
  'job.progress',
  'job.completed',
  'job.failed',
  'job.cancelled',
  'job.deleted',
  'transcript.partial',
  'queue.changed',
  'model.status',
  'media.fetch',
] as const;
export type EventType = (typeof EVENT_TYPES)[number];

export interface ProgressEvent {
  job_id: string;
  status?: JobStatus;
  stage?: JobStage;
  percent?: number;
  processed_seconds?: number;
  total_seconds?: number;
  eta_seconds?: number | null;
  message?: string | null;
  error?: ErrorPayload;
}

export const DEFAULT_SETTINGS: TranscriptionSettings = {
  language: 'he',
  model_id: 'ivrit-ai/whisper-large-v3',
  device: 'auto',
  quality: 'balanced',
  timestamps: 'segment',
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
  vocabulary_prompt: '',
  auto_download_model: true,
  enable_diarization: false,
  output_dir: null,
  export_formats: [],
};
