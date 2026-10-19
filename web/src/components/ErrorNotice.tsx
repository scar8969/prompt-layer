import { ApiError } from '../api/client.ts';
import type { ApiErrorCode } from '../api/types.ts';

export interface ErrorInfo {
  code: ApiErrorCode | string | null;
  message: string;
  hint: string | null;
  requestId: string | null;
  details: Record<string, unknown> | null;
}

/**
 * Unwraps whatever was thrown into the four things the contract promises. The
 * backend's code, message, hint and request_id are shown verbatim; nothing is
 * paraphrased and nothing is invented.
 */
export function formatApiError(error: unknown): ErrorInfo {
  if (error instanceof ApiError) {
    return {
      code: error.code,
      message: error.message,
      hint: error.hint,
      requestId: error.requestId,
      details: error.details,
    };
  }
  if (error instanceof Error) {
    const withCode = error as Error & { code?: string };
    return {
      code: withCode.code ?? null,
      message: error.message,
      hint: null,
      requestId: null,
      details: null,
    };
  }
  if (typeof error === 'string' && error.trim()) {
    return { code: null, message: error, hint: null, requestId: null, details: null };
  }
  return {
    code: null,
    message: 'Something failed and no error object reached the client.',
    hint: null,
    requestId: null,
    details: null,
  };
}

export function ErrorNotice({
  error,
  onRetry,
  retryLabel = 'Try again',
}: {
  error: unknown;
  onRetry?: () => void;
  retryLabel?: string;
}) {
  const info = formatApiError(error);
  return (
    <div className="panel panel--danger" role="alert">
      <p className="panel__title">
        {info.code ? <span className="mono">{info.code}</span> : 'Request failed'}
      </p>
      <p style={{ margin: '0 0 var(--s2)' }}>{info.message}</p>
      {info.hint ? <p className="muted" style={{ margin: '0 0 var(--s2)' }}>{info.hint}</p> : null}
      {info.requestId ? (
        <p className="dim mono" style={{ margin: 0 }}>
          request_id: {info.requestId}
        </p>
      ) : null}
      {onRetry ? (
        <div className="row row--tight" style={{ marginTop: 'var(--s3)' }}>
          <button type="button" className="btn btn--sm" onClick={onRetry}>
            {retryLabel}
          </button>
        </div>
      ) : null}
    </div>
  );
}
