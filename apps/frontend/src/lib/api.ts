/**
 * Typed client for the local backend.
 *
 * Every call carries the session bearer token. The token is injected by the
 * Tauri shell (which reads it from the backend's handshake file) and falls
 * back to a dev-only value from the Vite environment.
 */

import type {
  DeepSeekStatus,
  ExportFormat,
  ExportResponse,
  ImportedMedia,
  JobDetail,
  JobSummary,
  ModelDownloadStatus,
  ModelInfo,
  SonioxStatus,
  StorageUsage,
  SystemInfo,
  Transcript,
  TranscriptAnalysis,
  TranscriptionSettings,
  UrlFetchStatus,
  Segment,
} from '@shared/index';

/** A backend error, carrying the Hebrew message and remedy for display. */
export class ApiError extends Error {
  constructor(
    readonly code: string,
    readonly messageHe: string,
    readonly remedyHe: string,
    readonly detail: string,
    readonly status: number,
    readonly context: Record<string, unknown> = {},
  ) {
    super(`${code}: ${detail || messageHe}`);
    this.name = 'ApiError';
  }

  static async fromResponse(response: Response): Promise<ApiError> {
    let code = 'INTERNAL';
    let message = 'אירעה שגיאה בלתי צפויה.';
    let remedy = 'נסו שוב, או בדקו את יומן היישום.';
    let detail = '';
    let context: Record<string, unknown> = {};
    try {
      const body = await response.json();
      code = body.code ?? code;
      message = body.message ?? message;
      remedy = body.remedy ?? remedy;
      detail = body.detail ?? '';
      context = body.context ?? {};
      // FastAPI validation errors have a different shape.
      if (!body.code && Array.isArray(body.detail)) {
        code = 'INVALID_REQUEST';
        message = 'הבקשה אינה תקינה.';
        detail = JSON.stringify(body.detail);
      }
    } catch {
      detail = response.statusText;
    }
    return new ApiError(code, message, remedy, detail, response.status, context);
  }
}

interface RuntimeConfig {
  baseUrl: string;
  token: string;
}

let runtime: RuntimeConfig = {
  baseUrl: '',
  token: (import.meta.env?.VITE_TIKTUV_TOKEN as string | undefined) ?? '',
};

/** Called once at startup by the desktop shell (or the dev bootstrap). */
export function configureApi(config: Partial<RuntimeConfig>): void {
  runtime = { ...runtime, ...config };
}

export function getToken(): string {
  return runtime.token;
}

export function getBaseUrl(): string {
  return runtime.baseUrl;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (runtime.token) headers.set('Authorization', `Bearer ${runtime.token}`);
  if (init.body && !(init.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json');
  }

  let response: Response;
  try {
    response = await fetch(`${runtime.baseUrl}${path}`, { ...init, headers });
  } catch (cause) {
    throw new ApiError(
      'BACKEND_UNREACHABLE',
      'לא ניתן להתחבר לשירות המקומי.',
      'ודאו שהיישום פועל, ונסו להפעיל אותו מחדש.',
      String(cause),
      0,
    );
  }

  if (response.status === 401) handleStaleToken();
  if (!response.ok) throw await ApiError.fromResponse(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/**
 * Recover from a rotated session token.
 *
 * The backend mints a new token every start. When Tiktuv runs in an app window
 * with a persistent browser profile, that window can restore the previous
 * session's document — which still holds the *old* token — and then every
 * request comes back 401 with nothing on screen to explain it.
 *
 * The page carries its token in the HTML the backend serves, so reloading is
 * what fetches a fresh one. Guarded so a genuinely rejected token cannot put
 * the window into a reload loop.
 */
const RELOAD_GUARD_KEY = 'tiktuv:token-reloaded-at';
const RELOAD_GUARD_MS = 10_000;

function handleStaleToken(): void {
  if (typeof window === 'undefined' || typeof sessionStorage === 'undefined') return;
  try {
    const last = Number(sessionStorage.getItem(RELOAD_GUARD_KEY) ?? 0);
    if (Date.now() - last < RELOAD_GUARD_MS) return; // already tried; do not loop
    sessionStorage.setItem(RELOAD_GUARD_KEY, String(Date.now()));
  } catch {
    return; // storage unavailable: better to do nothing than loop
  }
  window.location.reload();
}

const json = (body: unknown): RequestInit => ({ body: JSON.stringify(body) });

// --------------------------------------------------------------------------
export const api = {
  health: () => request<{ status: string; version: string }>('/api/health'),

  // -- system ------------------------------------------------------------
  system: () => request<SystemInfo>('/api/system'),
  storage: () => request<StorageUsage>('/api/system/storage'),
  cleanupTemp: () =>
    request<{ reclaimed_bytes: number }>('/api/system/cleanup/temp', { method: 'POST' }),

  /** Whether this session can show a native folder dialog. */
  canPickFolder: () =>
    request<{ available: boolean; path: string | null }>('/api/system/pick-folder'),
  /** Opens the dialog and resolves with the chosen path, or null if cancelled. */
  pickFolder: (initial: string | null) =>
    request<{ available: boolean; path: string | null }>('/api/system/pick-folder', {
      method: 'POST',
      ...json({ initial }),
    }),

  // -- models ------------------------------------------------------------
  models: () => request<ModelInfo[]>('/api/models'),
  downloadModel: (modelId: string) =>
    request<ModelDownloadStatus>('/api/models/download', {
      method: 'POST',
      ...json({ model_id: modelId }),
    }),
  downloadStatus: () => request<ModelDownloadStatus>('/api/models/download'),
  cancelDownload: () =>
    request<ModelDownloadStatus>('/api/models/download', { method: 'DELETE' }),
  deleteModel: (modelId: string) =>
    request<{ reclaimed_bytes: number }>(`/api/models/${modelId}`, { method: 'DELETE' }),

  // -- Soniox (cloud ASR) ------------------------------------------------
  sonioxStatus: () => request<SonioxStatus>('/api/soniox/status'),
  setSonioxKey: (apiKey: string) =>
    request<SonioxStatus>('/api/soniox/key', { method: 'PUT', ...json({ api_key: apiKey }) }),
  clearSonioxKey: () => request<SonioxStatus>('/api/soniox/key', { method: 'DELETE' }),

  // -- DeepSeek (AI analysis) --------------------------------------------
  deepseekStatus: () => request<DeepSeekStatus>('/api/deepseek/status'),
  setDeepseekKey: (apiKey: string) =>
    request<DeepSeekStatus>('/api/deepseek/key', { method: 'PUT', ...json({ api_key: apiKey }) }),
  clearDeepseekKey: () => request<DeepSeekStatus>('/api/deepseek/key', { method: 'DELETE' }),

  // -- media -------------------------------------------------------------
  importMedia: (path: string) =>
    request<ImportedMedia>('/api/media/import', { method: 'POST', ...json({ path }) }),

  uploadMedia: (file: File) => {
    const form = new FormData();
    form.append('file', file, file.name);
    form.append('filename', file.name);
    return request<ImportedMedia>('/api/media/upload', { method: 'POST', body: form });
  },

  /** Start downloading a Zoom recording. Returns immediately. */
  importFromUrl: (url: string, passcode: string | null) =>
    request<UrlFetchStatus>('/api/media/from-url', {
      method: 'POST',
      ...json({ url, passcode }),
    }),
  urlImportStatus: () => request<UrlFetchStatus>('/api/media/from-url'),
  cancelUrlImport: () =>
    request<UrlFetchStatus>('/api/media/from-url', { method: 'DELETE' }),

  // -- jobs --------------------------------------------------------------
  createJob: (mediaToken: string, settings: TranscriptionSettings, autostart = true) =>
    request<JobDetail>('/api/jobs', {
      method: 'POST',
      ...json({ media_token: mediaToken, settings, autostart }),
    }),
  listJobs: () => request<JobSummary[]>('/api/jobs'),
  getJob: (id: string) => request<JobDetail>(`/api/jobs/${id}`),
  startJob: (id: string) => request<unknown>(`/api/jobs/${id}/start`, { method: 'POST' }),
  cancelJob: (id: string) => request<unknown>(`/api/jobs/${id}/cancel`, { method: 'POST' }),
  retryJob: (id: string, resume = true) =>
    request<JobDetail>(`/api/jobs/${id}/retry?resume=${resume}`, { method: 'POST' }),
  deleteJob: (id: string, deleteFiles = false) =>
    request<unknown>(`/api/jobs/${id}?delete_files=${deleteFiles}`, { method: 'DELETE' }),
  deleteJobOutputs: (id: string) =>
    request<{ removed: number }>(`/api/jobs/${id}/outputs`, { method: 'DELETE' }),
  clearCompleted: () =>
    request<{ removed: number }>('/api/jobs/clear/completed', { method: 'POST' }),

  // -- transcript --------------------------------------------------------
  getTranscript: (id: string) => request<Transcript>(`/api/jobs/${id}/transcript`),
  saveTranscript: (id: string, segments: Segment[], speakers?: Record<string, string>) =>
    request<Transcript>(`/api/jobs/${id}/transcript`, {
      method: 'PUT',
      ...json({ segments, speakers: speakers ?? null }),
    }),

  // -- AI analysis -------------------------------------------------------
  getAnalysis: (id: string) => request<TranscriptAnalysis>(`/api/jobs/${id}/analysis`),
  createAnalysis: (id: string) =>
    request<TranscriptAnalysis>(`/api/jobs/${id}/analysis`, { method: 'POST' }),

  /** URL for the <audio>/<video> element. The token rides as a query
   *  parameter because media elements cannot send headers. */
  mediaUrl: (id: string) =>
    `${runtime.baseUrl}/api/jobs/${id}/media?token=${encodeURIComponent(runtime.token)}`,

  // -- exports -----------------------------------------------------------
  exportFormats: () => request<ExportFormat[]>('/api/export/formats'),
  exportJob: (id: string, formats: ExportFormat[], outputDir?: string | null) =>
    request<ExportResponse>(`/api/jobs/${id}/export`, {
      method: 'POST',
      ...json({ formats, output_dir: outputDir ?? null }),
    }),
  exportBulk: (jobIds: string[], formats: ExportFormat[], outputDir?: string | null) =>
    request<{ exported: ExportResponse[]; failed: Record<string, string> }>(
      '/api/export/bulk',
      { method: 'POST', ...json({ job_ids: jobIds, formats, output_dir: outputDir ?? null }) },
    ),
  /** URL for downloading a generated export. The token rides as a query
   *  parameter because plain download links cannot send headers. */
  downloadUrl: (path: string) =>
    `${runtime.baseUrl}/api/exports/download?path=${encodeURIComponent(path)}&token=${encodeURIComponent(runtime.token)}`,
};

export type Api = typeof api;
