/**
 * Media player synchronized with the transcript.
 *
 * Exposes an imperative handle so the editor can seek when a timestamp is
 * clicked, and reports the current time back so the active segment can be
 * highlighted.
 */

import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { formatClock } from '@/lib/format';
import { Numeric } from '@/components/ui/Primitives';

export interface PlayerHandle {
  seek: (seconds: number) => void;
  play: () => void;
  pause: () => void;
  toggle: () => void;
}

const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 2];
const SKIP_SECONDS = 5;

export const MediaPlayer = forwardRef<
  PlayerHandle,
  {
    src: string;
    hasVideo: boolean;
    onTimeUpdate: (seconds: number) => void;
  }
>(function MediaPlayer({ src, hasVideo, onTimeUpdate }, ref) {
  const mediaRef = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [duration, setDuration] = useState(0);
  const [current, setCurrent] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useImperativeHandle(ref, () => ({
    seek: (seconds) => {
      const media = mediaRef.current;
      if (!media) return;
      media.currentTime = Math.max(0, seconds);
      void media.play().catch(() => undefined);
    },
    play: () => void mediaRef.current?.play().catch(() => undefined),
    pause: () => mediaRef.current?.pause(),
    toggle: () => {
      const media = mediaRef.current;
      if (!media) return;
      if (media.paused) void media.play().catch(() => undefined);
      else media.pause();
    },
  }));

  useEffect(() => {
    const media = mediaRef.current;
    if (media) media.playbackRate = speed;
  }, [speed]);

  const skip = (delta: number) => {
    const media = mediaRef.current;
    if (!media) return;
    media.currentTime = Math.max(0, Math.min(media.duration || 0, media.currentTime + delta));
  };

  return (
    <div className="card p-3">
      {/*
        `dir="ltr"` on the media element only: a media player's transport
        controls are universally left-to-right, and the browser's native
        controls assume it. The surrounding UI stays RTL.
      */}
      <video
        ref={mediaRef}
        src={src}
        dir="ltr"
        data-testid="media-element"
        className={hasVideo ? 'w-full rounded-lg bg-black' : 'hidden'}
        onLoadedMetadata={(event) => setDuration(event.currentTarget.duration || 0)}
        onTimeUpdate={(event) => {
          const time = event.currentTarget.currentTime;
          setCurrent(time);
          onTimeUpdate(time);
        }}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onError={() => setError('לא ניתן לנגן את קובץ המדיה.')}
      />

      {error && (
        <p role="alert" className="mb-2 text-sm text-red-700">
          {error}
        </p>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          type="button"
          className="btn-secondary"
          aria-label={`דילוג ${SKIP_SECONDS} שניות אחורה`}
          onClick={() => skip(-SKIP_SECONDS)}
        >
          ⏪ {SKIP_SECONDS}
        </button>

        <button
          type="button"
          className="btn-primary min-w-[92px]"
          aria-label={playing ? 'השהיה' : 'ניגון'}
          onClick={() => {
            const media = mediaRef.current;
            if (!media) return;
            if (media.paused) void media.play().catch(() => undefined);
            else media.pause();
          }}
        >
          {playing ? 'השהה' : 'נגן'}
        </button>

        <button
          type="button"
          className="btn-secondary"
          aria-label={`דילוג ${SKIP_SECONDS} שניות קדימה`}
          onClick={() => skip(SKIP_SECONDS)}
        >
          {SKIP_SECONDS} ⏩
        </button>

        <span className="mx-2 text-sm text-ink-600">
          <Numeric>
            {formatClock(current)} / {formatClock(duration)}
          </Numeric>
        </span>

        <label className="ms-auto flex items-center gap-2 text-sm text-ink-600">
          מהירות
          <select
            className="field-input w-24 py-1"
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
