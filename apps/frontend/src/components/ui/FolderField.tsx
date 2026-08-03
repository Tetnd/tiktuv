/**
 * A folder path with a native "browse" button.
 *
 * The button only appears when the session can actually show a dialog — in the
 * desktop window it can, in the browser-fallback session it cannot (a web page
 * has no way to learn a real filesystem path). Rather than offer a button that
 * silently does nothing there, the field falls back to plain typing.
 *
 * The path is always shown and always editable, so pasting one still works.
 */

import { useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { useStore } from '@/state/store';

interface Props {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  hint?: string;
}

export function FolderField({ id, label, value, onChange, placeholder, hint }: Props) {
  const [canBrowse, setCanBrowse] = useState(false);
  const [busy, setBusy] = useState(false);
  const pushToast = useStore((s) => s.pushToast);

  useEffect(() => {
    let cancelled = false;
    void api
      .canPickFolder()
      .then((result) => {
        if (!cancelled) setCanBrowse(result.available);
      })
      .catch(() => {
        if (!cancelled) setCanBrowse(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const browse = async () => {
    setBusy(true);
    try {
      const result = await api.pickFolder(value || null);
      // A cancelled dialog returns null: leave whatever was there.
      if (result.path) onChange(result.path);
    } catch {
      pushToast({
        tone: 'error',
        title: 'לא ניתן לפתוח את חלון בחירת התיקייה.',
        detail: 'הקלידו או הדביקו את הנתיב במקום זאת.',
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      <div className="flex gap-2">
        <input
          id={id}
          type="text"
          className="field-input ltr-run flex-1"
          dir="ltr"
          placeholder={placeholder}
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
        {canBrowse && (
          <button
            type="button"
            className="btn-secondary shrink-0"
            disabled={busy}
            onClick={() => void browse()}
          >
            {busy ? 'בוחר…' : 'עיון…'}
          </button>
        )}
      </div>
      {hint && <p className="field-hint">{hint}</p>}
    </div>
  );
}
