import { useId, useState, type ReactNode } from 'react';
import { Icon } from './Icon.tsx';
import { Badge } from './Badge.tsx';

export function Section({
  title,
  actions,
  children,
  description,
}: {
  title: string;
  actions?: ReactNode;
  children: ReactNode;
  description?: ReactNode;
}) {
  return (
    <section className="section">
      <div className="section__head">
        <div>
          <h2 className="section__title">{title}</h2>
          {description ? <p className="section__subtitle muted">{description}</p> : null}
        </div>
        {actions ? <div className="row row--tight">{actions}</div> : null}
      </div>
      <div className="section__body">{children}</div>
    </section>
  );
}

export function Card({
  children,
  flush,
  sunken,
}: {
  children: ReactNode;
  flush?: boolean;
  sunken?: boolean;
}) {
  return (
    <div className={['card', flush ? 'card--flush' : '', sunken ? 'card--sunken' : ''].filter(Boolean).join(' ')}>
      {children}
    </div>
  );
}

export function KeyValueList({ rows }: { rows: { key: string; value: ReactNode }[] }) {
  return (
    <div className="kv">
      {rows.map((row) => (
        <div className="kv__pair" key={row.key} style={{ display: 'contents' }}>
          <span className="kv__key">{row.key}</span>
          <span className="kv__value">{row.value}</span>
        </div>
      ))}
    </div>
  );
}

export function CodeBlock({ code, label }: { code: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="stack stack--tight">
      <div className="row row--between">
        {label ? <span className="label">{label}</span> : <span />}
        <CopyButton value={code} onCopied={() => setCopied(true)} />
      </div>
      <pre className="code">{code}</pre>
      <span className="visually-hidden" role="status">
        {copied ? 'Copied to clipboard' : ''}
      </span>
    </div>
  );
}

export function CopyButton({ value, onCopied }: { value: string; onCopied?: () => void }) {
  const [state, setState] = useState<'idle' | 'done' | 'failed'>('idle');
  return (
    <button
      type="button"
      className="btn btn--sm"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setState('done');
          onCopied?.();
        } catch {
          setState('failed');
        }
        window.setTimeout(() => setState('idle'), 1800);
      }}
    >
      <span className="btn__icon">
        <Icon name={state === 'done' ? 'check' : 'copy'} size={13} />
      </span>
      {state === 'done' ? 'Copied' : state === 'failed' ? 'Copy failed' : 'Copy'}
    </button>
  );
}

export function Pagination({
  hasMore,
  onMore,
  loaded,
  loading,
}: {
  hasMore: boolean;
  onMore: () => void;
  loaded: number;
  loading: boolean;
}) {
  return (
    <div className="pagination">
      <span className="muted">
        {loaded} loaded{hasMore ? '' : ' — end of list'}
      </span>
      {hasMore ? (
        <button type="button" className="btn btn--sm" onClick={onMore} disabled={loading}>
          {loading ? 'Loading…' : 'Load more'}
        </button>
      ) : null}
    </div>
  );
}

export function FilterBar({ children, label }: { children: ReactNode; label: string }) {
  return (
    <div className="toolbar" role="search" aria-label={label}>
      {children}
    </div>
  );
}

export function MockNote({ children }: { children: ReactNode }) {
  return (
    <div className="panel panel--mock">
      <p className="panel__title">
        <Badge tone="mock">Local development placeholder</Badge>
      </p>
      {children}
    </div>
  );
}

export function Disclosure({ summary, children, open: initialOpen }: { summary: string; children: ReactNode; open?: boolean }) {
  const id = useId();
  return (
    <details className="card" open={initialOpen}>
      <summary className="card__head" style={{ cursor: 'pointer' }}>
        <span className="card__title">{summary}</span>
        <span className="topbar__spacer" />
        <Icon name="chevronDown" size={14} />
      </summary>
      <div className="card__body" id={id}>
        {children}
      </div>
    </details>
  );
}
