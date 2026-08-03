/**
 * Inline SVG icons.
 *
 * Deliberately not emoji: emoji render differently on every platform, change
 * size unpredictably next to text, cannot inherit `currentColor`, and are
 * announced by screen readers as their Unicode name ("fast-forward button")
 * rather than the action. These are plain shapes that inherit colour and size.
 *
 * Every icon is `aria-hidden`; the accessible name belongs on the button.
 */

interface IconProps {
  className?: string;
}

/** Circular arrow anticlockwise, with the number of seconds inside. */
export function SkipBackIcon({ seconds = 5, className }: IconProps & { seconds?: number }) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {/* Arc sweeping anticlockwise, open at the top-left. */}
      <path d="M4.5 9.5A8 8 0 1 0 12 4" />
      <polyline points="4 4 4 9.5 9.5 9.5" />
      <text
        x="12"
        y="15.6"
        textAnchor="middle"
        fontSize="7.5"
        fontWeight="600"
        fill="currentColor"
        stroke="none"
        // The digits must stay left-to-right inside an RTL document.
        direction="ltr"
      >
        {seconds}
      </text>
    </svg>
  );
}

/** Circular arrow clockwise, with the number of seconds inside. */
export function SkipForwardIcon({ seconds = 5, className }: IconProps & { seconds?: number }) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M19.5 9.5A8 8 0 1 1 12 4" />
      <polyline points="20 4 20 9.5 14.5 9.5" />
      <text
        x="12"
        y="15.6"
        textAnchor="middle"
        fontSize="7.5"
        fontWeight="600"
        fill="currentColor"
        stroke="none"
        direction="ltr"
      >
        {seconds}
      </text>
    </svg>
  );
}

export function PlayIcon({ className }: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      fill="currentColor"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M8 5.14v13.72a.6.6 0 0 0 .92.5l10.7-6.86a.6.6 0 0 0 0-1l-10.7-6.86a.6.6 0 0 0-.92.5Z" />
    </svg>
  );
}

export function PauseIcon({ className }: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      fill="currentColor"
      aria-hidden="true"
      focusable="false"
    >
      <rect x="7" y="5" width="3.6" height="14" rx="1.1" />
      <rect x="13.4" y="5" width="3.6" height="14" rx="1.1" />
    </svg>
  );
}

export function SpeedIcon({ className }: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M12 20a8 8 0 1 1 8-8" />
      <path d="m12 12 4.5-3.2" />
      <circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function SearchIcon({ className }: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.9}
      strokeLinecap="round"
      aria-hidden="true"
      focusable="false"
    >
      <circle cx="11" cy="11" r="6.5" />
      <path d="m16 16 4.5 4.5" />
    </svg>
  );
}

export function ChevronUpIcon({ className }: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <polyline points="6 14.5 12 8.5 18 14.5" />
    </svg>
  );
}

export function ChevronDownIcon({ className }: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <polyline points="6 9.5 12 15.5 18 9.5" />
    </svg>
  );
}

export function CloseIcon({ className }: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d="m6 6 12 12M18 6 6 18" />
    </svg>
  );
}
