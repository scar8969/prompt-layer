import { useEffect, useId, useState } from 'react';
import { models as modelsApi, presets as presetsApi } from '../../api/endpoints.ts';
import type { Job, Model, Preset } from '../../api/types.ts';
import { Dialog } from '../../components/Dialog.tsx';
import { Button } from '../../components/Button.tsx';
import { Icon } from '../../components/Icon.tsx';
import { useJobWatch, isTerminalState } from '../../state/jobs.ts';
import { invalidate, useQuery } from '../../state/query.ts';
import { useToasts } from '../../state/toasts.tsx';
import { ModelPicker } from '../studio/ModelPicker.tsx';

/**
 * A preset's tile, telling the truth about what it is showing.
 *
 * When a workspace has spent its own key on a render, the real frame is shown and
 * labelled "your render". Otherwise the deterministic style card is shown and
 * labelled as one. There is no third state where a placeholder is dressed up as
 * a prediction.
 */
export function PresetArt({
  preset,
  alt,
  className,
  style,
}: {
  preset: Preset;
  alt: string;
  className?: string;
  style?: React.CSSProperties;
}) {
  const rendered = preset.preview?.status === 'completed' ? preset.preview.thumbnail_url : null;
  const src = rendered ?? preset.thumbnail_url ?? null;
  if (!src) return null;
  return (
    <img
      src={src}
      alt={alt}
      loading="lazy"
      className={className}
      style={style}
      data-preview={rendered ? 'render' : 'style-card'}
    />
  );
}

/** A small, truthful badge for the tile corner. */
export function PresetArtBadge({ preset }: { preset: Preset }) {
  const preview = preset.preview;
  if (preview && preview.status === 'completed') {
    return (
      <span className="hf-card__tag" style={{ left: 'auto', right: 10 }} title="Rendered with your own provider key">
        <Icon name="sparkle" size={11} /> your render
      </span>
    );
  }
  if (preview && (preview.status === 'queued' || preview.status === 'processing' || preview.status === 'submitting' || preview.status === 'downloading' || preview.status === 'validating')) {
    return (
      <span className="hf-card__tag" style={{ left: 'auto', right: 10 }}>
        <Icon name="clock" size={11} /> rendering
      </span>
    );
  }
  if (preview && (preview.status === 'failed' || preview.status === 'cancelled' || preview.status === 'timed_out')) {
    return (
      <span className="hf-card__tag" style={{ left: 'auto', right: 10 }} title={preview.error || 'The render failed.'}>
        <Icon name="alert" size={11} /> render failed
      </span>
    );
  }
  return null;
}

/**
 * Queue one real render for a preset on a model the workspace connected.
 *
 * The model is never chosen for the user: a preview spends their money, so the
 * picker is part of the action. The dialog stays open while the queue advances
 * and closes itself when the job reaches a terminal state, because a render can
 * take minutes and the user should be able to leave the modal up and watch.
 */
export function RenderPreviewButton({
  preset,
  onRendered,
  label = 'Render preview',
  size = 'sm',
}: {
  preset: Preset;
  onRendered?: () => void;
  label?: string;
  size?: 'sm' | 'md';
}) {
  const toasts = useToasts();
  const [open, setOpen] = useState(false);
  const [modelId, setModelId] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState<Job | null>(null);
  const [error, setError] = useState<unknown>(null);
  const { job } = useJobWatch(submitted);
  const headingId = useId();

  const models = useQuery<Model[]>(
    `models?modality=${preset.modality}&enabled=1`,
    () => modelsApi.list({ modality: preset.modality, enabled: true }),
    { staleMs: 30_000, enabled: open },
  );

  // Models that cannot render this modality are filtered by the picker already,
  // but a preset can be an image look and the workspace can have no image model.
  const eligible = (models.data ?? []).filter((model) => model.modality === preset.modality);

  useEffect(() => {
    if (!job || !isTerminalState(job.state)) return;
    if (job.state === 'succeeded') {
      invalidate('presets');
      toasts.push({
        tone: 'success',
        title: 'Preview rendered',
        text: `${preset.name} now shows a real frame from your key.`,
      });
      onRendered?.();
      setOpen(false);
      setSubmitted(null);
    } else if (job.state === 'failed') {
      setError(new Error(job.error?.message || 'The provider did not produce a frame.'));
      setSubmitted(null);
    }
  }, [job, onRendered, preset.name, toasts]);

  const render = async () => {
    if (!modelId) return;
    setError(null);
    try {
      const created = await presetsApi.renderPreview(preset.id, modelId);
      setSubmitted(created);
      toasts.push({
        tone: 'info',
        title: 'Preview queued',
        text: 'The render runs through the same queue as any other generation.',
      });
    } catch (err) {
      setError(err);
    }
  };

  const busy = submitted !== null && !isTerminalState(submitted.state);

  return (
    <>
      <button
        type="button"
        className={`hf-btn${size === 'sm' ? ' hf-btn--sm' : ''}`}
        onClick={(event) => {
          event.stopPropagation();
          setOpen(true);
        }}
        title="Spend your own provider key on one real frame for this preset"
      >
        <Icon name="sparkle" size={13} /> {label}
      </button>

      <Dialog open={open} onClose={() => setOpen(false)} title="Render this preset" width="narrow">
        <div className="stack" aria-labelledby={headingId}>
          <p style={{ margin: 0, color: 'var(--text-muted)' }} id={headingId}>
            This produces <strong>one real frame</strong> for <em>{preset.name}</em>, using your own
            provider account. The platform does not host a model and does not pay for the render.
          </p>

          <ModelPicker
            models={eligible}
            value={modelId}
            onChange={setModelId}
            modality={preset.modality === 'video' ? 'video' : 'image'}
            disabled={busy}
            disabledReason={busy ? 'A render is already in progress.' : null}
          />

          {models.loading ? <p className="dim">Reading the model registry…</p> : null}
          {!models.loading && !eligible.length ? (
            <p className="dim" style={{ margin: 0 }}>
              No connected {preset.modality} model is registered. Add one on API Connections first.
            </p>
          ) : null}

          {error ? (
            <p style={{ margin: 0, color: 'var(--danger)' }}>
              {error instanceof Error ? error.message : 'The render could not be queued.'}
            </p>
          ) : null}

          {submitted ? (
            <p className="dim" style={{ margin: 0 }} aria-live="polite">
              Job {submitted.id} · {job?.state ?? submitted.state}. The queue reports the state; nothing
              here guesses progress.
            </p>
          ) : null}
        </div>

        <div className="row row--tight" style={{ marginTop: 'var(--s4)', justifyContent: 'flex-end' }}>
          <Button onClick={() => setOpen(false)}>Cancel</Button>
          <Button variant="primary" onClick={() => void render()} disabled={!modelId || busy}>
            {busy ? 'Rendering…' : 'Render preview'}
          </Button>
        </div>
      </Dialog>
    </>
  );
}
