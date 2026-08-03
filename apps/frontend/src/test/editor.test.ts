import { describe, expect, it } from 'vitest';
import type { Segment } from '@shared/index';
import { activeSegmentIndex } from '@/components/TranscriptEditor';
import {
  escapeRegExp,
  findMatches,
  replaceAllMatches,
  replaceMatch,
  stepMatch,
} from '@/lib/search';

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
  seg(2, 8, 12, 'ולסיום, שאלות ותשובות על התקציב'),
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
    expect(activeSegmentIndex(SEGMENTS, 7)).toBe(1);
  });

  it('returns -1 before the first segment starts', () => {
    expect(activeSegmentIndex([seg(0, 5, 8, 'מאוחר')], 1)).toBe(-1);
  });

  it('handles an empty transcript', () => {
    expect(activeSegmentIndex([], 5)).toBe(-1);
  });
});

// --------------------------------------------------------------------------
describe('findMatches', () => {
  it('finds every occurrence across segments', () => {
    const matches = findMatches(SEGMENTS, 'התקציב');
    expect(matches).toHaveLength(2);
    expect(matches.map((m) => m.segmentIndex)).toEqual([1, 2]);
  });

  it('reports offsets that select exactly the match', () => {
    const [match] = findMatches(SEGMENTS, 'נדבר');
    expect(match).toBeDefined();
    const text = SEGMENTS[match!.segmentIndex]!.text;
    expect(text.slice(match!.start, match!.end)).toBe('נדבר');
  });

  it('finds several occurrences inside one segment', () => {
    const source = [seg(0, 0, 1, 'כן כן כן')];
    expect(findMatches(source, 'כן')).toHaveLength(3);
  });

  it('returns nothing for an empty query', () => {
    expect(findMatches(SEGMENTS, '')).toEqual([]);
  });

  it('returns nothing when there is no match', () => {
    expect(findMatches(SEGMENTS, 'אנגלית')).toEqual([]);
  });

  it('ignores case by default', () => {
    const source = [seg(0, 0, 1, 'Whisper ו-whisper')];
    expect(findMatches(source, 'whisper')).toHaveLength(2);
  });

  it('respects case when asked', () => {
    const source = [seg(0, 0, 1, 'Whisper ו-whisper')];
    expect(findMatches(source, 'whisper', { caseSensitive: true })).toHaveLength(1);
  });

  it('matches whole words only when asked', () => {
    const source = [seg(0, 0, 1, 'תקציב התקציבים תקציב')];
    expect(findMatches(source, 'תקציב')).toHaveLength(3);
    expect(findMatches(source, 'תקציב', { wholeWord: true })).toHaveLength(2);
  });

  it('treats the query literally, not as a regular expression', () => {
    // A transcript full of punctuation must not throw or match wildly.
    const source = [seg(0, 0, 1, 'מה קורה? (בערך) 3+4')];
    expect(findMatches(source, '?')).toHaveLength(1);
    expect(findMatches(source, '(בערך)')).toHaveLength(1);
    expect(findMatches(source, '3+4')).toHaveLength(1);
    expect(findMatches(source, '.')).toHaveLength(0);
  });

  it('finds Hebrew with punctuation attached', () => {
    expect(findMatches(SEGMENTS, 'ולסיום,')).toHaveLength(1);
  });
});

describe('escapeRegExp', () => {
  it('escapes regex metacharacters', () => {
    expect(escapeRegExp('a.b*c')).toBe('a\\.b\\*c');
  });

  it('leaves Hebrew untouched', () => {
    expect(escapeRegExp('שלום')).toBe('שלום');
  });
});

describe('stepMatch', () => {
  it('advances and wraps at the end', () => {
    expect(stepMatch(0, 3, 1)).toBe(1);
    expect(stepMatch(2, 3, 1)).toBe(0);
  });

  it('goes back and wraps at the start', () => {
    expect(stepMatch(1, 3, -1)).toBe(0);
    expect(stepMatch(0, 3, -1)).toBe(2);
  });

  it('is safe with no matches', () => {
    expect(stepMatch(0, 0, 1)).toBe(0);
  });
});

describe('replaceMatch', () => {
  it('replaces only the chosen occurrence', () => {
    const source = [seg(0, 0, 1, 'כן כן כן')];
    const [, second] = findMatches(source, 'כן');
    const result = replaceMatch(source, second!, 'לא');
    expect(result[0]!.text).toBe('כן לא כן');
  });

  it('marks the segment as edited', () => {
    const [match] = findMatches(SEGMENTS, 'נדבר');
    const result = replaceMatch(SEGMENTS, match!, 'נשוחח');
    expect(result[1]!.edited).toBe(true);
    expect(result[0]!.edited).toBe(false);
  });

  it('does not mutate the input', () => {
    const [match] = findMatches(SEGMENTS, 'נדבר');
    replaceMatch(SEGMENTS, match!, 'נשוחח');
    expect(SEGMENTS[1]!.text).toContain('נדבר');
  });
});

describe('replaceAllMatches', () => {
  it('replaces every occurrence and counts them', () => {
    const { segments, count } = replaceAllMatches(SEGMENTS, 'התקציב', 'התוכנית');
    expect(count).toBe(2);
    expect(segments[1]!.text).toBe('היום נדבר על התוכנית');
    expect(segments[2]!.text).toContain('התוכנית');
  });

  it('handles several occurrences in one segment without corrupting offsets', () => {
    const source = [seg(0, 0, 1, 'כן כן כן')];
    const { segments, count } = replaceAllMatches(source, 'כן', 'בהחלט');
    expect(count).toBe(3);
    expect(segments[0]!.text).toBe('בהחלט בהחלט בהחלט');
  });

  it('handles a replacement longer than the match', () => {
    const source = [seg(0, 0, 1, 'א א א')];
    const { segments } = replaceAllMatches(source, 'א', 'אאאאא');
    expect(segments[0]!.text).toBe('אאאאא אאאאא אאאאא');
  });

  it('handles a replacement shorter than the match', () => {
    const source = [seg(0, 0, 1, 'אבגד אבגד')];
    const { segments } = replaceAllMatches(source, 'אבגד', 'א');
    expect(segments[0]!.text).toBe('א א');
  });

  it('can delete text', () => {
    const source = [seg(0, 0, 1, 'אה אני חושב אה')];
    const { segments } = replaceAllMatches(source, 'אה ', '');
    expect(segments[0]!.text).toBe('אני חושב אה');
  });

  it('reports zero when nothing matches', () => {
    const { count, segments } = replaceAllMatches(SEGMENTS, 'אנגלית', 'עברית');
    expect(count).toBe(0);
    expect(segments).toBe(SEGMENTS);
  });

  it('is a no-op for an empty query', () => {
    const { count } = replaceAllMatches(SEGMENTS, '', 'x');
    expect(count).toBe(0);
  });

  it('never reverses Hebrew text', () => {
    const { segments } = replaceAllMatches(SEGMENTS, 'התקציב', 'המדיניות');
    expect(segments[1]!.text.startsWith('היום')).toBe(true);
  });
});
