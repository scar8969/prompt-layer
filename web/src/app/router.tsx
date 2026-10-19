import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

/**
 * A hash router small enough to read in one sitting. It exists because the task
 * forbids adding a routing dependency, and because the studio only needs
 * path + query + navigate.
 */

export interface Route {
  path: string;
  params: URLSearchParams;
  hash: string;
}

function parseHash(raw: string): Route {
  const value = raw.replace(/^#/, '');
  const [pathPart, queryPart] = value.split('?');
  const path = pathPart && pathPart.startsWith('/') ? pathPart : '/';
  return { path, params: new URLSearchParams(queryPart ?? ''), hash: value };
}

interface RouterValue {
  route: Route;
  navigate: (to: string, options?: { replace?: boolean }) => void;
  back: () => void;
}

const RouterContext = createContext<RouterValue | null>(null);

export function RouterProvider({ children }: { children: ReactNode }) {
  const [route, setRoute] = useState<Route>(() =>
    parseHash(typeof location === 'undefined' ? '' : location.hash),
  );

  useEffect(() => {
    const onChange = () => setRoute(parseHash(location.hash));
    window.addEventListener('hashchange', onChange);
    if (!location.hash) {
      location.replace('#/');
    }
    return () => window.removeEventListener('hashchange', onChange);
  }, []);

  const navigate = useCallback((to: string, options?: { replace?: boolean }) => {
    const target = to.startsWith('#') ? to : `#${to}`;
    if (location.hash === target) {
      setRoute(parseHash(target));
      return;
    }
    if (options?.replace) {
      location.replace(target);
    } else {
      location.hash = target;
    }
  }, []);

  const back = useCallback(() => {
    history.back();
  }, []);

  const value = useMemo<RouterValue>(() => ({ route, navigate, back }), [route, navigate, back]);

  return <RouterContext.Provider value={value}>{children}</RouterContext.Provider>;
}

export function useRouter(): RouterValue {
  const value = useContext(RouterContext);
  if (!value) throw new Error('useRouter must be used inside <RouterProvider>');
  return value;
}

export function useRoutePath(): string {
  return useRouter().route.path;
}

/** Splits "/projects/abc" against "/projects/:id". */
export function matchPath(
  pattern: string,
  path: string,
): Record<string, string> | null {
  const patternParts = pattern.split('/').filter(Boolean);
  const pathParts = path.split('/').filter(Boolean);
  if (patternParts.length !== pathParts.length) return null;
  const params: Record<string, string> = {};
  for (let i = 0; i < patternParts.length; i += 1) {
    const expected = patternParts[i];
    const actual = pathParts[i];
    if (expected.startsWith(':')) {
      params[expected.slice(1)] = decodeURIComponent(actual);
      continue;
    }
    if (expected !== actual) return null;
  }
  return params;
}

/** A link that behaves like an anchor but keeps the router state in sync. */
export function Link({
  to,
  children,
  className,
  title,
  onNavigate,
  ariaCurrent,
}: {
  to: string;
  children: ReactNode;
  className?: string;
  title?: string;
  onNavigate?: () => void;
  ariaCurrent?: 'page';
}) {
  const { navigate } = useRouter();
  return (
    <a
      href={`#${to}`}
      className={className}
      title={title}
      aria-current={ariaCurrent}
      onClick={(event) => {
        if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.shiftKey) return;
        event.preventDefault();
        navigate(to);
        onNavigate?.();
      }}
    >
      {children}
    </a>
  );
}
