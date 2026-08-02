/** RTL layout, upload flow, queue rendering, progress and failure states. */

import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { JobSummary } from '@shared/index';
import { DEFAULT_SETTINGS } from '@shared/index';
import { QueueScreen } from '@/components/QueueScreen';
import { UploadZone } from '@/components/UploadZone';
import { ProgressBar, StatusPill } from '@/components/ui/Primitives';
import { useStore } from '@/state/store';

function makeJob(overrides: Partial<JobSummary> = {}): JobSummary {
  return {
    id: 'job-1',
    filename: 'ישיבת הנהלה.mp3',
    source_path: 'C:/media/ישיבת הנהלה.mp3',
    size_bytes: 5_242_880,
    duration: 1830,
    status: 'completed',
    progress: {
      stage: 'done',
      percent: 1,
      processed_seconds: 1830,
      total_seconds: 1830,
      eta_seconds: null,
      message: null,
    },
    model_id: 'ivrit-ai/whisper-large-v3',
    created_at: '2026-08-01T09:00:00Z',
    started_at: '2026-08-01T09:00:05Z',
    finished_at: '2026-08-01T09:12:00Z',
    last_opened_at: null,
    error: null,
    output_paths: {},
    has_transcript: true,
    ...overrides,
  };
}

const resetStore = () =>
  useStore.setState({
    screen: 'queue',
    connected: true,
    settings: { ...DEFAULT_SETTINGS },
    pending: [],
    jobs: [],
    models: [],
    system: null,
    activeJobId: null,
    transcript: null,
    toasts: [],
  });

beforeEach(resetStore);

// --------------------------------------------------------------------------
describe('RTL layout', () => {
  it('renders inside a right-to-left document', () => {
    document.documentElement.setAttribute('dir', 'rtl');
    document.documentElement.setAttribute('lang', 'he');
    render(<UploadZone />);
    expect(document.documentElement.dir).toBe('rtl');
    expect(document.documentElement.lang).toBe('he');
  });

  it('does not set an explicit direction on Hebrew text nodes', () => {
    // Per-element `dir` would defeat the document-level RTL context and is a
    // common way Hebrew UIs end up inconsistent.
    const { container } = render(<UploadZone />);
    const heading = screen.getByRole('heading', { level: 2 });
    expect(heading).not.toHaveAttribute('dir');
    expect(container.querySelectorAll('[dir="ltr"]').length).toBe(0);
  });

  it('isolates Latin runs with a CSS class instead of reordering them', () => {
    const { container } = render(<UploadZone />);
    const run = container.querySelector('.ltr-run');
    expect(run).not.toBeNull();
    // The text is stored in normal order; only its rendering is isolated.
    expect(run?.textContent).toContain('MP3');
  });

  it('renders Hebrew headings in logical order', () => {
    render(<UploadZone />);
    expect(
      screen.getByRole('heading', { name: 'גררו לכאן קבצי אודיו או וידאו' }),
    ).toBeInTheDocument();
  });
});

describe('UploadZone', () => {
  it('offers a file picker', () => {
    render(<UploadZone />);
    expect(screen.getByRole('button', { name: 'בחירת קבצים' })).toBeInTheDocument();
  });

  it('accepts a supported file and hands it to the store', async () => {
    const addFiles = vi.fn().mockResolvedValue(undefined);
    useStore.setState({ addFiles });

    render(<UploadZone />);
    const input = screen.getByTestId('file-input');
    const file = new File(['audio'], 'ישיבה.mp3', { type: 'audio/mpeg' });
    await userEvent.upload(input, file);

    await waitFor(() => expect(addFiles).toHaveBeenCalledTimes(1));
    expect(addFiles.mock.calls[0]?.[0]?.[0]?.name).toBe('ישיבה.mp3');
  });

  it('rejects an unsupported dropped file and explains why', async () => {
    // Drag-and-drop is the path that matters here: the file picker filters by
    // the `accept` attribute, but a drop does not, so an unsupported file can
    // only reach the handler this way.
    const addFiles = vi.fn();
    const pushToast = vi.fn();
    useStore.setState({ addFiles, pushToast });

    render(<UploadZone />);
    fireEvent.drop(screen.getByTestId('upload-zone'), {
      dataTransfer: { files: [new File(['x'], 'notes.txt', { type: 'text/plain' })] },
    });

    await waitFor(() => expect(pushToast).toHaveBeenCalled());
    expect(addFiles).not.toHaveBeenCalled();
    expect(pushToast.mock.calls[0]?.[0]?.tone).toBe('error');
  });

  it('accepts a supported dropped file', async () => {
    const addFiles = vi.fn().mockResolvedValue(undefined);
    useStore.setState({ addFiles });

    render(<UploadZone />);
    fireEvent.drop(screen.getByTestId('upload-zone'), {
      dataTransfer: { files: [new File(['a'], 'ישיבה.mp4', { type: 'video/mp4' })] },
    });

    await waitFor(() => expect(addFiles).toHaveBeenCalledTimes(1));
  });

  it('highlights the drop zone while a file is dragged over it', () => {
    render(<UploadZone />);
    const zone = screen.getByTestId('upload-zone');
    expect(zone.className).toContain('border-ink-300');

    fireEvent.dragOver(zone, { dataTransfer: { files: [] } });
    expect(zone.className).toContain('border-brand-500');

    fireEvent.dragLeave(zone);
    expect(zone.className).toContain('border-ink-300');
  });
});

describe('Queue', () => {
  it('shows an empty state with no jobs', () => {
    render(<QueueScreen />);
    expect(screen.getByText('אין עדיין עבודות')).toBeInTheDocument();
  });

  it('lists a job with its Hebrew filename and status', () => {
    useStore.setState({ jobs: [makeJob()] });
    render(<QueueScreen />);
    expect(screen.getByText(/ישיבת הנהלה/)).toBeInTheDocument();
    expect(screen.getByText('הושלם')).toBeInTheDocument();
  });

  it('shows a progress bar and stage for a running job', () => {
    useStore.setState({
      jobs: [
        makeJob({
          status: 'running',
          progress: {
            stage: 'transcribing',
            percent: 0.42,
            processed_seconds: 768,
            total_seconds: 1830,
            eta_seconds: 300,
            message: null,
          },
        }),
      ],
    });
    render(<QueueScreen />);

    const bar = screen.getByRole('progressbar');
    expect(bar).toHaveAttribute('aria-valuenow', '42');
    expect(screen.getAllByText('מתמלל').length).toBeGreaterThan(0);
  });

  it('renders a failure with its Hebrew message and remedy', () => {
    useStore.setState({
      jobs: [
        makeJob({
          status: 'failed',
          error: {
            code: 'FFMPEG_MISSING',
            message: 'FFmpeg לא נמצא במחשב.',
            remedy: 'התקינו FFmpeg והוסיפו אותו ל-PATH.',
            detail: '',
            context: {},
          },
        }),
      ],
    });
    render(<QueueScreen />);

    const alert = screen.getByRole('alert');
    expect(within(alert).getByText('FFmpeg לא נמצא במחשב.')).toBeInTheDocument();
    expect(within(alert).getByText(/התקינו FFmpeg/)).toBeInTheDocument();
  });

  it('offers retry for a failed job and cancel for a running one', () => {
    useStore.setState({ jobs: [makeJob({ id: 'a', status: 'failed' })] });
    const { rerender } = render(<QueueScreen />);
    expect(screen.getByRole('button', { name: 'נסה שוב' })).toBeInTheDocument();

    act(() => useStore.setState({ jobs: [makeJob({ id: 'a', status: 'running' })] }));
    rerender(<QueueScreen />);
    expect(screen.getByRole('button', { name: 'ביטול' })).toBeInTheDocument();
  });

  it('opens the transcript when asked', async () => {
    const openTranscript = vi.fn().mockResolvedValue(undefined);
    useStore.setState({ jobs: [makeJob()], openTranscript });

    render(<QueueScreen />);
    await userEvent.click(screen.getByRole('button', { name: 'פתיחת התמליל' }));
    expect(openTranscript).toHaveBeenCalledWith('job-1');
  });

  it('cancels a running job', async () => {
    const cancelJob = vi.fn().mockResolvedValue(undefined);
    useStore.setState({ jobs: [makeJob({ status: 'running' })], cancelJob });

    render(<QueueScreen />);
    await userEvent.click(screen.getByRole('button', { name: 'ביטול' }));
    expect(cancelJob).toHaveBeenCalledWith('job-1');
  });

  it('offers to clear finished jobs only when there are some', () => {
    useStore.setState({ jobs: [makeJob({ status: 'running' })] });
    const { rerender } = render(<QueueScreen />);
    expect(screen.queryByText('ניקוי עבודות שהסתיימו')).not.toBeInTheDocument();

    act(() => useStore.setState({ jobs: [makeJob({ status: 'completed' })] }));
    rerender(<QueueScreen />);
    expect(screen.getByText('ניקוי עבודות שהסתיימו')).toBeInTheDocument();
  });

  it('lists pending files and starts them', async () => {
    const startAll = vi.fn().mockResolvedValue(undefined);
    useStore.setState({
      startAll,
      pending: [
        {
          localId: 'p1',
          filename: 'הקלטה.mp3',
          sizeBytes: 1024,
          durationSeconds: 90,
          mediaToken: 'tok',
          status: 'ready',
          error: null,
        },
      ],
    });

    render(<QueueScreen />);
    expect(screen.getByText('הקלטה.mp3')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /התחל תמלול/ }));
    expect(startAll).toHaveBeenCalled();
  });

  it('shows an import error on a pending file', () => {
    useStore.setState({
      pending: [
        {
          localId: 'p1',
          filename: 'bad.mp3',
          sizeBytes: 0,
          durationSeconds: null,
          mediaToken: null,
          status: 'error',
          error: 'קובץ המדיה פגום או שאינו קובץ מדיה.',
        },
      ],
    });
    render(<QueueScreen />);
    expect(screen.getByText('קובץ המדיה פגום או שאינו קובץ מדיה.')).toBeInTheDocument();
  });
});

describe('Accessibility', () => {
  it('progress bars expose their value to assistive technology', () => {
    render(<ProgressBar value={0.25} label="התקדמות" />);
    const bar = screen.getByRole('progressbar', { name: 'התקדמות' });
    expect(bar).toHaveAttribute('aria-valuenow', '25');
    expect(bar).toHaveAttribute('aria-valuemin', '0');
    expect(bar).toHaveAttribute('aria-valuemax', '100');
  });

  it('clamps out-of-range progress values', () => {
    render(<ProgressBar value={5} />);
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100');
  });

  it('status pills carry readable text, not colour alone', () => {
    render(<StatusPill status="error">שגיאה</StatusPill>);
    expect(screen.getByText('שגיאה')).toBeInTheDocument();
  });

  it('the file input has an accessible name', () => {
    render(<UploadZone />);
    expect(screen.getByLabelText('בחירת קבצים לתמלול')).toBeInTheDocument();
  });

  it('every job action button has an accessible name', () => {
    useStore.setState({ jobs: [makeJob()] });
    render(<QueueScreen />);
    for (const button of screen.getAllByRole('button')) {
      expect(button.textContent?.trim() || button.getAttribute('aria-label')).toBeTruthy();
    }
  });

  it('the queue section is labelled', () => {
    render(<QueueScreen />);
    expect(screen.getByRole('region', { name: 'עבודות' })).toBeInTheDocument();
  });
});
