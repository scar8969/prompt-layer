import type { ReactNode } from 'react';
import { Button } from './Button.tsx';
import { Icon } from './Icon.tsx';
import { formatApiError } from './ErrorNotice.tsx';

export function Spinner({ small }: { small?: boolean }) {
  return <span className={small ? 'spinner spinner--sm' : 'spinner'} role="presentation" />;
}

export function LoadingState({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="state" role="status">
      <Spinner />
      <p className="state__title">{label}</p>
    </div>
  );
}

export function SkeletonRows({ rows = 3 }: { rows?: number }) {
  return (
    <div className="stack" aria-hidden="true">
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className="skeleton skeleton--line" />
      ))}
    </div>
  );
}

export function EmptyState({
  title,
  body,
  action,
  icon = 'info',
}: {
  title: string;
  body: ReactNode;
  action?: ReactNode;
  icon?: string;
}) {
  return (
    <div className="state">
      <span className="state__icon" aria-hidden="true">
        <Icon name={icon} size={20} />
      </span>
      <p className="state__title">{title}</p>
      <div className="state__body">{body}</div>
      {action ? <div className="state__actions">{action}</div> : null}
    </div>
  );
}

export function ErrorState({
  error,
  onRetry,
  title = 'That request failed',
}: {
  error: unknown;
  onRetry?: () => void;
  title?: string;
}) {
  const info = formatApiError(error);
  return (
    <div className="state state--error" role="alert">
      <span className="state__icon" aria-hidden="true">
        <Icon name="alert" size={20} />
      </span>
      <p className="state__title">{title}</p>
      <div className="state__body">
        {info.code ? (
          <p className="mono" style={{ margin: 0 }}>
            {info.code}
          </p>
        ) : null}
        <p style={{ margin: 0 }}>{info.message}</p>
        {info.hint ? <p className="muted" style={{ margin: 0 }}>{info.hint}</p> : null}
        {info.requestId ? (
          <p className="dim mono" style={{ margin: 0 }}>
            request_id: {info.requestId}
          </p>
        ) : null}
      </div>
      {onRetry ? (
        <div className="state__actions">
          <Button onClick={onRetry}>Try again</Button>
        </div>
      ) : null}
    </div>
  );
}
