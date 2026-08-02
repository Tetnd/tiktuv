import { describe, expect, it } from 'vitest';
import type { Segment } from '@shared/index';
import { activeSegmentIndex, replaceAll } from '@/components/TranscriptEditor';

const seg = (id: number, start: number, end: number, text: string): Segment => ({
  id,
  start,
  end,
  text,
  words: [],
  speaker: null,
  edited: false,
  boundary_merged: false,
});

const SEGMENTS = [
  seg(0, 0, 3, 'שלום וברוכים הבאים'),
  seg(1, 3, 6, 'היום נדבר על התקציב'),
  seg(2, 8, 12, 'ולסיום, שאלות ותשובות'),
];

describe('activeSegmentIndex', () => {
  it('finds the segment covering the current time', () => {
    expect(activeSegmentIndex(SEGMENTS, 1)).toBe(0);
    expect(activeSegmentIndex(SEGMENTS, 4)).toBe(1);
    expect(activeSegmentIndex(SEGMENTS, 9)).toBe(2);
  });

  it('is inclusive of the start and exclusive of the end', () => {
    expect(activeSegmentIndex(SEGMENTS, 3)).toBe(1);
    expect(activeSegmentIndex(SEGMENTS, 0)).toBe(0);
  });

  it('holds the last segment during a gap rather than clearing', () => {
    // 7s falls in the silence between segment 1 and 2.
    expect(activeSegmentIndex(SEGMENTS, 7)).toBe(1);
  });

  it('returns -1 before the first segment starts', () => {
    expect(activeSegmentIndex([seg(0, 5, 8, 'מאוחר')], 1)).toBe(-1);
  });

  it('handles an empty transcript', () => {
    expect(activeSegmentIndex([], 5)).toBe(-1);
  });
});

describe('replaceAll', () => {
  it('replaces every occurrence and counts them', () => {
    const { segments, count } = replaceAll(SEGMENTS, 'התקציב', 'התוכנית');
    expect(count).toBe(1);
    expect(segments[1]?.text).toBe('היום נדבר על התוכנית');
  });

  it('marks changed segments as edited', () => {
    const { segments } = replaceAll(SEGMENTS, 'שלום', 'ברכות');
    expect(segments[0]?.edited).toBe(true);
    expect(segments[1]?.edited).toBe(false);
  });

  it('counts multiple hits inside one segment', () => {
    const source = [seg(0, 0, 1, 'כן כן כן')];
    const { count, segments } = replaceAll(source, 'כן', 'לא');
    expect(count).toBe(3);
    expect(segments[0]?.text).toBe('לא לא לא');
  });

  it('is a no-op for an empty search string', () => {
    const { segments, count } = replaceAll(SEGMENTS, '', 'x');
    expect(count).toBe(0);
    expect(segments).toBe(SEGMENTS);
  });

  it('reports zero when nothing matches', () => {
    expect(replaceAll(SEGMENTS, 'אנגלית', 'עברית').count).toBe(0);
  });

  it('never reverses Hebrew text', () => {
    const { segments } = replaceAll(SEGMENTS, 'התקציב', 'המדיניות');
    expect(segments[1]?.text.startsWith('היום')).toBe(true);
  });
});
