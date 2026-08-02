/**
 * Application state.
 *
 * Deliberately one small store rather than a data-fetching library: the whole
 * app talks to a single local backend, and live updates arrive over SSE, so
 * cache invalidation is a matter of applying the event.
 */

import { create } from 'zustand';
import type {
  ExportFormat,
  JobSummary,
  ModelInfo,
  ProgressEvent,
  SystemInfo,
  Transcript,
  TranscriptionSettings,
} from '@shared/index';
import { DEFAULT_SETTINGS } from '@shared/index';
import { ApiError, api } from '@/lib/api';

export type Screen = 'queue' | 'editor' | 'history' | 'settings' | 'about';

/** A file the user dropped that is not yet a job. */
export interface PendingFile {
  localId: string;
  filename: string;
  sizeBytes: number;
  durationSeconds: number | null;
  mediaToken: string | null;
  status: 'importing' | 'ready' | 'error';
  error: string | null;
}

export interface Toast {
  id: string;
  tone: 'info' | 'success' | 'error';
  title: string;
  detail?: string;
}

interface State {
  screen: Screen;
  connected: boolean;
  settings: TranscriptionSettings;
  pending: PendingFile[];
  jobs: JobSummary[];
  models: ModelInfo[];
  system: SystemInfo | null;
  activeJobId: string | null;
  transcript: Transcript | null;
  transcriptLoading: boolean;
  toasts: Toast[];

  setScreen: (screen: Screen) => void;
  setConnected: (connected: boolean) => void;
  updateSettings: (patch: Partial<TranscriptionSettings>) => void;

  addFiles: (files: File[]) => Promise<void>;
  addLocalPaths: (paths: string[]) => Promise<void>;
  removePending: (localId: string) => void;
  clearPending: () => void;
  startAll: () => Promise<void>;

  refreshJobs: () => Promise<void>;
  refreshModels: () => Promise<void>;
  refreshSystem: () => Promise<void>;

  cancelJob: (id: string) => Promise<void>;
  retryJob: (id: string) => Promise<void>;
  deleteJob: (id: string, deleteFiles: boolean) => Promise<void>;
  clearCompleted: () => Promise<void>;

  openTranscript: (id: string) => Promise<void>;
  saveTranscript: (transcript: Transcript) => Promise<void>;
  closeTranscript: () => void;

  exportJob: (id: string, formats: ExportFormat[], outputDir?: string | null) => Promise<void>;

  applyProgress: (event: ProgressEvent) => void;
  pushToast: (toast: Omit<Toast, 'id'>) => void;
  dismissToast: (id: string) => void;
}

let idCounter = 0;
const nextId = () => `${Date.now().toString(36)}-${(idCounter += 1)}`;

function describe(error: unknown): { title: string; detail?: string } {
  if (error instanceof ApiError) {
    return { title: error.messageHe, detail: error.remedyHe };
  }
  return { title: 'אירעה שגיאה בלתי צפויה.', detail: String(error) };
}

export const useStore = create<State>((set, get) => ({
  screen: 'queue',
  connected: false,
  settings: { ...DEFAULT_SETTINGS },
  pending: [],
  jobs: [],
  models: [],
  system: null,
  activeJobId: null,
  transcript: null,
  transcriptLoading: false,
  toasts: [],

  setScreen: (screen) => set({ screen }),
  setConnected: (connected) => set({ connected }),
  updateSettings: (patch) => set((s) => ({ settings: { ...s.settings, ...patch } })),

  pushToast: (toast) =>
    set((s) => ({ toasts: [...s.toasts, { ...toast, id: nextId() }].slice(-4) })),
  dismissToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),

  // -- pending files -----------------------------------------------------
  addFiles: async (files) => {
    const entries: PendingFile[] = files.map((file) => ({
      localId: nextId(),
      filename: file.name,
      sizeBytes: file.size,
      durationSeconds: null,
      mediaToken: null,
      status: 'importing',
      error: null,
    }));
    set((s) => ({ pending: [...s.pending, ...entries] }));

    await Promise.all(
      files.map(async (file, index) => {
        const entry = entries[index];
        if (!entry) return;
        try {
          const imported = await api.uploadMedia(file);
          set((s) => ({
            pending: s.pending.map((p) =>
              p.localId === entry.localId
                ? {
                    ...p,
                    status: 'ready',
                    mediaToken: imported.token,
                    durationSeconds: imported.media.duration,
                    sizeBytes: imported.media.size_bytes,
                  }
                : p,
            ),
          }));
        } catch (error) {
          const { title } = describe(error);
          set((s) => ({
            pending: s.pending.map((p) =>
              p.localId === entry.localId ? { ...p, status: 'error', error: title } : p,
            ),
          }));
        }
      }),
    );
  },

  addLocalPaths: async (paths) => {
    for (const path of paths) {
      const localId = nextId();
      const filename = path.split(/[\\/]/).pop() ?? path;
      set((s) => ({
        pending: [
          ...s.pending,
          {
            localId,
            filename,
            sizeBytes: 0,
            durationSeconds: null,
            mediaToken: null,
            status: 'importing',
            error: null,
          },
        ],
      }));
      try {
        const imported = await api.importMedia(path);
        set((s) => ({
          pending: s.pending.map((p) =>
            p.localId === localId
              ? {
                  ...p,
                  status: 'ready',
                  mediaToken: imported.token,
                  durationSeconds: imported.media.duration,
                  sizeBytes: imported.media.size_bytes,
                  filename: imported.media.filename,
                }
              : p,
          ),
        }));
      } catch (error) {
        const { title } = describe(error);
        set((s) => ({
          pending: s.pending.map((p) =>
            p.localId === localId ? { ...p, status: 'error', error: title } : p,
          ),
        }));
      }
    }
  },

  removePending: (localId) =>
    set((s) => ({ pending: s.pending.filter((p) => p.localId !== localId) })),
  clearPending: () => set({ pending: [] }),

  startAll: async () => {
    const { pending, settings, pushToast } = get();
    const ready = pending.filter((p) => p.status === 'ready' && p.mediaToken);
    if (ready.length === 0) return;

    for (const entry of ready) {
      try {
        await api.createJob(entry.mediaToken as string, settings, true);
        set((s) => ({ pending: s.pending.filter((p) => p.localId !== entry.localId) }));
      } catch (error) {
        const { title, detail } = describe(error);
        pushToast({ tone: 'error', title, detail });
      }
    }
    await get().refreshJobs();
  },

  // -- jobs --------------------------------------------------------------
  refreshJobs: async () => {
    try {
      set({ jobs: await api.listJobs() });
    } catch (error) {
      const { title, detail } = describe(error);
      get().pushToast({ tone: 'error', title, detail });
    }
  },

  refreshModels: async () => {
    try {
      set({ models: await api.models() });
    } catch {
      /* the settings screen shows an empty list; the system check explains why */
    }
  },

  refreshSystem: async () => {
    try {
      set({ system: await api.system() });
    } catch {
      /* preflight surfaces the failure */
    }
  },

  cancelJob: async (id) => {
    try {
      await api.cancelJob(id);
    } catch (error) {
      const { title, detail } = describe(error);
      get().pushToast({ tone: 'error', title, detail });
    }
    await get().refreshJobs();
  },

  retryJob: async (id) => {
    try {
      await api.retryJob(id, true);
      get().pushToast({ tone: 'info', title: 'העבודה הוחזרה לתור.' });
    } catch (error) {
      const { title, detail } = describe(error);
      get().pushToast({ tone: 'error', title, detail });
    }
    await get().refreshJobs();
  },

  deleteJob: async (id, deleteFiles) => {
    try {
      await api.deleteJob(id, deleteFiles);
      if (get().activeJobId === id) set({ activeJobId: null, transcript: null });
    } catch (error) {
      const { title, detail } = describe(error);
      get().pushToast({ tone: 'error', title, detail });
    }
    await get().refreshJobs();
  },

  clearCompleted: async () => {
    try {
      const { removed } = await api.clearCompleted();
      get().pushToast({ tone: 'success', title: `הוסרו ${removed} עבודות שהסתיימו.` });
    } catch (error) {
      const { title, detail } = describe(error);
      get().pushToast({ tone: 'error', title, detail });
    }
    await get().refreshJobs();
  },

  // -- transcript --------------------------------------------------------
  openTranscript: async (id) => {
    set({ activeJobId: id, transcriptLoading: true, screen: 'editor' });
    try {
      set({ transcript: await api.getTranscript(id), transcriptLoading: false });
    } catch (error) {
      const { title, detail } = describe(error);
      set({ transcript: null, transcriptLoading: false });
      get().pushToast({ tone: 'error', title, detail });
    }
  },

  saveTranscript: async (transcript) => {
    try {
      const saved = await api.saveTranscript(
        transcript.job_id,
        transcript.segments,
        transcript.speakers,
      );
      set({ transcript: saved });
    } catch (error) {
      const { title, detail } = describe(error);
      get().pushToast({ tone: 'error', title, detail });
      throw error;
    }
  },

  closeTranscript: () => set({ activeJobId: null, transcript: null, screen: 'queue' }),

  exportJob: async (id, formats, outputDir) => {
    try {
      const result = await api.exportJob(id, formats, outputDir);
      get().pushToast({
        tone: 'success',
        title: `יוצאו ${result.results.length} קבצים.`,
        detail: result.results[0]?.path,
      });
      await get().refreshJobs();
    } catch (error) {
      const { title, detail } = describe(error);
      get().pushToast({ tone: 'error', title, detail });
    }
  },

  // -- live updates ------------------------------------------------------
  applyProgress: (event) => {
    if (!event.job_id) return;
    set((s) => ({
      jobs: s.jobs.map((job) =>
        job.id !== event.job_id
          ? job
          : {
              ...job,
              status: event.status ?? job.status,
              error: event.error ?? job.error,
              progress: {
                ...job.progress,
                stage: event.stage ?? job.progress.stage,
                percent: event.percent ?? job.progress.percent,
                processed_seconds: event.processed_seconds ?? job.progress.processed_seconds,
                total_seconds: event.total_seconds ?? job.progress.total_seconds,
                eta_seconds:
                  event.eta_seconds === undefined ? job.progress.eta_seconds : event.eta_seconds,
                message: event.message ?? null,
              },
            },
      ),
    }));
  },
}));
