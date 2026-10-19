import { useEffect, useRef, type ReactNode } from 'react';
import { Icon } from './Icon.tsx';

/**
 * A native <dialog> so the browser supplies focus containment, Escape handling
 * and the backdrop. We only add what the element cannot do on its own: initial
 * focus, and returning focus to whatever opened the sheet.
 */
export function Dialog({
  open,
  onClose,
  title,
  children,
  footer,
  width = 'default',
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  footer?: ReactNode;
  width?: 'default' | 'wide' | 'narrow';
}) {
  const ref = useRef<HTMLDialogElement | null>(null);
  const restoreRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    if (open && !node.open) {
      restoreRef.current = document.activeElement as HTMLElement | null;
      node.showModal();
      const first = node.querySelector<HTMLElement>(
        'input:not([type="hidden"]), textarea, select, button, [href], [tabindex]:not([tabindex="-1"])',
      );
      first?.focus();
    } else if (!open && node.open) {
      node.close();
      restoreRef.current?.focus();
    }
  }, [open]);

  return (
    <dialog
      ref={ref}
      className={width === 'default' ? 'sheet' : `sheet sheet--${width}`}
      aria-label={title}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClose={onClose}
    >
      <div className="sheet__head">
        <span className="sheet__title">{title}</span>
        <span className="topbar__spacer" />
        <button type="button" className="btn btn--icon" aria-label="Close" onClick={onClose}>
          <Icon name="close" />
        </button>
      </div>
      <div className="sheet__body">{children}</div>
      {footer ? <div className="sheet__foot">{footer}</div> : null}
    </dialog>
  );
}

export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel = 'Confirm',
  danger,
  pending,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  body: ReactNode;
  confirmLabel?: string;
  danger?: boolean;
  pending?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <Dialog
      open={open}
      onClose={onCancel}
      title={title}
      width="narrow"
      footer={
        <>
          <button type="button" className="btn" onClick={onCancel} disabled={pending}>
            Cancel
          </button>
          <button
            type="button"
            className={danger ? 'btn btn--danger' : 'btn btn--primary'}
            onClick={onConfirm}
            disabled={pending}
          >
            {pending ? 'Working…' : confirmLabel}
          </button>
        </>
      }
    >
      {body}
    </Dialog>
  );
}

export function Drawer({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    ref.current?.focus();
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="drawer" role="dialog" aria-modal="true" aria-label={title}>
      <button type="button" className="drawer__scrim" aria-label="Close panel" onClick={onClose} />
      <div className="drawer__panel" ref={ref} tabIndex={-1}>
        <div className="sheet__head">
          <span className="sheet__title">{title}</span>
          <span className="topbar__spacer" />
          <button type="button" className="btn btn--icon" aria-label="Close panel" onClick={onClose}>
            <Icon name="close" />
          </button>
        </div>
        <div className="sheet__body">{children}</div>
      </div>
    </div>
  );
}
