import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { adoptCsrfFromCookie, setCsrfToken } from '../api/client.ts';
import { auth, system } from '../api/endpoints.ts';
import type { MeResponse, SystemStatus, User, Workspace } from '../api/types.ts';
import { clearCache } from './query.ts';

export type SessionPhase = 'checking' | 'anonymous' | 'authenticated';

interface SessionValue {
  phase: SessionPhase;
  user: User | null;
  workspace: Workspace | null;
  workspaces: MeResponse['workspaces'];
  permissions: string[];
  /** True when the backend returned 401 and the shell must defer to the auth screen. */
  can: (permission: string) => boolean;
  signIn: (payload: { user: User; workspace: Workspace; csrfToken: string }) => void;
  signOut: () => Promise<void>;
  refresh: () => Promise<void>;
}

const SessionContext = createContext<SessionValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<SessionPhase>('checking');
  const [me, setMe] = useState<MeResponse | null>(null);

  const load = useCallback(async () => {
    try {
      const value = await auth.me();
      // The CSRF token only arrives with login/register, so a page reload has to
      // recover it. If the backend publishes it as a readable cookie we adopt it;
      // if not, we hold no token and the backend's own 403 is what the reader sees.
      adoptCsrfFromCookie();
      setMe(value);
      setPhase('authenticated');
    } catch {
      // Any failure to read /api/auth/me means the client holds no session. If
      // the cause was something else (a 500, a network blip) the screens that
      // need data show that error verbatim; the shell does not invent a session.
      setMe(null);
      setPhase('anonymous');
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      await load();
      if (cancelled) return;
    })();
    return () => {
      cancelled = true;
    };
  }, [load]);

  const value = useMemo<SessionValue>(
    () => ({
      phase,
      user: me?.user ?? null,
      workspace: me?.workspace ?? null,
      workspaces: me?.workspaces ?? [],
      permissions: me?.permissions ?? [],
      can: (permission: string) => {
        if (!me) return false;
        // Without a permission list the backend has told us nothing, so we do
        // not forbid; every mutating call is still authorised server-side.
        if (!me.permissions.length) return true;
        return me.permissions.includes(permission);
      },
      signIn: ({ user, workspace, csrfToken }) => {
        setCsrfToken(csrfToken);
        setMe({ user, workspace, workspaces: [], permissions: [] });
        setPhase('authenticated');
        void load();
      },
      signOut: async () => {
        try {
          await auth.logout();
        } finally {
          setCsrfToken(null);
          clearCache();
          setMe(null);
          setPhase('anonymous');
        }
      },
      refresh: load,
    }),
    [phase, me, load],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error('useSession must be used inside <SessionProvider>');
  return value;
}

/* -------------------------------------------------------- system status */

interface StatusValue {
  status: SystemStatus | null;
  error: unknown;
  loading: boolean;
  reload: () => void;
}

const StatusContext = createContext<StatusValue | null>(null);

/**
 * The backend's own report of itself: adapters, whether the mock provider is
 * enabled, the billing mode and anything it says is missing. Screens read this
 * rather than assuming what the server can do.
 */
export function StatusProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    system
      .status()
      .then((value) => {
        if (cancelled) return;
        setStatus(value);
        setError(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [nonce]);

  const value = useMemo<StatusValue>(
    () => ({ status, error, loading, reload: () => setNonce((n) => n + 1) }),
    [status, error, loading],
  );

  return <StatusContext.Provider value={value}>{children}</StatusContext.Provider>;
}

export function useStatus(): StatusValue {
  const value = useContext(StatusContext);
  if (!value) throw new Error('useStatus must be used inside <StatusProvider>');
  return value;
}

/**
 * The billing sentence. It is written once, from the backend's own billing
 * object, so no screen can accidentally imply that this platform charges for a
 * request it does not charge for.
 */
export function billingSentence(billing: { mode: string; platform_credits: boolean; note: string } | null | undefined): string {
  if (!billing) return 'The backend has not reported a billing mode yet.';
  if (billing.platform_credits) {
    return `Billing mode: ${billing.mode}. Platform credits are enabled, so usage is metered here.`;
  }
  return `Billing mode: ${billing.mode}. This platform does not charge for generations — each request is billed by your own provider account.${billing.note ? ` ${billing.note}` : ''}`;
}
