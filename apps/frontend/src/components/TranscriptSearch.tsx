/**
 * Find and replace bar for the transcript.
 *
 * Search stands on its own: it reports how many matches there are and lets you
 * walk them. Replacing is a separate, optional step rather than the only way
 * to use the box.
 *
 * The query is submitted with Enter rather than searched as you type. Matching
 * on every keystroke scrolls the transcript to whatever the first letter hit
 * while you are still typing the word, which is disorienting and throws away
 * your place.
 */

import { useEffect, useRef, useState } from 'react';
import { Numeric } from '@/components/ui/Primitives';
import { ChevronDownIcon, ChevronUpIcon, CloseIcon, SearchIcon } from '@/components/ui/Icons';

export interface SearchOptions {
  caseSensitive: boolean;
  wholeWord: boolean;
}

interface Props {
  /** The query actually being searched — only changes when submitted. */
  query: string;
  /** Commit a new query. Called on Enter, not on every keystroke. */
  onQueryChange: (value: string) => void;
  replacement: string;
  onReplacementChange: (value: string) => void;
  options: SearchOptions;
  onOptionsChange: (options: SearchOptions) => void;
  matchCount: number;
  activeIndex: number;
  onStep: (delta: number) => void;
  onReplaceCurrent: () => void;
  onReplaceAll: () => void;
}

export function TranscriptSearch({
  query,
  onQueryChange,
  replacement,
  onReplacementChange,
  options,
  onOptionsChange,
  matchCount,
  activeIndex,
  onStep,
  onReplaceCurrent,
  onReplaceAll,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [showReplace, setShowReplace] = useState(false);

  // What is typed, separate from what is being searched. Searching on every
  // keystroke scrolls the transcript around while you are still typing the
  // word, so the query is only committed when you press Enter.
  const [draft, setDraft] = useState(query);

  // Keep the box in step when the query is changed from outside (cleared,
  // or replaced by a different transcript).
  useEffect(() => {
    setDraft(query);
  }, [query]);

  const pending = draft !== query;

  /**
   * Enter means "search this", and once it is already searched, "next match".
   */
  const submit = (delta: number) => {
    if (pending) onQueryChange(draft);
    else onStep(delta);
  };

  const clear = () => {
    setDraft('');
    onQueryChange('');
  };

  // Ctrl+F focuses the box, as it would in any editor.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'f') {
        event.preventDefault();
        inputRef.current?.focus();
        inputRef.current?.select();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  const hasQuery = query.length > 0;
  const noResults = hasQuery && matchCount === 0;

  return (
    <section className="card p-3" aria-label="חיפוש בתמליל">
      <div className="flex flex-wrap items-center gap-2">
        {/* -- query ---------------------------------------------------- */}
        <div className="relative min-w-[220px] flex-1">
          <SearchIcon className="pointer-events-none absolute inset-y-0 end-3 my-auto h-4 w-4 text-ink-400" />
          <input
            ref={inputRef}
            type="search"
            className={`field-input pe-9 ${!pending && noResults ? 'border-red-300' : ''}`}
            placeholder="חיפוש בתמליל…"
            aria-label="חיפוש בתמליל"
            aria-describedby="search-count"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault();
                submit(event.shiftKey ? -1 : 1);
              } else if (event.key === 'Escape') {
                event.preventDefault();
                clear();
              }
            }}
          />
        </div>

        {/* -- count + stepping ----------------------------------------- */}
        <p
          id="search-count"
          aria-live="polite"
          className={`min-w-[92px] text-sm ${noResults ? 'text-red-700' : 'text-ink-600'}`}
        >
          {pending && draft.length > 0 && (
            <span className="text-ink-400">Enter לחיפוש</span>
          )}
          {!pending && !hasQuery && <span className="text-ink-400">הקלידו וחפשו</span>}
          {!pending && noResults && 'אין תוצאות'}
          {!pending && hasQuery && matchCount > 0 && (
            <>
              <Numeric>{activeIndex + 1}</Numeric> מתוך <Numeric>{matchCount}</Numeric>
            </>
          )}
        </p>

        <div className="flex items-center gap-1">
          <button
            type="button"
            className="flex h-9 w-9 items-center justify-center rounded-lg text-ink-600 transition-colors hover:bg-ink-100 disabled:opacity-40 disabled:hover:bg-transparent"
            aria-label="התוצאה הקודמת"
            title="התוצאה הקודמת (Shift+Enter)"
            disabled={matchCount === 0 || pending}
            onClick={() => onStep(-1)}
          >
            <ChevronUpIcon className="h-5 w-5" />
          </button>
          <button
            type="button"
            className="flex h-9 w-9 items-center justify-center rounded-lg text-ink-600 transition-colors hover:bg-ink-100 disabled:opacity-40 disabled:hover:bg-transparent"
            aria-label="התוצאה הבאה"
            title="התוצאה הבאה (Enter)"
            disabled={matchCount === 0 || pending}
            onClick={() => onStep(1)}
          >
            <ChevronDownIcon className="h-5 w-5" />
          </button>
          {(hasQuery || draft.length > 0) && (
            <button
              type="button"
              className="flex h-9 w-9 items-center justify-center rounded-lg text-ink-500 transition-colors hover:bg-ink-100"
              aria-label="ניקוי החיפוש"
              title="ניקוי החיפוש (Esc)"
              onClick={clear}
            >
              <CloseIcon className="h-4 w-4" />
            </button>
          )}
        </div>

        {/* -- options --------------------------------------------------- */}
        <div className="flex items-center gap-3 text-xs text-ink-600">
          <label className="flex items-center gap-1.5">
            <input
              type="checkbox"
              className="h-3.5 w-3.5 rounded border-ink-300 text-brand-600"
              checked={options.caseSensitive}
              onChange={(event) =>
                onOptionsChange({ ...options, caseSensitive: event.target.checked })
              }
            />
            תלוי רישיות
          </label>
          <label className="flex items-center gap-1.5">
            <input
              type="checkbox"
              className="h-3.5 w-3.5 rounded border-ink-300 text-brand-600"
              checked={options.wholeWord}
              onChange={(event) =>
                onOptionsChange({ ...options, wholeWord: event.target.checked })
              }
            />
            מילה שלמה
          </label>
        </div>

        <button
          type="button"
          className="btn-ghost ms-auto text-sm"
          aria-expanded={showReplace}
          onClick={() => setShowReplace((value) => !value)}
        >
          {showReplace ? 'הסתר החלפה' : 'החלפה'}
        </button>
      </div>

      {/* -- replace, only when asked for ------------------------------- */}
      {showReplace && (
        <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-ink-200 pt-3">
          <input
            type="text"
            className="field-input min-w-[220px] flex-1"
            placeholder="החלפה ב…"
            aria-label="טקסט להחלפה"
            value={replacement}
            onChange={(event) => onReplacementChange(event.target.value)}
          />
          <button
            type="button"
            className="btn-secondary"
            disabled={matchCount === 0}
            onClick={onReplaceCurrent}
          >
            החלף
          </button>
          <button
            type="button"
            className="btn-secondary"
            disabled={matchCount === 0}
            onClick={onReplaceAll}
          >
            החלף הכול
          </button>
        </div>
      )}
    </section>
  );
}
