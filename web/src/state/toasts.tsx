import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';
import { Icon } from '../components/Icon.tsx';

export type ToastTone = 'success' | 'error' | 'info' | 'warning';

export interface Toast {
  id: string;
  tone: ToastTone;
  title: string;
  text?: string;
}

interface ToastValue {
  push: (toast: Omit<Toast, 'id'>) => void;
  dismiss: (id: string) => void;
}

const ToastContext = createContext<ToastValue | null>(null);

let counter = 0;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const dismiss = useCallback((id: string) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const push = useCallback(
    (toast: Omit<Toast, 'id'>) => {
      counter += 1;
      const id = `toast-${counter}`;
      setToasts((current) => [...current, { ...toast, id }]);
      // Errors stay until dismissed; everything else clears itself.
      if (toast.tone !== 'error') {
        window.setTimeout(() => dismiss(id), 6000);
      }
    },
    [dismiss],
  );

  const value = useMemo<ToastValue>(() => ({ push, dismiss }), [push, dismiss]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      {/* One polite region for confirmations, errors announced immediately. */}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((toast) => (
          <div key={toast.id} className={`toast toast--${toast.tone}`}>
            <span aria-hidden="true">
              <Icon name={toast.tone === 'error' ? 'alert' : toast.tone === 'success' ? 'check' : 'info'} />
            </span>
            <div className="toast__body">
              <div className="toast__title">{toast.title}</div>
              {toast.text ? <div className="toast__text">{toast.text}</div> : null}
            </div>
            <button
              type="button"
              className="btn btn--icon btn--sm"
              aria-label="Dismiss notification"
              onClick={() => dismiss(toast.id)}
            >
              <Icon name="close" size={12} />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToasts(): ToastValue {
  const value = useContext(ToastContext);
  if (!value) throw new Error('useToasts must be used inside <ToastProvider>');
  return value;
}
