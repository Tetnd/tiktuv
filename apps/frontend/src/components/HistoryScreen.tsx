/** Local job history: reopen, retry, delete records, delete generated files. */

import { useState } from 'react';
import { STATUS_LABELS, formatBytes, formatClock, formatDateTime, truncateMiddle } from '@/lib/format';
import { Badge, EmptyState, Numeric } from '@/components/ui/Primitives';
import { api } from '@/lib/api';
import { useStore } from '@/state/store';

export function HistoryScreen() {
  const jobs = useStore((s) => s.jobs);
  const openTranscript = useStore((s) => s.openTranscript);
  const retryJob = useStore((s) => s.retryJob);
  const deleteJob = useStore((s) => s.deleteJob);
  const refreshJobs = useStore((s) => s.refreshJobs);
  const pushToast = useStore((s) => s.pushToast);
  const [busy, setBusy] = useState<string | null>(null);

  const deleteOutputs = async (jobId: string) => {
    setBusy(jobId);
    try {
      const { removed } = await api.deleteJobOutputs(jobId);
      pushToast({ tone: 'success', title: `נמחקו ${removed} קבצי פלט.` });
      await refreshJobs();
    } catch {
      pushToast({ tone: 'error', title: 'מחיקת קבצי הפלט נכשלה.' });
    } finally {
      setBusy(null);
    }
  };

  if (jobs.length === 0) {
    return (
      <div className="mx-auto w-full max-w-6xl p-6">
        <EmptyState title="ההיסטוריה ריקה" detail="עבודות שתשלימו יופיעו כאן." />
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-6xl p-6">
      <h1 className="mb-4 text-lg font-semibold text-ink-900">היסטוריית תמלולים</h1>

      <div className="card overflow-x-auto">
        <table className="w-full text-start text-sm">
          <caption className="sr-only">רשימת כל עבודות התמלול השמורות במחשב</caption>
          <thead className="bg-ink-50 text-xs uppercase text-ink-600">
            <tr>
              <th scope="col" className="p-3 text-start">שם הקובץ</th>
              <th scope="col" className="p-3 text-start">מצב</th>
              <th scope="col" className="p-3 text-start">משך</th>
              <th scope="col" className="p-3 text-start">גודל</th>
              <th scope="col" className="p-3 text-start">נוצר</th>
              <th scope="col" className="p-3 text-start">נפתח לאחרונה</th>
              <th scope="col" className="p-3 text-start">קבצים</th>
              <th scope="col" className="p-3 text-start">פעולות</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-200">
            {jobs.map((job) => (
              <tr key={job.id} className="align-top">
                <td className="p-3">
                  <p className="font-medium text-ink-900" title={job.filename}>
                    {truncateMiddle(job.filename, 40)}
                  </p>
                  <p className="mt-0.5 text-xs text-ink-500 ltr-run" title={job.source_path}>
                    {truncateMiddle(job.source_path, 46)}
                  </p>
                  {job.error && (
                    <p className="mt-1 text-xs text-red-700">{job.error.message}</p>
                  )}
                </td>
                <td className="p-3">
                  <Badge tone={job.status}>{STATUS_LABELS[job.status]}</Badge>
                </td>
                <td className="p-3"><Numeric>{formatClock(job.duration)}</Numeric></td>
                <td className="p-3">{formatBytes(job.size_bytes)}</td>
                <td className="p-3 text-xs text-ink-600">{formatDateTime(job.created_at)}</td>
                <td className="p-3 text-xs text-ink-600">{formatDateTime(job.last_opened_at)}</td>
                <td className="p-3 text-xs">
                  {Object.keys(job.output_paths).length === 0 ? (
                    <span className="text-ink-400">—</span>
                  ) : (
                    <ul className="space-y-0.5">
                      {Object.entries(job.output_paths).map(([format, path]) => (
                        <li key={format}>
                          <a
                            className="text-brand-700 underline"
                            href={api.downloadUrl(path)}
                            download
                          >
                            {format.toUpperCase()}
                          </a>
                        </li>
                      ))}
                    </ul>
                  )}
                </td>
                <td className="p-3">
                  <div className="flex flex-wrap gap-1.5">
                    {job.has_transcript && (
                      <button
                        type="button"
                        className="btn-ghost px-2 py-1 text-xs"
                        onClick={() => void openTranscript(job.id)}
                      >
                        פתיחה
                      </button>
                    )}
                    {(job.status === 'failed' || job.status === 'cancelled') && (
                      <button
                        type="button"
                        className="btn-ghost px-2 py-1 text-xs"
                        onClick={() => void retryJob(job.id)}
                      >
                        נסה שוב
                      </button>
                    )}
                    {Object.keys(job.output_paths).length > 0 && (
                      <button
                        type="button"
                        className="btn-ghost px-2 py-1 text-xs"
                        disabled={busy === job.id}
                        onClick={() => void deleteOutputs(job.id)}
                      >
                        מחק קבצים
                      </button>
                    )}
                    <button
                      type="button"
                      className="btn-ghost px-2 py-1 text-xs text-red-700"
                      onClick={() => void deleteJob(job.id, false)}
                    >
                      מחק רשומה
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="mt-3 text-xs text-ink-500">
        מחיקת רשומה מסירה את התמליל ואת עותק המדיה הפנימי, אך משאירה קבצי פלט שייצאתם.
        למחיקתם השתמשו ב"מחק קבצים".
      </p>
    </div>
  );
}
