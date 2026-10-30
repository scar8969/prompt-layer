import type { ButtonHTMLAttributes } from 'react';

type Variant = 'primary' | 'ghost' | 'danger' | 'plain';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: 'md' | 'sm' | 'lg';
  block?: boolean;
  /** A reason renders as a permanent explanation next to a disabled button. */
  disabledReason?: string | null;
}

export function Button({
  variant = 'plain',
  size = 'md',
  block,
  disabledReason,
  disabled,
  title,
  className,
  children,
  ...rest
}: ButtonProps) {
  const classes = [
    'btn',
    variant !== 'plain' ? `btn--${variant}` : '',
    size !== 'md' ? `btn--${size}` : '',
    block ? 'btn--block' : '',
    className ?? '',
  ]
    .filter(Boolean)
    .join(' ');
  return (
    <button
      type="button"
      className={classes}
      disabled={disabled}
      title={disabled ? disabledReason ?? title : title}
      {...rest}
    >
      {children}
    </button>
  );
}

export function IconButton({
  label,
  variant = 'ghost',
  size = 'md',
  className,
  ...rest
}: ButtonProps & { label: string }) {
  return (
    <Button
      variant={variant}
      size={size}
      aria-label={label}
      className={['btn--icon', className ?? ''].filter(Boolean).join(' ')}
      title={rest.title ?? label}
      {...rest}
    >
      {rest.children}
    </Button>
  );
}

