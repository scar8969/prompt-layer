import { useEffect, useRef, useState, type DragEvent } from 'react';
import type { Asset, Output } from '../api/types.ts';
import { formatBytes, formatSeconds, looksLikeVideo, truncate } from '../lib/util.ts';
import { Badge, MockBadge } from './Badge.tsx';
import { Icon } from './Icon.tsx';

/**
 * Every image and video in this studio is loaded from a URL the backend
 * returned. There is no generated placeholder artwork anywhere: an output with
 * no URL renders an explicit "not delivered" card instead of a picture.
 */

export function AssetImage({
  src,
  alt,
  className,
  fallbackLabel = 'No preview from the backend',
}: {
  src: string | null | undefined;
  alt: string;
  className?: string;
  fallbackLabel?: string;
}) {
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    setFailed(false);
  }, [src]);

  if (!src || failed) {
    return (
      <div className="media__placeholder">
        <Icon name="image" size={18} />
        <p style={{ margin: 'var(--s1) 0 0' }}>{failed ? 'The image failed to load.' : fallbackLabel}</p>
      </div>
    );
  }
  return (
    <img
      src={src}
      alt={alt}
      className={className}
      loading="lazy"
      decoding="async"
      onError={() => setFailed(true)}
    />
  );
}

export interface MediaItem {
  id: string;
  kind: 'image' | 'video';
  url: string | null;
  thumbnailUrl?: string | null;
  isMock: boolean;
  label: string;
  meta?: string;
  asset?: Asset;
  output?: Output;
}

export function outputToMedia(output: Output, index: number): MediaItem {
  const kind = looksLikeVideo(output.content_type ?? '') ? 'video' : 'image';
  const dimensions = output.width && output.height ? `${output.width}×${output.height}` : null;
  return {
    id: output.id || `output-${index}`,
    kind,
    url: output.url ?? null,
    isMock: output.is_mock === true,
    label: output.file_name ?? `Output ${index + 1}`,
    meta: [dimensions, output.duration_seconds ? formatSeconds(output.duration_seconds) : null, formatBytes(output.byte_size)]
      .filter((part) => part && part !== '—')
      .join(' · '),
    output,
  };
}

export function assetToMedia(asset: Asset): MediaItem {
  const kind = asset.kind === 'video' ? 'video' : 'image';
  return {
    id: asset.id,
    kind,
    url: asset.content_url ?? null,
    thumbnailUrl: asset.thumbnail_url ?? null,
    isMock: asset.is_mock === true,
    label: asset.name,
    meta: [
      asset.width && asset.height ? `${asset.width}×${asset.height}` : null,
      asset.duration_seconds ? formatSeconds(asset.duration_seconds) : null,
      formatBytes(asset.byte_size),
      asset.source,
    ]
      .filter((part) => part && part !== '—')
      .join(' · '),
    asset,
  };
}

/** The centre canvas: one item, zoomable, with its provenance stated. */
export function MediaCanvas({
  item,
  emptyLabel = 'Nothing on the canvas yet.',
  emptyHint,
}: {
  item: MediaItem | null;
  emptyLabel?: string;
  emptyHint?: string;
}) {
  const [zoom, setZoom] = useState(1);
  useEffect(() => {
    setZoom(1);
  }, [item?.id]);

  if (!item) {
    return (
      <div className="canvas">
        <div className="state" style={{ border: 0, background: 'transparent' }}>
          <span className="state__icon" aria-hidden="true">
            <Icon name="image" size={20} />
          </span>
          <p className="state__title">{emptyLabel}</p>
          {emptyHint ? <div className="state__body muted">{emptyHint}</div> : null}
        </div>
      </div>
    );
  }

  return (
    <>
      <div className="toolbar toolbar--sticky">
        <div className="toolbar__group">
          <button
            type="button"
            className="btn btn--icon"
            aria-label="Zoom out"
            onClick={() => setZoom((z) => Math.max(0.25, Number((z - 0.25).toFixed(2))))}
          >
            <Icon name="zoomOut" />
          </button>
          <span className="numeric muted" style={{ minWidth: 46, textAlign: 'center' }}>
            {Math.round(zoom * 100)}%
          </span>
          <button
            type="button"
            className="btn btn--icon"
            aria-label="Zoom in"
            onClick={() => setZoom((z) => Math.min(4, Number((z + 0.25).toFixed(2))))}
          >
            <Icon name="zoomIn" />
          </button>
          <button type="button" className="btn btn--sm" onClick={() => setZoom(1)}>
            Reset
          </button>
        </div>
        <div className="toolbar__spacer" />
        <div className="toolbar__group">
          <MockBadge isMock={item.isMock} />
          <span className="muted" style={{ fontSize: 'var(--fs-xs)' }}>
            {item.meta}
          </span>
          {item.url ? (
            <>
              <a className="btn btn--sm" href={item.url} target="_blank" rel="noreferrer">
                <span className="btn__icon">
                  <Icon name="external" size={13} />
                </span>
                Open original
              </a>
              <a className="btn btn--sm" href={item.url} download={item.label}>
                <span className="btn__icon">
                  <Icon name="download" size={13} />
                </span>
                Download
              </a>
            </>
          ) : (
            <span className="dim">The backend returned no URL for this output.</span>
          )}
        </div>
      </div>
      <div className="canvas">
        <div className="canvas__stage" style={{ transform: `scale(${zoom})` }}>
          {item.url === null ? (
            <div className="media__placeholder">
              <Icon name="alert" size={18} />
              <p>This output has no URL, so there is nothing to show.</p>
            </div>
          ) : item.kind === 'video' ? (
            <video className="canvas__image" src={item.url} controls preload="metadata" />
          ) : (
            <AssetImage src={item.url} alt={item.label} className="canvas__image" />
          )}
        </div>
      </div>
    </>
  );
}

export function MediaGrid({
  items,
  columns = 3,
  selectedId,
  onSelect,
  emptyLabel = 'No results yet.',
}: {
  items: MediaItem[];
  columns?: number;
  selectedId?: string | null;
  onSelect?: (item: MediaItem) => void;
  emptyLabel?: string;
}) {
  if (!items.length) {
    return (
      <div className="state">
        <p className="state__title">{emptyLabel}</p>
      </div>
    );
  }
  return (
    <div className="canvas__grid" style={{ gridTemplateColumns: `repeat(auto-fit, minmax(${Math.max(
      120,
      Math.round(520 / columns),
    )}px, 1fr))` }}>
      {items.map((item) => (
        <figure
          key={item.id}
          className="card"
          style={{
            margin: 0,
            borderColor: selectedId === item.id ? 'var(--accent)' : undefined,
            cursor: onSelect ? 'pointer' : undefined,
          }}
          onClick={onSelect ? () => onSelect(item) : undefined}
        >
          <div className="media media--square">
            {item.url === null ? (
              <div className="media__placeholder">No URL returned for this output.</div>
            ) : item.kind === 'video' ? (
              <video src={item.url} controls preload="metadata" />
            ) : (
              <AssetImage src={item.thumbnailUrl ?? item.url} alt={item.label} />
            )}
          </div>
          <figcaption className="asset-tile__body">
            <span className="asset-tile__name">{truncate(item.label, 40)}</span>
            <span className="asset-tile__meta">{item.meta}</span>
            {item.isMock ? <MockBadge isMock /> : null}
          </figcaption>
        </figure>
      ))}
    </div>
  );
}

export function JobStateBadge({ state, progress, progressIsEstimate }: { state: string; progress: number | null; progressIsEstimate: boolean | null }) {
  const tone =
    state === 'succeeded'
      ? 'ok'
      : state === 'failed' || state === 'cancelled'
        ? 'danger'
        : state === 'running' || state === 'queued'
          ? 'accent'
          : 'plain';
  const showProgress = progressIsEstimate === false && typeof progress === 'number';
  return (
    <span className="row row--tight">
      <Badge tone={tone}>{state}</Badge>
      {showProgress ? <span className="numeric muted">{Math.round(progress)}%</span> : null}
    </span>
  );
}

/* -------------------------------------------------------------- upload */

export interface UploadProgress {
  fileName: string;
  /** null while the browser has not reported a length (indeterminate). */
  percent: number | null;
  state: 'uploading' | 'done' | 'failed' | 'cancelled';
  message?: string;
}

export function Dropzone({
  onFiles,
  accept,
  hint,
  disabled,
  disabledReason,
  maxBytes,
}: {
  onFiles: (files: File[]) => void;
  accept?: string;
  hint?: string;
  disabled?: boolean;
  disabledReason?: string | null;
  maxBytes?: number | null;
}) {
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);

  const accept_ = (files: File[]) => {
    setLocalError(null);
    if (!files.length) return;
    if (accept && accept !== '*') {
      const allowed = accept.split(',').map((part) => part.trim());
      const rejected = files.filter(
        (file) => !allowed.some((rule) => (rule.endsWith('/*') ? file.type.startsWith(rule.slice(0, -1)) : file.type === rule)),
      );
      if (rejected.length) {
        setLocalError(`Skipped ${rejected.map((f) => f.name).join(', ')}: the file type is not accepted here.`);
        return;
      }
    }
    onFiles(files);
  };

  return (
    <div
      className="dropzone"
      data-dragging={dragging || undefined}
      onDragOver={(event: DragEvent<HTMLDivElement>) => {
        if (disabled) return;
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event: DragEvent<HTMLDivElement>) => {
        if (disabled) return;
        event.preventDefault();
        setDragging(false);
        accept_(Array.from(event.dataTransfer.files));
      }}
    >
      <Icon name="upload" size={18} />
      <p style={{ margin: 'var(--s2) 0' }}>
        {disabled ? disabledReason ?? 'Uploads are unavailable.' : 'Drop files here, or choose them from disk.'}
      </p>
      <button
        type="button"
        className="btn btn--sm"
        disabled={disabled}
        title={disabled ? disabledReason ?? undefined : undefined}
        onClick={() => inputRef.current?.click()}
      >
        Choose files
      </button>
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={accept}
        className="visually-hidden"
        aria-label="Choose files to upload"
        tabIndex={-1}
        onChange={(event) => {
          accept_(Array.from(event.target.files ?? []));
          event.target.value = '';
        }}
      />
      {hint ? <p className="field__hint">{hint}</p> : null}
      {maxBytes ? (
        <p className="field__hint">
          The backend accepts uploads up to {formatBytes(maxBytes)}; a larger file is rejected there.
        </p>
      ) : null}
      {localError ? (
        <p className="field__error" role="alert">
          {localError}
        </p>
      ) : null}
    </div>
  );
}
