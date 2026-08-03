import '@testing-library/jest-dom/vitest';
import { afterEach, vi } from 'vitest';
import { cleanup } from '@testing-library/react';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// jsdom does not implement these; components touch them on render.
if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => undefined,
    removeListener: () => undefined,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false,
  })) as typeof window.matchMedia;
}

// jsdom defines these but they throw "Not implemented", so replace them
// unconditionally rather than only when absent.
HTMLMediaElement.prototype.play = function play(this: HTMLMediaElement) {
  Object.defineProperty(this, 'paused', { value: false, configurable: true });
  this.dispatchEvent(new Event('play'));
  return Promise.resolve();
};
HTMLMediaElement.prototype.pause = function pause(this: HTMLMediaElement) {
  Object.defineProperty(this, 'paused', { value: true, configurable: true });
  this.dispatchEvent(new Event('pause'));
};
