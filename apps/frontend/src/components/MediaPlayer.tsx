/**
 * Media player synchronized with the transcript.
 *
 * Two deliberate layout decisions:
 *
 * 1. **The transport controls and the timeline are laid out left-to-right**,
 *    inside an otherwise right-to-left document. Media transport is a spatial
 *    metaphor for time, not for reading order — every player, including
 *    YouTube and VLC in Hebrew and Arabic, keeps rewind on the left and
 *    forward on the right. Letting RTL mirror them puts a left-pointing arrow
 *    on the "forward" button, which is what this replaces.
 *
 * 2. **The bar sticks to the top of the viewport.** Editing a long transcript
 *    means scrolling far from the player, and playback has to stay
 *    interruptible without scrolling back up.
 */

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from 'react';
import { formatClock } from '@/lib/format';
import {
  PauseIcon,
  PlayIcon,
  SkipBackIcon,
  SkipForwardIcon,
  SpeedIcon,
} from '@/components/ui/Icons';

export interface PlayerHandle {
  seek: (seconds: number) => void;
  play: () => void;
  pause: () => void;
  toggle: () => void;
}

const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2];
const SKIP_SECONDS = 5;

/**
 * Start playback, tolerating environments where `play()` returns nothing.
 *
 * `HTMLMediaElement.play()` only began returning a Promise in 2016; older
 * engines return void, and so does jsdom. Calling `.catch` on the result
 * unconditionally throws a TypeError that would surface as a dead play button.
 */
function safePlay(media: HTMLMediaElement | null): void {
  const started = media?.play();
  if (started && typeof started.catch === 'function') {
    // A rejected play() is normal — autoplay policies, or a seek mid-load.
    started.catch(() => undefined);
  }
}

export const MediaPlayer = forwardRef<
  PlayerHandle,
  {
    src: string;
    hasVideo: boolean;
    onTimeUpdate: (seconds: number) => void;
  }
>(function MediaPlayer({ src, hasVideo, onTimeUpdate }, ref) {
  const mediaRef = useRef<HTMLVideoElement>(null);
  const barRef = useRef<HTMLDivElement>(null);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [duration, setDuration] = useState(0);
  const [current, setCurrent] = useState(0);
  const [buffered, setBuffered] = useState(0);
  const [scrubbing, setScrubbing] = useState(false);
  const [stuck, setStuck] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useImperativeHandle(ref, () => ({
    seek: (seconds) => {
      const media = mediaRef.current;
      if (!media) return;
      media.currentTime = Math.max(0, seconds);
      safePlay(media);
    },
    play: () => safePlay(mediaRef.current),
    pause: () => mediaRef.current?.pause(),
    toggle: () => togglePlayback(),
  }));

  const togglePlayback = useCallback(() => {
    const media = mediaRef.current;
    if (!media) return;
    if (media.paused) safePlay(media);
    else media.pause();
  }, []);

  useEffect(() => {
    const media = mediaRef.current;
    if (media) media.playbackRate = speed;
  }, [speed]);

  // Raise the elevation shadow only once the bar is actually floating over
  // content.
  //
  // Observing the bar itself with a -1px top margin is the standard way to
  // detect a stuck sticky element: while it flows normally it is fully inside
  // the shrunken root, and the moment it pins, that top pixel is clipped and
  // the ratio drops below 1.
  //
  // Deliberately not a scroll listener driving requestAnimationFrame: rAF is
  // paused whenever the page is not compositing (a background window), which
  // both stops updates and leaves the pending-frame flag set, so the shadow
  // never recovers. An observer has neither problem, and reads no layout.
  useEffect(() => {
    const bar = barRef.current;
    if (!bar || typeof IntersectionObserver === 'undefined') return;
    const observer = new IntersectionObserver(
      ([entry]) => setStuck((entry?.intersectionRatio ?? 1) < 1),
      { threshold: [1], rootMargin: '-1px 0px 0px 0px' },
    );
    observer.observe(bar);
    return () => observer.disconnect();
  }, []);

  const skip = (delta: number) => {
    const media = mediaRef.current;
    if (!media) return;
    media.currentTime = Math.max(0, Math.min(media.duration || 0, media.currentTime + delta));
  };

  const seekTo = (seconds: number) => {
    const media = mediaRef.current;
    if (!media) return;
    media.currentTime = seconds;
    setCurrent(seconds);
  };

  const progress = duration > 0 ? (current / duration) * 100 : 0;
  const bufferedPercent = duration > 0 ? Math.min(100, (buffered / duration) * 100) : 0;

  return (
    <div
      ref={barRef}
      data-testid="media-player"
      className={`sticky top-0 z-30 rounded-xl border border-ink-200 bg-white/95 backdrop-blur transition-shadow ${
        stuck ? 'shadow-lg' : 'shadow-sm'
      }`}
    >
        {/*
          `dir="ltr"` on the media element: the browser's own controls (which
          appear in full-screen) assume a left-to-right transport.
        */}
        <video
          ref={mediaRef}
          src={src}
          dir="ltr"
          data-testid="media-element"
          className={
            hasVideo
              ? 'max-h-[38vh] w-full rounded-t-xl bg-black object-contain'
              : 'hidden'
          }
          onLoadedMetadata={(event) => setDuration(event.currentTarget.duration || 0)}
          onTimeUpdate={(event) => {
            const time = event.currentTarget.currentTime;
            // While dragging, the slider owns the displayed position.
            if (!scrubbing) setCurrent(time);
            onTimeUpdate(time);
          }}
          onProgress={(event) => {
            const ranges = event.currentTarget.buffered;
            if (ranges.length > 0) setBuffered(ranges.end(ranges.length - 1));
          }}
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
          onEnded={() => setPlaying(false)}
          onError={() => setError('לא ניתן לנגן את קובץ המדיה.')}
        />

        {error && (
          <p role="alert" className="px-4 pt-3 text-sm text-red-700">
            {error}
          </p>
        )}

        {/* -- timeline ------------------------------------------------- */}
        <div className="px-4 pt-3" dir="ltr">
          <div className="relative flex h-4 items-center">
            <div className="absolute inset-x-0 h-1.5 rounded-full bg-ink-200" />
            <div
              className="absolute h-1.5 rounded-full bg-ink-300"
              style={{ width: `${bufferedPercent}%` }}
            />
            <div
              className="absolute h-1.5 rounded-full bg-brand-500"
              style={{ width: `${progress}%` }}
            />
            <input
              type="range"
              className="media-scrubber absolute inset-x-0 w-full"
              min={0}
              max={Math.max(duration, 0.1)}
              step={0.01}
              value={current}
              aria-label="מיקום בהקלטה"
              aria-valuetext={`${formatClock(current)} מתוך ${formatClock(duration)}`}
              onMouseDown={() => setScrubbing(true)}
              onTouchStart={() => setScrubbing(true)}
              onChange={(event) => seekTo(Number(event.target.value))}
              onMouseUp={() => setScrubbing(false)}
              onTouchEnd={() => setScrubbing(false)}
            />
          </div>
        </div>

        {/* -- controls ------------------------------------------------- */}
        <div className="flex flex-wrap items-center gap-3 px-4 pb-3 pt-1.5">
          {/*
            Left-to-right: rewind sits left of play, forward sits right of it,
            so each arrow points the way it moves.
          */}
          <div dir="ltr" className="flex items-center gap-1.5">
            <button
              type="button"
              className="flex h-10 w-10 items-center justify-center rounded-full text-ink-600 transition-colors hover:bg-ink-100 hover:text-ink-900"
              aria-label={`אחורה ${SKIP_SECONDS} שניות`}
              title={`אחורה ${SKIP_SECONDS} שניות`}
              onClick={() => skip(-SKIP_SECONDS)}
            >
              <SkipBackIcon seconds={SKIP_SECONDS} className="h-6 w-6" />
            </button>

            <button
              type="button"
              className="flex h-12 w-12 items-center justify-center rounded-full bg-brand-600 text-white shadow-sm transition-colors hover:bg-brand-700"
              aria-label={playing ? 'השהיה' : 'ניגון'}
              title={playing ? 'השהיה' : 'ניגון'}
              onClick={togglePlayback}
            >
              {playing ? (
                <PauseIcon className="h-6 w-6" />
              ) : (
                // Nudged right so the triangle looks centred in the circle.
                <PlayIcon className="ms-0.5 h-6 w-6" />
              )}
            </button>

            <button
              type="button"
              className="flex h-10 w-10 items-center justify-center rounded-full text-ink-600 transition-colors hover:bg-ink-100 hover:text-ink-900"
              aria-label={`קדימה ${SKIP_SECONDS} שניות`}
              title={`קדימה ${SKIP_SECONDS} שניות`}
              onClick={() => skip(SKIP_SECONDS)}
            >
              <SkipForwardIcon seconds={SKIP_SECONDS} className="h-6 w-6" />
            </button>
          </div>

          <p className="numeric text-sm text-ink-600" aria-hidden="true">
            <span className="font-medium text-ink-900">{formatClock(current)}</span>
            <span className="mx-1 text-ink-400">/</span>
            <span>{formatClock(duration)}</span>
          </p>

          <label className="ms-auto flex items-center gap-2 text-sm text-ink-600">
            <SpeedIcon className="h-4 w-4 text-ink-400" />
            <span className="sr-only sm:not-sr-only">מהירות</span>
            <select
              className="rounded-lg border border-ink-300 bg-white px-2 py-1 text-sm text-ink-900 outline-none focus:border-brand-500"
              aria-label="מהירות ניגון"
              value={speed}
              onChange={(event) => setSpeed(Number(event.target.value))}
            >
              {SPEEDS.map((value) => (
                <option key={value} value={value}>
                  {value}×
                </option>
              ))}
            </select>
          </label>
      </div>
    </div>
  );
});
