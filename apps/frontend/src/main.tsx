import React from 'react';
import ReactDOM from 'react-dom/client';
import App from '@/App';
import { configureApi } from '@/lib/api';
import '@/index.css';

/**
 * Resolve the backend address and session token before rendering.
 *
 * Three sources, in priority order:
 *
 * 1. The Tauri shell's `connection` command — the packaged desktop app. The
 *    shell spawned the backend and read its handshake file, so it is the only
 *    party that knows the port and token. Nothing is baked into the bundle.
 * 2. `window.__TIKTUV__`, for embedding Tiktuv in another local host page.
 * 3. `VITE_TIKTUV_TOKEN` from `.env.local`, for `vite dev` against a backend
 *    started by hand. The dev server proxies `/api`, so no base URL is needed.
 */
declare global {
  interface Window {
    __TIKTUV__?: { baseUrl?: string; token?: string };
    __TAURI_INTERNALS__?: {
      invoke: <T>(command: string, args?: Record<string, unknown>) => Promise<T>;
    };
  }
}

interface Connection {
  base_url: string;
  token: string;
  version: string;
  data_dir: string;
}

async function resolveConnection(): Promise<{ baseUrl: string; token: string }> {
  const tauri = window.__TAURI_INTERNALS__;
  if (tauri?.invoke) {
    try {
      const connection = await tauri.invoke<Connection>('connection');
      return { baseUrl: connection.base_url, token: connection.token };
    } catch (error) {
      // Fall through to the other sources rather than rendering a dead app;
      // the connection indicator in the header will show the failure.
      console.error('could not reach the Tiktuv shell', error);
    }
  }

  // Served by the backend itself: the token arrives in a <meta> tag. It has to
  // be a meta tag and not an inline script — the page's Content-Security-Policy
  // is `script-src 'self'`, which blocks inline scripts silently.
  const meta = document.querySelector<HTMLMetaElement>('meta[name="tiktuv-token"]');
  if (meta?.content) {
    return { baseUrl: '', token: meta.content };
  }

  const injected = window.__TIKTUV__;
  return {
    baseUrl: injected?.baseUrl ?? '',
    token:
      injected?.token ?? (import.meta.env?.VITE_TIKTUV_TOKEN as string | undefined) ?? '',
  };
}

async function bootstrap(): Promise<void> {
  configureApi(await resolveConnection());

  const container = document.getElementById('root');
  if (!container) throw new Error('#root is missing from index.html');

  ReactDOM.createRoot(container).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
}

void bootstrap();
