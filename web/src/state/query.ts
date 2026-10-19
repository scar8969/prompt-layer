import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * A hand-written fetch-on-demand hook with a shared in-memory cache. It covers
 * exactly what the studio needs: cache a read, revalidate on demand, and let a
 * mutation invalidate a prefix of keys. No third-party state library.
 */

interface CacheEntry {
  value: unknown;
  updatedAt: number;
}

const cache = new Map<string, CacheEntry>();
const listeners = new Map<string, Set<() => void>>();

export function invalidate(prefix: string): void {
  for (const key of [...cache.keys()]) {
    if (key === prefix || key.startsWith(prefix)) {
      cache.delete(key);
      const set = listeners.get(key);
      if (set) for (const notify of set) notify();
    }
  }
}

export function readCache<T>(key: string): T | undefined {
  return cache.get(key)?.value as T | undefined;
}

export function writeCache<T>(key: string, value: T): void {
  cache.set(key, { value, updatedAt: Date.now() });
  const set = listeners.get(key);
  if (set) for (const notify of set) notify();
}

export function clearCache(): void {
  cache.clear();
  for (const set of listeners.values()) for (const notify of set) notify();
}

function subscribe(key: string, notify: () => void): () => void {
  let set = listeners.get(key);
  if (!set) {
    set = new Set();
    listeners.set(key, set);
  }
  set.add(notify);
  return () => {
    set.delete(notify);
    if (set.size === 0) listeners.delete(key);
  };
}

export interface QueryState<T> {
  data: T | undefined;
  error: unknown;
  /** True only while the very first load for this key is in flight. */
  loading: boolean;
  /** True while a background revalidation is in flight over existing data. */
  refreshing: boolean;
  reload: () => void;
}

export interface QueryOptions {
  /** Skip the request entirely (e.g. no model selected yet). */
  enabled?: boolean;
  /** Treat cached data as fresh for this long before refetching on remount. */
  staleMs?: number;
}

export function useQuery<T>(
  key: string | null,
  fetcher: () => Promise<T>,
  options: QueryOptions = {},
): QueryState<T> {
  const { enabled = true, staleMs = 15_000 } = options;
  const active = key !== null && enabled;
  const cached = key ? readCache<T>(key) : undefined;

  const [data, setData] = useState<T | undefined>(cached);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState<boolean>(active && cached === undefined);
  const [refreshing, setRefreshing] = useState(false);
  const [nonce, setNonce] = useState(0);

  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const keyRef = useRef(key);
  keyRef.current = key;

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    if (!active || !key) {
      setLoading(false);
      setRefreshing(false);
      return;
    }

    const cachedNow = readCache<T>(key);
    const fresh = cachedNow !== undefined && Date.now() - (cache.get(key)?.updatedAt ?? 0) < staleMs;
    if (fresh && nonce === 0) {
      setData(cachedNow);
      setLoading(false);
      return;
    }

    let cancelled = false;
    if (cachedNow === undefined) setLoading(true);
    else setRefreshing(true);

    fetcherRef
      .current()
      .then((value) => {
        if (cancelled || keyRef.current !== key) return;
        writeCache(key, value);
        setData(value);
        setError(null);
      })
      .catch((err: unknown) => {
        if (cancelled || keyRef.current !== key) return;
        setError(err);
      })
      .finally(() => {
        if (cancelled || keyRef.current !== key) return;
        setLoading(false);
        setRefreshing(false);
      });

    return () => {
      cancelled = true;
    };
  }, [key, active, nonce, staleMs]);

  // Re-render when somebody else writes to this key (e.g. a mutation reload).
  useEffect(() => {
    if (!key) return;
    return subscribe(key, () => {
      const next = readCache<T>(key);
      if (next !== undefined) setData(next);
    });
  }, [key]);

  return { data, error, loading, refreshing, reload };
}

/** A mutation helper: run, then invalidate the keys the mutation touched. */
export function useMutation<Input, Output>(
  run: (input: Input) => Promise<Output>,
  invalidateKeys: string[] = [],
): {
  mutate: (input: Input) => Promise<Output>;
  pending: boolean;
  error: unknown;
  reset: () => void;
} {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const runRef = useRef(run);
  runRef.current = run;
  const keysRef = useRef(invalidateKeys);
  keysRef.current = invalidateKeys;

  const mutate = useCallback(async (input: Input) => {
    setPending(true);
    setError(null);
    try {
      const output = await runRef.current(input);
      for (const key of keysRef.current) invalidate(key);
      return output;
    } catch (err) {
      setError(err);
      throw err;
    } finally {
      setPending(false);
    }
  }, []);

  return { mutate, pending, error, reset: () => setError(null) };
}
