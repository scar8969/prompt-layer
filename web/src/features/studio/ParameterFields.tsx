import { Checkbox, Select, TextInput } from '../../components/Field.tsx';
import { Slider, TagInput } from '../../components/Controls.tsx';
import type { ParameterSpec } from '../../api/types.ts';
import { num, str } from '../../lib/util.ts';

/**
 * Renders whatever parameter schema the backend supplied for the selected
 * model. Nothing is invented: a parameter the backend did not declare is not
 * drawn, and a value the user never touched is not sent.
 */
export function ParameterFields({
  specs,
  values,
  onChange,
  disabled,
  disabledReason,
}: {
  specs: ParameterSpec[];
  values: Record<string, unknown>;
  onChange: (name: string, value: unknown) => void;
  disabled?: boolean;
  disabledReason?: string | null;
}) {
  const visible = specs.filter((spec) => !spec.prompt_mapped && !spec.api_key && !spec.advanced);
  const advanced = specs.filter((spec) => !spec.prompt_mapped && !spec.api_key && spec.advanced);
  const withheld = specs.filter((spec) => spec.prompt_mapped || spec.api_key);

  if (!specs.length) {
    return (
      <p className="muted" style={{ margin: 0 }}>
        The model profile declares no tunable parameters, so the request is the prompt alone.
      </p>
    );
  }

  return (
    <div className="stack stack--tight">
      {visible.map((spec) => (
        <ParameterControl
          key={spec.name}
          spec={spec}
          value={values[spec.name]}
          onChange={(next) => onChange(spec.name, next)}
          disabled={disabled}
          disabledReason={disabledReason}
        />
      ))}

      {advanced.length ? (
        <details className="card card--sunken">
          <summary style={{ cursor: 'pointer' }} className="card__title">
            Advanced parameters ({advanced.length})
          </summary>
          <div className="stack stack--tight" style={{ marginTop: 'var(--s3)' }}>
            {advanced.map((spec) => (
              <ParameterControl
                key={spec.name}
                spec={spec}
                value={values[spec.name]}
                onChange={(next) => onChange(spec.name, next)}
                disabled={disabled}
                disabledReason={disabledReason}
              />
            ))}
          </div>
        </details>
      ) : null}

      {withheld.length ? (
        <p className="dim" style={{ margin: 0, fontSize: 'var(--fs-xs)' }}>
          {withheld.length} parameter{withheld.length === 1 ? '' : 's'} of this model
          {withheld.some((spec) => spec.api_key) ? ' carries a server-side credential' : ' is mapped from the prompt'} and
          is not editable in the client.
        </p>
      ) : null}
    </div>
  );
}

function ParameterControl({
  spec,
  value,
  onChange,
  disabled,
  disabledReason,
}: {
  spec: ParameterSpec;
  value: unknown;
  onChange: (next: unknown) => void;
  disabled?: boolean;
  disabledReason?: string | null;
}) {
  const label = spec.label ?? spec.name;
  const hint = [spec.description, disabled ? disabledReason : null].filter(Boolean).join(' ') || undefined;
  const type = spec.type.toLowerCase();
  const optionValues = spec.options ?? [];

  if (optionValues.length) {
    return (
      <Select
        label={label}
        hint={hint}
        value={typeof value === 'string' ? value : String(spec.default ?? '')}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        options={optionValues.map((option) => ({
          value: option,
          label: spec.option_labels?.[option] ?? option,
        }))}
      />
    );
  }

  if (type === 'boolean' || type === 'bool') {
    return (
      <Checkbox
        label={label}
        hint={hint}
        checked={value === true}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
    );
  }

  if (['integer', 'number', 'float', 'int'].includes(type)) {
    const minimum = num(spec.minimum);
    const maximum = num(spec.maximum);
    const step = num(spec.step) ?? (type === 'integer' || type === 'int' ? 1 : 0.01);
    const current = num(value) ?? num(spec.default) ?? minimum ?? 0;
    if (minimum !== null && maximum !== null && maximum > minimum) {
      return (
        <Slider
          label={label}
          value={current}
          min={minimum}
          max={maximum}
          step={step}
          onChange={onChange}
          disabled={disabled}
          disabledReason={disabledReason}
          hint={spec.description}
          format={(next) => String(Number(next.toFixed(4)))}
        />
      );
    }
    return (
      <TextInput
        label={label}
        hint={hint}
        type="number"
        value={current}
        step={step}
        min={minimum ?? undefined}
        max={maximum ?? undefined}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value === '' ? null : Number(event.target.value))}
      />
    );
  }

  if (type === 'array' || type === 'list' || type === 'string_list') {
    const list = Array.isArray(value) ? value.map((item) => String(item)) : [];
    const numeric = spec.item_type === 'number' || spec.item_type === 'integer';
    return (
      <TagInput
        label={label}
        values={list}
        hint={hint}
        onChange={(next) => onChange(numeric ? next.map((item) => Number(item)).filter((item) => Number.isFinite(item)) : next)}
      />
    );
  }

  return (
    <TextInput
      label={label}
      hint={hint}
      value={typeof value === 'string' ? value : str(spec.default) ?? ''}
      maxLength={spec.max_length ?? undefined}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}
