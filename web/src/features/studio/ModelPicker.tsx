import { useId } from 'react';
import type { Model } from '../../api/types.ts';
import { groupByProvider } from '../../state/models.ts';
import { MockBadge } from '../../components/Badge.tsx';
import { Icon } from '../../components/Icon.tsx';

/**
 * The model selector. Models are grouped by provider and the placeholder
 * provider is named in the group label, so a mock model can never be picked
 * without the disclosure being visible at the point of selection. The badge
 * below repeats it for the current choice.
 */
export function ModelPicker({
  models,
  value,
  onChange,
  modality,
  disabled,
  disabledReason,
}: {
  models: Model[];
  value: string | null;
  onChange: (id: string) => void;
  modality: 'image' | 'video' | 'all';
  disabled?: boolean;
  disabledReason?: string | null;
}) {
  const id = useId();
  const eligible = models.filter((model) => modality === 'all' || model.modality === modality);
  const groups = groupByProvider(eligible);
  const selected = eligible.find((model) => model.id === value) ?? null;

  if (!eligible.length) {
    return (
      <div className="panel panel--warn">
        <p className="panel__title">
          <Icon name="alert" size={13} /> No {modality === 'all' ? '' : modality} model is registered
        </p>
        <p style={{ margin: 0 }}>
          A model has to be registered on a connection before it can generate anything. Open{' '}
          <strong>API Connections</strong> to add a provider, test the key and describe what the model can do.
        </p>
      </div>
    );
  }

  return (
    <div className="field">
      <label className="field__label" htmlFor={id}>
        Model
      </label>
      <select
        id={id}
        className="select"
        value={value ?? ''}
        disabled={disabled}
        title={disabled ? disabledReason ?? undefined : undefined}
        onChange={(event) => onChange(event.target.value)}
      >
        {value === null ? <option value="">Choose a model…</option> : null}
        {groups.map((group) => (
          <optgroup key={group.label} label={group.label}>
            {group.options.map((model) => (
              <option key={model.id} value={model.id}>
                {model.display_name}
                {model.is_enabled ? '' : ' (disabled)'}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
      <div className="row row--tight" style={{ flexWrap: 'wrap' }}>
        <MockBadge isMock={selected?.provider.is_mock} />
        {selected ? (
          <span className="dim" style={{ fontSize: 'var(--fs-xs)' }}>
            {selected.provider.adapter_label} · {selected.provider.connection_name} ·{' '}
            <span className="mono">{selected.model_key}</span>
          </span>
        ) : (
          <span className="dim" style={{ fontSize: 'var(--fs-xs)' }}>
            Nothing is selected, so nothing can be submitted.
          </span>
        )}
      </div>
      {disabled && disabledReason ? <p className="field__hint">{disabledReason}</p> : null}
    </div>
  );
}
