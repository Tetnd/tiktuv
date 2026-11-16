/** Drag-and-drop area plus a file picker. */

import { useCallback, useRef, useState } from 'react';
import { ZoomImportDialog } from '@/components/ZoomImportDialog';
import { ACCEPT_ATTRIBUTE, ACCEPTED_EXTENSIONS, isAcceptedFile } from '@/lib/format';
import { useStore } from '@/state/store';

export function UploadZone() {
  const addFiles = useStore((s) => s.addFiles);
  const pushToast = useStore((s) => s.pushToast);
  const models = useStore((s) => s.models);
  const settings = useStore((s) => s.settings);
  const update = useStore((s) => s.updateSettings);
  const [dragging, setDragging] = useState(false);
  const [zoomOpen, setZoomOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  // Stable, so the dialog's effects do not re-run on every parent render.
  const closeZoom = useCallback(() => setZoomOpen(false), []);

  const selectedModel = models.find((model) => model.id === settings.model_id);
  const usingCloud = selectedModel?.is_cloud === true;

  const accept = useCallback(
    (fileList: FileList | null) => {
      if (!fileList || fileList.length === 0) return;
      const files = Array.from(fileList);
      const usable = files.filter((file) => isAcceptedFile(file.name));
      const rejected = files.length - usable.length;

      if (rejected > 0) {
        pushToast({
          tone: 'error',
          title: `${rejected} קבצים לא נתמכים דולגו.`,
          detail: `סוגים נתמכים: ${ACCEPTED_EXTENSIONS.join(', ')}`,
        });
      }
      if (usable.length > 0) void addFiles(usable);
    },
    [addFiles, pushToast],
  );

  return (
    <div
      data-testid="upload-zone"
      onDragOver={(event) => {
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => {
        event.preventDefault();
        setDragging(false);
        accept(event.dataTransfer.files);
      }}
      className={`rounded-2xl border-2 border-dashed p-10 text-center transition-colors ${
        dragging ? 'border-brand-500 bg-brand-50' : 'border-ink-300 bg-white'
      }`}
    >
      <h2 className="text-lg font-semibold text-ink-800">גררו לכאן קבצי אודיו או וידאו</h2>
      <p className="mt-2 text-sm text-ink-500">
        נתמכים:{' '}
        <span className="ltr-run font-mono text-xs">
          {ACCEPTED_EXTENSIONS.join(', ').toUpperCase()}
        </span>
      </p>

      <div className="mx-auto mt-4 max-w-sm">
        <label htmlFor="upload-engine" className="field-label">
          מנוע תמלול
        </label>
        <select
          id="upload-engine"
          className="field-input"
          value={settings.model_id}
          onChange={(event) => update({ model_id: event.target.value })}
        >
          {models.length === 0 && (
            <option value={settings.model_id}>{settings.model_id}</option>
          )}
          {models.map((model) => (
            <option key={model.id} value={model.id}>
              {model.label_he}
              {!model.is_cloud && !model.downloaded ? ' — טרם הורד' : ''}
            </option>
          ))}
        </select>
      </div>

      <p className="mt-3 text-sm text-ink-500">
        {usingCloud
          ? 'האודיו יישלח לשרתים של Soniox לצורך תמלול.'
          : 'הקבצים מעובדים על המחשב שלכם בלבד ואינם נשלחים לשום מקום.'}
      </p>

      <div className="mt-5 flex flex-wrap items-center justify-center gap-2">
        <button
          type="button"
          className="btn-primary"
          onClick={() => inputRef.current?.click()}
        >
          בחירת קבצים
        </button>
        <button
          type="button"
          className="btn-secondary"
          onClick={() => setZoomOpen(true)}
        >
          תמלול מקישור Zoom
        </button>
      </div>

      <input
        ref={inputRef}
        data-testid="file-input"
        type="file"
        multiple
        accept={ACCEPT_ATTRIBUTE}
        className="hidden"
        aria-label="בחירת קבצים לתמלול"
        onChange={(event) => {
          accept(event.target.files);
          // Reset so selecting the same file twice re-triggers change.
          event.target.value = '';
        }}
      />

      {zoomOpen && <ZoomImportDialog onClose={closeZoom} />}
    </div>
  );
}
