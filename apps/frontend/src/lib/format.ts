/**
 * Hebrew display formatting.
 *
 * None of these functions reverse a string. Direction is handled by the
 * document's `dir="rtl"` and by Unicode marks where a neutral character would
 * otherwise be pulled to the wrong side.
 */

import type { CheckStatus, ExportFormat, JobStage, JobStatus } from '@shared/index';

/** Left-to-right mark: keeps a Latin/digit run intact inside Hebrew text. */
export const LRM = '‎';
/** Right-to-left mark. */
export const RLM = '‏';

export const STAGE_LABELS: Record<JobStage, string> = {
  pending: 'ממתין בתור',
  preparing_media: 'מכין את המדיה',
  loading_model: 'טוען מודל',
  transcribing: 'מתמלל',
  diarizing: 'מזהה דוברים',
  generating_subtitles: 'מייצר כתוביות',
  exporting: 'מייצא',
  done: 'הושלם',
};

export const STATUS_LABELS: Record<JobStatus, string> = {
  queued: 'בתור',
  running: 'פועל',
  completed: 'הושלם',
  failed: 'נכשל',
  cancelled: 'בוטל',
};

export const FORMAT_LABELS: Record<ExportFormat, string> = {
  txt: 'טקסט רגיל (TXT)',
  txt_timestamped: 'טקסט עם חותמות זמן (TXT)',
  docx: 'מסמך Word (DOCX)',
  pdf: 'מסמך PDF',
  srt: 'כתוביות SRT',
  vtt: 'כתוביות VTT',
  json: 'נתונים מובנים (JSON)',
};

export const CHECK_TONE: Record<CheckStatus, string> = {
  ok: 'text-emerald-700 bg-emerald-50 border-emerald-200',
  warning: 'text-amber-700 bg-amber-50 border-amber-200',
  error: 'text-red-700 bg-red-50 border-red-200',
};

/** Wrap a Latin/digit run so bidi keeps it together inside a Hebrew string. */
export function isolate(value: string | number): string {
  return `${LRM}${value}${LRM}`;
}

/** `MM:SS` or `H:MM:SS`. Always rendered inside an isolate. */
export function formatClock(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) seconds = 0;
  const total = Math.floor(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n: number) => String(n).padStart(2, '0');
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

/** Duration in Hebrew words, for screen readers and summaries. */
export function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return 'לא ידוע';
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;

  const parts: string[] = [];
  if (hours) parts.push(hours === 1 ? 'שעה' : `${isolate(hours)} שעות`);
  if (minutes) parts.push(minutes === 1 ? 'דקה' : `${isolate(minutes)} דקות`);
  if (!hours && secs) parts.push(secs === 1 ? 'שנייה' : `${isolate(secs)} שניות`);
  return parts.join(' ו-') || 'פחות משנייה';
}

const SIZE_UNITS = ['בייט', 'ק"ב', 'מ"ב', 'ג"ב', 'ט"ב'];

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return `${isolate(0)} בייט`;
  const exponent = Math.min(SIZE_UNITS.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const value = bytes / 1024 ** exponent;
  const rounded = exponent === 0 ? Math.round(value) : Number(value.toFixed(1));
  return `${isolate(rounded)} ${SIZE_UNITS[exponent]}`;
}

export function formatPercent(fraction: number): string {
  const clamped = Math.max(0, Math.min(1, Number.isFinite(fraction) ? fraction : 0));
  return `${isolate(Math.round(clamped * 100))}%`;
}

export function formatEta(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return '';
  if (seconds < 5) return 'עוד רגע';
  return `נותרו כ-${formatDuration(seconds)}`;
}

/** Date and time in the Hebrew locale, using the Gregorian calendar. */
export function formatDateTime(iso: string | null): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat('he-IL', {
    dateStyle: 'short',
    timeStyle: 'short',
  }).format(date);
}

/** Truncate for display without breaking a surrogate pair. */
export function truncateMiddle(value: string, max = 48): string {
  const characters = Array.from(value);
  if (characters.length <= max) return value;
  const head = Math.ceil((max - 1) / 2);
  const tail = Math.floor((max - 1) / 2);
  return `${characters.slice(0, head).join('')}…${characters.slice(-tail).join('')}`;
}

export function fileExtension(filename: string): string {
  const index = filename.lastIndexOf('.');
  return index > 0 ? filename.slice(index + 1).toLowerCase() : '';
}

export const ACCEPTED_EXTENSIONS = [
  'mp3', 'mp4', 'wav', 'm4a', 'mov', 'mkv', 'webm', 'aac', 'flac', 'ogg', 'opus',
];

export const ACCEPT_ATTRIBUTE = ACCEPTED_EXTENSIONS.map((ext) => `.${ext}`).join(',');

export function isAcceptedFile(filename: string): boolean {
  return ACCEPTED_EXTENSIONS.includes(fileExtension(filename));
}
