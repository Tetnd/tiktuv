import { useEffect } from 'react';
import type { Screen } from '@/state/store';
import { useStore } from '@/state/store';
import { subscribeToEvents } from '@/lib/events';
import { QueueScreen } from '@/components/QueueScreen';
import { TranscriptEditor } from '@/components/TranscriptEditor';
import { HistoryScreen } from '@/components/HistoryScreen';
import { SystemScreen } from '@/components/SystemScreen';

const NAV: Array<{ id: Screen; label: string }> = [
  { id: 'queue', label: 'תמלול' },
  { id: 'history', label: 'היסטוריה' },
  { id: 'settings', label: 'מערכת ומודלים' },
];

function Toasts() {
  const toasts = useStore((s) => s.toasts);
  const dismiss = useStore((s) => s.dismissToast);

  if (toasts.length === 0) return null;
  const tone: Record<string, string> = {
    info: 'border-ink-200 bg-white',
    success: 'border-emerald-200 bg-emerald-50',
    error: 'border-red-200 bg-red-50',
  };

  return (
    <div
      aria-live="polite"
      className="fixed bottom-4 start-4 z-50 flex w-80 flex-col gap-2"
    >
      {toasts.map((toast) => (
        <div
          key={toast.id}
          role={toast.tone === 'error' ? 'alert' : 'status'}
          className={`animate-fade-in rounded-lg border p-3 shadow-md ${tone[toast.tone]}`}
        >
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="text-sm font-medium text-ink-900">{toast.title}</p>
              {toast.detail && (
                <p className="mt-0.5 break-words text-xs text-ink-600">{toast.detail}</p>
              )}
            </div>
            <button
              type="button"
              className="text-ink-400 hover:text-ink-700"
              aria-label="סגירת ההודעה"
              onClick={() => dismiss(toast.id)}
            >
              ✕
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

function Header() {
  const screen = useStore((s) => s.screen);
  const setScreen = useStore((s) => s.setScreen);
  const connected = useStore((s) => s.connected);

  return (
    <header className="border-b border-ink-200 bg-white">
      <div className="mx-auto flex w-full max-w-6xl items-center gap-6 px-6 py-3">
        <div className="flex items-baseline gap-2">
          <span className="text-xl font-bold text-brand-700">תכתוב</span>
          <span className="text-xs text-ink-500">תמלול עברית מקומי</span>
        </div>

        <nav aria-label="ניווט ראשי" className="flex gap-1">
          {NAV.map((item) => (
            <button
              key={item.id}
              type="button"
              aria-current={screen === item.id ? 'page' : undefined}
              onClick={() => setScreen(item.id)}
              className={`rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
                screen === item.id
                  ? 'bg-brand-50 text-brand-700'
                  : 'text-ink-600 hover:bg-ink-100'
              }`}
            >
              {item.label}
            </button>
          ))}
        </nav>

        <div className="ms-auto flex items-center gap-2 text-xs text-ink-500">
          <span
            aria-hidden="true"
            className={`h-2 w-2 rounded-full ${connected ? 'bg-emerald-500' : 'bg-ink-300'}`}
          />
          <span>{connected ? 'מחובר לשירות המקומי' : 'מתחבר…'}</span>
        </div>
      </div>
    </header>
  );
}

export default function App() {
  const screen = useStore((s) => s.screen);
  const refreshJobs = useStore((s) => s.refreshJobs);
  const refreshModels = useStore((s) => s.refreshModels);
  const applyProgress = useStore((s) => s.applyProgress);
  const setConnected = useStore((s) => s.setConnected);

  useEffect(() => {
    // Jobs and models are needed right away. System info is not: it imports
    // PyTorch on the backend (~seconds on a cold start) and is only displayed
    // on the settings screen, so it is fetched lazily there instead.
    void refreshJobs();
    void refreshModels();
  }, [refreshJobs, refreshModels]);

  useEffect(() => {
    // `EventSource` does not exist in the jsdom test environment.
    if (typeof EventSource === 'undefined') return;
    return subscribeToEvents({
      onProgress: applyProgress,
      onJobFinished: () => void refreshJobs(),
      onQueueChanged: () => void refreshJobs(),
      onConnectionChange: setConnected,
    });
  }, [applyProgress, refreshJobs, setConnected]);

  return (
    <div className="flex min-h-full flex-col bg-ink-50">
      <Header />
      <main className="flex-1">
        {screen === 'queue' && <QueueScreen />}
        {screen === 'editor' && <TranscriptEditor />}
        {screen === 'history' && <HistoryScreen />}
        {screen === 'settings' && <SystemScreen />}
      </main>
      <Toasts />
    </div>
  );
}
