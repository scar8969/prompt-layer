import { useEffect, useState } from 'react';
import { assets as assetsApi, characters as charactersApi } from '../../api/endpoints.ts';
import type { Asset, Character, CharacterReference, Model } from '../../api/types.ts';
import { Badge, MockBadge } from '../../components/Badge.tsx';
import { Button } from '../../components/Button.tsx';
import { Dialog } from '../../components/Dialog.tsx';
import { Select, TextArea, TextInput } from '../../components/Field.tsx';
import { ErrorNotice } from '../../components/ErrorNotice.tsx';
import { EmptyState, ErrorState, LoadingState } from '../../components/State.tsx';
import { AssetImage } from '../../components/Media.tsx';
import { DestructiveAction } from '../../components/Confirm.tsx';
import { TagInput } from '../../components/Controls.tsx';
import { invalidate, useQuery } from '../../state/query.ts';
import { useToasts } from '../../state/toasts.tsx';
import { useModels } from '../../state/models.ts';
import { formatDateTime, truncate } from '../../lib/util.ts';

/**
 * Character Library. A character is a named bundle of description, attributes,
 * a default prompt and reference assets. Nothing is inferred: the attributes
 * map and the references are exactly what the backend stores.
 */
export function CharactersScreen() {
  const toasts = useToasts();
  const characters = useQuery<Character[]>('characters', () => charactersApi.list(), { staleMs: 10_000 });
  const [editing, setEditing] = useState<Character | 'new' | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);

  const list = characters.data ?? [];

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__heading">Character Library</h1>
          <p className="page__subtitle">
            Keep a person, creature or product consistent across generations: one description, one set of attributes,
            one default prompt, and the reference assets that anchor it.
          </p>
        </div>
        <div className="page__actions">
          <Button size="sm" onClick={characters.reload}>
            Refresh
          </Button>
          <Button size="sm" variant="primary" onClick={() => setEditing('new')}>
            New character
          </Button>
        </div>
      </div>

      {characters.loading ? <LoadingState label="Reading characters…" /> : null}
      {characters.error ? <ErrorState error={characters.error} onRetry={characters.reload} /> : null}

      {!characters.loading && !characters.error && !list.length ? (
        <EmptyState
          icon="person"
          title="No character yet"
          body="A character records what makes someone recognisable — build, wardrobe, distinguishing features — so the description can be reused instead of retyped."
          action={
            <Button variant="primary" onClick={() => setEditing('new')}>
              Create the first character
            </Button>
          }
        />
      ) : null}

      {list.length ? (
        <div className="grid grid--2">
          {list.map((character) => (
            <div className="card" key={character.id}>
              <div className="card__head">
                <span className="card__title">{character.name}</span>
                {character.reference_count ? (
                  <Badge tone="plain">{character.reference_count} refs</Badge>
                ) : (
                  <Badge tone="plain">no references</Badge>
                )}
              </div>
              <p className="muted" style={{ margin: 'var(--s2) 0 0' }}>
                {character.description ?? 'No description.'}
              </p>
              {character.default_prompt ? (
                <p className="mono" style={{ margin: 'var(--s2) 0 0', fontSize: 'var(--fs-xs)' }}>
                  {truncate(character.default_prompt, 160)}
                </p>
              ) : null}
              {character.tags?.length ? (
                <div className="chip-row" style={{ marginTop: 'var(--s2)' }}>
                  {character.tags.map((tag) => (
                    <span className="chip" key={tag}>
                      {tag}
                    </span>
                  ))}
                </div>
              ) : null}
              <div className="row row--between" style={{ marginTop: 'var(--s3)' }}>
                <span className="dim" style={{ fontSize: 'var(--fs-xs)' }}>
                  {formatDateTime(character.created_at)}
                </span>
                <span className="row row--tight">
                  <Button size="sm" onClick={() => setOpenId(character.id)}>
                    References
                  </Button>
                  <Button size="sm" onClick={() => setEditing(character)}>
                    Edit
                  </Button>
                  <DestructiveAction
                    label="Delete"
                    title={`Delete "${character.name}"?`}
                    body={<p style={{ margin: 0 }}>The character record is removed. Reference assets are not deleted.</p>}
                    confirmLabel="Delete character"
                    onConfirm={async () => {
                      await charactersApi.remove(character.id);
                      invalidate('characters');
                      characters.reload();
                      toasts.push({ tone: 'info', title: 'Character deleted', text: character.name });
                    }}
                  />
                </span>
              </div>
            </div>
          ))}
        </div>
      ) : null}

      <CharacterDialog
        value={editing}
        onClose={() => setEditing(null)}
        onSaved={() => {
          setEditing(null);
          invalidate('characters');
          characters.reload();
        }}
      />

      <ReferenceDialog
        characterId={openId}
        onClose={() => {
          setOpenId(null);
          invalidate('characters');
          characters.reload();
        }}
      />
    </div>
  );
}

function CharacterDialog({
  value,
  onClose,
  onSaved,
}: {
  value: Character | 'new' | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const models = useModels({ modality: 'image' });
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [defaultPrompt, setDefaultPrompt] = useState('');
  const [preferredModel, setPreferredModel] = useState('');
  const [tags, setTags] = useState<string[]>([]);
  const [attributes, setAttributes] = useState<{ key: string; value: string }[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (value === null) return;
    if (value === 'new') {
      setName('');
      setDescription('');
      setDefaultPrompt('');
      setPreferredModel('');
      setTags([]);
      setAttributes([]);
    } else {
      setName(value.name);
      setDescription(value.description ?? '');
      setDefaultPrompt(value.default_prompt ?? '');
      setPreferredModel(value.preferred_model_id ?? '');
      setTags(value.tags ?? []);
      setAttributes(
        Object.entries(value.attributes ?? {}).map(([key, attribute]) => ({ key, value: attribute })),
      );
    }
    setError(null);
  }, [value === 'new' ? 'new' : value?.id]);

  const save = async () => {
    setPending(true);
    setError(null);
    const attributeMap: Record<string, string> = {};
    for (const entry of attributes) {
      if (entry.key.trim() && entry.value.trim()) attributeMap[entry.key.trim()] = entry.value.trim();
    }
    try {
      const payload = {
        name: name.trim(),
        description: description.trim() || undefined,
        default_prompt: defaultPrompt.trim() || undefined,
        preferred_model_id: preferredModel || null,
        tags: tags.length ? tags : undefined,
        attributes: Object.keys(attributeMap).length ? attributeMap : undefined,
      };
      if (value === 'new') await charactersApi.create(payload);
      else if (value) await charactersApi.patch(value.id, payload as Partial<Character>);
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
      title={value === 'new' ? 'New character' : 'Edit character'}
      width="wide"
      footer={
        <>
          <Button onClick={onClose} disabled={pending}>
            Cancel
          </Button>
          <Button variant="primary" onClick={() => void save()} disabled={!name.trim() || pending}>
            {pending ? 'Saving…' : 'Save character'}
          </Button>
        </>
      }
    >
      <div className="grid grid--2">
        <div className="stack">
          <TextInput label="Name" value={name} onChange={(event) => setName(event.target.value)} required />
          <TextArea
            label="Description"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            hint="Who this is, in the words you would repeat in every prompt."
          />
          <TextArea
            label="Default prompt fragment"
            tall
            value={defaultPrompt}
            onChange={(event) => setDefaultPrompt(event.target.value)}
            hint="The client does not auto-insert this. Use it as a copy source in the studio."
          />
          <Select
            label="Preferred model"
            value={preferredModel}
            onChange={(event) => setPreferredModel(event.target.value)}
            placeholder="No preference"
            options={(models.data ?? []).map((model: Model) => ({ value: model.id, label: model.display_name }))}
            hint="A preference only — the studios still require you to pick a model."
          />
          <TagInput label="Tags" values={tags} onChange={setTags} />
        </div>

        <div className="stack stack--tight">
          <span className="field__label">Attributes</span>
          <p className="field__hint" style={{ margin: 0 }}>
            Free-form key/value pairs, stored exactly as written: build, wardrobe, age, distinguishing marks.
          </p>
          {attributes.map((entry, index) => (
            <div className="row row--tight" key={index}>
              <input
                className="input"
                placeholder="key"
                aria-label={`Attribute ${index + 1} key`}
                value={entry.key}
                onChange={(event) => {
                  const next = [...attributes];
                  next[index] = { ...next[index], key: event.target.value };
                  setAttributes(next);
                }}
              />
              <input
                className="input"
                placeholder="value"
                aria-label={`Attribute ${index + 1} value`}
                value={entry.value}
                onChange={(event) => {
                  const next = [...attributes];
                  next[index] = { ...next[index], value: event.target.value };
                  setAttributes(next);
                }}
              />
              <Button
                size="sm"
                onClick={() => setAttributes(attributes.filter((_, position) => position !== index))}
              >
                Remove
              </Button>
            </div>
          ))}
          <Button size="sm" onClick={() => setAttributes([...attributes, { key: '', value: '' }])}>
            Add attribute
          </Button>
        </div>
      </div>
      {error ? <ErrorNotice error={error} /> : null}
    </Dialog>
  );
}

function ReferenceDialog({ characterId, onClose }: { characterId: string | null; onClose: () => void }) {
  const toasts = useToasts();
  const [character, setCharacter] = useState<Character | null>(null);
  const [references, setReferences] = useState<CharacterReference[]>([]);
  const [assetsList, setAssetsList] = useState<Asset[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);
  const [chosen, setChosen] = useState('');
  const [role, setRole] = useState('reference');

  useEffect(() => {
    if (!characterId) return;
    let cancelled = false;
    setLoading(true);
    void (async () => {
      try {
        const [bundle, assetPage] = await Promise.all([
          charactersApi.get(characterId),
          assetsApi.list({ kind: 'image', limit: 60 }),
        ]);
        if (cancelled) return;
        setCharacter(bundle.character);
        setReferences(bundle.references);
        setAssetsList(assetPage.assets);
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
  }, [characterId]);

  if (!characterId) return null;

  const add = async () => {
    if (!chosen) return;
    try {
      const updated = await charactersApi.addReference(characterId, { asset_id: chosen, role });
      setCharacter(updated);
      const bundle = await charactersApi.get(characterId);
      setReferences(bundle.references);
      setChosen('');
      toasts.push({ tone: 'success', title: 'Reference attached' });
    } catch (err) {
      setError(err);
    }
  };

  return (
    <Dialog open title={`References — ${character?.name ?? 'character'}`} onClose={onClose} width="wide">
      {loading ? <LoadingState label="Reading references…" /> : null}
      {error ? <ErrorNotice error={error} /> : null}

      {references.length ? (
        <div className="asset-grid">
          {references.map((reference) => {
            const asset = assetsList.find((item) => item.id === reference.asset_id);
            return (
              <div className="asset-tile" key={reference.asset_id + reference.role}>
                <div className="asset-tile__media">
                  <AssetImage
                    src={reference.url ?? asset?.thumbnail_url ?? asset?.content_url ?? null}
                    alt={asset?.name ?? reference.asset_id}
                  />
                </div>
                <div className="asset-tile__body">
                  <span className="asset-tile__name">{asset?.name ?? reference.asset_id}</span>
                  <span className="asset-tile__meta">{reference.role}</span>
                  <MockBadge isMock={asset?.is_mock} />
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <p className="muted" style={{ margin: 0 }}>
          No reference asset is attached to this character yet.
        </p>
      )}

      <div className="row row--tight" style={{ marginTop: 'var(--s3)' }}>
        <Select
          label="Asset to attach"
          value={chosen}
          onChange={(event) => setChosen(event.target.value)}
          placeholder="Choose an image asset"
          options={assetsList.map((asset) => ({ value: asset.id, label: asset.name }))}
        />
        <TextInput
          label="Role"
          value={role}
          onChange={(event) => setRole(event.target.value)}
          hint="For example face, body or outfit."
        />
        <Button variant="primary" onClick={() => void add()} disabled={!chosen}>
          Attach
        </Button>
      </div>
    </Dialog>
  );
}
