import { useEffect, useMemo, useState } from 'react';
import { jobs as jobsApi } from '../../api/endpoints.ts';
import type { Job } from '../../api/types.ts';
import { FilterBar, Pagination, Section } from '../../components/Data.tsx';
import { Select, TextInput } from '../../components/Field.tsx';
import { EmptyState, ErrorState, LoadingState } from '../../components/State.tsx';
import { Button } from '../../components/Button.tsx';
import { JobCard } from './JobCard.tsx';
import { isTerminalState, usePolling } from '../../state/jobs.ts';
import { useRoutePath } from '../../app/router.tsx';

const STATES = [
  { value: '', label: 'Any state' },
  { value: 'queued', label: 'Queued' },
  { value: 'processing', label: 'Processing' },
  { value: 'succeeded', label: 'Succeeded' },
  { value: 'failed', label: 'Failed' },
  { value: 'cancelled', label: 'Cancelled' },
];

const MODALITIES = [
  { value: '', label: 'Images and video' },
  { value: 'image', label: 'Images' },
  { value: 'video', label: 'Video' },
];

/**
 * History & Jobs. The list refreshes itself only while at least one job is
 * still running, and only while the tab is visible; nothing here ticks a
 * progress bar the provider did not report.
 */
export function JobsScreen() {
  const path = useRoutePath();
  const [state, setState] = useState('');
  const [modality, setModality] = useState('');
  const [query, setQuery] = useState('');
  const [jobs, setJobs] = useState<Job[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [nonce, setNonce] = useState(0);

  const load = async (nextCursor: string | null) => {
    try {
      const page = await jobsApi.list({
        state: state || undefined,
        modality: modality || undefined,
        limit: 25,
        cursor: nextCursor ?? undefined,
      });
      setJobs((current) => (nextCursor ? [...current, ...page.jobs] : page.jobs));
      setCursor(page.next_cursor);
      setError(null);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  };

  useEffect(() => {
    setLoading(true);
    setCursor(null);
    void load(null);
    // Re-query whenever a filter changes or the reader asks for fresh data.
  }, [state, modality, nonce]);

  const anyRunning = useMemo(() => jobs.some((job) => !isTerminalState(job.state)), [jobs]);

  usePolling(async () => {
    try {
      const page = await jobsApi.list({
        state: state || undefined,
        modality: modality || undefined,
        limit: 25,
      });
      setJobs(page.jobs);
      setCursor(page.next_cursor);
      setError(null);
      return page.jobs.every((job) => isTerminalState(job.state));
    } catch {
      return true;
    }
  }, anyRunning && path === '/history');

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return jobs;
    return jobs.filter(
      (job) =>
        (job.prompt ?? '').toLowerCase().includes(needle) ||
        (job.model?.display_name ?? '').toLowerCase().includes(needle) ||
        job.id.toLowerCase().includes(needle),
    );
  }, [jobs, query]);

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__heading">History &amp; Jobs</h1>
          <p className="page__subtitle">
            Every request this workspace has made, its real state, what it cost and what came back. Jobs are read
            from the backend; nothing on this page is simulated.
          </p>
        </div>
        <div className="page__actions">
          <Button
            onClick={() => {
              setNonce((n) => n + 1);
            }}
          >
            Refresh
          </Button>
        </div>
      </div>

      <FilterBar label="Filter jobs">
        <div className="toolbar__group" style={{ flex: 1, minWidth: 220 }}>
          <TextInput
            label="Search prompts, models or job ids"
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search this page of results"
          />
        </div>
        <div className="toolbar__group">
          <Select
            label="State"
            value={state}
            onChange={(event) => setState(event.target.value)}
            options={STATES}
          />
        </div>
        <div className="toolbar__group">
          <Select
            label="Modality"
            value={modality}
            onChange={(event) => setModality(event.target.value)}
            options={MODALITIES}
          />
        </div>
        <div className="toolbar__spacer" />
        <span className="dim" style={{ fontSize: 'var(--fs-xs)' }}>
          {anyRunning ? 'Live: at least one job is still running.' : 'No job is currently running.'}
        </span>
      </FilterBar>

      {error ? <ErrorState error={error} onRetry={() => setNonce((n) => n + 1)} /> : null}

      {loading ? <LoadingState label="Reading your job history…" /> : null}

      {!loading && !error && visible.length === 0 ? (
        <EmptyState
          icon="clock"
          title={jobs.length ? 'No job matches that search' : 'No jobs yet'}
          body={
            jobs.length
              ? 'The filters above are narrowing the list. Clear the search to see everything on this page.'
              : 'Jobs appear here the moment a generation request has been accepted by the backend. Open Image Studio or Video Studio to make the first one.'
          }
          action={
            jobs.length ? (
              <Button
                onClick={() => {
                  setQuery('');
                  setState('');
                  setModality('');
                }}
              >
                Clear filters
              </Button>
            ) : null
          }
        />
      ) : null}

      {visible.length ? (
        <Section title={`${visible.length} job${visible.length === 1 ? '' : 's'} on this page`}>
          <div className="stack">
            {visible.map((job) => (
              <JobCard key={job.id} job={job} />
            ))}
          </div>
          <Pagination
            hasMore={cursor !== null}
            loaded={jobs.length}
            loading={loadingMore}
            onMore={() => {
              setLoadingMore(true);
              void load(cursor);
            }}
          />
        </Section>
      ) : null}
    </div>
  );
}
