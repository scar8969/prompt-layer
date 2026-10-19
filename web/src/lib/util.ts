export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function str(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null;
}

export function num(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

export function bool(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null;
}

export function arr(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

export function strList(value: unknown): string[] {
  return arr(value).filter((item): item is string => typeof item === 'string');
}

export class Ids {
  static next(prefix: string): string {
    const random =
      typeof globalThis.crypto?.randomUUID === 'function'
        ? globalThis.crypto.randomUUID()
        : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
    return `${prefix}_${random}`.replace(/[^a-zA-Z0-9_-]/g, '');
  }
}

export function clamp(value: number, min: number, max: number): number {
  return value < min ? min : value > max ? max : value;
}

/** Byte-size formatting for asset metadata; never used for cost or progress. */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes)) return '—';
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value >= 10 ? 0 : 1)} ${units[unit]}`;
}

/** Duration formatting for real durations returned by the provider. */
export function formatSeconds(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return '—';
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 2 : 1)} s`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds - minutes * 60;
  return `${minutes}m ${rest.toFixed(0)}s`;
}

export function formatDurationMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return '—';
  return formatSeconds(ms / 1000);
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function formatRelative(value: string | null | undefined): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const diff = Date.now() - date.getTime();
  const table: [number, string][] = [
    [60_000, 'second'],
    [3_600_000, 'minute'],
    [86_400_000, 'hour'],
    [604_800_000, 'day'],
  ];
  if (diff < 60_000) return 'just now';
  const units = ['minute', 'hour', 'day', 'week'];
  let index = 0;
  for (let i = 0; i < table.length; i += 1) {
    if (diff < table[i][0]) {
      index = i;
      break;
    }
    index = units.length - 1;
  }
  const divisors = [60_000, 3_600_000, 86_400_000, 604_800_000];
  const amount = Math.floor(diff / divisors[index]);
  return `${amount} ${units[index]}${amount === 1 ? '' : 's'} ago`;
}

/**
 * Cost strings are built here so that the word "estimate" can never be dropped
 * by an individual screen. Formatting is the only thing this function does.
 */
export function formatCost(estimate: {
  amount?: number | null;
  currency?: string | null;
  is_estimate?: boolean;
  is_billable?: boolean;
  note?: string | null;
} | null | undefined): string {
  if (!estimate) return 'No cost information returned by the backend.';
  const currency = estimate.currency ?? 'USD';
  const amount = estimate.amount;
  if (amount === null || amount === undefined) {
    return estimate.note?.trim().length
      ? estimate.note
      : 'The backend did not return a numeric cost for this request.';
  }
  const formatted = `${amount.toFixed(4)} ${currency}`;
  return estimate.is_estimate ? `${formatted} — estimate` : formatted;
}

export function truncate(text: string | null | undefined, max = 120): string {
  if (!text) return '';
  const clean = text.replace(/\s+/g, ' ').trim();
  return clean.length <= max ? clean : `${clean.slice(0, max - 1)}…`;
}

export function titleCase(value: string): string {
  return value
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export function unique<T>(items: T[]): T[] {
  return Array.from(new Set(items));
}

export function omitEmpty<T extends Record<string, unknown>>(input: T): Partial<T> {
  const output: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(input)) {
    if (value === undefined || value === null || value === '') continue;
    output[key] = value;
  }
  return output as Partial<T>;
}

/**
 * MIME/size validation for uploads happens on the backend; this client-side check
 * exists only to avoid a pointless round trip and never replaces the backend rule.
 */
export function looksLikeImage(contentType: string): boolean {
  return contentType.startsWith('image/');
}

export function looksLikeVideo(contentType: string): boolean {
  return contentType.startsWith('video/');
}
