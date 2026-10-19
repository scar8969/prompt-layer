import { useEffect, useRef, useState } from 'react';
import { jobs as jobsApi } from '../api/endpoints.ts';
import type { Job } from '../api/types.ts';
import { writeCache } from './query.ts';

/**
 * Job state helpers. Nothing here invents progress: it watches the job the
 * backend reported and stops the moment that job reaches a terminal state.
 */

const TERMINAL = new Set(['succeeded', 'failed', 'cancelled', 'canceled']);

export function isTerminalState(state: string | null | undefined): boolean {
  return state ? TERMINAL.has(state) : true;
}

export function jobCacheKey(id: string): string {
  return `job:${id}`;
}

/**
 * A backoff loop that runs only while the tab is visible. When the tab is
 * hidden the timer stops; returning to the tab resumes it. The task returns
 * true once there is nothing left to wait for.
 */
export function usePolling(task: () => Promise<boolean>, active: boolean): void {
  const taskRef = useRef(task);
  taskRef.current = task;

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    let timer = 0;
    let delay = 1200;
    let paused = document.visibilityState !== 'visible';

    function schedule(ms: number): void {
      if (cancelled) return;
      timer = window.setTimeout(() => {
        void run();
      }, ms);
    }

    async function run(): Promise<void> {
      if (cancelled) return;
      if (document.visibilityState !== 'visible') {
        paused = true;
        return;
      }
      let done = false;
      try {
        done = await taskRef.current();
      } catch {
        done = true;
      }
      if (cancelled || done) return;
      delay = Math.min(10_000, Math.round(delay * 1.35));
      schedule(delay);
    }

    const onVisibility = () => {
      if (document.visibilityState === 'visible' && paused) {
        paused = false;
        delay = 1200;
        schedule(400);
      }
    };
    document.addEventListener('visibilitychange', onVisibility);
    schedule(600);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [active]);
}

/**
 * Watches one job. The caller seeds it with whatever the POST returned, so the
 * first paint already shows the provider's real response rather than a guess.
 */
export function useJobWatch(initial: Job | null): {
  job: Job | null;
  error: unknown;
} {
  const [job, setJob] = useState<Job | null>(initial);
  const [error, setError] = useState<unknown>(null);
  const seedId = initial?.id ?? null;

  useEffect(() => {
    setJob(initial);
    setError(null);
    // Deliberately keyed on the id: a new poll result must not reset the watch.
  }, [seedId]);

  const active = job !== null && !isTerminalState(job.state);

  usePolling(async () => {
    if (!job) return true;
    try {
      const next = await jobsApi.get(job.id);
      setJob(next);
      setError(null);
      writeCache(jobCacheKey(job.id), next);
      return isTerminalState(next.state);
    } catch (err) {
      setError(err);
      return true;
    }
  }, active);

  return { job, error };
}
