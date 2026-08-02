import { describe, expect, it } from 'vitest';
import {
  LRM,
  formatBytes,
  formatClock,
  formatDuration,
  formatEta,
  formatPercent,
  isAcceptedFile,
  isolate,
  truncateMiddle,
} from '@/lib/format';

describe('formatClock', () => {
  it.each([
    [0, '00:00'],
    [59, '00:59'],
    [60, '01:00'],
    [3599, '59:59'],
    [3600, '1:00:00'],
    [3725, '1:02:05'],
  ])('formats %i seconds as %s', (seconds, expected) => {
    expect(formatClock(seconds)).toBe(expected);
  });

  it('clamps negative and non-finite input', () => {
    expect(formatClock(-10)).toBe('00:00');
    expect(formatClock(Number.NaN)).toBe('00:00');
  });
});

describe('bidi isolation', () => {
  it('wraps a value in left-to-right marks', () => {
    expect(isolate(42)).toBe(`${LRM}42${LRM}`);
  });

  it('never reverses the characters it wraps', () => {
    expect(isolate('v1.2').replaceAll(LRM, '')).toBe('v1.2');
  });

  it('keeps Hebrew in logical order inside formatted output', () => {
    // Two hours is "שעתיים" colloquially, but the app says "2 שעות" so the
    // number can be isolated. Assert the Hebrew word is not mangled.
    expect(formatDuration(7200)).toContain('שעות');
    expect(formatDuration(7200).replaceAll(LRM, '')).toContain('2 שעות');
  });
});

describe('formatDuration', () => {
  it('returns a Hebrew phrase for known durations', () => {
    expect(formatDuration(60)).toContain('דקה');
    expect(formatDuration(125).replaceAll(LRM, '')).toContain('2 דקות');
  });

  it('handles unknown and zero durations', () => {
    expect(formatDuration(0)).toBe('לא ידוע');
    expect(formatDuration(-1)).toBe('לא ידוע');
  });
});

describe('formatBytes', () => {
  it('scales through the Hebrew unit names', () => {
    expect(formatBytes(512)).toContain('בייט');
    expect(formatBytes(2048)).toContain('ק"ב');
    expect(formatBytes(5 * 1024 ** 2)).toContain('מ"ב');
    expect(formatBytes(3 * 1024 ** 3)).toContain('ג"ב');
  });

  it('handles zero and invalid values', () => {
    expect(formatBytes(0)).toContain('בייט');
    expect(formatBytes(Number.NaN)).toContain('בייט');
  });
});

describe('formatPercent', () => {
  it('rounds and clamps', () => {
    expect(formatPercent(0.5).replaceAll(LRM, '')).toBe('50%');
    expect(formatPercent(1.4).replaceAll(LRM, '')).toBe('100%');
    expect(formatPercent(-1).replaceAll(LRM, '')).toBe('0%');
  });
});

describe('formatEta', () => {
  it('is empty when unknown', () => {
    expect(formatEta(null)).toBe('');
    expect(formatEta(undefined)).toBe('');
  });

  it('describes remaining time in Hebrew', () => {
    expect(formatEta(2)).toBe('עוד רגע');
    expect(formatEta(120)).toContain('נותרו');
  });
});

describe('truncateMiddle', () => {
  it('leaves short strings alone', () => {
    expect(truncateMiddle('ישיבה.mp3', 40)).toBe('ישיבה.mp3');
  });

  it('shortens long Hebrew names without corrupting characters', () => {
    const name = `${'ישיבת הנהלה '.repeat(10)}.mp3`;
    const result = truncateMiddle(name, 20);
    expect(Array.from(result).length).toBeLessThanOrEqual(20);
    expect(result).toContain('…');
    // Every character must still be a valid Hebrew letter or punctuation.
    expect(result).not.toContain('�');
  });
});

describe('isAcceptedFile', () => {
  it.each(['a.mp3', 'a.MP4', 'שיחה.wav', 'x.m4a', 'y.mkv', 'z.webm'])('accepts %s', (name) => {
    expect(isAcceptedFile(name)).toBe(true);
  });

  it.each(['a.txt', 'a.exe', 'noextension', 'a.mp3.exe'])('rejects %s', (name) => {
    expect(isAcceptedFile(name)).toBe(false);
  });
});
