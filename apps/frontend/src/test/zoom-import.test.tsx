/** Zoom link validation, the entry button, and the import dialog's flow. */

import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { UrlFetchStatus } from '@shared/index';
import { ZoomImportDialog, looksLikeZoomLink } from '@/components/ZoomImportDialog';
import { UploadZone } from '@/components/UploadZone';
import { api } from '@/lib/api';

const SHARE_LINK =
  'https://us06web.zoom.us/rec/play/g-sIMAh9_mspB3kBmg_k3tVoYBMRvLkM6E4QZCaOYrY' +
  'WcoDRs3jmh-x-WMj9TT1OB2zkpj_bvtdAWvRj.JcdUMGI55kC3GbOA?accessLevel=meeting';

function status(overrides: Partial<UrlFetchStatus> = {}): UrlFetchStatus {
  return {
    state: 'downloading',
    title: 'Zoom 2026-05-28 14-03',
    percent: 0.25,
    downloaded_bytes: 128_000_000,
    total_bytes: 511_000_000,
    eta_seconds: 90,
    error: null,
    media: null,
    media_token: null,
    ...overrides,
  };
}

describe('looksLikeZoomLink', () => {
  it.each([
    SHARE_LINK,
    'https://zoom.us/rec/share/abc.def',
    'https://acme.zoom.us/rec/play/x',
    '  https://us06web.zoom.us/rec/share/x  ',
  ])('accepts a recording link: %s', (url) => {
    expect(looksLikeZoomLink(url)).toBe(true);
  });

  it.each([
    'https://us06web.zoom.us/j/12345', // a live meeting, not a recording
    'https://zoom.us.evil.com/rec/play/x', // lookalike host
    'https://youtube.com/watch?v=x',
    'not a url',
    '',
  ])('rejects everything else: %s', (url) => {
    expect(looksLikeZoomLink(url)).toBe(false);
  });
});

describe('UploadZone', () => {
  it('offers the Zoom import beside the file picker', async () => {
    render(<UploadZone />);
    const button = screen.getByRole('button', { name: /Zoom/ });
    await userEvent.click(button);
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
  });
});

describe('ZoomImportDialog', () => {
  it('refuses a non-Zoom link without calling the backend', async () => {
    const spy = vi.spyOn(api, 'importFromUrl');
    render(<ZoomImportDialog onClose={() => undefined} />);

    await userEvent.type(screen.getByLabelText(/קישור ההקלטה/), 'https://example.com/a.mp4');
    await userEvent.click(screen.getByRole('button', { name: /הורד ותמלל/ }));

    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(spy).not.toHaveBeenCalled();
  });

  it('starts the download and shows progress', async () => {
    vi.spyOn(api, 'importFromUrl').mockResolvedValue(status());
    render(<ZoomImportDialog onClose={() => undefined} />);

    await userEvent.type(screen.getByLabelText(/קישור ההקלטה/), SHARE_LINK);
    await userEvent.click(screen.getByRole('button', { name: /הורד ותמלל/ }));

    expect(await screen.findByText(/מוריד את ההקלטה/)).toBeInTheDocument();
    expect(screen.getByText('Zoom 2026-05-28 14-03')).toBeInTheDocument();
    // While running, the only action is to stop.
    expect(screen.getByRole('button', { name: 'עצור' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /הורד ותמלל/ })).not.toBeInTheDocument();
  });

  it('queues the job once the file has landed', async () => {
    vi.spyOn(api, 'importFromUrl').mockResolvedValue(
      status({ state: 'ready', percent: 1, media_token: 'tok-123' }),
    );
    const createJob = vi.spyOn(api, 'createJob').mockResolvedValue({} as never);
    const onClose = vi.fn();
    render(<ZoomImportDialog onClose={onClose} />);

    await userEvent.type(screen.getByLabelText(/קישור ההקלטה/), SHARE_LINK);
    await userEvent.click(screen.getByRole('button', { name: /הורד ותמלל/ }));

    await waitFor(() => expect(createJob).toHaveBeenCalledWith('tok-123', expect.anything(), true));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('queues one job per recording however often the effect re-runs', async () => {
    // Regression: `status` arrives as a fresh object from every poll and the
    // parent hands down a fresh `onClose` on every render, so the effect ran
    // repeatedly and each run started another transcription.
    vi.spyOn(api, 'importFromUrl').mockResolvedValue(
      status({ state: 'ready', percent: 1, media_token: 'tok-123' }),
    );
    const createJob = vi.spyOn(api, 'createJob').mockResolvedValue({} as never);

    const { rerender } = render(<ZoomImportDialog onClose={() => undefined} />);
    await userEvent.type(screen.getByLabelText(/קישור ההקלטה/), SHARE_LINK);
    await userEvent.click(screen.getByRole('button', { name: /הורד ותמלל/ }));
    await waitFor(() => expect(createJob).toHaveBeenCalled());

    // Every re-render passes a new closure, as the real parent did.
    for (let i = 0; i < 6; i += 1) {
      rerender(<ZoomImportDialog onClose={() => undefined} />);
    }
    await waitFor(() => expect(createJob).toHaveBeenCalledTimes(1));
    expect(createJob).toHaveBeenCalledTimes(1);
  });

  it('reports a failed download', async () => {
    vi.spyOn(api, 'importFromUrl').mockResolvedValue(
      status({ state: 'failed', error: 'ההורדה נכשלה.' }),
    );
    render(<ZoomImportDialog onClose={() => undefined} />);

    await userEvent.type(screen.getByLabelText(/קישור ההקלטה/), SHARE_LINK);
    await userEvent.click(screen.getByRole('button', { name: /הורד ותמלל/ }));

    expect(await screen.findByRole('alert')).toHaveTextContent('ההורדה נכשלה.');
  });

  it('keeps the link box left-to-right inside the RTL page', () => {
    render(<ZoomImportDialog onClose={() => undefined} />);
    expect(screen.getByLabelText(/קישור ההקלטה/)).toHaveAttribute('dir', 'ltr');
  });
});
