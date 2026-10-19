import type { Job } from '../../api/types.ts';
import { Badge, MockBadge } from '../../components/Badge.tsx';
import { ProgressBar } from '../../components/Controls.tsx';
import { AssetImage } from '../../components/Media.tsx';
import { Icon } from '../../components/Icon.tsx';
import { formatCost, formatDateTime, formatDurationMs, looksLikeVideo, truncate } from '../../lib/util.ts';
import { Link } from '../../app/router.tsx';
import { isTerminalState } from '../../state/jobs.ts';

/**
 * One job, rendered from the record the backend returned. Progress is only a
 * bar when the provider reported a measured number; otherwise the card states
 * the job's state and says so.
 */
export function JobCard({ job, compact }: { job: Job; compact?: boolean }) {
  const state = job.state;
  const tone =
    state === 'succeeded'
      ? 'ok'
      : state === 'failed' || state === 'cancelled' || state === 'canceled'
        ? 'danger'
        : isTerminalState(state)
          ? 'plain'
          : 'accent';

  return (
    <article className={job.is_mock ? 'job job--mock' : 'job'}>
      <div className="job__head">
        <Badge tone={tone}>{state}</Badge>
        <MockBadge isMock={job.is_mock} />
        <span className="job__prompt" title={job.prompt ?? ''}>
          {truncate(job.prompt, compact ? 60 : 110) || 'No prompt recorded'}
        </span>
        <span className="topbar__spacer" />
        <Link to={`/history/${job.id}`} className="btn btn--sm">
          Details
        </Link>
      </div>

      <div className="job__meta">
        <span>{job.model ? job.model.display_name : job.model_id ?? 'No model recorded'}</span>
        <span className="mono">{job.mode}</span>
        <span className="mono">{job.kind}</span>
        <span>{formatDateTime(job.created_at)}</span>
        {job.elapsed_ms !== null ? <span className="numeric">{formatDurationMs(job.elapsed_ms)}</span> : null}
        {job.cost_estimate ? <span className="numeric">{formatCost(job.cost_estimate)}</span> : null}
      </div>

      {!isTerminalState(state) ? (
        <ProgressBar
          value={job.progress}
          isEstimate={job.progress_is_estimate}
          label={`Job ${state}`}
        />
      ) : null}

      {job.error ? (
        <div className="panel panel--danger" role="alert">
          <p className="panel__title mono">{job.error.code}</p>
          <p style={{ margin: 0 }}>{job.error.message}</p>
          {job.error.hint ? <p className="muted" style={{ margin: 'var(--s1) 0 0' }}>{job.error.hint}</p> : null}
        </div>
      ) : null}

      {job.outputs.length ? (
        <div className="job__outputs">
          {job.outputs.map((output, index) => {
            const video = looksLikeVideo(output.content_type ?? '');
            return (
              <figure key={output.id || index} className="asset-tile" style={{ margin: 0 }}>
                <div className="asset-tile__media">
                  {output.url === null ? (
                    <div className="media__placeholder">
                      <Icon name="alert" size={16} />
                      <p style={{ margin: 'var(--s1) 0 0' }}>No URL returned</p>
                    </div>
                  ) : video ? (
                    <video src={output.url} controls preload="metadata" />
                  ) : (
                    <AssetImage src={output.url} alt={truncate(job.prompt, 80) || `Output ${index + 1}`} />
                  )}
                </div>
                <figcaption className="asset-tile__body">
                  <span className="asset-tile__name">{output.file_name ?? `Output ${index + 1}`}</span>
                  <span className="asset-tile__meta">
                    {output.width && output.height ? `${output.width}×${output.height}` : 'dimensions not reported'}
                    {output.seed !== null ? ` · seed ${output.seed}` : ''}
                  </span>
                  <MockBadge isMock={output.is_mock} />
                </figcaption>
              </figure>
            );
          })}
        </div>
      ) : isTerminalState(state) && state === 'succeeded' ? (
        <p className="muted" style={{ margin: 0 }}>
          The backend reported this job as succeeded but returned no outputs.
        </p>
      ) : null}

      {job.cost_estimate && job.cost_estimate.is_billable === false ? (
        <p className="dim" style={{ margin: 0, fontSize: 'var(--fs-xs)' }}>
          Not billable on this platform: the request is billed by your own provider account.
        </p>
      ) : null}
    </article>
  );
}
