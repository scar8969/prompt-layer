import { useEffect, useState } from 'react';
import { presets as presetsApi, prompts as promptsApi } from '../../api/endpoints.ts';
import type { Preset, PromptRecord } from '../../api/types.ts';
import { Badge } from '../../components/Badge.tsx';
import { Button } from '../../components/Button.tsx';
import { FilterBar, Section } from '../../components/Data.tsx';
import { Dialog } from '../../components/Dialog.tsx';
import { Select, TextArea, TextInput } from '../../components/Field.tsx';
import { ErrorNotice } from '../../components/ErrorNotice.tsx';
import { EmptyState, ErrorState, LoadingState } from '../../components/State.tsx';
import { DestructiveAction } from '../../components/Confirm.tsx';
import { TagInput, Tabs } from '../../components/Controls.tsx';
import { Icon } from '../../components/Icon.tsx';
import { Link } from '../../app/router.tsx';
import { invalidate } from '../../state/query.ts';
import { useToasts } from '../../state/toasts.tsx';
import { useQuery } from '../../state/query.ts';
import { formatDateTime, truncate } from '../../lib/util.ts';

type Tab = 'presets' | 'prompts';

/**
 * Presets & Workflows. A preset is a stored prompt template plus settings; a
 * saved prompt is a piece of text you want to reuse. Both are records the
 * backend owns, so both are edited through its API and never kept locally.
 */
export function PresetsScreen() {
  const [tab, setTab] = useState<Tab>('presets');

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__heading">Presets &amp; Workflows</h1>
          <p className="page__subtitle">
            Reusable starting points: a prompt template, its negative prompt and the settings that go with it. Selecting
            one in a studio fills the form; nothing is submitted until you say so.
          </p>
        </div>
      </div>

      <Tabs
        label="Library section"
        value={tab}
        onChange={setTab}
        options={[
          { value: 'presets', label: 'Presets' },
          { value: 'prompts', label: 'Saved prompts' },
        ]}
      />

      {tab === 'presets' ? <PresetPanel /> : <PromptPanel />}
    </div>
  );
}

function PresetPanel() {
  const toasts = useToasts();
  const [modality, setModality] = useState('');
  const [query, setQuery] = useState('');
  const [applied, setApplied] = useState('');
  const [editing, setEditing] = useState<Preset | 'new' | null>(null);
  const [nonce, setNonce] = useState(0);

  const presets = useQuery<Preset[]>(
    `presets?${modality};${applied}`,
    () => presetsApi.list({ modality: modality || undefined, q: applied || undefined }),
    { staleMs: 10_000 },
  );

  const list = presets.data ?? [];

  return (
    <div className="stack">
      <FilterBar label="Filter presets">
        <div className="toolbar__group" style={{ flex: 1, minWidth: 200 }}>
          <TextInput
            label="Search presets"
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') setApplied(query.trim());
            }}
          />
        </div>
        <div className="toolbar__group">
          <Select
            label="Modality"
            value={modality}
            onChange={(event) => setModality(event.target.value)}
            options={[
              { value: '', label: 'Images and video' },
              { value: 'image', label: 'Images' },
              { value: 'video', label: 'Video' },
            ]}
          />
        </div>
        <div className="toolbar__spacer" />
        <Button size="sm" onClick={() => setApplied(query.trim())}>
          <span className="btn__icon">
            <Icon name="search" size={13} />
          </span>
          Search
        </Button>
        <Button size="sm" variant="primary" onClick={() => setEditing('new')}>
          <span className="btn__icon">
            <Icon name="plus" size={13} />
          </span>
          New preset
        </Button>
      </FilterBar>

      {presets.loading ? <LoadingState label="Reading presets…" /> : null}
      {presets.error ? <ErrorState error={presets.error} onRetry={presets.reload} /> : null}

      {!presets.loading && !presets.error && !list.length ? (
        <EmptyState
          icon="sliders"
          title="No preset yet"
          body="A preset saves a prompt template and the settings around it, so a look you like is one click away next time."
          action={
            <Button variant="primary" onClick={() => setEditing('new')}>
              Create the first preset
            </Button>
          }
        />
      ) : null}

      {list.length ? (
        <div className="grid grid--2">
          {list.map((preset) => (
            <div className="card" key={preset.id}>
              <div className="card__head">
                <span className="card__title">{preset.name}</span>
                <Badge tone="plain">{preset.modality}</Badge>
                {preset.category ? <Badge>{preset.category}</Badge> : null}
                {preset.is_favorite ? <Badge tone="accent">favourite</Badge> : null}
              </div>
              <p className="muted" style={{ margin: 'var(--s2) 0' }}>
                {preset.description ?? 'No description.'}
              </p>
              {preset.thumbnail_url ? (
                <div className="media media--square" style={{ maxWidth: 160 }}>
                  <img src={preset.thumbnail_url} alt={`Preview of ${preset.name}`} loading="lazy" />
                </div>
              ) : (
                <p className="dim" style={{ margin: 0, fontSize: 'var(--fs-xs)' }}>
                  No thumbnail was returned for this preset, so none is shown.
                </p>
              )}
              <p className="mono" style={{ margin: 'var(--s2) 0 0', fontSize: 'var(--fs-xs)' }}>
                {truncate(preset.prompt_template, 140) || 'No prompt template stored.'}
              </p>
              <div className="row row--between" style={{ marginTop: 'var(--s3)' }}>
                <span className="dim" style={{ fontSize: 'var(--fs-xs)' }}>
                  {formatDateTime(preset.created_at)}
                </span>
                <span className="row row--tight">
                  <Button size="sm" onClick={() => setEditing(preset)}>
                    Edit
                  </Button>
                  <Button
                    size="sm"
                    onClick={async () => {
                      try {
                        await presetsApi.favorite(preset.id, !preset.is_favorite);
                        invalidate('presets');
                        presets.reload();
                        setNonce((n) => n + 1);
                      } catch (err) {
                        toasts.push({
                          tone: 'error',
                          title: 'That failed',
                          text: err instanceof Error ? err.message : 'Unknown error',
                        });
                      }
                    }}
                  >
                    {preset.is_favorite ? 'Unfavourite' : 'Favourite'}
                  </Button>
                  <DestructiveAction
                    label="Delete"
                    title={`Delete "${preset.name}"?`}
                    body={<p style={{ margin: 0 }}>The preset is removed from the library. Jobs that used it are not touched.</p>}
                    confirmLabel="Delete preset"
                    onConfirm={async () => {
                      await presetsApi.remove(preset.id);
                      invalidate('presets');
                      presets.reload();
                      toasts.push({ tone: 'info', title: 'Preset deleted', text: preset.name });
                      setNonce((n) => n + 1);
                    }}
                  />
                  <Link
                    to={preset.modality === 'video' ? '/video' : '/image'}
                    className="btn btn--sm"
                    title="Open the studio where this preset can be selected"
                  >
                    Use
                  </Link>
                </span>
              </div>
            </div>
          ))}
        </div>
      ) : null}

      <PresetDialog
        value={editing}
        onClose={() => setEditing(null)}
        onSaved={() => {
          setEditing(null);
          invalidate('presets');
          presets.reload();
          setNonce((n) => n + 1);
        }}
      />
      <span className="visually-hidden" role="status">
        {nonce > 0 ? 'Library updated' : ''}
      </span>
    </div>
  );
}

function PresetDialog({
  value,
  onClose,
  onSaved,
}: {
  value: Preset | 'new' | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [modality, setModality] = useState('image');
  const [category, setCategory] = useState('');
  const [promptTemplate, setPromptTemplate] = useState('');
  const [negativeTemplate, setNegativeTemplate] = useState('');
  const [aspectRatio, setAspectRatio] = useState('');
  const [notes, setNotes] = useState('');
  const [error, setError] = useState<unknown>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (value === null) return;
    if (value === 'new') {
      setName('');
      setDescription('');
      setModality('image');
      setCategory('');
      setPromptTemplate('');
      setNegativeTemplate('');
      setAspectRatio('');
      setNotes('');
    } else {
      setName(value.name);
      setDescription(value.description ?? '');
      setModality(value.modality === 'video' ? 'video' : 'image');
      setCategory(value.category ?? '');
      setPromptTemplate(value.prompt_template ?? '');
      setNegativeTemplate(value.negative_prompt_template ?? '');
      setAspectRatio(value.aspect_ratio ?? '');
      setNotes(value.notes ?? '');
    }
    setError(null);
  }, [value === 'new' ? 'new' : value?.id]);

  const save = async () => {
    setPending(true);
    setError(null);
    const payload = {
      name: name.trim(),
      description: description.trim() || null,
      modality,
      category: category.trim() || null,
      prompt_template: promptTemplate.trim() || null,
      negative_prompt_template: negativeTemplate.trim() || null,
      aspect_ratio: aspectRatio.trim() || null,
      notes: notes.trim() || null,
    };
    try {
      if (value === 'new') await presetsApi.create(payload as Partial<Preset> & { name: string; modality: string });
      else if (value) await presetsApi.patch(value.id, payload as Partial<Preset>);
      onSaved();
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  };

  return (
    <Dialog
      open={value !== null}
      onClose={onClose}
      title={value === 'new' ? 'New preset' : 'Edit preset'}
      footer={
        <>
          <Button onClick={onClose} disabled={pending}>
            Cancel
          </Button>
          <Button variant="primary" onClick={() => void save()} disabled={!name.trim() || pending}>
            {pending ? 'Saving…' : 'Save preset'}
          </Button>
        </>
      }
    >
      <div className="stack">
        <TextInput label="Name" value={name} onChange={(event) => setName(event.target.value)} required />
        <TextInput label="Description" value={description} onChange={(event) => setDescription(event.target.value)} />
        <Select
          label="Modality"
          value={modality}
          onChange={(event) => setModality(event.target.value)}
          options={[
            { value: 'image', label: 'Image' },
            { value: 'video', label: 'Video' },
          ]}
        />
        <TextInput
          label="Category"
          value={category}
          onChange={(event) => setCategory(event.target.value)}
          hint="A free-form grouping label, such as portrait or product."
        />
        <TextArea
          label="Prompt template"
          tall
          value={promptTemplate}
          onChange={(event) => setPromptTemplate(event.target.value)}
          hint="Placeholders are not interpreted by this client; the template is inserted as written."
        />
        <TextArea
          label="Negative prompt template"
          value={negativeTemplate}
          onChange={(event) => setNegativeTemplate(event.target.value)}
        />
        <TextInput
          label="Aspect ratio"
          value={aspectRatio}
          onChange={(event) => setAspectRatio(event.target.value)}
          hint="Applied only when the selected model declares it."
        />
        <TextArea label="Notes" value={notes} onChange={(event) => setNotes(event.target.value)} />
        {error ? <ErrorNotice error={error} /> : null}
      </div>
    </Dialog>
  );
}

function PromptPanel() {
  const toasts = useToasts();
  const [query, setQuery] = useState('');
  const [applied, setApplied] = useState('');
  const [editing, setEditing] = useState<PromptRecord | 'new' | null>(null);
  const [nonce, setNonce] = useState(0);
  const prompts = useQuery<PromptRecord[]>(
    `prompts?${applied}`,
    () => promptsApi.list({ q: applied || undefined }),
    { staleMs: 10_000 },
  );

  const list = prompts.data ?? [];

  return (
    <div className="stack">
      <FilterBar label="Filter saved prompts">
        <div className="toolbar__group" style={{ flex: 1, minWidth: 200 }}>
          <TextInput
            label="Search saved prompts"
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') setApplied(query.trim());
            }}
          />
        </div>
        <div className="toolbar__spacer" />
        <Button size="sm" onClick={() => setApplied(query.trim())}>
          Search
        </Button>
        <Button size="sm" variant="primary" onClick={() => setEditing('new')}>
          New saved prompt
        </Button>
      </FilterBar>

      {prompts.loading ? <LoadingState label="Reading saved prompts…" /> : null}
      {prompts.error ? <ErrorState error={prompts.error} onRetry={prompts.reload} /> : null}

      {!prompts.loading && !prompts.error && !list.length ? (
        <EmptyState
          icon="library"
          title="No saved prompt yet"
          body="Save a prompt you keep retyping. The backend stores it; this client never keeps a copy in the browser."
          action={
            <Button variant="primary" onClick={() => setEditing('new')}>
              Save the first prompt
            </Button>
          }
        />
      ) : null}

      {list.length ? (
        <Section title={`${list.length} saved prompt${list.length === 1 ? '' : 's'}`}>
          <div className="stack">
            {list.map((prompt) => (
              <div className="card" key={prompt.id}>
                <div className="card__head">
                  <span className="card__title">{prompt.title ?? truncate(prompt.text, 48)}</span>
                  <span className="dim" style={{ fontSize: 'var(--fs-xs)' }}>
                    {formatDateTime(prompt.created_at)}
                  </span>
                </div>
                <p style={{ margin: 'var(--s2) 0 0', whiteSpace: 'pre-wrap' }}>{prompt.text}</p>
                {prompt.tags?.length ? (
                  <div className="chip-row" style={{ marginTop: 'var(--s2)' }}>
                    {prompt.tags.map((tag) => (
                      <span className="chip" key={tag}>
                        {tag}
                      </span>
                    ))}
                  </div>
                ) : null}
                <div className="row row--tight" style={{ marginTop: 'var(--s3)' }}>
                  <Button size="sm" onClick={() => setEditing(prompt)}>
                    Edit
                  </Button>
                  <Button
                    size="sm"
                    onClick={async () => {
                      try {
                        await navigator.clipboard.writeText(prompt.text);
                        toasts.push({ tone: 'success', title: 'Copied to the clipboard' });
                      } catch {
                        toasts.push({
                          tone: 'error',
                          title: 'The clipboard refused',
                          text: 'Copy the text from the card instead.',
                        });
                      }
                    }}
                  >
                    Copy
                  </Button>
                  <DestructiveAction
                    label="Delete"
                    title="Delete this saved prompt?"
                    body={<p style={{ margin: 0 }}>The record is removed. Jobs that used the text are untouched.</p>}
                    confirmLabel="Delete prompt"
                    onConfirm={async () => {
                      await promptsApi.remove(prompt.id);
                      invalidate('prompts');
                      prompts.reload();
                      setNonce((n) => n + 1);
                    }}
                  />
                </div>
              </div>
            ))}
          </div>
        </Section>
      ) : null}

      <PromptDialog
        value={editing}
        onClose={() => setEditing(null)}
        onSaved={() => {
          setEditing(null);
          invalidate('prompts');
          prompts.reload();
          setNonce((n) => n + 1);
        }}
      />
      <span className="visually-hidden" role="status">
        {nonce > 0 ? 'Saved prompts updated' : ''}
      </span>
    </div>
  );
}

function PromptDialog({
  value,
  onClose,
  onSaved,
}: {
  value: PromptRecord | 'new' | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [title, setTitle] = useState('');
  const [text, setText] = useState('');
  const [negative, setNegative] = useState('');
  const [tags, setTags] = useState<string[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (value === null) return;
    if (value === 'new') {
      setTitle('');
      setText('');
      setNegative('');
      setTags([]);
    } else {
      setTitle(value.title ?? '');
      setText(value.text);
      setNegative(value.negative_prompt ?? '');
      setTags(value.tags ?? []);
    }
    setError(null);
  }, [value === 'new' ? 'new' : value?.id]);

  const save = async () => {
    setPending(true);
    setError(null);
    try {
      if (value === 'new') {
        await promptsApi.create({
          text: text.trim(),
          title: title.trim() || undefined,
          negative_prompt: negative.trim() || undefined,
          tags: tags.length ? tags : undefined,
        });
      } else if (value) {
        await promptsApi.patch(value.id, {
          title: title.trim() || null,
          text: text.trim(),
          negative_prompt: negative.trim() || null,
          tags,
        });
      }
      onSaved();
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  };

  return (
    <Dialog
      open={value !== null}
      onClose={onClose}
      title={value === 'new' ? 'New saved prompt' : 'Edit saved prompt'}
      footer={
        <>
          <Button onClick={onClose} disabled={pending}>
            Cancel
          </Button>
          <Button
            variant="primary"
            onClick={() => void save()}
            disabled={!text.trim() || pending}
          >
            {pending ? 'Saving…' : 'Save prompt'}
          </Button>
        </>
      }
    >
      <div className="stack">
        <TextInput label="Title" value={title} onChange={(event) => setTitle(event.target.value)} />
        <TextArea label="Prompt" tall value={text} onChange={(event) => setText(event.target.value)} required />
        <TextArea
          label="Negative prompt"
          value={negative}
          onChange={(event) => setNegative(event.target.value)}
        />
        <TagInput label="Tags" values={tags} onChange={setTags} />
        {error ? <ErrorNotice error={error} /> : null}
      </div>
    </Dialog>
  );
}
