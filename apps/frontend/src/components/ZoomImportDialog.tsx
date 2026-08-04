/**
 * Import a Zoom cloud recording by pasting its share link.
 *
 * The recording is downloaded to this computer and then transcribed exactly
 * like a dropped file — nothing is sent anywhere. Fetching the link is the
 * only outbound request, and it goes to Zoom.
 */

import { useEffect, useRef, useState } from 'react';
import type { UrlFetchStatus } from '@shared/index';
import { api, ApiError } from '@/lib/api';
import { formatBytes, formatEta, formatPercent } from '@/lib/format';
import { ProgressBar } from '@/components/ui/Primitives';
import { useStore } from '@/state/store';

const POLL_MS = 700;

/** Matches what the backend accepts, so bad links are caught before a request. */
export function looksLikeZoomLink(value: string): boolean {
  try {
    const url = new URL(value.trim());
    if (url.protocol !== 'https:' && url.protocol !== 'http:') return false;
    if (!/(^|\.)zoom\.us$/i.test(url.hostname)) return false;
    return /^\/rec\/(play|share)\//i.test(url.pathname);
  } catch {
    return false;
  }
}

export function ZoomImportDialog({ onClose }: { onClose: () => void }) {
  const [url, setUrl] = useState('');
  const [passcode, setPasscode] = useState('');
  const [needsPasscode, setNeedsPasscode] = useState(false);
  const [status, setStatus] = useState<UrlFetchStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const settings = useStore((s) => s.settings);
  const refreshJobs = useStore((s) => s.refreshJobs);
  const pushToast = useStore((s) => s.pushToast);

  useEffect(() => {
    inputRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !running) onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  });

  const running = status?.state === 'resolving' || status?.state === 'downloading';

  // Poll while the download runs.
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => {
      void api
        .urlImportStatus()
        .then(setStatus)
        .catch(() => undefined);
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [running]);

  /**
   * Downloads already queued, so one recording becomes exactly one job.
   *
   * This effect re-runs whenever any dependency changes identity — a new
   * `status` object from a poll, or a fresh `onClose` closure from the parent
   * re-rendering — and every run would otherwise fire another `createJob`.
   * A `cancelled` flag cannot help: by the time cleanup runs the request is
   * already in flight. The token is claimed *before* awaiting anything.
   */
  const queuedTokens = useRef<Set<string>>(new Set());

  // Once the file has landed, queue it for transcription and close.
  useEffect(() => {
    const token = status?.state === 'ready' ? status.media_token : null;
    if (!token || queuedTokens.current.has(token)) return;
    queuedTokens.current.add(token);

    let cancelled = false;
    void (async () => {
      try {
        await api.createJob(token, settings, true);
        if (cancelled) return;
        pushToast({ tone: 'success', title: 'ההקלטה הורדה והתמלול התחיל.' });
        await refreshJobs();
        onClose();
      } catch (cause) {
        // Let the user try again with the file that is already downloaded.
        queuedTokens.current.delete(token);
        if (!cancelled) {
          setError(
            cause instanceof ApiError ? cause.messageHe : 'לא ניתן להתחיל את התמלול.',
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [status, settings, refreshJobs, pushToast, onClose]);

  const start = async () => {
    setError(null);
    if (!looksLikeZoomLink(url)) {
      setError('הדביקו קישור להקלטת Zoom (rec/share או rec/play).');
      return;
    }
    setStarting(true);
    try {
      setStatus(await api.importFromUrl(url.trim(), passcode || null));
    } catch (cause) {
      if (cause instanceof ApiError) {
        if (cause.context?.passcode_required || cause.detail.includes('passcode')) {
          setNeedsPasscode(true);
          setError('ההקלטה מוגנת בסיסמה. הזינו אותה ונסו שוב.');
        } else {
          setError(`${cause.messageHe} ${cause.remedyHe}`.trim());
        }
      } else {
        setError('לא ניתן היה להתחיל את ההורדה.');
      }
    } finally {
      setStarting(false);
    }
  };

  const cancel = async () => {
    await api.cancelUrlImport().then(setStatus).catch(() => undefined);
  };

  const failed = status?.state === 'failed';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink-900/40 p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="zoom-title"
        className="w-full max-w-xl rounded-xl bg-white p-5 shadow-xl"
      >
        <h2 id="zoom-title" className="text-lg font-semibold text-ink-900">
          תמלול מקישור Zoom
        </h2>
        <p className="mt-1 text-sm text-ink-500">
          הדביקו קישור להקלטת ענן. ההקלטה תורד למחשב שלכם ותתומלל מקומית.
        </p>

        <label className="field-label mt-4 block" htmlFor="zoom-url">
          קישור ההקלטה
        </label>
        <textarea
          id="zoom-url"
          ref={inputRef}
          dir="ltr"
          rows={3}
          className="field-input ltr-run resize-none font-mono text-xs"
          placeholder="https://us06web.zoom.us/rec/share/…"
          value={url}
          disabled={running}
          onChange={(event) => setUrl(event.target.value)}
        />

        {needsPasscode && (
          <div className="mt-3">
            <label className="field-label" htmlFor="zoom-passcode">
              סיסמת ההקלטה
            </label>
            <input
              id="zoom-passcode"
              type="password"
              className="field-input"
              value={passcode}
              disabled={running}
              onChange={(event) => setPasscode(event.target.value)}
            />
          </div>
        )}

        {/* -- progress ---------------------------------------------------- */}
        {status && running && (
          <div className="mt-4 rounded-lg border border-brand-200 bg-brand-50 p-3">
            <p className="text-sm font-medium text-ink-800">
              {status.state === 'resolving' ? 'מאתר את ההקלטה…' : 'מוריד את ההקלטה…'}
            </p>
            {status.title && <p className="mt-0.5 text-xs text-ink-600">{status.title}</p>}
            {status.state === 'downloading' && (
              <div className="mt-2">
                <ProgressBar value={status.percent} label="התקדמות ההורדה" />
                <p className="mt-1 flex flex-wrap gap-2 text-xs text-ink-600">
                  <span>{formatPercent(status.percent)}</span>
                  <span>·</span>
                  <span>
                    {formatBytes(status.downloaded_bytes)} מתוך{' '}
                    {formatBytes(status.total_bytes)}
                  </span>
                  {status.eta_seconds != null && (
                    <>
                      <span>·</span>
                      <span>{formatEta(status.eta_seconds)}</span>
                    </>
                  )}
                </p>
              </div>
            )}
          </div>
        )}

        {status?.state === 'cancelled' && (
          <p className="mt-3 text-sm text-ink-600">ההורדה בוטלה.</p>
        )}

        {(error || failed) && (
          <p role="alert" className="mt-3 text-sm text-red-700">
            {error || status?.error}
          </p>
        )}

        <p className="mt-4 text-xs text-ink-400">
          ההורדה מתבצעת ישירות מ-Zoom אל המחשב שלכם. השתמשו רק בהקלטות שיש לכם
          הרשאה אליהן.
        </p>

        <div className="mt-4 flex justify-start gap-2">
          {running ? (
            <button type="button" className="btn-secondary" onClick={() => void cancel()}>
              עצור
            </button>
          ) : (
            <button
              type="button"
              className="btn-primary"
              disabled={starting || url.trim().length === 0}
              onClick={() => void start()}
            >
              {starting ? 'מתחיל…' : 'הורד ותמלל'}
            </button>
          )}
          <button
            type="button"
            className="btn-ghost"
            disabled={running}
            onClick={onClose}
          >
            סגירה
          </button>
        </div>
      </div>
    </div>
  );
}
