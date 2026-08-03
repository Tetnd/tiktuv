/**
 * First-run model download.
 *
 * Tiktuv is useless without the Hebrew model, so waiting for the user to find
 * the settings screen and click a button just means their first transcription
 * fails. This starts the download by itself.
 *
 * It is automatic but never silent: the size and the destination folder are on
 * screen the whole time, progress is live, and it can be stopped. That keeps
 * the promise that the only network access is visible and interruptible.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import type { ModelDownloadStatus } from '@shared/index';
import { api } from '@/lib/api';
import { formatBytes, formatEta, formatPercent } from '@/lib/format';
import { Numeric, ProgressBar } from '@/components/ui/Primitives';
import { useStore } from '@/state/store';

const POLL_MS = 1000;

export function ModelDownloadBanner() {
  const models = useStore((s) => s.models);
  const settings = useStore((s) => s.settings);
  const refreshModels = useStore((s) => s.refreshModels);
  const refreshSystem = useStore((s) => s.refreshSystem);
  const pushToast = useStore((s) => s.pushToast);

  const [status, setStatus] = useState<ModelDownloadStatus | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const autoStarted = useRef(false);

  const model = models.find((entry) => entry.id === settings.model_id);
  const needsDownload = Boolean(model && !model.downloaded);

  const poll = useCallback(async () => {
    try {
      setStatus(await api.downloadStatus());
    } catch {
      /* transient; the next tick retries */
    }
  }, []);

  // Kick the download off once, as soon as we know the model is missing.
  useEffect(() => {
    if (!needsDownload || autoStarted.current || !model) return;
    if (!settings.auto_download_model) return;
    autoStarted.current = true;
    void (async () => {
      try {
        setStatus(await api.downloadModel(model.id));
      } catch {
        pushToast({
          tone: 'error',
          title: 'לא ניתן להתחיל את הורדת המודל.',
          detail: 'בדקו את החיבור לאינטרנט ונסו שוב ממסך "מערכת ומודלים".',
        });
      }
    })();
  }, [model, needsDownload, pushToast, settings.auto_download_model]);

  // Poll while a download is in flight.
  useEffect(() => {
    if (!status || status.state !== 'downloading') return;
    const timer = setInterval(() => void poll(), POLL_MS);
    return () => clearInterval(timer);
  }, [poll, status]);

  // Refresh the catalogue when it finishes, so the banner disappears.
  const previousState = useRef<string | null>(null);
  useEffect(() => {
    if (!status) return;
    if (previousState.current === 'downloading' && status.state === 'ready') {
      pushToast({ tone: 'success', title: 'המודל הורד והתמלול מוכן לשימוש.' });
      void refreshModels();
      void refreshSystem();
    }
    previousState.current = status.state;
  }, [pushToast, refreshModels, refreshSystem, status]);

  useEffect(() => {
    void poll();
  }, [poll]);

  if (!model || dismissed) return null;
  if (model.downloaded && status?.state !== 'downloading') return null;

  const downloading = status?.state === 'downloading';
  const failed = status?.state === 'failed';
  const cancelled = status?.state === 'cancelled';

  return (
    <section
      role="status"
      aria-live="polite"
      className={`card p-4 ${failed ? 'border-red-200 bg-red-50' : 'border-brand-200 bg-brand-50'}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-base font-semibold text-ink-900">
            {downloading && 'מוריד את מודל התמלול העברי…'}
            {failed && 'הורדת המודל נכשלה.'}
            {cancelled && 'הורדת המודל בוטלה.'}
            {!downloading && !failed && !cancelled && 'נדרש מודל תמלול'}
          </h2>

          <p className="mt-1 text-sm text-ink-600">
            {model.label_he} — {formatBytes(model.approx_download_bytes)}
          </p>
          <p className="mt-0.5 text-xs text-ink-500">
            נשמר בתיקייה: <span className="ltr-run break-all">{model.cache_path}</span>
          </p>
          <p className="mt-0.5 text-xs text-ink-500">
            זו הפעולה היחידה ביישום שמשתמשת באינטרנט. לאחר ההורדה הכול פועל מקומית.
          </p>

          {downloading && status && (
            <div className="mt-3 max-w-xl">
              <ProgressBar value={status.percent} label="התקדמות הורדת המודל" />
              <p className="mt-1 flex flex-wrap gap-2 text-xs text-ink-600">
                <span>{formatPercent(status.percent)}</span>
                <span>·</span>
                <span>
                  {formatBytes(status.downloaded_bytes)} מתוך {formatBytes(status.total_bytes)}
                </span>
                {status.files_total > 0 && (
                  <>
                    <span>·</span>
                    <span>
                      קובץ <Numeric>{status.files_done + 1}</Numeric>/
                      <Numeric>{status.files_total}</Numeric>
                    </span>
                  </>
                )}
                {status.eta_seconds != null && (
                  <>
                    <span>·</span>
                    <span>{formatEta(status.eta_seconds)}</span>
                  </>
                )}
              </p>
            </div>
          )}

          {failed && status?.error && (
            <p className="mt-2 text-sm text-red-700">{status.error}</p>
          )}
          {(failed || cancelled) && (
            <p className="mt-1 text-xs text-ink-600">
              ההורדה מתחדשת מהנקודה שבה הופסקה — הקבצים שכבר ירדו נשמרו.
            </p>
          )}
        </div>

        <div className="flex shrink-0 gap-2">
          {downloading ? (
            <button
              type="button"
              className="btn-secondary"
              onClick={() => void api.cancelDownload().then(setStatus).catch(() => undefined)}
            >
              עצור
            </button>
          ) : (
            <button
              type="button"
              className="btn-primary"
              onClick={() =>
                void api
                  .downloadModel(model.id)
                  .then(setStatus)
                  .catch(() =>
                    pushToast({ tone: 'error', title: 'לא ניתן להתחיל את ההורדה.' }),
                  )
              }
            >
              {failed || cancelled ? 'המשך הורדה' : 'הורדה'}
            </button>
          )}
          <button
            type="button"
            className="btn-ghost"
            aria-label="הסתרת ההודעה"
            onClick={() => setDismissed(true)}
          >
            הסתר
          </button>
        </div>
      </div>
    </section>
  );
}
