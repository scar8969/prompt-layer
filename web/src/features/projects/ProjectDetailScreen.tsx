import { useEffect, useState } from 'react';
import { projects as projectsApi } from '../../api/endpoints.ts';
import type { Asset, Job, Project } from '../../api/types.ts';
import { Badge } from '../../components/Badge.tsx';
import { Button } from '../../components/Button.tsx';
import { KeyValueList, Section } from '../../components/Data.tsx';
import { Dialog } from '../../components/Dialog.tsx';
import { TextArea, TextInput } from '../../components/Field.tsx';
import { ErrorNotice } from '../../components/ErrorNotice.tsx';
import { Checkbox } from '../../components/Field.tsx';
import { EmptyState, ErrorState, LoadingState } from '../../components/State.tsx';
import { MediaGrid, assetToMedia } from '../../components/Media.tsx';
import { Link, useRouter } from '../../app/router.tsx';
import { DestructiveAction } from '../../components/Confirm.tsx';
import { invalidate } from '../../state/query.ts';
import { useToasts } from '../../state/toasts.tsx';
import { formatDateTime } from '../../lib/util.ts';
import { JobCard } from '../jobs/JobCard.tsx';

interface Detail {
  project: Project;
  assets: Asset[];
  jobs: Job[];
}

/** One project: what is filed in it, and the jobs that produced it. */
export function ProjectDetailScreen({ projectId }: { projectId: string }) {
  const { navigate } = useRouter();
  const toasts = useToasts();
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [favorite, setFavorite] = useState(false);
  const [archived, setArchived] = useState(false);
  const [saveError, setSaveError] = useState<unknown>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void (async () => {
      try {
        const [bundle, jobList] = await Promise.all([projectsApi.get(projectId), projectsApi.jobs(projectId)]);
        if (cancelled) return;
        setDetail({ project: bundle.project, assets: bundle.assets, jobs: jobList });
        setError(null);
      } catch (err) {
        if (!cancelled) setError(err);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [projectId, nonce]);

  const openEditor = () => {
    if (!detail) return;
    setName(detail.project.name);
    setDescription(detail.project.description ?? '');
    setFavorite(detail.project.is_favorite);
    setArchived(detail.project.is_archived);
    setSaveError(null);
    setEditing(true);
  };

  const save = async () => {
    if (!detail) return;
    setPending(true);
    setSaveError(null);
    try {
      const updated = await projectsApi.patch(detail.project.id, {
        name: name.trim() || detail.project.name,
        description: description.trim(),
        is_favorite: favorite,
        is_archived: archived,
      });
      setDetail({ ...detail, project: updated });
      setEditing(false);
      invalidate('projects');
      toasts.push({ tone: 'success', title: 'Project updated', text: updated.name });
    } catch (err) {
      setSaveError(err);
    } finally {
      setPending(false);
    }
  };

  const remove = async () => {
    if (!detail) return;
    const result = await projectsApi.remove(detail.project.id);
    invalidate('projects');
    invalidate('assets');
    toasts.push({
      tone: 'info',
      title: 'Project deleted',
      text: `${result.assets_unassigned} asset${result.assets_unassigned === 1 ? '' : 's'} were unassigned; nothing was deleted.`,
    });
    navigate('/projects');
  };

  if (loading) return <LoadingState label="Reading the project…" />;
  if (error) return <ErrorState error={error} onRetry={() => setNonce((n) => n + 1)} />;
  if (!detail) return <ErrorState error={new Error('The backend returned no project.')} />;

  const { project, assets, jobs } = detail;

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <div className="row row--tight">
            <Badge tone={project.is_archived ? 'plain' : 'ok'}>
              {project.is_archived ? 'archived' : 'active'}
            </Badge>
            {project.is_favorite ? <Badge tone="accent">favourite</Badge> : null}
          </div>
          <h1 className="page__heading">{project.name}</h1>
          <p className="page__subtitle">{project.description ?? 'No description on this project.'}</p>
        </div>
        <div className="page__actions">
          <Link to="/projects" className="btn btn--sm">
            All projects
          </Link>
          <Button size="sm" onClick={openEditor}>
            Edit
          </Button>
          <DestructiveAction
            label="Delete project"
            title="Delete this project?"
            body={
              <p style={{ margin: 0 }}>
                The project folder is removed. Its assets are not deleted — the backend reports how many were
                unassigned.
              </p>
            }
            confirmLabel="Delete project"
            onConfirm={remove}
          />
        </div>
      </div>

      <div className="grid grid--2">
        <Section title="Project">
          <KeyValueList
            rows={[
              { key: 'Identifier', value: <span className="mono">{project.id}</span> },
              { key: 'Created', value: formatDateTime(project.created_at) },
              { key: 'Updated', value: formatDateTime(project.updated_at) },
              { key: 'Assets', value: String(assets.length) },
              { key: 'Jobs', value: String(jobs.length) },
            ]}
          />
        </Section>

        <Section title="What this page does not do">
          <p className="muted" style={{ margin: 0 }}>
            There is no rename-on-drag, no cover image and no fake activity graph. The counts above are the records the
            backend returned for this project.
          </p>
        </Section>
      </div>

      <Section
        title={`Assets in this project (${assets.length})`}
        actions={
          <Link to={`/assets?project_id=${project.id}`} className="btn btn--sm">
            Open in library
          </Link>
        }
      >
        {assets.length ? (
          <MediaGrid items={assets.map(assetToMedia)} columns={4} />
        ) : (
          <EmptyState
            icon="library"
            title="No asset filed here yet"
            body="Generate something into this project from Image Studio, or move an existing asset in from the Asset Library."
            action={
              <Link to="/image" className="btn btn--primary">
                Open Image Studio
              </Link>
            }
          />
        )}
      </Section>

      <Section title={`Jobs in this project (${jobs.length})`}>
        {jobs.length ? (
          <div className="stack">
            {jobs.map((job) => (
              <JobCard key={job.id} job={job} compact />
            ))}
          </div>
        ) : (
          <p className="muted" style={{ margin: 0 }}>
            No job was submitted with this project selected.
          </p>
        )}
      </Section>

      <Dialog
        open={editing}
        onClose={() => setEditing(false)}
        title="Edit project"
        width="narrow"
        footer={
          <>
            <Button onClick={() => setEditing(false)} disabled={pending}>
              Cancel
            </Button>
            <Button variant="primary" onClick={() => void save()} disabled={pending}>
              {pending ? 'Saving…' : 'Save changes'}
            </Button>
          </>
        }
      >
        <div className="stack">
          <TextInput label="Name" value={name} onChange={(event) => setName(event.target.value)} required />
          <TextArea
            label="Description"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
          <Checkbox
            label="Favourite"
            checked={favorite}
            onChange={(event) => setFavorite(event.target.checked)}
          />
          <Checkbox
            label="Archived"
            hint="Archived projects stay readable but are hidden from the active list."
            checked={archived}
            onChange={(event) => setArchived(event.target.checked)}
          />
          {saveError ? <ErrorNotice error={saveError} /> : null}
        </div>
      </Dialog>
    </div>
  );
}
