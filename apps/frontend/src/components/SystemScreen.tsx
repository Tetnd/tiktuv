/** First-run preflight, model management, storage and privacy controls. */

import { useEffect, useState } from 'react';
import { formatBytes } from '@/lib/format';
import { Field, StatusPill } from '@/components/ui/Primitives';
import { api, ApiError } from '@/lib/api';
import { useStore } from '@/state/store';

export function PreflightPanel() {
  const system = useStore((s) => s.system);
  const refreshSystem = useStore((s) => s.refreshSystem);

  if (!system) {
    return (
      <section className="card p-4">
        <p className="text-sm text-ink-500">בודק את סביבת העבודה…</p>
      </section>
    );
  }

  const blocking = system.checks.filter((check) => check.status === 'error');

  return (
    <section className="card p-4" aria-labelledby="preflight-heading">
      <div className="mb-3 flex items-center justify-between">
        <h2 id="preflight-heading" className="text-base font-semibold text-ink-800">
          בדיקת מערכת
        </h2>
        <button type="button" className="btn-ghost" onClick={() => void refreshSystem()}>
          רענון
        </button>
      </div>

      {blocking.length > 0 && (
        <div role="alert" className="mb-3 rounded-lg border border-red-200 bg-red-50 p-3">
          <p className="text-sm font-medium text-red-800">
            יש {blocking.length} בעיות שחוסמות תמלול.
          </p>
        </div>
      )}

      <ul className="divide-y divide-ink-200">
        {system.checks.map((check) => (
          <li key={check.id} className="flex items-start gap-3 py-2.5">
            <StatusPill status={check.status}>
              {check.status === 'ok' ? 'תקין' : check.status === 'warning' ? 'אזהרה' : 'שגיאה'}
            </StatusPill>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-ink-800">{check.label_he}</p>
              {check.detail && <p className="text-xs text-ink-600">{check.detail}</p>}
              {check.remedy && <p className="mt-0.5 text-xs text-ink-500">{check.remedy}</p>}
            </div>
          </li>
        ))}
      </ul>

      <dl className="mt-4 grid gap-2 border-t border-ink-200 pt-3 text-xs text-ink-600 sm:grid-cols-2">
        <div><dt className="inline font-medium">גרסה: </dt><dd className="inline ltr-run">{system.app_version}</dd></div>
        <div><dt className="inline font-medium">מערכת: </dt><dd className="inline ltr-run">{system.platform}</dd></div>
        <div><dt className="inline font-medium">Python: </dt><dd className="inline ltr-run">{system.python_version}</dd></div>
        <div><dt className="inline font-medium">FFmpeg: </dt><dd className="inline ltr-run">{system.ffmpeg_version ?? 'לא נמצא'}</dd></div>
        <div><dt className="inline font-medium">PyTorch: </dt><dd className="inline ltr-run">{system.torch_version ?? 'לא מותקן'}</dd></div>
        <div><dt className="inline font-medium">CUDA: </dt><dd className="inline ltr-run">{system.cuda_version ?? 'לא זמין'}</dd></div>
      </dl>
    </section>
  );
}

function ModelsPanel() {
  const models = useStore((s) => s.models);
  const refreshModels = useStore((s) => s.refreshModels);
  const pushToast = useStore((s) => s.pushToast);
  const [busy, setBusy] = useState<string | null>(null);

  const download = async (id: string, bytes: number, path: string) => {
    const approx = formatBytes(bytes);
    const confirmed = window.confirm(
      `להוריד את המודל ${id}?\n\nגודל ההורדה: כ-${approx}\nיישמר בתיקייה:\n${path}\n\n` +
        'זו הפעולה היחידה ביישום שדורשת חיבור לאינטרנט.',
    );
    if (!confirmed) return;

    setBusy(id);
    try {
      await api.downloadModel(id);
      pushToast({ tone: 'info', title: 'ההורדה החלה ברקע.', detail: 'רעננו כדי לראות את המצב.' });
    } catch {
      pushToast({ tone: 'error', title: 'לא ניתן היה להתחיל את ההורדה.' });
    } finally {
      setBusy(null);
    }
  };

  const remove = async (id: string) => {
    if (!window.confirm(`למחוק את קובצי המודל ${id} מהמחשב?`)) return;
    setBusy(id);
    try {
      const { reclaimed_bytes } = await api.deleteModel(id);
      pushToast({ tone: 'success', title: `שוחררו ${formatBytes(reclaimed_bytes)}.` });
      await refreshModels();
    } catch {
      pushToast({ tone: 'error', title: 'מחיקת המודל נכשלה.' });
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="card p-4" aria-labelledby="models-heading">
      <h2 id="models-heading" className="mb-3 text-base font-semibold text-ink-800">
        מודלים
      </h2>
      <ul className="divide-y divide-ink-200">
        {models.map((model) => (
          <li key={model.id} className="flex flex-wrap items-start gap-3 py-3">
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-ink-900">{model.label_he}</p>
              <p className="mt-0.5 text-xs text-ink-500 ltr-run">{model.id}</p>
              {model.notes_he && <p className="mt-1 text-xs text-ink-600">{model.notes_he}</p>}
              <p className="mt-1 text-xs text-ink-500">
                גודל הורדה: כ-{formatBytes(model.approx_download_bytes)} · רישיון:{' '}
                <span className="ltr-run">{model.license}</span>
              </p>
              <p className="mt-0.5 text-xs text-ink-400 ltr-run" title={model.cache_path}>
                {model.cache_path}
              </p>
            </div>
            <div className="flex shrink-0 gap-2">
              {model.downloaded ? (
                <>
                  <StatusPill status="ok">הורד</StatusPill>
                  <button
                    type="button"
                    className="btn-danger"
                    disabled={busy === model.id}
                    onClick={() => void remove(model.id)}
                  >
                    מחיקה
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  className="btn-secondary"
                  disabled={busy === model.id}
                  onClick={() =>
                    void download(model.id, model.approx_download_bytes, model.cache_path)
                  }
                >
                  הורדה
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function StoragePanel() {
  const system = useStore((s) => s.system);
  const refreshSystem = useStore((s) => s.refreshSystem);
  const pushToast = useStore((s) => s.pushToast);
  const storage = system?.storage;
  const [busy, setBusy] = useState(false);

  if (!storage) return null;

  const clearTemp = async () => {
    setBusy(true);
    try {
      const { reclaimed_bytes } = await api.cleanupTemp();
      pushToast({ tone: 'success', title: `שוחררו ${formatBytes(reclaimed_bytes)}.` });
      await refreshSystem();
    } catch {
      pushToast({ tone: 'error', title: 'לא ניתן לנקות קבצים זמניים כרגע.' });
    } finally {
      setBusy(false);
    }
  };

  const rows: Array<[string, string]> = [
    ['תיקיית הנתונים', storage.data_dir],
    ['תיקיית המודלים', storage.models_dir],
    ['תיקיית קבצים זמניים', storage.temp_dir],
  ];

  return (
    <section className="card p-4" aria-labelledby="storage-heading">
      <h2 id="storage-heading" className="mb-3 text-base font-semibold text-ink-800">
        אחסון ופרטיות
      </h2>

      <p className="mb-3 text-sm text-ink-600">
        כל הנתונים נשמרים במחשב שלכם בלבד. אין העלאה של הקלטות, אין איסוף נתוני שימוש,
        ואין תקשורת חיצונית — למעט הורדת מודל שאתם יוזמים במפורש.
      </p>

      <dl className="space-y-2 text-xs">
        {rows.map(([label, value]) => (
          <div key={label}>
            <dt className="font-medium text-ink-700">{label}</dt>
            <dd className="ltr-run break-all text-ink-500">{value}</dd>
          </div>
        ))}
      </dl>

      <ul className="mt-4 grid gap-1 text-xs text-ink-600 sm:grid-cols-2">
        <li>מדיה: {formatBytes(storage.media_bytes)}</li>
        <li>תמלילים: {formatBytes(storage.transcripts_bytes)}</li>
        <li>ייצוא: {formatBytes(storage.exports_bytes)}</li>
        <li>קבצים זמניים: {formatBytes(storage.temp_bytes)}</li>
        <li>מודלים: {formatBytes(storage.models_bytes)}</li>
        <li>פנוי בדיסק: {formatBytes(storage.free_disk_bytes)}</li>
      </ul>

      <button type="button" className="btn-secondary mt-4" disabled={busy} onClick={() => void clearTemp()}>
        מחיקת קבצים זמניים
      </button>
    </section>
  );
}

export function SystemScreen() {
  const system = useStore((s) => s.system);
  const refreshSystem = useStore((s) => s.refreshSystem);

  // System info is fetched lazily: it imports PyTorch on the backend, which is
  // a multi-second stall we do not want while the app is first opening.
  useEffect(() => {
    if (system === null) void refreshSystem();
  }, [system, refreshSystem]);

  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-6 p-6">
      <SonioxPanel />
      <DeepSeekPanel />
      <PreflightPanel />
      <ModelsPanel />
      <StoragePanel />
    </div>
  );
}

function DeepSeekPanel() {
  const pushToast = useStore((s) => s.pushToast);
  const [key, setKey] = useState('');
  const [status, setStatus] = useState<{ configured: boolean; masked_key: string } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void api.deepseekStatus().then(setStatus).catch(() => undefined);
  }, []);

  const save = async () => {
    const trimmed = key.trim();
    if (!trimmed) return;
    setBusy(true);
    try {
      setStatus(await api.setDeepseekKey(trimmed));
      setKey('');
      pushToast({ tone: 'success', title: 'מפתח ה-API נשמר.' });
    } catch (error) {
      const detail = error instanceof ApiError ? error.messageHe : undefined;
      pushToast({ tone: 'error', title: 'שמירת המפתח נכשלה.', detail });
    } finally {
      setBusy(false);
    }
  };

  const clear = async () => {
    setBusy(true);
    try {
      setStatus(await api.clearDeepseekKey());
      pushToast({ tone: 'info', title: 'מפתח ה-API הוסר.' });
    } catch {
      pushToast({ tone: 'error', title: 'הסרת המפתח נכשלה.' });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card p-4" aria-labelledby="deepseek-heading">
      <h2 id="deepseek-heading" className="mb-2 text-base font-semibold text-ink-800">
        DeepSeek (ניתוח AI)
      </h2>
      <p className="mb-3 text-sm text-ink-600">
        מנוע ה-AI שמפיק את תקציר, נקודות המפתח, המשימות, הישויות וניתוח הדוברים.
        התמליל נשלח ל-DeepSeek רק בעת יצירת ניתוח.
      </p>

      {status?.configured ? (
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-sm text-ink-700">
            מפתח מוגדר:{' '}
            <span className="numeric ltr-run font-mono">{status.masked_key}</span>
          </span>
          <button
            type="button"
            className="btn-ghost"
            disabled={busy}
            onClick={() => void clear()}
          >
            הסרת מפתח
          </button>
        </div>
      ) : (
        <div className="grid gap-3">
          <Field label="מפתח API" htmlFor="deepseek-key">
            <input
              id="deepseek-key"
              type="password"
              className="field-input ltr-run"
              value={key}
              onChange={(event) => setKey(event.target.value)}
              placeholder="DeepSeek API key"
              autoComplete="off"
              spellCheck={false}
            />
          </Field>
          <div>
            <button
              type="button"
              className="btn-primary"
              disabled={busy || !key.trim()}
              onClick={() => void save()}
            >
              שמירה
            </button>
          </div>
        </div>
      )}

      <p className="mt-3 text-xs text-ink-500">
        המפתח נשמר מקומית בתיקיית הנתונים של היישום בלבד.
      </p>
    </section>
  );
}

function SonioxPanel() {
  const pushToast = useStore((s) => s.pushToast);
  const [key, setKey] = useState('');
  const [status, setStatus] = useState<{ configured: boolean; masked_key: string } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void api.sonioxStatus().then(setStatus).catch(() => undefined);
  }, []);

  const save = async () => {
    const trimmed = key.trim();
    if (!trimmed) return;
    setBusy(true);
    try {
      setStatus(await api.setSonioxKey(trimmed));
      setKey('');
      pushToast({ tone: 'success', title: 'מפתח ה-API נשמר.' });
    } catch (error) {
      const detail = error instanceof ApiError ? error.messageHe : undefined;
      pushToast({ tone: 'error', title: 'שמירת המפתח נכשלה.', detail });
    } finally {
      setBusy(false);
    }
  };

  const clear = async () => {
    setBusy(true);
    try {
      setStatus(await api.clearSonioxKey());
      pushToast({ tone: 'info', title: 'מפתח ה-API הוסר.' });
    } catch {
      pushToast({ tone: 'error', title: 'הסרת המפתח נכשלה.' });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card p-4" aria-labelledby="soniox-heading">
      <h2 id="soniox-heading" className="mb-2 text-base font-semibold text-ink-800">
        Soniox (תמלול בענן)
      </h2>
      <p className="mb-3 text-sm text-ink-600">
        תמלול עברי מדויק דרך שירות הענן של Soniox. כדי להשתמש בו, בחרו את המודל
        "Soniox" בהגדרות התמלול. שימו לב: האודיו נשלח לשרתים של Soniox.
      </p>

      {status?.configured ? (
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-sm text-ink-700">
            מפתח מוגדר:{' '}
            <span className="numeric ltr-run font-mono">{status.masked_key}</span>
          </span>
          <button
            type="button"
            className="btn-ghost"
            disabled={busy}
            onClick={() => void clear()}
          >
            הסרת מפתח
          </button>
        </div>
      ) : (
        <div className="grid gap-3">
          <Field label="מפתח API" htmlFor="soniox-key">
            <input
              id="soniox-key"
              type="password"
              className="field-input ltr-run"
              value={key}
              onChange={(event) => setKey(event.target.value)}
              placeholder="Soniox API key"
              autoComplete="off"
              spellCheck={false}
            />
          </Field>
          <div>
            <button
              type="button"
              className="btn-primary"
              disabled={busy || !key.trim()}
              onClick={() => void save()}
            >
              שמירה
            </button>
          </div>
        </div>
      )}

      <p className="mt-3 text-xs text-ink-500">
        המפתח נשמר מקומית בתיקיית הנתונים של היישום בלבד.
      </p>
    </section>
  );
}
