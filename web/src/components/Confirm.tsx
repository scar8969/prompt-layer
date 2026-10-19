import { useState } from 'react';
import { ConfirmDialog } from './Dialog.tsx';
import { formatApiError } from './ErrorNotice.tsx';

/**
 * A confirm-then-run wrapper for destructive actions. It reports the failure
 * the backend produced instead of pretending the delete succeeded.
 */
export function DestructiveAction({
  label,
  title,
  body,
  confirmLabel,
  onConfirm,
}: {
  label: string;
  title: string;
  body: React.ReactNode;
  confirmLabel: string;
  onConfirm: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);

  return (
    <>
      <button
        type="button"
        className="btn btn--danger btn--sm"
        onClick={() => {
          setError(null);
          setOpen(true);
        }}
      >
        {label}
      </button>
      <ConfirmDialog
        open={open}
        title={title}
        body={
          <>
            {body}
            {error ? (
              <p className="field__error" role="alert">
                {formatApiError(error).code ? `${formatApiError(error).code}: ` : ''}
                {formatApiError(error).message}
              </p>
            ) : null}
          </>
        }
        confirmLabel={confirmLabel}
        danger
        pending={pending}
        onCancel={() => setOpen(false)}
        onConfirm={async () => {
          setPending(true);
          try {
            await onConfirm();
            setOpen(false);
          } catch (err) {
            setError(err);
          } finally {
            setPending(false);
          }
        }}
      />
    </>
  );
}
