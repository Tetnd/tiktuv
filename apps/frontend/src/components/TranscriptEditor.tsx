/**
 * RTL transcript editor.
 *
 * Text is edited in logical order in ordinary `<textarea>` elements; direction
 * comes from the document. Undo/redo is a local snapshot stack, and edits are
 * autosaved to the backend after a short idle period.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Segment } from '@shared/index';
import { api } from '@/lib/api';
import { formatClock, truncateMiddle } from '@/lib/format';
import { Numeric } from '@/components/ui/Primitives';
import { MediaPlayer, type PlayerHandle } from '@/components/MediaPlayer';
import { ExportDialog } from '@/components/ExportDialog';
import { useStore } from '@/state/store';

const AUTOSAVE_DELAY_MS = 1200;
const MAX_HISTORY = 60;

/** Index of the segment covering `time`, or -1. */
export function activeSegmentIndex(segments: Segment[], time: number): number {
  for (let index = 0; index < segments.length; index += 1) {
    const segment = segments[index];
    if (!segment) continue;
    if (time >= segment.start && time < segment.end) return index;
  }
  // Between segments: highlight the one just passed, so the view does not jump
  // back to nothing during a pause in speech.
  let candidate = -1;
  for (let index = 0; index < segments.length; index += 1) {
    const segment = segments[index];
    if (segment && segment.start <= time) candidate = index;
  }
  return candidate;
}

export function replaceAll(
  segments: Segment[],
  find: string,
  replaceWith: string,
): { segments: Segment[]; count: number } {
  if (!find) return { segments, count: 0 };
  let count = 0;
  const next = segments.map((segment) => {
    if (!segment.text.includes(find)) return segment;
    const parts = segment.text.split(find);
    count += parts.length - 1;
    return { ...segment, text: parts.join(replaceWith), edited: true };
  });
  return { segments: next, count };
}

export function TranscriptEditor() {
  const transcript = useStore((s) => s.transcript);
  const loading = useStore((s) => s.transcriptLoading);
  const activeJobId = useStore((s) => s.activeJobId);
  const jobs = useStore((s) => s.jobs);
  const saveTranscript = useStore((s) => s.saveTranscript);
  const closeTranscript = useStore((s) => s.closeTranscript);
  const pushToast = useStore((s) => s.pushToast);

  const [segments, setSegments] = useState<Segment[]>([]);
  const [speakers, setSpeakers] = useState<Record<string, string>>({});
  const [history, setHistory] = useState<Segment[][]>([]);
  const [future, setFuture] = useState<Segment[][]>([]);
  const [currentTime, setCurrentTime] = useState(0);
  const [findText, setFindText] = useState('');
  const [replaceText, setReplaceText] = useState('');
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [exportOpen, setExportOpen] = useState(false);

  const playerRef = useRef<PlayerHandle>(null);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const dirty = useRef(false);

  const job = jobs.find((entry) => entry.id === activeJobId);

  useEffect(() => {
    if (!transcript) return;
    setSegments(transcript.segments);
    setSpeakers(transcript.speakers ?? {});
    setHistory([]);
    setFuture([]);
    dirty.current = false;
    setSaveState('idle');
  }, [transcript]);

  const activeIndex = useMemo(
    () => activeSegmentIndex(segments, currentTime),
    [segments, currentTime],
  );

  // -- persistence -------------------------------------------------------
  const persist = useCallback(
    async (next: Segment[], nextSpeakers: Record<string, string>) => {
      if (!transcript) return;
      setSaveState('saving');
      try {
        await saveTranscript({ ...transcript, segments: next, speakers: nextSpeakers });
        dirty.current = false;
        setSaveState('saved');
      } catch {
        setSaveState('error');
      }
    },
    [saveTranscript, transcript],
  );

  const scheduleSave = useCallback(
    (next: Segment[], nextSpeakers: Record<string, string>) => {
      dirty.current = true;
      if (saveTimer.current) clearTimeout(saveTimer.current);
      saveTimer.current = setTimeout(() => void persist(next, nextSpeakers), AUTOSAVE_DELAY_MS);
    },
    [persist],
  );

  useEffect(
    () => () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
    },
    [],
  );

  // -- mutation with undo history ---------------------------------------
  const commit = useCallback(
    (next: Segment[], nextSpeakers = speakers) => {
      setHistory((stack) => [...stack, segments].slice(-MAX_HISTORY));
      setFuture([]);
      setSegments(next);
      setSpeakers(nextSpeakers);
      scheduleSave(next, nextSpeakers);
    },
    [scheduleSave, segments, speakers],
  );

  const undo = useCallback(() => {
    setHistory((stack) => {
      const previous = stack[stack.length - 1];
      if (!previous) return stack;
      setFuture((forward) => [segments, ...forward]);
      setSegments(previous);
      scheduleSave(previous, speakers);
      return stack.slice(0, -1);
    });
  }, [scheduleSave, segments, speakers]);

  const redo = useCallback(() => {
    setFuture((stack) => {
      const [next, ...rest] = stack;
      if (!next) return stack;
      setHistory((back) => [...back, segments]);
      setSegments(next);
      scheduleSave(next, speakers);
      return rest;
    });
  }, [scheduleSave, segments, speakers]);

  // Ctrl+Z / Ctrl+Y (and Ctrl+Shift+Z) at the document level.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!event.ctrlKey && !event.metaKey) return;
      const key = event.key.toLowerCase();
      if (key === 'z' && !event.shiftKey) {
        event.preventDefault();
        undo();
      } else if (key === 'y' || (key === 'z' && event.shiftKey)) {
        event.preventDefault();
        redo();
      } else if (key === 's') {
        event.preventDefault();
        void persist(segments, speakers);
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [persist, redo, segments, speakers, undo]);

  const editSegment = (index: number, text: string) => {
    const next = segments.map((segment, position) =>
      position === index ? { ...segment, text, edited: true } : segment,
    );
    commit(next);
  };

  const renameSpeaker = (key: string, label: string) => {
    commit(segments, { ...speakers, [key]: label });
  };

  const copyAll = async () => {
    const text = segments.map((segment) => segment.text).join('\n');
    try {
      await navigator.clipboard.writeText(text);
      pushToast({ tone: 'success', title: 'התמליל הועתק.' });
    } catch {
      pushToast({ tone: 'error', title: 'ההעתקה נכשלה.', detail: 'ייתכן שאין הרשאת גישה ללוח.' });
    }
  };

  const doReplaceAll = () => {
    const { segments: next, count } = replaceAll(segments, findText, replaceText);
    if (count === 0) {
      pushToast({ tone: 'info', title: 'לא נמצאו התאמות.' });
      return;
    }
    commit(next);
    pushToast({ tone: 'success', title: `הוחלפו ${count} מופעים.` });
  };

  // -- render ------------------------------------------------------------
  if (loading) {
    return <div className="p-10 text-center text-ink-500">טוען תמליל…</div>;
  }
  if (!transcript || !activeJobId) {
    return <div className="p-10 text-center text-ink-500">לא נבחר תמליל.</div>;
  }

  const speakerKeys = Array.from(
    new Set(segments.map((segment) => segment.speaker).filter((value): value is string => !!value)),
  );

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-4 p-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <h1 className="truncate text-lg font-semibold text-ink-900">
            {truncateMiddle(job?.filename ?? 'תמליל', 60)}
          </h1>
          <p className="text-xs text-ink-500">
            <Numeric>{segments.length}</Numeric> מקטעים ·{' '}
            <Numeric>{formatClock(transcript.duration)}</Numeric>
            {transcript.partial && ' · תמליל חלקי'}
          </p>
        </div>

        <div className="flex flex-wrap gap-2">
          <span
            aria-live="polite"
            className={`self-center text-xs ${
              saveState === 'error' ? 'text-red-700' : 'text-ink-500'
            }`}
          >
            {saveState === 'saving' && 'שומר…'}
            {saveState === 'saved' && 'נשמר'}
            {saveState === 'error' && 'השמירה נכשלה'}
          </span>
          <button type="button" className="btn-ghost" onClick={undo} disabled={history.length === 0}>
            ביטול פעולה
          </button>
          <button type="button" className="btn-ghost" onClick={redo} disabled={future.length === 0}>
            ביצוע חוזר
          </button>
          <button type="button" className="btn-secondary" onClick={() => void copyAll()}>
            העתק הכול
          </button>
          <button type="button" className="btn-primary" onClick={() => setExportOpen(true)}>
            ייצוא
          </button>
          <button type="button" className="btn-ghost" onClick={closeTranscript}>
            סגירה
          </button>
        </div>
      </header>

      <MediaPlayer
        ref={playerRef}
        src={api.mediaUrl(activeJobId)}
        hasVideo={Boolean(job?.filename?.match(/\.(mp4|mov|mkv|webm)$/i))}
        onTimeUpdate={setCurrentTime}
      />

      {/* -- find & replace -------------------------------------------- */}
      <div className="card flex flex-wrap items-end gap-3 p-3">
        <div className="min-w-[180px] flex-1">
          <label className="field-label" htmlFor="find-input">
            חיפוש
          </label>
          <input
            id="find-input"
            className="field-input"
            value={findText}
            onChange={(event) => setFindText(event.target.value)}
          />
        </div>
        <div className="min-w-[180px] flex-1">
          <label className="field-label" htmlFor="replace-input">
            החלפה ב־
          </label>
          <input
            id="replace-input"
            className="field-input"
            value={replaceText}
            onChange={(event) => setReplaceText(event.target.value)}
          />
        </div>
        <button type="button" className="btn-secondary" onClick={doReplaceAll} disabled={!findText}>
          החלף הכול
        </button>
      </div>

      {/* -- speakers --------------------------------------------------- */}
      {speakerKeys.length > 0 && (
        <div className="card flex flex-wrap gap-3 p-3">
          <span className="self-center text-sm font-medium text-ink-700">דוברים:</span>
          {speakerKeys.map((key, index) => (
            <input
              key={key}
              className="field-input w-40"
              aria-label={`שם דובר ${index + 1}`}
              value={speakers[key] ?? `דובר ${index + 1}`}
              onChange={(event) => renameSpeaker(key, event.target.value)}
            />
          ))}
        </div>
      )}

      {/* -- segments --------------------------------------------------- */}
      <ol className="card divide-y divide-ink-200" data-testid="segment-list">
        {segments.map((segment, index) => {
          const isActive = index === activeIndex;
          return (
            <li
              key={segment.id}
              data-testid={`segment-${segment.id}`}
              data-active={isActive || undefined}
              className={`flex gap-3 p-3 transition-colors ${
                isActive ? 'bg-brand-50' : 'bg-white'
              }`}
            >
              <button
                type="button"
                className="numeric h-fit shrink-0 rounded px-2 py-1 text-xs text-brand-700 hover:bg-brand-100"
                aria-label={`מעבר לזמן ${formatClock(segment.start)}`}
                onClick={() => playerRef.current?.seek(segment.start)}
              >
                {formatClock(segment.start)}
              </button>

              <textarea
                aria-label={`מקטע ${index + 1}`}
                className="min-h-[52px] w-full resize-y rounded-lg border border-transparent bg-transparent p-2 text-[15px] leading-relaxed text-ink-900 outline-none focus:border-brand-400 focus:bg-white"
                value={segment.text}
                onChange={(event) => editSegment(index, event.target.value)}
                rows={Math.max(1, Math.ceil(segment.text.length / 90))}
              />
            </li>
          );
        })}
      </ol>

      {exportOpen && (
        <ExportDialog jobId={activeJobId} onClose={() => setExportOpen(false)} />
      )}
    </div>
  );
}
