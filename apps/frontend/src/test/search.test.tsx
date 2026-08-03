/** The find/replace bar: search works on its own, replace is opt-in. */

import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { TranscriptSearch, type SearchOptions } from '@/components/TranscriptSearch';

function Harness({
  matchCount = 0,
  activeIndex = 0,
  onStep = vi.fn(),
  onReplaceCurrent = vi.fn(),
  onReplaceAll = vi.fn(),
  initialQuery = '',
  onCommit = vi.fn(),
}: {
  matchCount?: number;
  activeIndex?: number;
  onStep?: (delta: number) => void;
  onReplaceCurrent?: () => void;
  onReplaceAll?: () => void;
  initialQuery?: string;
  onCommit?: (value: string) => void;
}) {
  const [query, setQuery] = useState(initialQuery);
  const [replacement, setReplacement] = useState('');
  const [options, setOptions] = useState<SearchOptions>({
    caseSensitive: false,
    wholeWord: false,
  });
  return (
    <TranscriptSearch
      query={query}
      onQueryChange={(value) => {
        onCommit(value);
        setQuery(value);
      }}
      replacement={replacement}
      onReplacementChange={setReplacement}
      options={options}
      onOptionsChange={setOptions}
      matchCount={matchCount}
      activeIndex={activeIndex}
      onStep={onStep}
      onReplaceCurrent={onReplaceCurrent}
      onReplaceAll={onReplaceAll}
    />
  );
}

const box = () => screen.getByRole('searchbox', { name: 'חיפוש בתמליל' });

describe('search on its own', () => {
  it('shows a search field without requiring replace', () => {
    render(<Harness />);
    expect(box()).toBeInTheDocument();
    // The replace field is not shown until asked for.
    expect(screen.queryByLabelText('טקסט להחלפה')).not.toBeInTheDocument();
  });

  it('prompts before anything is typed', () => {
    render(<Harness />);
    expect(screen.getByText('הקלידו וחפשו')).toBeInTheDocument();
  });

  it('does not search while typing', async () => {
    const onCommit = vi.fn();
    render(<Harness onCommit={onCommit} />);
    await userEvent.type(box(), 'תקציב');
    // Every keystroke updates the box, none of them starts a search.
    expect(box()).toHaveValue('תקציב');
    expect(onCommit).not.toHaveBeenCalled();
    expect(screen.getByText('Enter לחיפוש')).toBeInTheDocument();
  });

  it('searches when Enter is pressed', async () => {
    const onCommit = vi.fn();
    render(<Harness onCommit={onCommit} />);
    await userEvent.type(box(), 'תקציב');
    expect(onCommit).not.toHaveBeenCalled();

    fireEvent.keyDown(box(), { key: 'Enter' });
    expect(onCommit).toHaveBeenCalledWith('תקציב');
  });

  it('steps through results once the query is already submitted', async () => {
    const onCommit = vi.fn();
    const onStep = vi.fn();
    render(
      <Harness initialQuery="תקציב" matchCount={3} onCommit={onCommit} onStep={onStep} />,
    );
    // The box already holds the submitted query, so Enter advances instead of
    // re-running the same search.
    fireEvent.keyDown(box(), { key: 'Enter' });
    expect(onStep).toHaveBeenCalledWith(1);
    expect(onCommit).not.toHaveBeenCalled();
  });

  it('re-submits rather than stepping after the query is edited', async () => {
    const onCommit = vi.fn();
    const onStep = vi.fn();
    render(
      <Harness initialQuery="תקציב" matchCount={3} onCommit={onCommit} onStep={onStep} />,
    );
    await userEvent.type(box(), 'ים');
    fireEvent.keyDown(box(), { key: 'Enter' });
    expect(onCommit).toHaveBeenCalledWith('תקציבים');
    expect(onStep).not.toHaveBeenCalled();
  });

  it('reports the position within the results', () => {
    render(<Harness initialQuery="תקציב" matchCount={7} activeIndex={2} />);
    const count = screen.getByText(/מתוך/);
    expect(count.textContent?.replace(/‎/g, '')).toContain('3 מתוך 7');
  });

  it('says so when nothing matches', () => {
    render(<Harness initialQuery="אנגלית" matchCount={0} />);
    expect(screen.getByText('אין תוצאות')).toBeInTheDocument();
  });

  it('announces the count to assistive technology', () => {
    render(<Harness initialQuery="x" matchCount={3} />);
    expect(screen.getByText(/מתוך/).closest('[aria-live]')).toHaveAttribute(
      'aria-live',
      'polite',
    );
  });
});

describe('stepping through results', () => {
  it('moves to the next result with the button', async () => {
    const onStep = vi.fn();
    render(<Harness initialQuery="x" matchCount={3} onStep={onStep} />);
    await userEvent.click(screen.getByRole('button', { name: 'התוצאה הבאה' }));
    expect(onStep).toHaveBeenCalledWith(1);
  });

  it('moves to the previous result with the button', async () => {
    const onStep = vi.fn();
    render(<Harness initialQuery="x" matchCount={3} onStep={onStep} />);
    await userEvent.click(screen.getByRole('button', { name: 'התוצאה הקודמת' }));
    expect(onStep).toHaveBeenCalledWith(-1);
  });

  it('advances on Enter when the query is already submitted', () => {
    const onStep = vi.fn();
    render(<Harness initialQuery="x" matchCount={3} onStep={onStep} />);
    fireEvent.keyDown(box(), { key: 'Enter' });
    expect(onStep).toHaveBeenCalledWith(1);
  });

  it('goes back on Shift+Enter', () => {
    const onStep = vi.fn();
    render(<Harness initialQuery="x" matchCount={3} onStep={onStep} />);
    fireEvent.keyDown(box(), { key: 'Enter', shiftKey: true });
    expect(onStep).toHaveBeenCalledWith(-1);
  });

  it('disables stepping until the edited query is submitted', async () => {
    render(<Harness initialQuery="x" matchCount={3} />);
    expect(screen.getByRole('button', { name: 'התוצאה הבאה' })).toBeEnabled();
    await userEvent.type(box(), 'y');
    expect(screen.getByRole('button', { name: 'התוצאה הבאה' })).toBeDisabled();
  });

  it('disables stepping when there is nothing to step through', () => {
    render(<Harness initialQuery="x" matchCount={0} />);
    expect(screen.getByRole('button', { name: 'התוצאה הבאה' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'התוצאה הקודמת' })).toBeDisabled();
  });
});

describe('clearing', () => {
  it('clears with Escape', () => {
    render(<Harness initialQuery="תקציב" matchCount={2} />);
    fireEvent.keyDown(box(), { key: 'Escape' });
    expect(box()).toHaveValue('');
  });

  it('clearing also drops the submitted query', () => {
    const onCommit = vi.fn();
    render(<Harness initialQuery="תקציב" matchCount={2} onCommit={onCommit} />);
    fireEvent.keyDown(box(), { key: 'Escape' });
    expect(onCommit).toHaveBeenCalledWith('');
  });

  it('shows the clear button for unsubmitted text too', async () => {
    render(<Harness />);
    await userEvent.type(box(), 'abc');
    expect(screen.getByRole('button', { name: 'ניקוי החיפוש' })).toBeInTheDocument();
  });

  it('clears with the clear button', async () => {
    render(<Harness initialQuery="תקציב" matchCount={2} />);
    await userEvent.click(screen.getByRole('button', { name: 'ניקוי החיפוש' }));
    expect(box()).toHaveValue('');
  });

  it('hides the clear button when the field is empty', () => {
    render(<Harness />);
    expect(screen.queryByRole('button', { name: 'ניקוי החיפוש' })).not.toBeInTheDocument();
  });
});

describe('options', () => {
  it('offers case sensitivity and whole-word matching', () => {
    render(<Harness />);
    expect(screen.getByLabelText('תלוי רישיות')).toBeInTheDocument();
    expect(screen.getByLabelText('מילה שלמה')).toBeInTheDocument();
  });

  it('toggles case sensitivity', async () => {
    render(<Harness />);
    const toggle = screen.getByLabelText('תלוי רישיות');
    expect(toggle).not.toBeChecked();
    await userEvent.click(toggle);
    expect(toggle).toBeChecked();
  });
});

describe('replace is optional', () => {
  it('is hidden until revealed', async () => {
    render(<Harness initialQuery="x" matchCount={2} />);
    expect(screen.queryByRole('button', { name: 'החלף הכול' })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'החלפה' }));
    expect(screen.getByRole('button', { name: 'החלף הכול' })).toBeInTheDocument();
    expect(screen.getByLabelText('טקסט להחלפה')).toBeInTheDocument();
  });

  it('replaces just the current match', async () => {
    const onReplaceCurrent = vi.fn();
    render(<Harness initialQuery="x" matchCount={2} onReplaceCurrent={onReplaceCurrent} />);
    await userEvent.click(screen.getByRole('button', { name: 'החלפה' }));
    await userEvent.click(screen.getByRole('button', { name: 'החלף' }));
    expect(onReplaceCurrent).toHaveBeenCalled();
  });

  it('replaces every match', async () => {
    const onReplaceAll = vi.fn();
    render(<Harness initialQuery="x" matchCount={2} onReplaceAll={onReplaceAll} />);
    await userEvent.click(screen.getByRole('button', { name: 'החלפה' }));
    await userEvent.click(screen.getByRole('button', { name: 'החלף הכול' }));
    expect(onReplaceAll).toHaveBeenCalled();
  });

  it('cannot replace when nothing matches', async () => {
    render(<Harness initialQuery="x" matchCount={0} />);
    await userEvent.click(screen.getByRole('button', { name: 'החלפה' }));
    expect(screen.getByRole('button', { name: 'החלף הכול' })).toBeDisabled();
  });
});

describe('keyboard', () => {
  it('focuses the field on Ctrl+F', () => {
    render(<Harness />);
    expect(box()).not.toHaveFocus();
    fireEvent.keyDown(document, { key: 'f', ctrlKey: true });
    expect(box()).toHaveFocus();
  });
});
