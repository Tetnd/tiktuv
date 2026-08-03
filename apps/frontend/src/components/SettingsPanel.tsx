/** Transcription settings applied to newly queued jobs. */

import { useState } from 'react';
import type { ExportFormat } from '@shared/index';
import { EXPORT_FORMATS } from '@shared/index';
import { FORMAT_LABELS } from '@/lib/format';
import { Field, Toggle } from '@/components/ui/Primitives';
import { FolderField } from '@/components/ui/FolderField';
import { useStore } from '@/state/store';

export function SettingsPanel() {
  const settings = useStore((s) => s.settings);
  const update = useStore((s) => s.updateSettings);
  const models = useStore((s) => s.models);
  const system = useStore((s) => s.system);
  const [open, setOpen] = useState(false);

  const hasGpu = (system?.devices ?? []).some((device) => device.kind === 'cuda');
  const selectedModel = models.find((model) => model.id === settings.model_id);

  const toggleFormat = (format: ExportFormat, enabled: boolean) => {
    const next = enabled
      ? [...settings.export_formats, format]
      : settings.export_formats.filter((value) => value !== format);
    update({ export_formats: Array.from(new Set(next)) });
  };

  return (
    <section className="card" aria-labelledby="settings-heading">
      <button
        type="button"
        className="flex w-full items-center justify-between p-4 text-start"
        aria-expanded={open}
        aria-controls="settings-body"
        onClick={() => setOpen((value) => !value)}
      >
        <h2 id="settings-heading" className="text-base font-semibold text-ink-800">
          הגדרות תמלול
        </h2>
        <span className="text-sm text-ink-500">{open ? 'הסתר' : 'הצג'}</span>
      </button>

      {open && (
        <div id="settings-body" className="grid gap-6 border-t border-ink-200 p-4 md:grid-cols-2">
          {/* -- core ------------------------------------------------ */}
          <Field label="שפה" htmlFor="setting-language">
            <select
              id="setting-language"
              className="field-input"
              value={settings.language}
              onChange={(event) =>
                update({ language: event.target.value as typeof settings.language })
              }
            >
              <option value="he">עברית</option>
              <option value="auto">זיהוי אוטומטי</option>
              <option value="en">אנגלית</option>
            </select>
          </Field>

          <Field
            label="מודל"
            htmlFor="setting-model"
            hint={
              selectedModel && !selectedModel.hebrew_specialised
                ? 'שימו לב: זהו מודל כללי שאינו מותאם לעברית. הדיוק יהיה נמוך יותר.'
                : selectedModel?.notes_he
            }
          >
            <select
              id="setting-model"
              className="field-input"
              value={settings.model_id}
              onChange={(event) => update({ model_id: event.target.value })}
            >
              {models.length === 0 && <option value={settings.model_id}>{settings.model_id}</option>}
              {models.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.label_he}
                  {model.downloaded ? '' : ' — טרם הורד'}
                </option>
              ))}
            </select>
          </Field>

          <Field
            label="מעבד"
            htmlFor="setting-device"
            hint={hasGpu ? undefined : 'לא זוהה כרטיס מסך נתמך; התמלול יפעל על המעבד.'}
          >
            <select
              id="setting-device"
              className="field-input"
              value={settings.device}
              onChange={(event) =>
                update({ device: event.target.value as typeof settings.device })
              }
            >
              <option value="auto">אוטומטי</option>
              <option value="cuda">כרטיס מסך (GPU)</option>
              <option value="cpu">מעבד (CPU)</option>
            </select>
          </Field>

          <Field label="איכות" htmlFor="setting-quality">
            <select
              id="setting-quality"
              className="field-input"
              value={settings.quality}
              onChange={(event) =>
                update({ quality: event.target.value as typeof settings.quality })
              }
            >
              <option value="fast">מהיר</option>
              <option value="balanced">מאוזן</option>
              <option value="accurate">דיוק מרבי</option>
            </select>
          </Field>

          <Field label="חותמות זמן" htmlFor="setting-timestamps">
            <select
              id="setting-timestamps"
              className="field-input"
              value={settings.timestamps}
              onChange={(event) =>
                update({ timestamps: event.target.value as typeof settings.timestamps })
              }
            >
              <option value="none">ללא</option>
              <option value="segment">לפי מקטע</option>
              <option value="word">לפי מילה</option>
            </select>
          </Field>

          <Field
            label="גודל אצווה"
            htmlFor="setting-batch"
            hint="0 = בחירה אוטומטית לפי הזיכרון הפנוי."
          >
            <input
              id="setting-batch"
              type="number"
              min={0}
              max={64}
              className="field-input numeric"
              value={settings.batch_size}
              onChange={(event) => update({ batch_size: Number(event.target.value) || 0 })}
            />
          </Field>

          {/* -- subtitles -------------------------------------------- */}
          <Field label="אורך שורת כתובית מרבי" htmlFor="setting-line-length">
            <input
              id="setting-line-length"
              type="number"
              min={16}
              max={120}
              className="field-input numeric"
              value={settings.subtitle_max_line_length}
              onChange={(event) =>
                update({ subtitle_max_line_length: Number(event.target.value) || 42 })
              }
            />
          </Field>

          <Field label="מספר שורות כתובית מרבי" htmlFor="setting-max-lines">
            <input
              id="setting-max-lines"
              type="number"
              min={1}
              max={4}
              className="field-input numeric"
              value={settings.subtitle_max_lines}
              onChange={(event) => update({ subtitle_max_lines: Number(event.target.value) || 2 })}
            />
          </Field>

          {/* -- vocabulary ------------------------------------------- */}
          <div className="md:col-span-2">
            <Field
              label="מילון מונחים ושמות"
              htmlFor="setting-vocabulary"
              hint="שמות פרטיים, מונחים מקצועיים וראשי תיבות. המידע מועבר למודל כרמז ומשפר את איות המונחים."
            >
              <textarea
                id="setting-vocabulary"
                className="field-input min-h-[72px]"
                maxLength={2000}
                placeholder="לדוגמה: אורית לוי, ועדת הכספים, API, Kubernetes"
                value={settings.vocabulary_prompt}
                onChange={(event) => update({ vocabulary_prompt: event.target.value })}
              />
            </Field>
          </div>

          {/* -- text options ----------------------------------------- */}
          <div className="flex flex-col gap-3">
            <Toggle
              id="setting-fillers"
              checked={settings.preserve_filler_words}
              onChange={(value) => update({ preserve_filler_words: value })}
              label="שמירת מילות מילוי"
              hint='כמו "אה", "אמ", "כאילו".'
            />
            <Toggle
              id="setting-punctuation"
              checked={settings.normalize_punctuation}
              onChange={(value) => update({ normalize_punctuation: value })}
              label="תיקון פיסוק ורווחים"
            />
          </div>

          <div className="flex flex-col gap-3">
            <Toggle
              id="setting-paragraphs"
              checked={settings.split_paragraphs}
              onChange={(value) => update({ split_paragraphs: value })}
              label="חלוקה לפסקאות"
            />
            <Toggle
              id="setting-silence"
              checked={settings.detect_long_silences}
              onChange={(value) => update({ detect_long_silences: value })}
              label="זיהוי שתיקות ארוכות"
              hint="שתיקה ארוכה פותחת פסקה חדשה."
            />
            <Toggle
              id="setting-auto-download"
              checked={settings.auto_download_model}
              onChange={(value) => update({ auto_download_model: value })}
              label="הורדת מודל אוטומטית"
              hint="אם המודל חסר, הוא יורד אוטומטית לפני התמלול במקום להיכשל."
            />
            <Toggle
              id="setting-diarization"
              checked={settings.enable_diarization}
              onChange={(value) => update({ enable_diarization: value })}
              label="זיהוי דוברים (ניסיוני)"
              hint="דורש התקנת רכיב חיצוני. ללא הרכיב התמלול יופק ללא תוויות דוברים."
            />
          </div>

          {/* -- exports ---------------------------------------------- */}
          <div className="md:col-span-2">
            <p className="field-label">ייצוא אוטומטי בסיום</p>
            <div className="flex flex-wrap gap-3">
              {EXPORT_FORMATS.map((format) => (
                <label key={format} className="flex items-center gap-2 text-sm text-ink-700">
                  <input
                    type="checkbox"
                    className="h-4 w-4 rounded border-ink-300 text-brand-600"
                    checked={settings.export_formats.includes(format)}
                    onChange={(event) => toggleFormat(format, event.target.checked)}
                  />
                  {FORMAT_LABELS[format]}
                </label>
              ))}
            </div>
          </div>

          <div className="md:col-span-2">
            <FolderField
              id="setting-output"
              label="תיקיית פלט"
              value={settings.output_dir ?? ''}
              onChange={(value) => update({ output_dir: value || null })}
              placeholder="ברירת מחדל: תיקיית הייצוא של היישום"
              hint="השאירו ריק כדי לשמור בתיקיית הייצוא של היישום."
            />
          </div>
        </div>
      )}
    </section>
  );
}
