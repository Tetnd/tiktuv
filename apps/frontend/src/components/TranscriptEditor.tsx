/**
 * RTL transcript editor.
 *
 * Text is edited in logical order in ordinary `<textarea>` elements; direction
 * comes from the document. Undo/redo is a local snapshot stack, and edits are
 * autosaved to the backend after a short idle period.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Segment, TranscriptAnalysis } from '@shared/index';
import { api, ApiError } from '@/lib/api';
import { formatClock, truncateMiddle } from '@/lib/format';
import { Numeric } from '@/components/ui/Primitives';
import { MediaPlayer, type PlayerHandle } from '@/components/MediaPlayer';
import { ExportDialog } from '@/components/ExportDialog';
import { TranscriptSearch, type SearchOptions } from '@/components/TranscriptSearch';
import {
  AnalysisBody,
  ANALYSIS_TABS,
  type AnalysisState,
  type AnalysisTab,
} from '@/components/AnalysisPanel';
import { findMatches, replaceAllMatches, replaceMatch, stepMatch } from '@/lib/search';
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

export function TranscriptEditor() {
  const transcript = useStore((s) => s.transcript);
  const loading = useStore((s) => s.transcriptLoading);
  const activeJobId = useStore((s) => s.activeJobId);
  const jobs = useStore((s) => s.jobs);
  const saveTranscript = useStore((s) => s.saveTranscript);
  const closeTranscript = useStore((s) => s.closeTranscript);
  const refreshJobs = useStore((s) => s.refreshJobs);
  const pushToast = useStore((s) => s.pushToast);

  const [segments, setSegments] = useState<Segment[]>([]);
  const [speakers, setSpeakers] = useState<Record<string, string>>({});
  const [history, setHistory] = useState<Segment[][]>([]);
  const [future, setFuture] = useState<Segment[][]>([]);
  const [currentTime, setCurrentTime] = useState(0);
  const [findText, setFindText] = useState('');
  const [replaceText, setReplaceText] = useState('');
  const [searchOptions, setSearchOptions] = useState<SearchOptions>({
    caseSensitive: false,
    wholeWord: false,
  });
  const [matchIndex, setMatchIndex] = useState(0);
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [exportOpen, setExportOpen] = useState(false);
  const [pdfBusy, setPdfBusy] = useState(false);
  const [tab, setTab] = useState<'transcript' | AnalysisTab>('transcript');
  const [analysis, setAnalysis] = useState<TranscriptAnalysis | null>(null);
  const [analysisState, setAnalysisState] = useState<AnalysisState>('idle');
  const [analysisError, setAnalysisError] = useState<string | null>(null);

  const playerRef = useRef<PlayerHandle>(null);
  //: segment id -> its textarea, so a match can be scrolled to and selected.
  const textareaRefs = useRef(new Map<number, HTMLTextAreaElement>());
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

  const downloadPdf = async () => {
    if (!transcript || !activeJobId) return;
    setPdfBusy(true);
    // Persist any unsaved edits first, so the PDF reflects what is on screen.
    try {
      await api.saveTranscript(activeJobId, segments, speakers);
      dirty.current = false;
      setSaveState('saved');
    } catch {
      setSaveState('error');
      setPdfBusy(false);
      pushToast({ tone: 'error', title: 'השמירה נכשלה. ה-PDF לא נוצר.' });
      return;
    }

    try {
      const result = await api.exportJob(activeJobId, ['pdf'], null);
      const pdf = result.results.find((entry) => entry.format === 'pdf');
      if (!pdf) {
        pushToast({ tone: 'error', title: 'יצירת ה-PDF נכשלה.' });
        return;
      }
      const stem = (job?.filename ?? 'תמליל').replace(/\.[^.]+$/, '');
      const anchor = document.createElement('a');
      anchor.href = api.downloadUrl(pdf.path);
      anchor.download = `${stem}.pdf`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      pushToast({ tone: 'success', title: 'ה-PDF הורד.' });
      await refreshJobs();
    } catch (error) {
      const detail = error instanceof ApiError ? error.messageHe : undefined;
      pushToast({ tone: 'error', title: 'יצירת ה-PDF נכשלה.', detail });
    } finally {
      setPdfBusy(false);
    }
  };

  const loadAnalysis = useCallback(
    async (force: boolean) => {
      if (!activeJobId) return;
      setAnalysisState('loading');
      setAnalysisError(null);
      try {
        let result: TranscriptAnalysis;
        if (force) {
          result = await api.createAnalysis(activeJobId);
        } else {
          try {
            result = await api.getAnalysis(activeJobId);
          } catch (error) {
            if (error instanceof ApiError && error.status === 404) {
              // Not generated yet — generate it now.
              result = await api.createAnalysis(activeJobId);
            } else {
              throw error;
            }
          }
        }
        setAnalysis(result);
        setAnalysisState('ready');
      } catch (error) {
        setAnalysisError(error instanceof ApiError ? error.messageHe : String(error));
        setAnalysisState('error');
      }
    },
    [activeJobId],
  );

  useEffect(() => {
    if (tab !== 'transcript' && analysisState === 'idle') {
      void loadAnalysis(false);
    }
  }, [tab, analysisState, loadAnalysis]);

  // -- search ------------------------------------------------------------
  const matches = useMemo(
    () => findMatches(segments, findText, searchOptions),
    [segments, findText, searchOptions],
  );

  const matchesBySegment = useMemo(() => {
    const index = new Map<number, number>();
    for (const match of matches) {
      index.set(match.segmentIndex, (index.get(match.segmentIndex) ?? 0) + 1);
    }
    return index;
  }, [matches]);

  // Editing or retyping can leave the cursor past the end of the new results.
  useEffect(() => {
    setMatchIndex((current) => (current < matches.length ? current : 0));
  }, [matches.length]);

  /** Scroll a match into view and select it, so it is obvious where you are. */
  const revealMatch = useCallback(
    (index: number) => {
      const match = matches[index];
      if (!match) return;
      const textarea = textareaRefs.current.get(match.segmentId);
      if (!textarea) return;
      textarea.scrollIntoView({ block: 'center', behavior: 'smooth' });
      // Selecting without focusing keeps typing in the search box working,
      // and the highlighted segment shows which match is current.
      textarea.setSelectionRange(match.start, match.end);
    },
    [matches],
  );

  const stepSearch = useCallback(
    (delta: number) => {
      if (matches.length === 0) return;
      const next = stepMatch(matchIndex, matches.length, delta);
      setMatchIndex(next);
      revealMatch(next);
    },
    [matchIndex, matches.length, revealMatch],
  );

  // Jump to the first hit when a *new* query is submitted.
  //
  // Keyed on the query alone, not on the result count: editing a segment
  // changes how many matches exist, and re-revealing on that would yank the
  // view away from the line being typed.
  const lastRevealedQuery = useRef('');
  useEffect(() => {
    if (findText && findText !== lastRevealedQuery.current && matches.length > 0) {
      lastRevealedQuery.current = findText;
      revealMatch(0);
    } else if (!findText) {
      lastRevealedQuery.current = '';
    }
    // `matches` is recomputed in the same render as `findText`, so it is
    // already current here; depending on it too would defeat the guard above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [findText]);

  const doReplaceCurrent = () => {
    const match = matches[matchIndex];
    if (!match) return;
    commit(replaceMatch(segments, match, replaceText));
    pushToast({ tone: 'success', title: 'מופע אחד הוחלף.' });
  };

  const doReplaceAll = () => {
    const { segments: next, count } = replaceAllMatches(
      segments,
      findText,
      replaceText,
      searchOptions,
    );
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
          <button
            type="button"
            className="btn-secondary"
            disabled={pdfBusy}
            onClick={() => void downloadPdf()}
          >
            {pdfBusy ? 'מייצא…' : 'הורדת PDF'}
          </button>
          <button type="button" className="btn-primary" onClick={() => setExportOpen(true)}>
            ייצוא
          </button>
          <button type="button" className="btn-ghost" onClick={closeTranscript}>
            סגירה
          </button>
        </div>
      </header>

      <nav aria-label="תצוגות תמליל" className="flex flex-wrap gap-1 border-b border-ink-200 pb-2">
        <button
          type="button"
          aria-current={tab === 'transcript' ? 'page' : undefined}
          onClick={() => setTab('transcript')}
          className={`rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
            tab === 'transcript' ? 'bg-brand-50 text-brand-700' : 'text-ink-600 hover:bg-ink-100'
          }`}
        >
          תמלול
        </button>
        {ANALYSIS_TABS.map((entry) => (
          <button
            key={entry.id}
            type="button"
            aria-current={tab === entry.id ? 'page' : undefined}
            onClick={() => setTab(entry.id)}
            className={`rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
              tab === entry.id ? 'bg-brand-50 text-brand-700' : 'text-ink-600 hover:bg-ink-100'
            }`}
          >
            {entry.label}
          </button>
        ))}
      </nav>

      {tab === 'transcript' ? (
        <>
          <MediaPlayer
        ref={playerRef}
        src={api.mediaUrl(activeJobId)}
        hasVideo={Boolean(job?.filename?.match(/\.(mp4|mov|mkv|webm)$/i))}
        onTimeUpdate={setCurrentTime}
      />

      {/* -- find & replace -------------------------------------------- */}
      <TranscriptSearch
        query={findText}
        onQueryChange={(value) => {
          setFindText(value);
          setMatchIndex(0);
        }}
        replacement={replaceText}
        onReplacementChange={setReplaceText}
        options={searchOptions}
        onOptionsChange={setSearchOptions}
        matchCount={matches.length}
        activeIndex={matchIndex}
        onStep={stepSearch}
        onReplaceCurrent={doReplaceCurrent}
        onReplaceAll={doReplaceAll}
      />

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
          const isCurrentMatch = matches[matchIndex]?.segmentIndex === index;
          const hasMatch = matchesBySegment.has(index);
          return (
            <li
              key={segment.id}
              data-testid={`segment-${segment.id}`}
              data-active={isActive || undefined}
              data-match={hasMatch || undefined}
              data-current-match={isCurrentMatch || undefined}
              className={`flex gap-3 p-3 transition-colors ${
                isCurrentMatch
                  ? 'bg-amber-50 ring-2 ring-inset ring-amber-400'
                  : hasMatch
                    ? 'bg-amber-50/40'
                    : isActive
                      ? 'bg-brand-50'
                      : 'bg-white'
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

              <div className="min-w-0 flex-1">
                {segment.speaker && (
                  <span className="mb-1 inline-block rounded bg-brand-50 px-1.5 py-0.5 text-xs font-medium text-brand-700">
                    {speakers[segment.speaker] ?? segment.speaker}
                  </span>
                )}
                <textarea
                  ref={(node) => {
                    if (node) textareaRefs.current.set(segment.id, node);
                    else textareaRefs.current.delete(segment.id);
                  }}
                  aria-label={`מקטע ${index + 1}`}
                  className="min-h-[52px] w-full resize-y rounded-lg border border-transparent bg-transparent p-2 text-[15px] leading-relaxed text-ink-900 outline-none focus:border-brand-400 focus:bg-white"
                  value={segment.text}
                  onChange={(event) => editSegment(index, event.target.value)}
                  rows={Math.max(1, Math.ceil(segment.text.length / 90))}
                />
              </div>
            </li>
          );
        })}
      </ol>
        </>
      ) : (
        <AnalysisBody
          tab={tab}
          analysis={analysis}
          state={analysisState}
          error={analysisError}
          onRetry={() => void loadAnalysis(false)}
          onRegenerate={() => void loadAnalysis(true)}
        />
      )}

      {exportOpen && (
        <ExportDialog jobId={activeJobId} onClose={() => setExportOpen(false)} />
      )}
    </div>
  );
}
