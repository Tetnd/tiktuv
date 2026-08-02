/**
 * Typed client for the local backend.
 *
 * Every call carries the session bearer token. The token is injected by the
 * Tauri shell (which reads it from the backend's handshake file) and falls
 * back to a dev-only value from the Vite environment.
 */

import type {
  ExportFormat,
  ExportResponse,
  ImportedMedia,
  JobDetail,
  JobSummary,
  ModelInfo,
  StorageUsage,
  SystemInfo,
  Transcript,
  TranscriptionSettings,
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
  ) {
    super(`${code}: ${detail || messageHe}`);
    this.name = 'ApiError';
  }

  static async fromResponse(response: Response): Promise<ApiError> {
    let code = 'INTERNAL';
    let message = 'אירעה שגיאה בלתי צפויה.';
    let remedy = 'נסו שוב, או בדקו את יומן היישום.';
    let detail = '';
    try {
      const body = await response.json();
      code = body.code ?? code;
      message = body.message ?? message;
      remedy = body.remedy ?? remedy;
      detail = body.detail ?? '';
      // FastAPI validation errors have a different shape.
      if (!body.code && Array.isArray(body.detail)) {
        code = 'INVALID_REQUEST';
        message = 'הבקשה אינה תקינה.';
        detail = JSON.stringify(body.detail);
      }
    } catch {
      detail = response.statusText;
    }
    return new ApiError(code, message, remedy, detail, response.status);
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

  if (!response.ok) throw await ApiError.fromResponse(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
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

  // -- models ------------------------------------------------------------
  models: () => request<ModelInfo[]>('/api/models'),
  downloadModel: (modelId: string) =>
    request<ModelInfo>('/api/models/download', {
      method: 'POST',
      ...json({ model_id: modelId }),
    }),
  deleteModel: (modelId: string) =>
    request<{ reclaimed_bytes: number }>(`/api/models/${modelId}`, { method: 'DELETE' }),

  // -- media -------------------------------------------------------------
  importMedia: (path: string) =>
    request<ImportedMedia>('/api/media/import', { method: 'POST', ...json({ path }) }),

  uploadMedia: (file: File) => {
    const form = new FormData();
    form.append('file', file, file.name);
    form.append('filename', file.name);
    return request<ImportedMedia>('/api/media/upload', { method: 'POST', body: form });
  },

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
  downloadUrl: (path: string) =>
    `${runtime.baseUrl}/api/exports/download?path=${encodeURIComponent(path)}`,
};

export type Api = typeof api;
