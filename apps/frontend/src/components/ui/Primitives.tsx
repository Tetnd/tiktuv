/** Small presentational primitives shared across screens. */

import type { ReactNode } from 'react';
import type { CheckStatus } from '@shared/index';
import { CHECK_TONE, formatPercent } from '@/lib/format';

export function ProgressBar({
  value,
  label,
  tone = 'brand',
}: {
  value: number;
  label?: string;
  tone?: 'brand' | 'danger' | 'muted';
}) {
  const clamped = Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0));
  const fill =
    tone === 'danger' ? 'bg-red-500' : tone === 'muted' ? 'bg-ink-400' : 'bg-brand-500';

  return (
    <div
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(clamped * 100)}
      aria-label={label ?? 'התקדמות'}
      aria-valuetext={formatPercent(clamped)}
      className="h-2 w-full overflow-hidden rounded-full bg-ink-200"
    >
      {/*
        The track is RTL, so the fill grows from the right. Width alone is
        enough — the parent's `dir` anchors it to the correct edge.
      */}
      <div
        className={`h-full rounded-full transition-[width] duration-300 ${fill}`}
        style={{ width: `${clamped * 100}%` }}
      />
    </div>
  );
}

export function StatusPill({ status, children }: { status: CheckStatus; children: ReactNode }) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium ${CHECK_TONE[status]}`}
    >
      {children}
    </span>
  );
}

export function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: string }) {
  const tones: Record<string, string> = {
    neutral: 'bg-ink-100 text-ink-700',
    running: 'bg-brand-100 text-brand-800',
    completed: 'bg-emerald-100 text-emerald-800',
    failed: 'bg-red-100 text-red-800',
    cancelled: 'bg-amber-100 text-amber-800',
    queued: 'bg-ink-100 text-ink-700',
  };
  return (
    <span
      className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ${tones[tone] ?? tones.neutral}`}
    >
      {children}
    </span>
  );
}

/** A numeric or Latin value that must not be reordered by the bidi algorithm. */
export function Numeric({ children }: { children: ReactNode }) {
  return <span className="numeric">{children}</span>;
}

export function EmptyState({
  title,
  detail,
  action,
}: {
  title: string;
  detail?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed border-ink-300 px-6 py-14 text-center">
      <p className="text-base font-medium text-ink-700">{title}</p>
      {detail && <p className="max-w-md text-sm text-ink-500">{detail}</p>}
      {action}
    </div>
  );
}

export function Field({
  label,
  hint,
  htmlFor,
  children,
}: {
  label: string;
  hint?: string;
  htmlFor?: string;
  children: ReactNode;
}) {
  return (
    <div>
      <label className="field-label" htmlFor={htmlFor}>
        {label}
      </label>
      {children}
      {hint && <p className="field-hint">{hint}</p>}
    </div>
  );
}

export function Toggle({
  id,
  checked,
  onChange,
  label,
  hint,
}: {
  id: string;
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
  hint?: string;
}) {
  return (
    <div className="flex items-start gap-3">
      <input
        id={id}
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-1 h-4 w-4 rounded border-ink-300 text-brand-600"
      />
      <div>
        <label htmlFor={id} className="text-sm font-medium text-ink-800">
          {label}
        </label>
        {hint && <p className="text-xs text-ink-500">{hint}</p>}
      </div>
    </div>
  );
}
