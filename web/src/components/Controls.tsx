import { useId, useRef, type ReactNode } from 'react';
import { Icon } from './Icon.tsx';

/* --------------------------------------------------------------- switch */

/**
 * A native checkbox drives the visual switch: the label, the checked state and
 * the keyboard behaviour all come from the browser rather than from ARIA we
 * would have to maintain by hand.
 */
export function Switch({
  checked,
  onChange,
  label,
  hint,
  disabled,
  disabledReason,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  hint?: ReactNode;
  disabled?: boolean;
  disabledReason?: string | null;
}) {
  const id = useId();
  return (
    <div className="field__row">
      <label className="switch" htmlFor={id}>
        <input
          id={id}
          type="checkbox"
          checked={checked}
          disabled={disabled}
          title={disabled ? disabledReason ?? undefined : undefined}
          onChange={(event) => onChange(event.target.checked)}
        />
        <span className="switch__track" aria-hidden="true">
          <span className="switch__thumb" />
        </span>
      </label>
      <span>
        <span style={{ display: 'block' }}>{label}</span>
        {hint ? (
          <span className="dim" style={{ display: 'block', fontSize: 'var(--fs-xs)' }}>
            {hint}
          </span>
        ) : null}
        {disabled && disabledReason ? (
          <span className="dim" style={{ display: 'block', fontSize: 'var(--fs-xs)' }}>
            {disabledReason}
          </span>
        ) : null}
      </span>
    </div>
  );
}

/* ---------------------------------------------------------- segmented */

/**
 * A group of mutually exclusive choices. Options the caller marks as
 * unsupported are disabled and carry the reason that came from the model's
 * capability profile; nothing is ever enabled optimistically.
 */
export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
  disabled,
  disabledReason,
}: {
  value: T | null;
  options: { value: T; label: string; disabled?: boolean; title?: string }[];
  onChange: (next: T) => void;
  label: string;
  disabled?: boolean;
  disabledReason?: string | null;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  const move = (from: number, delta: number) => {
    const usable = options.map((option, index) => (option.disabled || disabled ? -1 : index)).filter((i) => i >= 0);
    if (!usable.length) return;
    const current = usable.indexOf(from);
    const next = usable[(current + delta + usable.length) % usable.length];
    onChange(options[next].value);
    refs.current[next]?.focus();
  };

  return (
    <div
      className="segmented"
      role="group"
      aria-label={label}
      title={disabled ? disabledReason ?? undefined : undefined}
    >
      {options.map((option, index) => {
        const selected = option.value === value;
        const isDisabled = disabled || option.disabled;
        return (
          <button
            key={option.value}
            ref={(node) => {
              refs.current[index] = node;
            }}
            type="button"
            aria-pressed={selected}
            className="segmented__item"
            disabled={isDisabled}
            title={option.title ?? (isDisabled ? disabledReason ?? 'Not supported by this model' : option.label)}
            tabIndex={selected && !isDisabled ? 0 : -1}
            onClick={() => onChange(option.value)}
            onKeyDown={(event) => {
              if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
                event.preventDefault();
                move(index, 1);
              } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
                event.preventDefault();
                move(index, -1);
              }
            }}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

/* --------------------------------------------------------------- tabs */

export function Tabs<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: { value: T; label: string; badge?: ReactNode }[];
  onChange: (next: T) => void;
  label: string;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const move = (from: number, delta: number) => {
    const next = (from + delta + options.length) % options.length;
    onChange(options[next].value);
    refs.current[next]?.focus();
  };
  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {options.map((option, index) => {
        const selected = option.value === value;
        return (
          <button
            key={option.value}
            ref={(node) => {
              refs.current[index] = node;
            }}
            type="button"
            role="tab"
            aria-selected={selected}
            className="tab"
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(option.value)}
            onKeyDown={(event) => {
              if (event.key === 'ArrowRight') {
                event.preventDefault();
                move(index, 1);
              } else if (event.key === 'ArrowLeft') {
                event.preventDefault();
                move(index, -1);
              }
            }}
          >
            {option.label}
            {option.badge}
          </button>
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------------ slider */

export function Slider({
  label,
  value,
  min,
  max,
  step,
  onChange,
  format,
  disabled,
  disabledReason,
  hint,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (next: number) => void;
  format?: (value: number) => string;
  disabled?: boolean;
  disabledReason?: string | null;
  hint?: ReactNode;
}) {
  const id = useId();
  return (
    <div className="field">
      <label className="field__label" htmlFor={id}>
        {label}
        <span className="numeric muted" style={{ marginLeft: 'var(--s2)' }}>
          {format ? format(value) : value}
        </span>
      </label>
      <input
        id={id}
        type="range"
        className="range"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        title={disabled ? disabledReason ?? undefined : undefined}
        onChange={(event) => onChange(Number(event.target.value))}
      />
      {hint ? <p className="field__hint">{hint}</p> : null}
      {disabled && disabledReason ? <p className="field__hint">{disabledReason}</p> : null}
    </div>
  );
}

/* ----------------------------------------------------------- progress */

/**
 * Progress is only ever rendered from a real number. When the backend says
 * progress_is_estimate is true, or gives no progress at all, this component
 * refuses to draw a bar — the caller shows the job state instead.
 */
export function ProgressBar({
  value,
  isEstimate,
  label,
}: {
  value: number | null | undefined;
  isEstimate: boolean | null | undefined;
  label: string;
}) {
  if (isEstimate !== false || value === null || value === undefined) {
    return (
      <p className="progress__meta" style={{ display: 'block' }}>
        {label}: no measured progress from the provider
        {isEstimate === true ? ' — the value it reports is an estimate, so no bar is drawn.' : '.'}
      </p>
    );
  }
  const pct = Math.max(0, Math.min(100, value));
  return (
    <div className="progress">
      <div
        className="progress__track"
        role="progressbar"
        aria-valuenow={Math.round(pct)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={label}
      >
        <div className="progress__fill" style={{ width: `${pct}%` }} />
      </div>
      <p className="progress__meta numeric" style={{ margin: 0 }}>
        <span>{Math.round(pct)}%</span>
        <span>reported by the provider</span>
      </p>
    </div>
  );
}

/* ---------------------------------------------------------------- tip */

/* ------------------------------------------------------------ tag list */

export function TagInput({
  label,
  values,
  onChange,
  placeholder = 'Type a value, then press Enter',
  hint,
}: {
  label: string;
  values: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  hint?: ReactNode;
}) {
  const id = useId();
  return (
    <div className="field">
      <span className="field__label" id={`${id}-label`}>
        {label}
      </span>
      <div className="chip-row" aria-labelledby={`${id}-label`}>
        {values.length ? (
          values.map((tag) => (
            <span key={tag} className="chip">
              {tag}
              <button
                type="button"
                className="btn btn--icon btn--sm"
                aria-label={`Remove ${tag}`}
                onClick={() => onChange(values.filter((value) => value !== tag))}
              >
                <Icon name="close" size={12} />
              </button>
            </span>
          ))
        ) : (
          <span className="dim">None yet.</span>
        )}
      </div>
      <input
        id={id}
        className="input"
        placeholder={placeholder}
        aria-label={`Add to ${label}`}
        onKeyDown={(event) => {
          if (event.key !== 'Enter') return;
          event.preventDefault();
          const raw = event.currentTarget.value.trim();
          if (!raw) return;
          if (!values.includes(raw)) onChange([...values, raw]);
          event.currentTarget.value = '';
        }}
      />
      {hint ? <p className="field__hint">{hint}</p> : null}
    </div>
  );
}

/* ------------------------------------------------------------- menus */

export function Menu({
  label,
  items,
}: {
  label: string;
  items: {
    label: string;
    onSelect: () => void;
    danger?: boolean;
    hint?: string;
    disabled?: boolean;
    disabledReason?: string;
  }[];
}) {
  return (
    <details className="menu">
      <summary className="btn btn--sm">{label}</summary>
      <div className="menu__list">
        {items.map((item) => (
          <button
            key={item.label}
            type="button"
            className={item.danger ? 'menu__item menu__item--danger' : 'menu__item'}
            disabled={item.disabled}
            title={item.disabled ? item.disabledReason ?? undefined : undefined}
            onClick={() => item.onSelect()}
          >
            <span>
              {item.label}
              {item.hint ? <span className="menu__hint">{item.hint}</span> : null}
            </span>
          </button>
        ))}
      </div>
    </details>
  );
}
