import type { ApiErrorBody, ApiErrorCode } from './types.ts';
import { arr, isRecord, str } from '../lib/util.ts';

/**
 * The client's only error type. It carries the backend's error object verbatim —
 * code, message, hint, details and request_id are never rewritten, only read.
 */
export class ApiError extends Error {
  readonly code: ApiErrorCode | string;
  readonly hint: string | null;
  readonly details: Record<string, unknown> | null;
  readonly requestId: string | null;
  readonly status: number | null;
  readonly body: ApiErrorBody | null;

  constructor(init: {
    code: ApiErrorCode | string;
    message: string;
    hint?: string | null;
    details?: Record<string, unknown> | null;
    requestId?: string | null;
    status?: number | null;
  }) {
    super(init.message);
    this.name = 'ApiError';
    this.code = init.code;
    this.hint = init.hint ?? null;
    this.details = init.details ?? null;
    this.requestId = init.requestId ?? null;
    this.status = init.status ?? null;
    this.body = {
      code: this.code,
      message: init.message,
      hint: this.hint,
      details: this.details,
      request_id: this.requestId,
    };
  }

  get isUnauthenticated(): boolean {
    return this.code === 'unauthenticated' || this.status === 401;
  }

  get isForbidden(): boolean {
    return this.code === 'forbidden' || this.status === 403;
  }
}

/* --------------------------------------------------------- session state */

// The CSRF token lives in memory for the lifetime of the page. It is never
// written to localStorage, sessionStorage, a URL, or a log.
let csrfToken: string | null = null;

export function setCsrfToken(token: string | null): void {
  csrfToken = token && token.length > 0 ? token : null;
}

export function getCsrfToken(): string | null {
  return csrfToken;
}

/**
 * The contract returns the CSRF token from login/register only, so a reload
 * loses it. Double-submit backends usually also publish it in a readable
 * cookie; if one is present we adopt it rather than pretend to have a token.
 * Nothing here can read the session cookie itself, which stays httpOnly.
 */
const CSRF_COOKIE_NAMES = ['csrf_token', 'csrf', 'xsrf-token', 'xsrf', 'studio_csrf'];

export function adoptCsrfFromCookie(): string | null {
  if (csrfToken) return csrfToken;
  if (typeof document === 'undefined' || !document.cookie) return null;
  for (const pair of document.cookie.split(';')) {
    const index = pair.indexOf('=');
    if (index < 0) continue;
    const name = pair.slice(0, index).trim().toLowerCase();
    if (!CSRF_COOKIE_NAMES.includes(name)) continue;
    const value = decodeURIComponent(pair.slice(index + 1).trim());
    if (value) {
      csrfToken = value;
      return value;
    }
  }
  return null;
}

/* ------------------------------------------------------------- transport */

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE';
  body?: unknown;
  /** Query parameters; null/undefined/'' values are dropped. */
  query?: Record<string, string | number | boolean | null | undefined>;
  signal?: AbortSignal;
  /** Raw bytes response (asset content) instead of JSON. */
  raw?: boolean;
  /** Absolute or backend-relative URL: when true, `path` is used as given. */
  absolute?: boolean;
}

export interface ApiRequestEvent {
  method: string;
  url: string;
}

const listeners = new Set<(event: ApiRequestEvent) => void>();

export function subscribeToRequests(listener: (event: ApiRequestEvent) => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/**
 * Where the Studio API lives, as a prefix in front of every route path.
 *
 * Empty when the client is served by the same process as the API, which is how
 * `python -m studio.app` and the Docker image both work: the React build is
 * served from the same origin, so `/api/health` is the right address.
 *
 * Set at build time when the API is mounted somewhere else. The Vercel
 * deployment is that case: Studio's routes are served by one function under
 * `/api/studio`, because `/api/health`, `/api/models` and `/api/presets` already
 * belong to the Prompt Layer functions in the same repository. Building with
 *
 *     VITE_STUDIO_API_PREFIX=/api/studio
 *
 * rewrites every request path in one place rather than editing 74 endpoint
 * definitions. The trailing slash is stripped so a value written either way
 * produces one slash, not two.
 */
export const API_PREFIX: string = (() => {
  const configured =
    typeof import.meta !== 'undefined' && import.meta.env
      ? String(import.meta.env.VITE_STUDIO_API_PREFIX ?? '')
      : '';
  const trimmed = configured.trim().replace(/\/+$/, '');
  return trimmed;
})();

/** The path a request should actually be sent to. */
export function withApiPrefix(path: string): string {
  if (!API_PREFIX) return path;
  if (path.startsWith(API_PREFIX)) return path;
  return `${API_PREFIX}${path.startsWith('/') ? '' : '/'}${path}`;
}

function buildUrl(path: string, query?: RequestOptions['query']): string {
  const base = typeof location === 'undefined' ? 'http://localhost' : location.origin;
  const url = new URL(withApiPrefix(path), base);
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value === undefined || value === null || value === '') continue;
      url.searchParams.set(key, String(value));
    }
  }
  if (typeof location === 'undefined') return url.pathname + url.search;
  return url.pathname + url.search + url.hash;
}

async function parseError(response: Response): Promise<ApiError> {
  let payload: unknown = null;
  const text = await response.text().catch(() => '');
  if (text.length > 0) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = null;
    }
  }
  const errorObject = isRecord(payload) && isRecord(payload.error) ? payload.error : null;
  if (errorObject) {
    return new ApiError({
      code: str(errorObject.code) ?? 'internal',
      message: str(errorObject.message) ?? response.statusText,
      hint: str(errorObject.hint),
      details: isRecord(errorObject.details) ? errorObject.details : null,
      requestId: str(errorObject.request_id),
      status: response.status,
    });
  }
  return new ApiError({
    code: response.status === 401 ? 'unauthenticated' : 'invalid_response',
    message:
      text.trim().length > 0
        ? text.trim().slice(0, 400)
        : `The backend returned ${response.status} ${response.statusText} with no error body.`,
    status: response.status,
  });
}

/**
 * The one fetch wrapper. Every endpoint function goes through it.
 *
 *  - `credentials: 'include'` sends the httpOnly studio_session cookie.
 *  - Non-GET requests carry X-CSRF-Token taken from the in-memory session payload.
 *  - Failures are normalised into ApiError; nothing is swallowed.
 */
export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? 'GET';
  const url = options.absolute ? path : buildUrl(path, options.query);
  const headers: Record<string, string> = { Accept: 'application/json' };
  let body: BodyInit | undefined;

  if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(options.body);
  }

  if (method !== 'GET') {
    // If no token is held the header is omitted rather than faked; the backend
    // is the authority on whether the request is authorised, and its own error
    // (including request_id) is what the reader will see.
    const token = csrfToken ?? adoptCsrfFromCookie();
    if (token) headers['X-CSRF-Token'] = token;
  }

  for (const listener of listeners) {
    listener({ method, url });
  }

  let response: Response;
  try {
    response = await fetch(url, {
      method,
      headers,
      body,
      credentials: 'include',
      signal: options.signal,
      cache: 'no-store',
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause;
    throw new ApiError({
      code: 'network_error',
      message:
        cause instanceof Error && cause.message
          ? `The request to ${url} failed: ${cause.message}`
          : `The request to ${url} failed before the backend answered.`,
      hint: 'Check that the studio backend is reachable from this browser, then try again.',
    });
  }

  if (!response.ok) {
    throw await parseError(response);
  }

  if (options.raw) {
    return (await response.blob()) as unknown as T;
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const text = await response.text();
  if (text.trim().length === 0) {
    return undefined as T;
  }

  try {
    return JSON.parse(text) as T;
  } catch {
    throw new ApiError({
      code: 'invalid_response',
      message: `The backend answered ${url} with a body that is not JSON.`,
      hint: 'This usually means a proxy returned an HTML error page instead of the API.',
      status: response.status,
    });
  }
}

/* ------------------------------------------------------------- envelopes */

/**
 * Reads a resource out of the documented envelope. If the key is missing the
 * client reports an invalid_response rather than inventing a value.
 */
export function envelope(payload: unknown, key: string): unknown {
  if (isRecord(payload) && key in payload) {
    return payload[key];
  }
  throw new ApiError({
    code: 'invalid_response',
    message: `The response has no "${key}" key.`,
    hint: 'The client and the studio backend disagree about the response envelope.',
    details: { received_keys: isRecord(payload) ? Object.keys(payload) : [] },
  });
}

export function envelopeList<K extends string, T>(
  payload: unknown,
  key: K,
  map: (item: unknown) => T,
): { items: T[]; nextCursor: string | null } {
  const value = envelope(payload, key);
  const source = arr(value);
  return {
    items: source.map(map),
    nextCursor: str(isRecord(payload) ? payload.next_cursor : null),
  };
}
