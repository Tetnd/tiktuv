/** Main screen: upload area, pending files, and the live job queue. */

import type { JobSummary } from '@shared/index';
import {
  STAGE_LABELS,
  STATUS_LABELS,
  formatBytes,
  formatClock,
  formatEta,
  formatPercent,
  truncateMiddle,
} from '@/lib/format';
import { Badge, EmptyState, Numeric, ProgressBar } from '@/components/ui/Primitives';
import { UploadZone } from '@/components/UploadZone';
import { SettingsPanel } from '@/components/SettingsPanel';
import { ModelDownloadBanner } from '@/components/ModelDownloadBanner';
import { useStore } from '@/state/store';

function PendingList() {
  const pending = useStore((s) => s.pending);
  const removePending = useStore((s) => s.removePending);
  const clearPending = useStore((s) => s.clearPending);
  const startAll = useStore((s) => s.startAll);

  if (pending.length === 0) return null;
  const ready = pending.filter((p) => p.status === 'ready').length;

  return (
    <section className="card p-4" aria-labelledby="pending-heading">
      <div className="mb-3 flex items-center justify-between">
        <h2 id="pending-heading" className="text-base font-semibold text-ink-800">
          קבצים ממתינים ({<Numeric>{pending.length}</Numeric>})
        </h2>
        <div className="flex gap-2">
          <button type="button" className="btn-ghost" onClick={clearPending}>
            נקה הכול
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={ready === 0}
            onClick={() => void startAll()}
          >
            התחל תמלול ({<Numeric>{ready}</Numeric>})
          </button>
        </div>
      </div>

      <ul className="divide-y divide-ink-200">
        {pending.map((file) => (
          <li key={file.localId} className="flex items-center gap-3 py-3">
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-ink-800" title={file.filename}>
                {truncateMiddle(file.filename, 60)}
              </p>
              <p className="mt-0.5 text-xs text-ink-500">
                {file.sizeBytes > 0 && <span>{formatBytes(file.sizeBytes)}</span>}
                {file.durationSeconds != null && (
                  <>
                    {' · '}
                    <Numeric>{formatClock(file.durationSeconds)}</Numeric>
                  </>
                )}
              </p>
              {file.status === 'error' && (
                <p className="mt-1 text-xs text-red-700">{file.error}</p>
              )}
            </div>

            <Badge tone={file.status === 'error' ? 'failed' : 'neutral'}>
              {file.status === 'importing'
                ? 'בודק קובץ…'
                : file.status === 'ready'
                  ? 'מוכן'
                  : 'שגיאה'}
            </Badge>

            <button
              type="button"
              className="btn-ghost"
              aria-label={`הסרת ${file.filename}`}
              onClick={() => removePending(file.localId)}
            >
              הסר
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function JobRow({ job }: { job: JobSummary }) {
  const cancelJob = useStore((s) => s.cancelJob);
  const retryJob = useStore((s) => s.retryJob);
  const deleteJob = useStore((s) => s.deleteJob);
  const openTranscript = useStore((s) => s.openTranscript);

  const isActive = job.status === 'running' || job.status === 'queued';
  const stageLabel = STAGE_LABELS[job.progress.stage] ?? '';

  return (
    <li className="py-4" data-testid={`job-${job.id}`}>
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className="truncate text-sm font-medium text-ink-900" title={job.filename}>
              {truncateMiddle(job.filename, 56)}
            </p>
            <Badge tone={job.status}>{STATUS_LABELS[job.status]}</Badge>
            {job.progress.stage !== 'done' && job.status === 'running' && (
              <Badge tone="running">{stageLabel}</Badge>
            )}
          </div>

          <p className="mt-1 text-xs text-ink-500">
            {formatBytes(job.size_bytes)}
            {' · '}
            <Numeric>{formatClock(job.duration)}</Numeric>
            {' · '}
            <span className="ltr-run">{job.model_id}</span>
          </p>

          {isActive && (
            <div className="mt-2 max-w-xl">
              <ProgressBar
                value={job.progress.percent}
                label={`התקדמות התמלול של ${job.filename}`}
              />
              <p className="mt-1 flex gap-2 text-xs text-ink-500">
                <span>{formatPercent(job.progress.percent)}</span>
                <span>·</span>
                <span>{stageLabel}</span>
                {job.progress.eta_seconds != null && (
                  <>
                    <span>·</span>
                    <span>{formatEta(job.progress.eta_seconds)}</span>
                  </>
                )}
              </p>
            </div>
          )}

          {job.error && (
            <div
              role="alert"
              className="mt-2 max-w-xl rounded-lg border border-red-200 bg-red-50 p-3"
            >
              <p className="text-sm font-medium text-red-800">{job.error.message}</p>
              <p className="mt-1 text-xs text-red-700">{job.error.remedy}</p>
            </div>
          )}
        </div>

        <div className="flex shrink-0 flex-wrap gap-2">
          {job.has_transcript && (
            <button
              type="button"
              className="btn-secondary"
              onClick={() => void openTranscript(job.id)}
            >
              פתיחת התמליל
            </button>
          )}
          {isActive && (
            <button
              type="button"
              className="btn-secondary"
              onClick={() => void cancelJob(job.id)}
            >
              ביטול
            </button>
          )}
          {(job.status === 'failed' || job.status === 'cancelled') && (
            <button
              type="button"
              className="btn-secondary"
              onClick={() => void retryJob(job.id)}
            >
              נסה שוב
            </button>
          )}
          {!isActive && (
            <button
              type="button"
              className="btn-danger"
              onClick={() => void deleteJob(job.id, false)}
            >
              מחיקה
            </button>
          )}
        </div>
      </div>
    </li>
  );
}

export function QueueScreen() {
  const jobs = useStore((s) => s.jobs);
  const clearCompleted = useStore((s) => s.clearCompleted);
  const finished = jobs.filter(
    (job) => job.status === 'completed' || job.status === 'failed' || job.status === 'cancelled',
  );

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-6">
      <ModelDownloadBanner />
      <UploadZone />
      <PendingList />
      <SettingsPanel />

      <section className="card p-4" aria-labelledby="queue-heading">
        <div className="mb-2 flex items-center justify-between">
          <h2 id="queue-heading" className="text-base font-semibold text-ink-800">
            עבודות
          </h2>
          {finished.length > 0 && (
            <button type="button" className="btn-ghost" onClick={() => void clearCompleted()}>
              ניקוי עבודות שהסתיימו
            </button>
          )}
        </div>

        {jobs.length === 0 ? (
          <EmptyState
            title="אין עדיין עבודות"
            detail="גררו קובץ אודיו או וידאו כדי להתחיל בתמלול."
          />
        ) : (
          <ul className="divide-y divide-ink-200">
            {jobs.map((job) => (
              <JobRow key={job.id} job={job} />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
