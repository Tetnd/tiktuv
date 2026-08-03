/**
 * Transcript search.
 *
 * Pure functions over the segment list, so the matching rules are testable
 * without a DOM and the editor only deals with presentation.
 */

import type { Segment } from '@shared/index';

export interface SearchMatch {
  /** Position of the segment in the list, not its id. */
  segmentIndex: number;
  segmentId: number;
  /** Character offsets within that segment's text. */
  start: number;
  end: number;
}

/**
 * Escape a user's query so it is matched literally.
 *
 * The query is typed by a person looking for words, not a regex author: a
 * transcript full of "(" or "?" would otherwise throw or match wildly.
 */
export function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * Every occurrence of ``query``, in reading order.
 *
 * Case-insensitivity is done with the regex ``i`` flag rather than by
 * lowercasing both sides: lowercasing can change a string's length (İ → i̇),
 * which would misalign the offsets used to select the match.
 */
export function findMatches(
  segments: Segment[],
  query: string,
  options: { caseSensitive?: boolean; wholeWord?: boolean } = {},
): SearchMatch[] {
  if (!query) return [];

  const source = options.wholeWord
    ? `(?<![\\p{L}\\p{N}])${escapeRegExp(query)}(?![\\p{L}\\p{N}])`
    : escapeRegExp(query);

  let pattern: RegExp;
  try {
    pattern = new RegExp(source, options.caseSensitive ? 'gu' : 'giu');
  } catch {
    // A lookbehind-unsupported engine, or an escape we did not anticipate.
    return [];
  }

  const matches: SearchMatch[] = [];
  segments.forEach((segment, segmentIndex) => {
    pattern.lastIndex = 0;
    let found: RegExpExecArray | null;
    while ((found = pattern.exec(segment.text)) !== null) {
      // A zero-length match would loop forever.
      if (found[0].length === 0) {
        pattern.lastIndex += 1;
        continue;
      }
      matches.push({
        segmentIndex,
        segmentId: segment.id,
        start: found.index,
        end: found.index + found[0].length,
      });
    }
  });
  return matches;
}

/** Replace exactly one match, leaving every other occurrence alone. */
export function replaceMatch(
  segments: Segment[],
  match: SearchMatch,
  replacement: string,
): Segment[] {
  return segments.map((segment, index) => {
    if (index !== match.segmentIndex) return segment;
    const text =
      segment.text.slice(0, match.start) + replacement + segment.text.slice(match.end);
    return { ...segment, text, edited: true };
  });
}

/** Replace every match. Returns the new segments and how many changed. */
export function replaceAllMatches(
  segments: Segment[],
  query: string,
  replacement: string,
  options: { caseSensitive?: boolean; wholeWord?: boolean } = {},
): { segments: Segment[]; count: number } {
  const matches = findMatches(segments, query, options);
  if (matches.length === 0) return { segments, count: 0 };

  const next = [...segments];
  // Apply back to front so earlier offsets stay valid as the text shifts.
  for (let index = matches.length - 1; index >= 0; index -= 1) {
    const match = matches[index];
    if (!match) continue;
    const segment = next[match.segmentIndex];
    if (!segment) continue;
    next[match.segmentIndex] = {
      ...segment,
      text: segment.text.slice(0, match.start) + replacement + segment.text.slice(match.end),
      edited: true,
    };
  }
  return { segments: next, count: matches.length };
}

/** Step through matches, wrapping at either end. */
export function stepMatch(current: number, total: number, delta: number): number {
  if (total <= 0) return 0;
  return (((current + delta) % total) + total) % total;
}
