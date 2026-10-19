import { useEffect, useState } from 'react';
import { jobs as jobsApi, generations } from '../../api/endpoints.ts';
import type { Job, JobEvent } from '../../api/types.ts';
import { Badge, MockBadge } from '../../components/Badge.tsx';
import { Button } from '../../components/Button.tsx';
import { KeyValueList, Section, CodeBlock } from '../../components/Data.tsx';
import { ProgressBar } from '../../components/Controls.tsx';
import { ErrorNotice } from '../../components/ErrorNotice.tsx';
import { ErrorState, LoadingState } from '../../components/State.tsx';
import { MediaCanvas, outputToMedia } from '../../components/Media.tsx';
import { Menu } from '../../components/Controls.tsx';
import { Link, useRouter } from '../../app/router.tsx';
import { useToasts } from '../../state/toasts.tsx';
import { isTerminalState, useJobWatch } from '../../state/jobs.ts';
import { formatCost, formatDateTime, formatDurationMs, formatSeconds, titleCase } from '../../lib/util.ts';
import { invalidate } from '../../state/query.ts';

/**
 * One job in full: its state, its real timings, its cost estimate, the exact
 * error the backend returned if it failed, the event trail, and the outputs.
 */
export function JobDetailScreen({ jobId }: { jobId: string }) {
  const { navigate } = useRouter();
  const toasts = useToasts();
  const [fetched, setFetched] = useState<Job | null>(null);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [events, setEvents] = useState<JobEvent[] | null>(null);
  const [eventError, setEventError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [pending, setPending] = useState<'cancel' | 'retry' | 'variations' | null>(null);
  const [variationCount, setVariationCount] = useState(2);
  const [selectedOutput, setSelectedOutput] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setFetched(null);
    setLoadError(null);
    jobsApi
      .get(jobId)
      .then((job) => {
        if (!cancelled) setFetched(job);
      })
      .catch((err: unknown) => {
        if (!cancelled) setLoadError(err);
      });
    return () => {
      cancelled = true;
    };
  }, [jobId]);

  const { job, error: watchError } = useJobWatch(fetched);
  const terminal = job ? isTerminalState(job.state) : false;

  useEffect(() => {
    if (!job || !terminal) return;
    let cancelled = false;
    jobsApi
      .events(job.id)
      .then((list) => {
        if (!cancelled) setEvents(list);
      })
      .catch((err: unknown) => {
        if (!cancelled) setEventError(err);
      });
    return () => {
      cancelled = true;
    };
  }, [job?.id, terminal, job?.state]);

  const run = async (kind: 'cancel' | 'retry' | 'variations') => {
    if (!job) return;
    setPending(kind);
    setActionError(null);
    try {
      if (kind === 'cancel') {
        const next = await jobsApi.cancel(job.id);
        setFetched(next);
        toasts.push({ tone: 'info', title: 'Cancel requested', text: 'The backend accepted the cancellation request.' });
      } else if (kind === 'retry') {
        const next = await jobsApi.retry(job.id);
        invalidate('jobs');
        toasts.push({ tone: 'info', title: 'Retry queued', text: `New job ${next.id}` });
        navigate(`/history/${next.id}`);
      } else {
        const next = await generations.variations(job.id, { count: variationCount });
        invalidate('jobs');
        toasts.push({ tone: 'info', title: 'Variation requested', text: `New job ${next.id}` });
        navigate(`/history/${next.id}`);
      }
    } catch (err) {
      setActionError(err);
    } finally {
      setPending(null);
    }
  };

  if (loadError) return <ErrorState error={loadError} title="This job could not be read" />;
  if (!job) return <LoadingState label="Reading the job…" />;

  const outputs = job.outputs.map(outputToMedia);
  const current = outputs[Math.min(selectedOutput, outputs.length - 1)] ?? null;

  return (
    <div className="page page--wide">
      <div className="page__header">
        <div>
          <div className="row row--tight" style={{ flexWrap: 'wrap' }}>
            <Badge tone={job.state === 'succeeded' ? 'ok' : job.state === 'failed' ? 'danger' : 'accent'}>
              {job.state}
            </Badge>
            <MockBadge isMock={job.is_mock} />
            <span className="mono dim">{job.id}</span>
          </div>
          <h1 className="page__heading">{job.mode} · {job.kind}</h1>
          <p className="page__subtitle">
            {job.model
              ? `${job.model.display_name} on ${job.model.provider.adapter_label} · ${job.model.provider.connection_name}`
              : 'The backend did not report which model served this job.'}
          </p>
        </div>
        <div className="page__actions">
          <Link to="/history" className="btn btn--sm">
            Back to history
          </Link>
          <Menu
            label="Actions"
            items={[
              {
                label: 'Cancel this job',
                hint: 'Asks the provider to stop the request.',
                onSelect: () => void run('cancel'),
                disabled: terminal,
                disabledReason: 'This job already reached a terminal state.',
              },
              {
                label: 'Retry',
                hint: 'Queues a new job with the same inputs.',
                onSelect: () => void run('retry'),
                disabled: !terminal,
                disabledReason: 'Wait for the current attempt to finish, or cancel it first.',
              },
              {
                label: `Request ${variationCount} variation${variationCount === 1 ? '' : 's'}`,
                hint: 'Derives new outputs from this job.',
                onSelect: () => void run('variations'),
                disabled: !terminal || job.state !== 'succeeded',
                disabledReason: 'Variations need a succeeded job with outputs.',
              },
            ]}
          />
        </div>
      </div>

      {watchError ? <ErrorNotice error={watchError} /> : null}
      {actionError ? <ErrorNotice error={actionError} /> : null}

      {!terminal ? (
        <div className="panel panel--info">
          <p className="panel__title">Live state</p>
          <ProgressBar value={job.progress} isEstimate={job.progress_is_estimate} label={`Job ${job.state}`} />
          {job.message ? <p style={{ margin: 'var(--s2) 0 0' }}>{job.message}</p> : null}
          <p className="dim" style={{ margin: 'var(--s1) 0 0', fontSize: 'var(--fs-xs)' }}>
            The client asks the backend for the latest state and stops as soon as the job is terminal.
          </p>
        </div>
      ) : null}

      <div className="grid grid--2">
        <Section title="Outputs">
          {outputs.length ? (
            <>
              <MediaCanvas
                item={current}
                emptyLabel="No output selected"
                emptyHint="Choose one of the outputs below."
              />
              <div className="asset-grid">
                {outputs.map((item, index) => (
                  <button
                    key={item.id}
                    type="button"
                    className="asset-tile"
                    aria-selected={index === selectedOutput}
                    onClick={() => setSelectedOutput(index)}
                  >
                    <span className="asset-tile__body">
                      <span className="asset-tile__name">{item.label}</span>
                      <span className="asset-tile__meta">{item.meta}</span>
                      <MockBadge isMock={item.isMock} />
                    </span>
                  </button>
                ))}
              </div>
            </>
          ) : (
            <p className="muted" style={{ margin: 0 }}>
              The backend returned no outputs for this job. Nothing is drawn in their place.
            </p>
          )}
        </Section>

        <div className="stack">
          <Section title="Request">
            <KeyValueList
              rows={[
                { key: 'Mode', value: <span className="mono">{job.mode}</span> },
                { key: 'Kind', value: <span className="mono">{job.kind}</span> },
                { key: 'Count', value: job.count ?? '—' },
                { key: 'Seed', value: job.seed ?? 'not pinned' },
                { key: 'Prompt', value: job.prompt ?? '—' },
                { key: 'Negative prompt', value: job.negative_prompt ?? '—' },
                { key: 'Attempt', value: job.attempt !== null ? `${job.attempt} of ${job.max_attempts ?? '?'}` : '—' },
                { key: 'Project', value: job.project_id ?? 'not in a project' },
                { key: 'Parent job', value: job.parent_job_id ?? 'none' },
                { key: 'Relation', value: job.relation ?? '—' },
              ]}
            />
          </Section>

          <Section title="Timing and cost">
            <KeyValueList
              rows={[
                { key: 'Created', value: formatDateTime(job.created_at) },
                { key: 'Started', value: formatDateTime(job.started_at) },
                { key: 'Finished', value: formatDateTime(job.finished_at) },
                { key: 'Elapsed', value: formatDurationMs(job.elapsed_ms) },
                {
                  key: 'Cost',
                  value: job.cost_estimate ? (
                    <span>
                      {formatCost(job.cost_estimate)}
                      {job.cost_estimate.is_estimate ? ' (estimate)' : ''}
                    </span>
                  ) : (
                    'No cost information returned'
                  ),
                },
                {
                  key: 'Billing',
                  value: job.cost_estimate
                    ? job.cost_estimate.is_billable
                      ? 'The backend marks this request billable on the platform.'
                      : 'Not billed here — the request is billed by your own provider account.'
                    : 'Unknown',
                },
                { key: 'Provider job', value: job.provider_job_id ?? 'not reported' },
                { key: 'Provider status', value: job.provider_status ?? 'not reported' },
                {
                  key: 'Progress',
                  value:
                    job.progress === null
                      ? 'no measured progress'
                      : `${job.progress}% ${job.progress_is_estimate ? '(provider estimate — not drawn as a bar)' : '(measured)'}`,
                },
              ]}
            />
          </Section>
        </div>
      </div>

      {job.error ? (
        <Section title="Provider error">
          <div className="panel panel--danger" role="alert">
            <p className="panel__title mono">{job.error.code}</p>
            <p style={{ margin: 0 }}>{job.error.message}</p>
            {job.error.hint ? <p className="muted" style={{ margin: 'var(--s2) 0 0' }}>{job.error.hint}</p> : null}
            {job.error.details ? (
              <div style={{ marginTop: 'var(--s3)' }}>
                <CodeBlock code={JSON.stringify(job.error.details, null, 2)} label="error.details" />
              </div>
            ) : null}
          </div>
        </Section>
      ) : null}

      <Section title="Parameters as submitted">
        {job.parameters && Object.keys(job.parameters).length ? (
          <CodeBlock code={JSON.stringify(job.parameters, null, 2)} label="parameters" />
        ) : (
          <p className="muted" style={{ margin: 0 }}>No extra parameters were sent with this request.</p>
        )}
        {job.references && job.references.length ? (
          <CodeBlock code={JSON.stringify(job.references, null, 2)} label="references" />
        ) : null}
      </Section>

      <Section title="Event trail">
        {eventError ? <ErrorNotice error={eventError} /> : null}
        {events === null && !eventError ? (
          <p className="muted" style={{ margin: 0 }}>
            {terminal ? 'Reading the event trail…' : 'The event trail is read once the job reaches a terminal state.'}
          </p>
        ) : null}
        {events && events.length ? (
          <ol className="timeline">
            {events.map((event) => (
              <li className="timeline__item" key={event.id}>
                <span
                  className={
                    event.kind === 'error' || event.state === 'failed'
                      ? 'timeline__dot timeline__dot--error'
                      : event.state === 'succeeded'
                        ? 'timeline__dot timeline__dot--ok'
                        : 'timeline__dot'
                  }
                  aria-hidden="true"
                />
                <div className="timeline__body">
                  <span className="timeline__kind">
                    {titleCase(event.kind)}
                    {event.state ? ` · ${event.state}` : ''}
                  </span>
                  <span className="dim" style={{ fontSize: 'var(--fs-xs)' }}>
                    {formatDateTime(event.created_at)}
                  </span>
                  {event.message ? <p style={{ margin: 'var(--s1) 0 0' }}>{event.message}</p> : null}
                </div>
              </li>
            ))}
          </ol>
        ) : null}
        {events && !events.length ? (
          <p className="muted" style={{ margin: 0 }}>The backend returned no events for this job.</p>
        ) : null}
      </Section>

      <Section title="Derived requests">
        <div className="row row--tight">
          <label className="field" style={{ minWidth: 180 }}>
            <span className="field__label">Variations to request</span>
            <input
              className="input"
              type="number"
              min={1}
              max={8}
              value={variationCount}
              onChange={(event) => setVariationCount(Math.max(1, Math.min(8, Number(event.target.value) || 1)))}
            />
          </label>
          <Button
            disabled={!terminal || job.state !== 'succeeded' || pending !== null}
            onClick={() => void run('variations')}
            disabledReason="Variations need a succeeded job."
          >
            {pending === 'variations' ? 'Requesting…' : `Request ${variationCount} variation${variationCount === 1 ? '' : 's'}`}
          </Button>
          <span className="dim" style={{ fontSize: 'var(--fs-xs)' }}>
            Each variation is a new job with its own cost.
          </span>
        </div>
        {job.parent_job_id ? (
          <p className="muted" style={{ margin: 0 }}>
            This job was derived from <Link to={`/history/${job.parent_job_id}`}>its parent job</Link>
            {job.relation ? ` (${job.relation})` : ''}.
          </p>
        ) : null}
      </Section>

      {outputs.length ? (
        <Section title="Output metadata">
          <div className="table-wrap">
            <table className="table table--compact">
              <thead>
                <tr>
                  <th>File</th>
                  <th>Type</th>
                  <th>Size</th>
                  <th>Dimensions</th>
                  <th>Duration</th>
                  <th>Seed</th>
                </tr>
              </thead>
              <tbody>
                {job.outputs.map((output, index) => (
                  <tr key={output.id || index}>
                    <td>{output.file_name ?? `Output ${index + 1}`}</td>
                    <td className="mono">{output.content_type ?? 'not reported'}</td>
                    <td className="num">{output.byte_size ?? '—'}</td>
                    <td className="num">{output.width && output.height ? `${output.width}×${output.height}` : '—'}</td>
                    <td className="num">{output.duration_seconds ? formatSeconds(output.duration_seconds) : '—'}</td>
                    <td className="num">{output.seed ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      ) : null}
    </div>
  );
}
