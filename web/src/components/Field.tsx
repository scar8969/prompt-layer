import { useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from 'react';

export interface FieldProps {
  label: string;
  hint?: ReactNode;
  error?: ReactNode;
  required?: boolean;
  htmlFor?: string;
  children: ReactNode;
}

export function Field({ label, hint, error, required, htmlFor, children }: FieldProps) {
  return (
    <div className="field">
      <label className="field__label" htmlFor={htmlFor}>
        {label}
        {required ? <span className="field__required" aria-hidden="true"> *</span> : null}
      </label>
      {children}
      {hint ? <p className="field__hint">{hint}</p> : null}
      {error ? (
        <p className="field__error" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

export function TextInput({
  label,
  hint,
  error,
  required,
  ...rest
}: { label: string; hint?: ReactNode; error?: ReactNode } & Omit<InputHTMLAttributes<HTMLInputElement>, 'className'>) {
  const id = useId();
  return (
    <Field label={label} hint={hint} error={error} required={required} htmlFor={id}>
      <input id={id} className="input" aria-invalid={error ? true : undefined} {...rest} />
    </Field>
  );
}

export function TextArea({
  label,
  hint,
  error,
  tall,
  ...rest
}: { label: string; hint?: ReactNode; error?: ReactNode; tall?: boolean } & Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, 'className'>) {
  const id = useId();
  return (
    <Field label={label} hint={hint} error={error} htmlFor={id}>
      <textarea
        id={id}
        className={tall ? 'textarea textarea--tall' : 'textarea'}
        aria-invalid={error ? true : undefined}
        {...rest}
      />
    </Field>
  );
}

export interface SelectOption {
  value: string;
  label: string;
  disabled?: boolean;
}

export function Select({
  label,
  hint,
  error,
  options,
  placeholder,
  ...rest
}: {
  label: string;
  hint?: ReactNode;
  error?: ReactNode;
  options: SelectOption[];
  placeholder?: string;
} & Omit<SelectHTMLAttributes<HTMLSelectElement>, 'className'>) {
  const id = useId();
  return (
    <Field label={label} hint={hint} error={error} htmlFor={id}>
      <select id={id} className="select" {...rest}>
        {placeholder ? <option value="">{placeholder}</option> : null}
        {options.map((option) => (
          <option key={option.value} value={option.value} disabled={option.disabled}>
            {option.label}
          </option>
        ))}
      </select>
    </Field>
  );
}

export function Checkbox({
  label,
  hint,
  ...rest
}: { label: string; hint?: ReactNode } & Omit<InputHTMLAttributes<HTMLInputElement>, 'className' | 'type'>) {
  return (
    <label className="checkbox">
      <input type="checkbox" {...rest} />
      <span>
        {label}
        {hint ? (
          <span className="dim" style={{ display: 'block', fontSize: 'var(--fs-xs)' }}>
            {hint}
          </span>
        ) : null}
      </span>
    </label>
  );
}
