import type { ReactNode } from 'react';

export type BadgeTone = 'plain' | 'ok' | 'warn' | 'danger' | 'accent' | 'mock';

export function Badge({ tone = 'plain', children }: { tone?: BadgeTone; children: ReactNode }) {
  return <span className={`badge badge--${tone}`}>{children}</span>;
}

/**
 * The single source of the mock disclosure. Every surface that names a model or
 * a job passes the backend flag through here, so a mock can never be shown
 * without the label.
 */
export function MockBadge({ isMock }: { isMock: boolean | undefined }) {
  if (!isMock) return null;
  return (
    <span className="badge badge--mock" title="The backend reports is_mock = true for this record.">
      Local development placeholder
    </span>
  );
}

export function CapabilityBadge({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span className={`badge badge--${ok ? 'ok' : 'plain'}`} title={ok ? 'Supported' : 'Not supported by this model'}>
      {ok ? '●' : '○'} {label}
    </span>
  );
}
