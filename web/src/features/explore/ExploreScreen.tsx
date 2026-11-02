import { useEffect, useMemo, useState } from 'react';
import { presets as presetsApi } from '../../api/endpoints.ts';
import type { Preset } from '../../api/types.ts';
import { Link, useRouter } from '../../app/router.tsx';
import { Icon } from '../../components/Icon.tsx';
import { useQuery } from '../../state/query.ts';
import { useToasts } from '../../state/toasts.tsx';
import { titleCase } from '../../lib/util.ts';
import { PresetArt, PresetArtBadge, RenderPreviewButton } from '../presets/PresetPreview.tsx';

/**
 * Explore.
 *
 * A wall of looks, the way the reference product has one — except every tile
 * here is a **style card**: vector art generated from that preset's own grade
 * words and framing words. No photograph, no model call, nothing pretending to
 * be a render. The label on the tile says so, and the detail panel repeats it
 * next to the prompt that would actually produce the frame.
 *
 * One query per modality, merged here, because the catalogue is two documents
 * (image and video) and the gallery is one wall.
 */

type Modality = 'all' | 'image' | 'video';

/**
 * Masonry, in reading order.
 *
 * CSS columns fill down one column before the next, so the first twelve looks
 * would all sit in the first column and the wall would read wrong. This splits
 * the list greedily by estimated height instead: each card lands in whichever
 * column is currently shortest, in DOM order, which is the arrangement a
 * contact sheet actually wants.
 */
function useColumnCount(): number {
  const [count, setCount] = useState(() => columnsFor(typeof window === 'undefined' ? 1440 : window.innerWidth));
  useEffect(() => {
    const onResize = () => setCount(columnsFor(window.innerWidth));
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  return count;
}

function columnsFor(width: number): number {
  if (width < 720) return 2;
  if (width < 1180) return 3;
  if (width < 1600) return 4;
  return 5;
}

function splitIntoColumns(items: Preset[], count: number): Preset[][] {
  const columns: Preset[][] = Array.from({ length: count }, () => []);
  const heights = new Array(count).fill(0);
  for (const preset of items) {
    let target = 0;
    for (let index = 1; index < count; index += 1) {
      if (heights[index] < heights[target]) target = index;
    }
    columns[target].push(preset);
    heights[target] += 1 / ratioValue(preset.aspect_ratio ?? (preset.modality === 'video' ? '16:9' : '4:5'));
  }
  return columns;
}

function ratioValue(raw: string): number {
  const [w, h] = raw.split(':').map((value) => Number(value));
  if (!w || !h) return 1;
  return w / h;
}

export function ExploreScreen() {
  const { navigate } = useRouter();
  const toasts = useToasts();
  const [modality, setModality] = useState<Modality>('all');
  const [category, setCategory] = useState('');
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState<'curated' | 'az'>('curated');
  const [open, setOpen] = useState<Preset | null>(null);

  const images = useQuery<Preset[]>('presets?modality=image', () => presetsApi.list({ modality: 'image' }), {
    staleMs: 60_000,
  });
  const videos = useQuery<Preset[]>('presets?modality=video', () => presetsApi.list({ modality: 'video' }), {
    staleMs: 60_000,
  });

  const all = useMemo(() => [...(images.data ?? []), ...(videos.data ?? [])], [images.data, videos.data]);
  const loading = images.loading || videos.loading;
  const error = images.error ?? videos.error;

  // The open dialog should follow the reloaded catalogue: after a render the
  // preset it is showing has new art, and the dialog must not keep the old copy.
  useEffect(() => {
    setOpen((current) => (current ? all.find((item) => item.id === current.id) ?? current : current));
  }, [all]);

  const categories = useMemo(() => {
    const counts = new Map<string, number>();
    for (const preset of all) {
      if (modality !== 'all' && preset.modality !== modality) continue;
      const key = preset.category ?? 'general';
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return [...counts.entries()]
      .map(([key, count]) => ({ key, label: titleCase(key), count }))
      .sort((a, b) => a.label.localeCompare(b.label));
  }, [all, modality]);

  // A category that no longer exists under the new modality would silently
  // empty the grid; it is cleared when the visible set stops containing it.
  useEffect(() => {
    if (category && !categories.some((entry) => entry.key === category)) setCategory('');
  }, [categories, category]);

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const list = all.filter((preset) => {
      if (modality !== 'all' && preset.modality !== modality) return false;
      if (category && (preset.category ?? 'general') !== category) return false;
      if (!needle) return true;
      const haystack = `${preset.name} ${preset.description ?? ''} ${preset.prompt_template ?? ''} ${
        preset.category ?? ''
      }`.toLowerCase();
      return haystack.includes(needle);
    });
    return sort === 'az' ? [...list].sort((a, b) => a.name.localeCompare(b.name)) : list;
  }, [all, modality, category, query, sort]);

  const columns = useColumnCount();
  const layout = useMemo(() => splitIntoColumns(visible, columns), [visible, columns]);

  const openStudio = (preset: Preset, run: boolean) => {
    const base = preset.modality === 'video' ? '/video' : '/image';
    navigate(`${base}?preset=${encodeURIComponent(preset.id)}${run ? '&go=1' : ''}`);
  };

  const copyPrompt = async (preset: Preset) => {
    try {
      await navigator.clipboard.writeText(preset.prompt_template ?? '');
      toasts.push({ tone: 'success', title: 'Prompt copied', text: preset.name });
    } catch {
      toasts.push({ tone: 'error', title: 'The clipboard refused', text: 'Open the look and copy from there.' });
    }
  };

  return (
    <div>
      <div className="hf-page-head">
        <div>
          <p className="hf-eyebrow">Explore · {all.length} looks</p>
          <h1 className="hf-display">Start from a look.</h1>
          <p className="hf-lede">
            Every tile is a sourced shot brief — the light, the glass, the grade, the finish — ready to run on any
            model you have connected. Previews are style cards drawn from each preset's own words, not renders.
          </p>
        </div>
        <div className="hf-search" style={{ minWidth: 280 }}>
          <Icon name="search" size={15} />
          <input
            type="search"
            value={query}
            placeholder="Search looks, light, glass…"
            aria-label="Search looks"
            onChange={(event) => setQuery(event.target.value)}
          />
          {query ? (
            <button type="button" className="hf-btn hf-btn--ghost hf-btn--sm" onClick={() => setQuery('')}>
              <Icon name="close" size={13} />
            </button>
          ) : null}
        </div>
      </div>

      <div
        style={{
          display: 'flex',
          gap: 12,
          alignItems: 'center',
          flexWrap: 'wrap',
          marginBottom: 10,
          justifyContent: 'space-between',
        }}
      >
        <div className="hf-seg" role="group" aria-label="Filter by modality">
          {(['all', 'image', 'video'] as Modality[]).map((value) => (
            <button key={value} type="button" aria-pressed={modality === value} onClick={() => setModality(value)}>
              {value === 'all' ? 'Everything' : value === 'image' ? 'Images' : 'Video'}
            </button>
          ))}
        </div>

        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span className="hf-mono" style={{ fontSize: 11, color: 'var(--text-dim)' }}>
            {visible.length} shown
          </span>
          <button
            type="button"
            className="hf-chip"
            aria-pressed={sort === 'az'}
            onClick={() => setSort(sort === 'az' ? 'curated' : 'az')}
            title="Sort the wall"
          >
            <Icon name="filter" size={12} /> {sort === 'az' ? 'A–Z' : 'Curated'}
          </button>
        </div>
      </div>

      <div className="hf-chips" role="group" aria-label="Filter by category">
        <button type="button" className="hf-chip" aria-pressed={category === ''} onClick={() => setCategory('')}>
          All looks <span className="hf-chip__count">{all.filter((p) => modality === 'all' || p.modality === modality).length}</span>
        </button>
        {categories.map((entry) => (
          <button
            key={entry.key}
            type="button"
            className="hf-chip"
            aria-pressed={category === entry.key}
            onClick={() => setCategory(entry.key)}
          >
            {entry.label} <span className="hf-chip__count">{entry.count}</span>
          </button>
        ))}
      </div>

      {loading ? (
        <div className="hf-gallery" aria-busy="true">
          {[0, 1, 2, 3, 4, 5, 6, 7].map((index) => (
            <div
              key={index}
              className="hf-skeleton"
              style={{ height: [320, 240, 380, 280, 300, 260, 360, 230][index] }}
            />
          ))}
        </div>
      ) : null}

      {error ? (
        <div className="hf-empty" style={{ marginTop: 20 }}>
          <p style={{ margin: 0 }}>The catalogue could not be read.</p>
          <button
            type="button"
            className="hf-btn hf-btn--primary"
            style={{ marginTop: 14 }}
            onClick={() => {
              images.reload();
              videos.reload();
            }}
          >
            Try again
          </button>
        </div>
      ) : null}

      {!loading && !error && visible.length ? (
        <div className="hf-gallery">
          {layout.map((column, index) => (
            <div className="hf-col" key={index}>
              {column.map((preset) => (
                <LookCard
                  key={preset.id}
                  preset={preset}
                  onOpen={() => setOpen(preset)}
                  onUse={() => openStudio(preset, false)}
                  onRun={() => openStudio(preset, true)}
                  onCopy={() => void copyPrompt(preset)}
                />
              ))}
            </div>
          ))}
        </div>
      ) : null}

      {!loading && !error && !visible.length ? (
        <div className="hf-empty" style={{ marginTop: 20 }}>
          <p style={{ margin: 0 }}>
            Nothing matches {query ? `“${query}”` : 'this filter'}. The catalogue has {all.length} looks in total.
          </p>
          <button
            type="button"
            className="hf-btn"
            style={{ marginTop: 14 }}
            onClick={() => {
              setQuery('');
              setCategory('');
              setModality('all');
            }}
          >
            Clear the filters
          </button>
        </div>
      ) : null}

      {open ? (
        <LookDetail
          preset={open}
          onClose={() => setOpen(null)}
          onUse={() => openStudio(open, false)}
          onRun={() => openStudio(open, true)}
          onCopy={() => void copyPrompt(open)}
          onRendered={() => {
            images.reload();
            videos.reload();
          }}
        />
      ) : null}
    </div>
  );
}

/** What the tile is actually showing, said plainly. */
function previewKind(preset: Preset): string {
  return preset.preview?.status === 'completed' ? 'your render' : 'style card';
}

/** The frame's proportions, so the wall reads like a contact sheet. */
function aspectOf(preset: Preset): string {  const raw = preset.aspect_ratio ?? (preset.modality === 'video' ? '16:9' : '4:5');
  const [w, h] = raw.split(':').map((value) => Number(value));
  if (!w || !h) return preset.modality === 'video' ? '16 / 9' : '4 / 5';
  return `${w} / ${h}`;
}

function LookCard({
  preset,
  onOpen,
  onUse,
  onRun,
  onCopy,
}: {
  preset: Preset;
  onOpen: () => void;
  onUse: () => void;
  onRun: () => void;
  onCopy: () => void;
}) {
  return (
    <article className="hf-card" onClick={onOpen} role="button" tabIndex={0} onKeyDown={(event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        onOpen();
      }
    }}>
      <div className="hf-card__media" style={{ aspectRatio: aspectOf(preset) }}>
        <PresetArt preset={preset} alt={`${preset.name} — ${previewKind(preset)}`} />
        <span className="hf-card__scrim" />
        <span className="hf-card__tag">
          <Icon name={preset.modality === 'video' ? 'video' : 'image'} size={11} />
          {preset.modality === 'video' ? 'motion' : 'still'}
        </span>
        <PresetArtBadge preset={preset} />
        <span className="hf-card__actions">
          <button
            type="button"
            className="hf-iconbtn"
            title="Copy this prompt"
            onClick={(event) => {
              event.stopPropagation();
              onCopy();
            }}
          >
            <Icon name="copy" size={13} />
          </button>
          <button
            type="button"
            className="hf-iconbtn"
            title="Open in the studio"
            onClick={(event) => {
              event.stopPropagation();
              onUse();
            }}
          >
            <Icon name="external" size={13} />
          </button>
        </span>
        <span className="hf-card__use">
          <button
            type="button"
            className="hf-btn hf-btn--sm hf-btn--primary"
            onClick={(event) => {
              event.stopPropagation();
              onRun();
            }}
          >
            <Icon name="sparkle" size={13} /> Generate
          </button>
          <button
            type="button"
            className="hf-btn hf-btn--sm"
            onClick={(event) => {
              event.stopPropagation();
              onUse();
            }}
          >
            Open
          </button>
        </span>
      </div>
      <div className="hf-card__overlay">
        <div style={{ minWidth: 0 }}>
          <span className="hf-card__title">{preset.name}</span>
          <span className="hf-card__sub">
            {[titleCase(preset.category ?? ''), preset.aspect_ratio].filter(Boolean).join(' · ')}
          </span>
        </div>
      </div>
    </article>
  );
}

function LookDetail({
  preset,
  onClose,
  onUse,
  onRun,
  onCopy,
  onRendered,
}: {
  preset: Preset;
  onClose: () => void;
  onUse: () => void;
  onRun: () => void;
  onCopy: () => void;
  onRendered: () => void;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const camera = preset.camera ?? {};
  const cameraText = Object.entries(camera)
    .map(([key, value]) => `${titleCase(key.replace(/_/g, ' '))}: ${String(value)}`)
    .join(' · ');

  return (
    <div className="hf-modal" role="dialog" aria-modal="true" aria-label={preset.name} onClick={onClose}>
      <div className="hf-modal__panel" onClick={(event) => event.stopPropagation()}>
        <div className="hf-modal__art">
          <PresetArt preset={preset} alt={`${preset.name} — ${previewKind(preset)}`} />
          <button type="button" className="hf-iconbtn hf-modal__close" onClick={onClose} aria-label="Close">
            <Icon name="close" size={14} />
          </button>
        </div>

        <div className="hf-modal__body">
          <div>
            <p className="hf-eyebrow" style={{ marginBottom: 8 }}>
              {titleCase(preset.category ?? '')} · {preset.modality === 'video' ? 'video' : 'image'} ·{' '}
              {previewKind(preset)}
            </p>
            <h2 className="hf-display hf-display--md" style={{ marginBottom: 10 }}>
              {preset.name}
            </h2>
            {preset.description ? (
              <p style={{ color: 'var(--text-muted)', margin: 0, lineHeight: 1.6, fontSize: 13.5 }}>
                {preset.description}
              </p>
            ) : null}
          </div>

          <div className="row row--tight" style={{ flexWrap: 'wrap', gap: 8 }}>
            <button type="button" className="hf-btn hf-btn--primary" onClick={onRun}>
              <Icon name="sparkle" size={14} /> Generate with this look
            </button>
            <button type="button" className="hf-btn" onClick={onUse}>
              Open in {preset.modality === 'video' ? 'Video' : 'Image'} Studio
            </button>
            <button type="button" className="hf-btn" onClick={onCopy}>
              <Icon name="copy" size={13} /> Copy prompt
            </button>
            <Link to={`/cinema?look=${encodeURIComponent(preset.id)}`} className="hf-btn">
              <Icon name="clapper" size={13} /> Shape it
            </Link>
            <RenderPreviewButton preset={preset} onRendered={onRendered} />
          </div>

          <div>
            <p className="hf-panel__title" style={{ marginBottom: 8 }}>
              The prompt
            </p>
            <pre className="hf-prompt">{preset.prompt_template || 'This preset carries no prompt template.'}</pre>
            {preset.negative_prompt_template ? (
              <>
                <p className="hf-panel__title" style={{ margin: '14px 0 8px' }}>
                  Negative
                </p>
                <pre className="hf-prompt" style={{ color: '#a9a49a' }}>
                  {preset.negative_prompt_template}
                </pre>
              </>
            ) : null}
          </div>

          <dl className="hf-kv">
            <dt>Frame</dt>
            <dd>{preset.aspect_ratio ?? 'model default'}</dd>
            <dt>Settings</dt>
            <dd>
              {preset.parameters
                ? Object.entries(preset.parameters)
                    .filter(([key]) => key !== 'aspect_ratio')
                    .map(([key, value]) => `${key.replace(/_/g, ' ')} ${String(value)}`)
                    .join(' · ') || 'none'
                : 'none'}
            </dd>
            {cameraText ? (
              <>
                <dt>Camera</dt>
                <dd>{cameraText}</dd>
              </>
            ) : null}
            <dt>Tags</dt>
            <dd>{(preset.tags ?? []).join(' · ') || '—'}</dd>
            <dt>About the art</dt>
            <dd style={{ color: 'var(--text-dim)' }}>
              {preset.preview?.status === 'completed'
                ? 'A real frame rendered on this workspace’s own provider key. The model and the date are recorded with the job.'
                : 'A style card generated from this preset’s own words. It is not a render, and it does not predict one.'}
            </dd>
          </dl>
        </div>
      </div>
    </div>
  );
}
