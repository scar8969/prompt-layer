import { useEffect, useState } from 'react';
import {
  assets as assetsApi,
  connections as connectionsApi,
  jobs as jobsApi,
  presets as presetsApi,
  projects as projectsApi,
} from '../../api/endpoints.ts';
import type { Asset, Connection, Job, Preset, Project } from '../../api/types.ts';
import { assetToMedia, MediaGrid } from '../../components/Media.tsx';
import { EmptyState, ErrorState, LoadingState } from '../../components/State.tsx';
import { Section } from '../../components/Data.tsx';
import { Badge, MockBadge } from '../../components/Badge.tsx';
import { Link } from '../../app/router.tsx';
import { useSession, useStatus, billingSentence } from '../../state/session.tsx';
import { formatRelative } from '../../lib/util.ts';
import { JobCard } from '../jobs/JobCard.tsx';

interface HomeData {
  jobs: Job[];
  assets: Asset[];
  projects: Project[];
  connections: Connection[];
  presets: Preset[];
}

/**
 * Home / Discover. Everything on this page is read from the backend: the
 * recent jobs it recorded, the assets it can serve, the connections it knows
 * about. When there is nothing yet, the page says so and points at the action
 * that would create something.
 */
export function HomeScreen() {
  const session = useSession();
  const { status } = useStatus();
  const [data, setData] = useState<HomeData | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void (async () => {
      const results = await Promise.allSettled([
        jobsApi.list({ limit: 6 }),
        assetsApi.list({ limit: 12 }),
        projectsApi.list(),
        connectionsApi.list(),
        presetsApi.list({}),
      ]);
      if (cancelled) return;
      const [jobPage, assetPage, projectList, connectionList, presetList] = results;
      const failure = results.find((result) => result.status === 'rejected');
      if (failure && failure.status === 'rejected') setError(failure.reason);
      else setError(null);
      setData({
        jobs: jobPage.status === 'fulfilled' ? jobPage.value.jobs : [],
        assets: assetPage.status === 'fulfilled' ? assetPage.value.assets : [],
        projects: projectList.status === 'fulfilled' ? projectList.value : [],
        connections: connectionList.status === 'fulfilled' ? connectionList.value : [],
        presets: presetList.status === 'fulfilled' ? presetList.value : [],
      });
      setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [nonce]);

  const missing = status?.missing_configuration ?? [];

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__heading">
            {session.user?.display_name ? `Welcome back, ${session.user.display_name}` : 'Prompt Layer Studio'}
          </h1>
          <p className="page__subtitle">
            A prompt-first studio over the models you connected yourself. Nothing here is generated for you: every
            image, video, cost and job below came from the backend.
          </p>
        </div>
      </div>

      {loading ? <LoadingState label="Reading your workspace…" /> : null}
      {error ? <ErrorState error={error} onRetry={() => setNonce((n) => n + 1)} /> : null}

      {status ? (
        <div className="panel panel--info">
          <p className="panel__title">Backend report</p>
          <p style={{ margin: 0 }}>{billingSentence(status.billing)}</p>
          <p className="muted" style={{ margin: 'var(--s2) 0 0' }}>
            {status.environment} · v{status.version} · {status.adapters.length} adapter
            {status.adapters.length === 1 ? '' : 's'} registered
            {status.mock_provider_enabled ? ' · the local development placeholder provider is enabled' : ''}
          </p>
          {missing.length ? (
            <p className="muted" style={{ margin: 'var(--s2) 0 0' }}>
              The backend reports missing configuration: {missing.join(', ')}.
            </p>
          ) : null}
        </div>
      ) : null}

      {data && !data.connections.length ? (
        <EmptyState
          icon="plug"
          title="No provider is connected yet"
          body="A provider connection is the first step: an API base URL, your key and a model id. The key is sent once and stored server-side; this client never shows it again."
          action={
            <Link to="/connections" className="btn btn--primary">
              Connect a provider
            </Link>
          }
        />
      ) : null}

      {data && data.connections.length ? (
        <Section
          title="Connections"
          actions={
            <Link to="/connections" className="btn btn--sm">
              Manage
            </Link>
          }
        >
          <div className="grid grid--3">
            {data.connections.slice(0, 6).map((connection) => (
              <Link key={connection.id} to={`/connections/${connection.id}`} className="card">
                <div className="card__head">
                  <span className="card__title">{connection.name}</span>
                  <Badge tone={connection.is_enabled ? 'ok' : 'plain'}>
                    {connection.is_enabled ? 'enabled' : 'disabled'}
                  </Badge>
                </div>
                <div className="stack stack--tight" style={{ marginTop: 'var(--s2)' }}>
                  <span className="dim">{connection.adapter_label}</span>
                  <span className="mono dim" style={{ fontSize: 'var(--fs-xs)' }}>
                    {connection.credential.present
                      ? connection.credential.last4
                        ? `key ••••${connection.credential.last4}`
                        : 'key on file'
                      : 'no key on file'}
                  </span>
                  <span className="dim" style={{ fontSize: 'var(--fs-xs)' }}>
                    {connection.model_count} model{connection.model_count === 1 ? '' : 's'} · last test{' '}
                    {connection.last_test_status ?? 'never run'}
                  </span>
                </div>
              </Link>
            ))}
          </div>
        </Section>
      ) : null}

      {data && data.jobs.length ? (
        <Section
          title="Recent jobs"
          actions={
            <Link to="/history" className="btn btn--sm">
              All history
            </Link>
          }
        >
          <div className="stack">
            {data.jobs.map((job) => (
              <JobCard key={job.id} job={job} compact />
            ))}
          </div>
        </Section>
      ) : null}

      {data && !data.jobs.length && !loading ? (
        <EmptyState
          icon="wand"
          title="No job has been recorded yet"
          body="When you submit a prompt from Image Studio or Video Studio, the job the backend creates appears here with its real state."
          action={
            <Link to="/image" className="btn btn--primary">
              Open Image Studio
            </Link>
          }
        />
      ) : null}

      {data && data.assets.length ? (
        <Section
          title="Recent assets"
          actions={
            <Link to="/assets" className="btn btn--sm">
              Asset library
            </Link>
          }
        >
          <MediaGrid items={data.assets.slice(0, 12).map(assetToMedia)} columns={4} />
        </Section>
      ) : null}

      {data ? (
        <div className="grid grid--2">
          <Section
            title="Projects"
            actions={
              <Link to="/projects" className="btn btn--sm">
                Open
              </Link>
            }
          >
            {data.projects.length ? (
              <div className="stack stack--tight">
                {data.projects.slice(0, 5).map((project) => (
                  <Link key={project.id} to={`/projects/${project.id}`} className="row row--between">
                    <span>{project.name}</span>
                    <span className="dim" style={{ fontSize: 'var(--fs-xs)' }}>
                      {project.asset_count ?? 0} assets · {formatRelative(project.created_at)}
                    </span>
                  </Link>
                ))}
              </div>
            ) : (
              <p className="muted" style={{ margin: 0 }}>
                No project yet. Projects group assets and the jobs that produced them.
              </p>
            )}
          </Section>

          <Section
            title="Presets"
            actions={
              <Link to="/presets" className="btn btn--sm">
                Browse
              </Link>
            }
          >
            {data.presets.length ? (
              <div className="stack stack--tight">
                {data.presets.slice(0, 5).map((preset) => (
                  <div key={preset.id} className="row row--between">
                    <span>{preset.name}</span>
                    <span className="dim" style={{ fontSize: 'var(--fs-xs)' }}>
                      {preset.modality}
                      {preset.category ? ` · ${preset.category}` : ''}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="muted" style={{ margin: 0 }}>
                No preset yet. A preset stores a prompt template and its settings.
              </p>
            )}
          </Section>
        </div>
      ) : null}

      <Section title="Start something">
        <div className="grid grid--2">
          <Link to="/image" className="card">
            <div className="card__head">
              <span className="card__title">Image Studio</span>
            </div>
            <p className="muted" style={{ margin: 'var(--s2) 0 0' }}>
              Write a prompt, choose a registered image model, set only the parameters that model declares, then submit.
            </p>
          </Link>
          <Link to="/video" className="card">
            <div className="card__head">
              <span className="card__title">Video Studio</span>
            </div>
            <p className="muted" style={{ margin: 'var(--s2) 0 0' }}>
              The same flow for video, with duration, aspect ratio, resolution and camera move — each one disabled
              unless the model profile declares it.
            </p>
          </Link>
        </div>
      </Section>

      <Section title="Placeholder disclosure">
        <p className="muted" style={{ margin: 0 }}>
          You are reading <MockBadge isMock /> on a model or job only when the backend reports{' '}
          <span className="mono">is_mock = true</span> for that record.
        </p>
      </Section>
    </div>
  );
}
