import { useEffect, useState } from 'react';
import { assets as assetsApi, projects as projectsApi } from '../../api/endpoints.ts';
import type { Asset, Project } from '../../api/types.ts';
import { Badge, MockBadge } from '../../components/Badge.tsx';
import { Button } from '../../components/Button.tsx';
import { FilterBar, Pagination, Section } from '../../components/Data.tsx';
import { Dialog } from '../../components/Dialog.tsx';
import { Checkbox, Select, TextInput } from '../../components/Field.tsx';
import { ErrorNotice } from '../../components/ErrorNotice.tsx';
import { EmptyState, ErrorState, LoadingState } from '../../components/State.tsx';
import { AssetImage, Dropzone } from '../../components/Media.tsx';
import { Icon } from '../../components/Icon.tsx';
import { DestructiveAction } from '../../components/Confirm.tsx';
import { useRouter } from '../../app/router.tsx';
import { invalidate, useQuery } from '../../state/query.ts';
import { useToasts } from '../../state/toasts.tsx';
import { useStatus } from '../../state/session.tsx';
import { formatBytes, formatDateTime, truncate } from '../../lib/util.ts';
import { Link } from '../../app/router.tsx';

const KINDS = [
  { value: '', label: 'Every kind' },
  { value: 'image', label: 'Images' },
  { value: 'video', label: 'Video' },
  { value: 'upload', label: 'Uploads' },
  { value: 'mask', label: 'Masks' },
];

/** The asset library: everything the backend can serve, and nothing it cannot. */
export function AssetsScreen() {
  const { route } = useRouter();
  const toasts = useToasts();
  const { status } = useStatus();
  const [kind, setKind] = useState('');
  const [favoriteOnly, setFavoriteOnly] = useState(false);
  const [projectFilter, setProjectFilter] = useState(route.params.get('project_id') ?? '');
  const [query, setQuery] = useState('');
  const [search, setSearch] = useState('');
  const [assets, setAssets] = useState<Asset[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [nonce, setNonce] = useState(0);
  const [selected, setSelected] = useState<Asset | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<unknown>(null);

  const projects = useQuery<Project[]>('projects', () => projectsApi.list(), { staleMs: 30_000 });

  const load = async (nextCursor: string | null) => {
    try {
      const page = await assetsApi.list({
        kind: kind || undefined,
        favorite: favoriteOnly || undefined,
        project_id: projectFilter || undefined,
        q: search || undefined,
        limit: 24,
        cursor: nextCursor ?? undefined,
      });
      setAssets((current) => (nextCursor ? [...current, ...page.assets] : page.assets));
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
  }, [kind, favoriteOnly, projectFilter, search, nonce]);

  const upload = async (files: File[]) => {
    setUploading(true);
    setUploadError(null);
    try {
      for (const file of files) {
        const buffer = await file.arrayBuffer();
        const base64 = toBase64(buffer);
        await assetsApi.upload({
          file_name: file.name,
          content_type: file.type || 'application/octet-stream',
          data_base64: base64,
          project_id: projectFilter || null,
        });
      }
      toasts.push({
        tone: 'success',
        title: `${files.length} file${files.length === 1 ? '' : 's'} uploaded`,
      });
      invalidate('assets');
      setNonce((n) => n + 1);
    } catch (err) {
      setUploadError(err);
    } finally {
      setUploading(false);
    }
  };

  const visible = assets;

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__heading">Asset Library</h1>
          <p className="page__subtitle">
            Every file this workspace can serve, whether it was generated here or uploaded. An asset with no URL is not
            drawn as an image — the tile says the backend returned none.
          </p>
        </div>
        <div className="page__actions">
          <Button size="sm" onClick={() => setNonce((n) => n + 1)}>
            Refresh
          </Button>
        </div>
      </div>

      <FilterBar label="Filter assets">
        <div className="toolbar__group" style={{ flex: 1, minWidth: 200 }}>
          <TextInput
            label="Search assets"
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') setSearch(query.trim());
            }}
            placeholder="Name or prompt text, then press Enter"
          />
        </div>
        <div className="toolbar__group">
          <Select label="Kind" value={kind} onChange={(event) => setKind(event.target.value)} options={KINDS} />
        </div>
        <div className="toolbar__group">
          <Select
            label="Project"
            value={projectFilter}
            onChange={(event) => setProjectFilter(event.target.value)}
            placeholder="Any project"
            options={(projects.data ?? []).map((project) => ({ value: project.id, label: project.name }))}
          />
        </div>
        <div className="toolbar__group">
          <Checkbox
            label="Favourites only"
            checked={favoriteOnly}
            onChange={(event) => setFavoriteOnly(event.target.checked)}
          />
        </div>
        <div className="toolbar__spacer" />
        <Button size="sm" onClick={() => setSearch(query.trim())}>
          <span className="btn__icon">
            <Icon name="search" size={13} />
          </span>
          Search
        </Button>
      </FilterBar>

      <Section title="Upload">
        <Dropzone
          onFiles={(files) => void upload(files)}
          accept="image/*,video/*"
          hint="Files are sent to the backend as base64 and stored server-side. The client keeps no copy."
          disabled={uploading}
          disabledReason={uploading ? 'An upload is already in progress.' : null}
          maxBytes={status?.adapters.length ? undefined : undefined}
        />
        {uploadError ? <ErrorNotice error={uploadError} /> : null}
      </Section>

      {loading ? <LoadingState label="Reading the asset library…" /> : null}
      {error ? <ErrorState error={error} onRetry={() => setNonce((n) => n + 1)} /> : null}

      {!loading && !error && !visible.length ? (
        <EmptyState
          icon="library"
          title="No asset matches this view"
          body="Assets appear once a job succeeds or a file is uploaded. Adjust the filters, or generate something in Image Studio."
          action={
            <Link to="/image" className="btn btn--primary">
              Open Image Studio
            </Link>
          }
        />
      ) : null}

      {visible.length ? (
        <>
          <div className="asset-grid">
            {visible.map((asset) => (
              <button
                key={asset.id}
                type="button"
                className="asset-tile"
                onClick={() => setSelected(asset)}
                aria-label={`Open ${asset.name}`}
              >
                <span className="asset-tile__media">
                  {asset.kind === 'video' ? (
                    asset.content_url ? (
                      <video src={asset.content_url} preload="metadata" muted />
                    ) : null
                  ) : (
                    <AssetImage src={asset.thumbnail_url ?? asset.content_url} alt={asset.name} />
                  )}
                </span>
                <span className="asset-tile__body">
                  <span className="asset-tile__name">{truncate(asset.name, 36)}</span>
                  <span className="asset-tile__meta">
                    {asset.kind} · {formatBytes(asset.byte_size)}
                    {asset.width && asset.height ? ` · ${asset.width}×${asset.height}` : ''}
                  </span>
                  {asset.is_favorite ? <Badge tone="accent">favourite</Badge> : null}
                  <MockBadge isMock={asset.is_mock} />
                </span>
              </button>
            ))}
          </div>
          <Pagination
            hasMore={cursor !== null}
            loaded={assets.length}
            loading={loadingMore}
            onMore={() => {
              setLoadingMore(true);
              void load(cursor);
            }}
          />
        </>
      ) : null}

      <AssetSheet
        asset={selected}
        projects={projects.data ?? []}
        onClose={() => setSelected(null)}
        onChanged={() => {
          setSelected(null);
          setNonce((n) => n + 1);
        }}
      />
    </div>
  );
}

function toBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  const chunk = 0x8000;
  for (let index = 0; index < bytes.length; index += chunk) {
    binary += String.fromCharCode(...bytes.subarray(index, index + chunk));
  }
  return btoa(binary);
}

function AssetSheet({
  asset,
  projects,
  onClose,
  onChanged,
}: {
  asset: Asset | null;
  projects: Project[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const toasts = useToasts();
  const [name, setName] = useState('');
  const [projectId, setProjectId] = useState('');
  const [favorite, setFavorite] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (!asset) return;
    setName(asset.name);
    setProjectId(asset.project_id ?? '');
    setFavorite(asset.is_favorite);
    setError(null);
  }, [asset?.id]);

  if (!asset) return null;

  const save = async () => {
    setPending(true);
    setError(null);
    try {
      await assetsApi.patch(asset.id, {
        name: name.trim() || asset.name,
        project_id: projectId || null,
        is_favorite: favorite,
      });
      invalidate('assets');
      toasts.push({ tone: 'success', title: 'Asset updated' });
      onChanged();
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  };

  const remove = async () => {
    await assetsApi.remove(asset.id);
    invalidate('assets');
    toasts.push({ tone: 'info', title: 'Asset deleted', text: asset.name });
    onChanged();
  };

  return (
    <Dialog
      open={asset !== null}
      onClose={onClose}
      title={asset.name}
      width="wide"
      footer={
        <>
          <Button onClick={onClose} disabled={pending}>
            Close
          </Button>
          <Button variant="primary" onClick={() => void save()} disabled={pending}>
            {pending ? 'Saving…' : 'Save changes'}
          </Button>
        </>
      }
    >
      <div className="grid grid--2">
        <div>
          <div className="media media--square">
            {asset.kind === 'video' ? (
              asset.content_url ? (
                <video src={asset.content_url} controls preload="metadata" />
              ) : (
                <div className="media__placeholder">The backend returned no URL for this asset.</div>
              )
            ) : (
              <AssetImage src={asset.content_url} alt={asset.name} />
            )}
          </div>
          <div className="row row--tight" style={{ marginTop: 'var(--s2)', flexWrap: 'wrap' }}>
            <MockBadge isMock={asset.is_mock} />
            {asset.content_url ? (
              <>
                <a className="btn btn--sm" href={asset.content_url} target="_blank" rel="noreferrer">
                  Open original
                </a>
                <a className="btn btn--sm" href={asset.content_url} download={asset.name}>
                  Download
                </a>
              </>
            ) : (
              <span className="dim">No URL returned.</span>
            )}
          </div>
        </div>

        <div className="stack stack--tight">
          <TextInput label="Name" value={name} onChange={(event) => setName(event.target.value)} />
          <Select
            label="Project"
            value={projectId}
            onChange={(event) => setProjectId(event.target.value)}
            placeholder="No project"
            options={projects.map((project) => ({ value: project.id, label: project.name }))}
          />
          <Checkbox
            label="Favourite"
            checked={favorite}
            onChange={(event) => setFavorite(event.target.checked)}
          />
          <dl className="kv">
            <dt className="kv__key">Kind</dt>
            <dd className="kv__value mono">{asset.kind}</dd>
            <dt className="kv__key">Content type</dt>
            <dd className="kv__value mono">{asset.content_type}</dd>
            <dt className="kv__key">Size</dt>
            <dd className="kv__value numeric">{formatBytes(asset.byte_size)}</dd>
            <dt className="kv__key">Dimensions</dt>
            <dd className="kv__value numeric">
              {asset.width && asset.height ? `${asset.width}×${asset.height}` : 'not reported'}
            </dd>
            <dt className="kv__key">Source</dt>
            <dd className="kv__value">{asset.source}</dd>
            <dt className="kv__key">Job</dt>
            <dd className="kv__value">
              {asset.job_id ? <Link to={`/history/${asset.job_id}`}>{asset.job_id}</Link> : 'not from a job'}
            </dd>
            <dt className="kv__key">Created</dt>
            <dd className="kv__value">{formatDateTime(asset.created_at)}</dd>
          </dl>
          {error ? <ErrorNotice error={error} /> : null}
          <DestructiveAction
            label="Delete asset"
            title="Delete this asset?"
            body={<p style={{ margin: 0 }}>The file is removed from storage. Jobs that produced it stay in history.</p>}
            confirmLabel="Delete asset"
            onConfirm={remove}
          />
        </div>
      </div>
    </Dialog>
  );
}
