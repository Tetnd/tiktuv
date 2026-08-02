/**
 * Server-Sent Events subscription.
 *
 * `EventSource` cannot set headers, so the token travels as a query parameter
 * (the backend validates it identically). Reconnection is automatic; we only
 * track connection state so the UI can show it and re-sync after a gap.
 */

import type { ProgressEvent } from '@shared/index';
import { getBaseUrl, getToken } from '@/lib/api';

export interface EventHandlers {
  onProgress: (event: ProgressEvent) => void;
  onJobFinished: () => void;
  onQueueChanged: () => void;
  onConnectionChange: (connected: boolean) => void;
}

export function subscribeToEvents(handlers: EventHandlers): () => void {
  const url = `${getBaseUrl()}/api/events?token=${encodeURIComponent(getToken())}`;

  let source: EventSource | null = null;
  let closed = false;

  const parse = (raw: string): ProgressEvent | null => {
    try {
      return JSON.parse(raw) as ProgressEvent;
    } catch {
      return null;
    }
  };

  const connect = () => {
    if (closed) return;
    source = new EventSource(url);

    source.onopen = () => handlers.onConnectionChange(true);

    source.onerror = () => {
      handlers.onConnectionChange(false);
      // EventSource retries on its own using the server's `retry:` hint.
    };

    source.addEventListener('job.progress', (event) => {
      const payload = parse((event as MessageEvent<string>).data);
      if (payload) handlers.onProgress(payload);
    });

    for (const name of ['job.completed', 'job.failed', 'job.cancelled'] as const) {
      source.addEventListener(name, (event) => {
        const payload = parse((event as MessageEvent<string>).data);
        if (payload) handlers.onProgress(payload);
        handlers.onJobFinished();
      });
    }

    for (const name of ['job.created', 'job.deleted', 'queue.changed'] as const) {
      source.addEventListener(name, () => handlers.onQueueChanged());
    }
  };

  connect();

  return () => {
    closed = true;
    source?.close();
    handlers.onConnectionChange(false);
  };
}
