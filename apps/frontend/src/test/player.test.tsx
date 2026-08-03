/** Media player: RTL-correct transport, no emoji, sticky, accessible. */

import { createRef } from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MediaPlayer, type PlayerHandle } from '@/components/MediaPlayer';
import { useStore } from '@/state/store';

function renderPlayer(overrides: Partial<Parameters<typeof MediaPlayer>[0]> = {}) {
  const onTimeUpdate = vi.fn();
  const ref = createRef<PlayerHandle>();
  const utils = render(
    <MediaPlayer
      ref={ref}
      src="http://127.0.0.1:8756/api/jobs/x/media"
      hasVideo={false}
      onTimeUpdate={onTimeUpdate}
      {...overrides}
    />,
  );
  return { ...utils, ref, onTimeUpdate };
}

/** The media element, which testing-library cannot reach by role. */
function media(): HTMLVideoElement {
  return screen.getByTestId('media-element') as HTMLVideoElement;
}

beforeEach(() => {
  document.documentElement.setAttribute('dir', 'rtl');
  useStore.setState({ toasts: [] });
});

describe('transport direction', () => {
  it('lays the transport out left-to-right inside the RTL document', () => {
    // Otherwise RTL mirrors the row and each arrow points away from the
    // direction it actually moves playback.
    renderPlayer();
    const back = screen.getByRole('button', { name: 'אחורה 5 שניות' });
    const group = back.parentElement;
    expect(group).toHaveAttribute('dir', 'ltr');
  });

  it('orders the controls rewind, play, forward', () => {
    renderPlayer();
    const group = screen.getByRole('button', { name: 'אחורה 5 שניות' }).parentElement;
    const labels = Array.from(group?.querySelectorAll('button') ?? []).map((b) =>
      b.getAttribute('aria-label'),
    );
    expect(labels).toEqual(['אחורה 5 שניות', 'ניגון', 'קדימה 5 שניות']);
  });

  it('lays the timeline out left-to-right', () => {
    renderPlayer();
    const slider = screen.getByRole('slider', { name: 'מיקום בהקלטה' });
    expect(slider.closest('[dir="ltr"]')).not.toBeNull();
  });
});

describe('no emoji', () => {
  // Pictographic ranges only. U+FE0F (variation selector) is excluded because
  // it is a combining mark, which eslint rightly rejects inside a class.
  const EMOJI = /[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{23E9}-\u{23FA}]/u;

  it('uses SVG icons rather than emoji glyphs', () => {
    const { container } = renderPlayer();
    expect(container.textContent ?? '').not.toMatch(EMOJI);
    expect(container.querySelectorAll('svg').length).toBeGreaterThanOrEqual(4);
  });

  it('marks the icons as decorative so screen readers read the button name', () => {
    const { container } = renderPlayer();
    for (const svg of Array.from(container.querySelectorAll('svg'))) {
      expect(svg).toHaveAttribute('aria-hidden', 'true');
    }
  });

  it('keeps the skip count readable inside the icon', () => {
    const { container } = renderPlayer();
    const numbers = Array.from(container.querySelectorAll('svg text')).map(
      (node) => node.textContent,
    );
    expect(numbers).toContain('5');
  });
});

describe('sticky behaviour', () => {
  it('is positioned sticky at the top of the viewport', () => {
    renderPlayer();
    const bar = screen.getByTestId('media-player');
    expect(bar.className).toContain('sticky');
    expect(bar.className).toContain('top-0');
  });

  it('sits above the transcript content', () => {
    renderPlayer();
    expect(screen.getByTestId('media-player').className).toMatch(/z-\d+/);
  });

  it('raises its shadow once it is pinned to the top', () => {
    renderPlayer();
    // jsdom reports a zero rect, i.e. top <= 0, so the bar reads as pinned.
    fireEvent.scroll(window);
    expect(screen.getByTestId('media-player').className).toContain('shadow');
  });
});

describe('controls', () => {
  it('toggles between play and pause labels', async () => {
    renderPlayer();
    expect(screen.getByRole('button', { name: 'ניגון' })).toBeInTheDocument();

    fireEvent.play(media());
    expect(screen.getByRole('button', { name: 'השהיה' })).toBeInTheDocument();

    fireEvent.pause(media());
    expect(screen.getByRole('button', { name: 'ניגון' })).toBeInTheDocument();
  });

  it('skips backward without going below zero', async () => {
    renderPlayer();
    const element = media();
    Object.defineProperty(element, 'duration', { value: 100, configurable: true });
    element.currentTime = 2;

    await userEvent.click(screen.getByRole('button', { name: 'אחורה 5 שניות' }));
    expect(element.currentTime).toBe(0);
  });

  it('skips forward without passing the end', async () => {
    renderPlayer();
    const element = media();
    Object.defineProperty(element, 'duration', { value: 10, configurable: true });
    element.currentTime = 8;

    await userEvent.click(screen.getByRole('button', { name: 'קדימה 5 שניות' }));
    expect(element.currentTime).toBe(10);
  });

  it('changes playback speed', async () => {
    renderPlayer();
    const select = screen.getByRole('combobox', { name: 'מהירות ניגון' });
    await userEvent.selectOptions(select, '1.5');
    expect(media().playbackRate).toBe(1.5);
  });

  it('offers speeds either side of normal', () => {
    renderPlayer();
    const options = Array.from(
      screen.getByRole('combobox', { name: 'מהירות ניגון' }).querySelectorAll('option'),
    ).map((option) => Number(option.value));
    expect(Math.min(...options)).toBeLessThan(1);
    expect(Math.max(...options)).toBeGreaterThan(1);
  });

  it('reports an unplayable file', () => {
    renderPlayer();
    fireEvent.error(media());
    expect(screen.getByRole('alert')).toHaveTextContent('לא ניתן לנגן את קובץ המדיה');
  });
});

describe('timeline', () => {
  it('seeks when dragged', () => {
    renderPlayer();
    const element = media();
    Object.defineProperty(element, 'duration', { value: 200, configurable: true });
    fireEvent.loadedMetadata(element);

    const slider = screen.getByRole('slider', { name: 'מיקום בהקלטה' });
    fireEvent.change(slider, { target: { value: '75' } });
    expect(element.currentTime).toBe(75);
  });

  it('announces the position as a clock time', () => {
    renderPlayer();
    const element = media();
    Object.defineProperty(element, 'duration', { value: 200, configurable: true });
    fireEvent.loadedMetadata(element);

    const slider = screen.getByRole('slider', { name: 'מיקום בהקלטה' });
    fireEvent.change(slider, { target: { value: '65' } });
    expect(slider.getAttribute('aria-valuetext')).toContain('01:05');
  });

  it('shows elapsed and total time', () => {
    renderPlayer();
    const element = media();
    Object.defineProperty(element, 'duration', { value: 215, configurable: true });
    fireEvent.loadedMetadata(element);
    expect(screen.getByTestId('media-player').textContent).toContain('03:35');
  });
});

describe('imperative handle', () => {
  it('seeks from the transcript', () => {
    const { ref } = renderPlayer();
    act(() => ref.current?.seek(42));
    expect(media().currentTime).toBe(42);
  });

  it('never seeks to a negative time', () => {
    const { ref } = renderPlayer();
    act(() => ref.current?.seek(-10));
    expect(media().currentTime).toBe(0);
  });
});

describe('video', () => {
  it('is hidden for audio-only media', () => {
    renderPlayer({ hasVideo: false });
    expect(media().className).toContain('hidden');
  });

  it('is shown for video media', () => {
    renderPlayer({ hasVideo: true });
    expect(media().className).not.toContain('hidden');
  });
});
