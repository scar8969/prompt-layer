import { useState } from 'react';
import { projects as projectsApi } from '../../api/endpoints.ts';
import type { Project } from '../../api/types.ts';
import { Badge } from '../../components/Badge.tsx';
import { Button } from '../../components/Button.tsx';
import { Section } from '../../components/Data.tsx';
import { Dialog } from '../../components/Dialog.tsx';
import { TextArea, TextInput } from '../../components/Field.tsx';
import { ErrorNotice } from '../../components/ErrorNotice.tsx';
import { EmptyState, ErrorState, LoadingState } from '../../components/State.tsx';
import { Icon } from '../../components/Icon.tsx';
import { Link } from '../../app/router.tsx';
import { invalidate, useMutation, useQuery } from '../../state/query.ts';
import { useToasts } from '../../state/toasts.tsx';
import { formatRelative } from '../../lib/util.ts';

/** Projects group assets and the jobs that produced them. */
export function ProjectsScreen() {
  const toasts = useToasts();
  const projects = useQuery<Project[]>('projects', () => projectsApi.list(), { staleMs: 10_000 });
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [filter, setFilter] = useState<'all' | 'favorite' | 'archived'>('all');

  const create = useMutation(
    (input: { name: string; description?: string }) => projectsApi.create(input),
    ['projects'],
  );

  const submit = async () => {
    if (!name.trim()) return;
    try {
      const project = await create.mutate({ name: name.trim(), description: description.trim() || undefined });
      setCreating(false);
      setName('');
      setDescription('');
      invalidate('projects');
      toasts.push({ tone: 'success', title: 'Project created', text: project.name });
    } catch {
      // The mutation keeps the error; it is rendered inside the dialog.
    }
  };

  const all = projects.data ?? [];
  const visible = all.filter((project) =>
    filter === 'favorite' ? project.is_favorite : filter === 'archived' ? project.is_archived : !project.is_archived,
  );

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__heading">Projects</h1>
          <p className="page__subtitle">
            A project is a folder over the workspace: the assets you filed there and the jobs that produced them.
          </p>
        </div>
        <div className="page__actions">
          <Button variant="primary" onClick={() => setCreating(true)}>
            <span className="btn__icon">
              <Icon name="folderPlus" size={14} />
            </span>
            New project
          </Button>
        </div>
      </div>

      <div className="toolbar">
        <div className="toolbar__group">
          <button
            type="button"
            className="chip"
            aria-pressed={filter === 'all'}
            onClick={() => setFilter('all')}
          >
            Active
          </button>
          <button
            type="button"
            className="chip"
            aria-pressed={filter === 'favorite'}
            onClick={() => setFilter('favorite')}
          >
            Favourites
          </button>
          <button
            type="button"
            className="chip"
            aria-pressed={filter === 'archived'}
            onClick={() => setFilter('archived')}
          >
            Archived
          </button>
        </div>
        <div className="toolbar__spacer" />
        <Button size="sm" onClick={projects.reload}>
          Refresh
        </Button>
      </div>

      {projects.loading ? <LoadingState label="Reading your projects…" /> : null}
      {projects.error ? <ErrorState error={projects.error} onRetry={projects.reload} /> : null}

      {!projects.loading && !projects.error && !visible.length ? (
        <EmptyState
          icon="folder"
          title={all.length ? 'Nothing in this view' : 'No project yet'}
          body={
            all.length
              ? 'No project matches the view you chose. Switch back to Active to see them.'
              : 'Create a project to file assets and keep the jobs that produced them together.'
          }
          action={
            all.length ? (
              <Button onClick={() => setFilter('all')}>Show active projects</Button>
            ) : (
              <Button variant="primary" onClick={() => setCreating(true)}>
                New project
              </Button>
            )
          }
        />
      ) : null}

      {visible.length ? (
        <Section title={`${visible.length} project${visible.length === 1 ? '' : 's'}`}>
          <div className="grid grid--3">
            {visible.map((project) => (
              <div key={project.id} className="card">
                <div className="card__head">
                  <Link to={`/projects/${project.id}`} className="card__title">
                    {project.name}
                  </Link>
                  {project.is_favorite ? <Badge tone="accent">favourite</Badge> : null}
                  {project.is_archived ? <Badge>archived</Badge> : null}
                </div>
                <p className="muted" style={{ margin: 'var(--s2) 0 0' }}>
                  {project.description ?? 'No description.'}
                </p>
                <div className="row row--between" style={{ marginTop: 'var(--s3)' }}>
                  <span className="dim" style={{ fontSize: 'var(--fs-xs)' }}>
                    {project.asset_count ?? 0} assets · created {formatRelative(project.created_at)}
                  </span>
                  <Link to={`/projects/${project.id}`} className="btn btn--sm">
                    Open
                  </Link>
                </div>
              </div>
            ))}
          </div>
        </Section>
      ) : null}

      <Dialog
        open={creating}
        onClose={() => setCreating(false)}
        title="New project"
        width="narrow"
        footer={
          <>
            <Button onClick={() => setCreating(false)} disabled={create.pending}>
              Cancel
            </Button>
            <Button variant="primary" onClick={() => void submit()} disabled={!name.trim() || create.pending}>
              {create.pending ? 'Creating…' : 'Create project'}
            </Button>
          </>
        }
      >
        <div className="stack">
          <TextInput
            label="Name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            required
          />
          <TextArea
            label="Description"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            hint="Optional. Shown on the project card."
          />
          {create.error ? <ErrorNotice error={create.error} /> : null}
        </div>
      </Dialog>
    </div>
  );
}
