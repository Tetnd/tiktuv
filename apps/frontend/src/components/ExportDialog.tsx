/** Format picker for exporting a single transcript. */

import { useEffect, useRef, useState } from 'react';
import type { ExportFormat } from '@shared/index';
import { EXPORT_FORMATS } from '@shared/index';
import { FORMAT_LABELS } from '@/lib/format';
import { FolderField } from '@/components/ui/FolderField';
import { useStore } from '@/state/store';

const DEFAULT_SELECTION: ExportFormat[] = ['txt', 'docx', 'pdf', 'srt'];

export function ExportDialog({ jobId, onClose }: { jobId: string; onClose: () => void }) {
  const exportJob = useStore((s) => s.exportJob);
  const settings = useStore((s) => s.settings);
  const [selected, setSelected] = useState<ExportFormat[]>(DEFAULT_SELECTION);
  const [outputDir, setOutputDir] = useState(settings.output_dir ?? '');
  const [busy, setBusy] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    dialogRef.current?.focus();
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  const toggle = (format: ExportFormat) =>
    setSelected((current) =>
      current.includes(format)
        ? current.filter((value) => value !== format)
        : [...current, format],
    );

  const submit = async () => {
    if (selected.length === 0) return;
    setBusy(true);
    await exportJob(jobId, selected, outputDir || null);
    setBusy(false);
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink-900/40 p-4">
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="export-title"
        tabIndex={-1}
        className="w-full max-w-lg rounded-xl bg-white p-5 shadow-xl"
      >
        <h2 id="export-title" className="text-lg font-semibold text-ink-900">
          ייצוא התמליל
        </h2>
        <p className="mt-1 text-sm text-ink-500">
          קובצי DOCX ו-PDF נוצרים עם פסקאות מימין לשמאל ופונט התומך בעברית.
        </p>

        <fieldset className="mt-4">
          <legend className="field-label">פורמטים</legend>
          <div className="grid gap-2 sm:grid-cols-2">
            {EXPORT_FORMATS.map((format) => (
              <label key={format} className="flex items-center gap-2 text-sm text-ink-700">
                <input
                  type="checkbox"
                  className="h-4 w-4 rounded border-ink-300 text-brand-600"
                  checked={selected.includes(format)}
                  onChange={() => toggle(format)}
                />
                {FORMAT_LABELS[format]}
              </label>
            ))}
          </div>
        </fieldset>

        <div className="mt-4">
          <FolderField
            id="export-dir"
            label="תיקיית יעד"
            value={outputDir}
            onChange={setOutputDir}
            placeholder="ברירת מחדל: תיקיית הייצוא של היישום"
          />
        </div>

        <div className="mt-5 flex justify-start gap-2">
          <button
            type="button"
            className="btn-primary"
            disabled={selected.length === 0 || busy}
            onClick={() => void submit()}
          >
            {busy ? 'מייצא…' : 'ייצוא'}
          </button>
          <button type="button" className="btn-ghost" onClick={onClose}>
            ביטול
          </button>
        </div>
      </div>
    </div>
  );
}
