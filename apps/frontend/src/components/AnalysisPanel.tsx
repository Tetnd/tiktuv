/** Renders one AI-analysis section produced by DeepSeek. */

import type { TranscriptAnalysis } from '@shared/index';
import { Badge } from '@/components/ui/Primitives';

export type AnalysisTab =
  | 'summary'
  | 'key_points'
  | 'action_items'
  | 'named_entities'
  | 'speaker_summaries'
  | 'speaker_quotes';

export const ANALYSIS_TABS: Array<{ id: AnalysisTab; label: string }> = [
  { id: 'summary', label: 'תקציר' },
  { id: 'key_points', label: 'נקודות מפתח' },
  { id: 'action_items', label: 'משימות' },
  { id: 'named_entities', label: 'יישויות' },
  { id: 'speaker_summaries', label: 'סיכום דוברים' },
  { id: 'speaker_quotes', label: 'ציטוטים' },
];

export type AnalysisState = 'idle' | 'loading' | 'ready' | 'error';

export function AnalysisBody({
  tab,
  analysis,
  state,
  error,
  onRetry,
  onRegenerate,
}: {
  tab: AnalysisTab;
  analysis: TranscriptAnalysis | null;
  state: AnalysisState;
  error: string | null;
  onRetry: () => void;
  onRegenerate: () => void;
}) {
  if (state === 'loading') {
    return <div className="card p-8 text-center text-ink-500">מנתח בעזרת DeepSeek…</div>;
  }
  if (state === 'error' || !analysis) {
    return (
      <div className="card p-8 text-center">
        <p className="text-sm text-red-700">{error ?? 'יצירת הניתוח נכשלה.'}</p>
        <button type="button" className="btn-secondary mt-3" onClick={onRetry}>
          נסה שוב
        </button>
      </div>
    );
  }

  return (
    <div className="card p-5">
      <div className="mb-4 flex items-center justify-between">
        <p className="text-xs text-ink-500">
          נוצר בעזרת DeepSeek
          {analysis.model ? (
            <>
              {' '}
              <span className="ltr-run numeric">{analysis.model}</span>
            </>
          ) : null}
        </p>
        <button type="button" className="btn-ghost" onClick={onRegenerate}>
          יצירה מחדש
        </button>
      </div>
      <Section tab={tab} analysis={analysis} />
    </div>
  );
}

function Section({ tab, analysis }: { tab: AnalysisTab; analysis: TranscriptAnalysis }) {
  switch (tab) {
    case 'summary':
      return (
        <p className="whitespace-pre-line text-[15px] leading-relaxed text-ink-900">
          {analysis.summary || 'לא הופק תקציר.'}
        </p>
      );

    case 'key_points':
      return <BulletList items={analysis.key_points} empty="לא נמצאו נקודות מפתח." />;

    case 'action_items':
      if (analysis.action_items.length === 0) {
        return <Empty text="לא נמצאו משימות או פריטי פעולה." />;
      }
      return (
        <ul className="space-y-2">
          {analysis.action_items.map((item, index) => (
            <li key={index} className="flex items-start gap-3 rounded-lg border border-ink-200 p-3">
              <span aria-hidden="true" className="mt-0.5 text-brand-600">
                ☐
              </span>
              <div className="min-w-0">
                <p className="text-sm text-ink-900">{item.task}</p>
                {item.assignee && (
                  <p className="mt-0.5 text-xs text-ink-500">מוטלת על: {item.assignee}</p>
                )}
              </div>
            </li>
          ))}
        </ul>
      );

    case 'named_entities':
      if (analysis.named_entities.length === 0) {
        return <Empty text="לא נמצאו ישויות בעלות שם." />;
      }
      return (
        <ul className="flex flex-wrap gap-2">
          {analysis.named_entities.map((entity, index) => (
            <li key={index} className="rounded-lg border border-ink-200 px-3 py-1.5 text-sm">
              <span className="font-medium text-ink-900">{entity.name}</span>{' '}
              <Badge tone="neutral">{entity.type}</Badge>
            </li>
          ))}
        </ul>
      );

    case 'speaker_summaries':
      if (analysis.speaker_summaries.length === 0) {
        return <Empty text="לא זוהו דוברים בתמליל." />;
      }
      return (
        <div className="space-y-3">
          {analysis.speaker_summaries.map((entry, index) => (
            <div key={index} className="rounded-lg border border-ink-200 p-3">
              <p className="text-sm font-semibold text-brand-700">{entry.speaker}</p>
              <p className="mt-1 whitespace-pre-line text-sm text-ink-800">{entry.summary}</p>
            </div>
          ))}
        </div>
      );

    case 'speaker_quotes':
      if (analysis.speaker_quotes.length === 0) {
        return <Empty text="לא זוהו דוברים בתמליל." />;
      }
      return (
        <div className="space-y-3">
          {analysis.speaker_quotes.map((entry, index) => (
            <div key={index} className="rounded-lg border border-ink-200 p-3">
              <p className="text-sm font-semibold text-brand-700">{entry.speaker}</p>
              <ul className="mt-1 space-y-1.5">
                {entry.quotes.map((quote, quoteIndex) => (
                  <li
                    key={quoteIndex}
                    className="border-s-2 border-ink-200 ps-3 text-sm italic text-ink-800"
                  >
                    “{quote}”
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      );
  }
}

function BulletList({ items, empty }: { items: string[]; empty: string }) {
  if (items.length === 0) return <Empty text={empty} />;
  return (
    <ul className="list-disc space-y-1.5 ps-6 text-[15px] leading-relaxed text-ink-900">
      {items.map((item, index) => (
        <li key={index}>{item}</li>
      ))}
    </ul>
  );
}

function Empty({ text }: { text: string }) {
  return <p className="text-sm text-ink-500">{text}</p>;
}
