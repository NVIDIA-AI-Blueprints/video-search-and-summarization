// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
/**
 * Theme replacements for the Material UI widgets used by warehouse analytics.
 * Built from @nvidia/foundations-react-core, rsuite, and Tailwind.
 */
// @ts-nocheck
import React from 'react';
import {
  Accordion as KaizenAccordion,
  Button as KaizenButton,
  Checkbox as KaizenCheckbox,
  Divider as KaizenDivider,
  Select as KaizenSelect,
  Switch as KaizenSwitch,
  Tag as KaizenTag,
  TextInput,
} from '@nvidia/foundations-react-core';
import { DatePicker, Slider as RsuiteSlider, Tooltip as RsuiteTooltip, Whisper } from 'rsuite';

type DivProps = React.HTMLAttributes<HTMLDivElement>;

const cx = (...parts: Array<string | false | undefined | null>) => parts.filter(Boolean).join(' ');

export const Box = ({ children, className, sx: _sx, ...rest }: DivProps & { sx?: unknown }) => (
  <div className={className} {...rest}>{children}</div>
);

export const Paper = ({ children, className, elevation: _e, ...rest }: DivProps & { elevation?: number }) => (
  <div className={cx('rounded border border-neutral-300 bg-white p-2 dark:border-neutral-700 dark:bg-neutral-900', className)} {...rest}>
    {children}
  </div>
);

export const Typography = ({
  children, className, variant, component, ...rest
}: DivProps & { variant?: string; component?: string }) => {
  const Tag = (component as keyof JSX.IntrinsicElements) || (variant === 'h6' ? 'h6' : 'span');
  return <Tag className={className} {...rest}>{children}</Tag>;
};

export const Divider = ({ className, ...rest }: DivProps) => (
  <KaizenDivider className={cx('my-2', className)} {...rest} />
);

export const Grid = ({ children, className, container: _c, item: _i, xs: _xs, ...rest }: DivProps & { container?: boolean; item?: boolean; xs?: number | string }) => (
  <div className={cx('flex flex-wrap gap-2', className)} {...rest}>{children}</div>
);

export const List = ({ children, className, ...rest }: DivProps) => (
  <div className={cx('flex flex-col gap-1 overflow-auto [&>*]:shrink-0', className)} {...rest}>{children}</div>
);

export const Button = ({
  children, onClick, disabled, className, variant, color, size, startIcon, style, title, type = 'button',
}: {
  children?: React.ReactNode;
  onClick?: React.MouseEventHandler;
  disabled?: boolean;
  className?: string;
  variant?: string;
  color?: string;
  size?: string;
  startIcon?: React.ReactNode;
  style?: React.CSSProperties;
  title?: string;
  type?: 'button' | 'submit' | 'reset';
}) => {
  const kind = color === 'success' || variant === 'contained' ? 'primary' : variant === 'text' ? 'tertiary' : 'secondary';
  return (
    <KaizenButton
      type={type}
      kind={kind as 'primary' | 'secondary' | 'tertiary'}
      size={size === 'small' ? 'small' : 'medium'}
      disabled={disabled}
      onClick={onClick}
      className={className}
      style={style}
      title={title}
    >
      {startIcon}
      {children}
    </KaizenButton>
  );
};

export const IconButton = ({
  children, onClick, disabled, className, title, style,
}: {
  children?: React.ReactNode;
  onClick?: React.MouseEventHandler;
  disabled?: boolean;
  className?: string;
  title?: string;
  style?: React.CSSProperties;
}) => (
  <button
    type="button"
    onClick={onClick}
    disabled={disabled}
    title={title}
    className={cx('inline-flex items-center justify-center rounded p-1 hover:bg-black/10 dark:hover:bg-white/10', className)}
    style={style}
  >
    {children}
  </button>
);

export const TextField = ({
  value, defaultValue, onChange, label, type = 'text', className, size, multiline, rows, disabled, placeholder, fullWidth, InputProps, inputRef, helperText, error,
}: {
  value?: string | number;
  defaultValue?: string | number;
  onChange?: (event: { target: { value: string } }) => void;
  label?: React.ReactNode;
  type?: string;
  className?: string;
  size?: string;
  multiline?: boolean;
  rows?: number;
  disabled?: boolean;
  placeholder?: string;
  fullWidth?: boolean;
  InputProps?: { endAdornment?: React.ReactNode };
  inputRef?: React.Ref<HTMLInputElement | HTMLTextAreaElement>;
  helperText?: React.ReactNode;
  error?: boolean;
}) => {
  if (multiline) {
    return (
      <label className={cx('flex flex-col gap-1 text-sm', fullWidth && 'w-full', className)}>
        {label}
        <textarea
          ref={inputRef as React.Ref<HTMLTextAreaElement>}
          className="rounded border border-neutral-400 bg-transparent p-2 dark:border-neutral-600"
          rows={rows || 3}
          value={value as string | undefined}
          defaultValue={defaultValue as string | undefined}
          disabled={disabled}
          placeholder={placeholder}
          onChange={(event) => onChange?.({ target: { value: event.target.value } })}
        />
        {helperText ? <span className={error ? 'text-red-500' : 'text-neutral-500'}>{helperText}</span> : null}
      </label>
    );
  }
  return (
    <label className={cx('flex flex-col gap-1 text-sm', fullWidth && 'w-full', className)}>
      {label}
      <span className="flex items-center gap-1">
        <TextInput
          ref={inputRef as React.Ref<HTMLInputElement>}
          type={type}
          value={value === undefined ? undefined : String(value)}
          defaultValue={defaultValue === undefined ? undefined : String(defaultValue)}
          disabled={disabled}
          placeholder={placeholder}
          size={size === 'small' ? 'small' : 'medium'}
          onValueChange={(next: string) => onChange?.({ target: { value: next } })}
        />
        {InputProps?.endAdornment}
      </span>
      {helperText ? <span className={error ? 'text-red-500' : 'text-neutral-500'}>{helperText}</span> : null}
    </label>
  );
};

export const Switch = ({
  checked, onChange, disabled, className,
}: {
  checked?: boolean;
  onChange?: (event: { target: { checked: boolean } }, checked: boolean) => void;
  disabled?: boolean;
  className?: string;
}) => (
  <KaizenSwitch
    className={className}
    checked={Boolean(checked)}
    disabled={disabled}
    onCheckedChange={(next: boolean) => onChange?.({ target: { checked: next } }, next)}
  />
);

export const Checkbox = ({
  checked, onChange, disabled, className,
}: {
  checked?: boolean;
  onChange?: (event: { target: { checked: boolean } }, checked: boolean) => void;
  disabled?: boolean;
  className?: string;
}) => (
  <KaizenCheckbox
    className={className}
    checked={Boolean(checked)}
    disabled={disabled}
    onCheckedChange={(next) => {
      const isChecked = next === true;
      onChange?.({ target: { checked: isChecked } }, isChecked);
    }}
  />
);

export const FormControlLabel = ({
  control, label, className,
}: {
  control: React.ReactElement;
  label?: React.ReactNode;
  className?: string;
}) => (
  <label className={cx('inline-flex items-center gap-2 text-sm', className)}>
    {control}
    <span>{label}</span>
  </label>
);

export const FormGroup = ({ children, className }: DivProps) => (
  <div className={cx('flex flex-col gap-2', className)}>{children}</div>
);

export const FormControl = ({ children, className, error, fullWidth, style }: DivProps & { error?: boolean; fullWidth?: boolean }) => (
  <div className={cx('flex flex-col gap-1', fullWidth && 'w-full', error && 'text-red-500', className)} style={style}>
    {children}
  </div>
);

export const FormHelperText = ({ children, className }: DivProps) => (
  <span className={cx('text-xs text-neutral-500', className)}>{children}</span>
);

export const InputLabel = ({ children, className, id }: { children?: React.ReactNode; className?: string; id?: string }) => (
  <label id={id} className={cx('text-sm', className)}>{children}</label>
);

export const MenuItem = ({
  children, value, onClick, className,
}: {
  children?: React.ReactNode;
  value?: string | number | readonly string[];
  onClick?: React.MouseEventHandler;
  className?: string;
}) => (
  <button
    type="button"
    value={value as string | number}
    className={cx(
      'block w-full cursor-pointer px-3 py-1.5 text-left text-sm text-neutral-900 hover:bg-neutral-100 dark:text-neutral-100 dark:hover:bg-neutral-800',
      className,
    )}
    onClick={onClick}
  >
    {children}
  </button>
);

export const Select = ({
  children, value, defaultValue, onChange, multiple, className, style, open, onOpen, onClose, labelId, id, title,
}: {
  children?: React.ReactNode;
  value?: string | string[] | number;
  defaultValue?: string | string[] | number;
  onChange?: (event: { target: { value: unknown } }) => void;
  multiple?: boolean;
  className?: string;
  style?: React.CSSProperties;
  open?: boolean;
  onOpen?: () => void;
  onClose?: () => void;
  labelId?: string;
  id?: string;
  title?: string;
}) => {
  const options = React.Children.toArray(children).filter(React.isValidElement) as React.ReactElement[];
  const items = options
    .filter((child) => child.type !== 'div' && (child.props as { value?: unknown }).value !== undefined)
    .map((child) => ({
      value: String((child.props as { value: string | number }).value),
      children: (child.props as { children?: React.ReactNode }).children,
    }));

  if (multiple) {
    const selected = Array.isArray(value) ? value.map(String) : [];
    return (
      <select
        id={id}
        aria-labelledby={labelId}
        multiple
        title={title}
        className={cx('min-w-[100px] rounded border border-neutral-400 bg-transparent p-1 dark:border-neutral-600', className)}
        style={style}
        value={selected}
        defaultValue={defaultValue as string[] | undefined}
        onFocus={onOpen}
        onBlur={onClose}
        onChange={(event) => {
          const next = Array.from(event.target.selectedOptions).map((option) => option.value);
          onChange?.({ target: { value: next } });
        }}
      >
        {items.map((item) => (
          <option key={item.value} value={item.value}>{item.children}</option>
        ))}
      </select>
    );
  }

  if (items.length > 0) {
    return (
      <KaizenSelect
        id={id}
        className={className}
        style={style}
        title={title}
        value={value === undefined ? undefined : String(value)}
        items={items}
        onValueChange={(next: string) => onChange?.({ target: { value: next } })}
      />
    );
  }

  return (
    <select
      id={id}
      aria-labelledby={labelId}
      title={title}
      className={cx('min-w-[100px] rounded border border-neutral-400 bg-transparent p-1 dark:border-neutral-600', className)}
      style={style}
      value={value as string | number | undefined}
      defaultValue={defaultValue as string | number | undefined}
      onFocus={onOpen}
      onBlur={onClose}
      onChange={(event) => onChange?.({ target: { value: event.target.value } })}
    >
      {children}
    </select>
  );
};

export const Chip = ({
  label, onClick, color, variant, style, className, icon,
}: {
  label?: React.ReactNode;
  onClick?: React.MouseEventHandler;
  color?: string;
  variant?: string;
  style?: React.CSSProperties;
  className?: string;
  icon?: React.ReactNode;
}) => (
  <span
    role={onClick ? 'button' : undefined}
    onClick={onClick}
    style={{ display: 'inline-flex', cursor: onClick ? 'pointer' : undefined, ...style }}
    className={className}
  >
    <KaizenTag
      color={color === 'success' ? 'green' : color === 'error' ? 'red' : 'gray'}
      kind={variant === 'outlined' ? 'outline' : 'solid'}
      readOnly={!onClick}
    >
      {icon}
      {label}
    </KaizenTag>
  </span>
);

export const CircularProgress = ({ size = 24, className }: { size?: number; className?: string }) => (
  <span
    className={cx('inline-block animate-spin rounded-full border-2 border-neutral-300 border-t-[#76b900]', className)}
    style={{ width: size, height: size }}
  />
);

export const Tooltip = ({
  title, children, placement,
}: {
  title?: React.ReactNode;
  children: React.ReactElement;
  placement?: string;
}) => {
  if (!title) return children;
  return (
    <Whisper
      placement={(placement as 'top') || 'top'}
      speaker={<RsuiteTooltip>{title}</RsuiteTooltip>}
    >
      {children}
    </Whisper>
  );
};

export const Slider = ({
  value, defaultValue, min = 0, max = 100, step = 1, onChange, className, style,
}: {
  value?: number;
  defaultValue?: number;
  min?: number;
  max?: number;
  step?: number;
  onChange?: (event: unknown, value: number) => void;
  className?: string;
  style?: React.CSSProperties;
}) => (
  <RsuiteSlider
    className={className}
    style={style}
    min={min}
    max={max}
    step={step}
    value={value}
    defaultValue={defaultValue}
    onChange={(next: number) => onChange?.(null, next)}
  />
);

export const AccordionSummary = ({ children, className, expandIcon }: DivProps & { expandIcon?: React.ReactNode }) => (
  <span className={cx('inline-flex items-center justify-between gap-2', className)}>
    <span>{children}</span>
    {expandIcon}
  </span>
);
AccordionSummary.displayName = 'AccordionSummary';

export const AccordionDetails = ({ children, className }: DivProps) => (
  <div className={className}>{children}</div>
);
AccordionDetails.displayName = 'AccordionDetails';

export const Accordion = ({ children, className, defaultExpanded }: DivProps & { defaultExpanded?: boolean }) => {
  const childArray = React.Children.toArray(children);
  let trigger: React.ReactNode;
  let content: React.ReactNode;
  childArray.forEach((child) => {
    if (!React.isValidElement(child)) return;
    if (child.type === AccordionSummary) trigger = child;
    if (child.type === AccordionDetails) content = (child.props as { children?: React.ReactNode }).children;
  });
  if (trigger !== undefined) {
    return (
      <KaizenAccordion
        className={className}
        defaultValue={defaultExpanded ? 'panel' : undefined}
        items={[{ value: 'panel', slotTrigger: trigger, slotContent: content }]}
      />
    );
  }
  return (
    <details className={cx('rounded border border-neutral-300 dark:border-neutral-700', className)} open={defaultExpanded}>
      {children}
    </details>
  );
};

export const Card = React.forwardRef<HTMLDivElement, DivProps & { raised?: boolean }>(
  ({ children, className, onClick, onMouseEnter, onMouseLeave, style, title, raised: _r, ...rest }, ref) => (
    <div
      ref={ref}
      title={title}
      className={cx('overflow-hidden rounded border border-neutral-300 bg-white text-neutral-900', className)}
      style={style}
      onClick={onClick}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
      {...rest}
    >
      {children}
    </div>
  ),
);
Card.displayName = 'Card';

export const CardHeader = ({
  title, subheader, avatar, action, className,
}: {
  title?: React.ReactNode;
  subheader?: React.ReactNode;
  avatar?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) => (
  <div className={cx('flex items-start gap-2 p-2', className)}>
    {avatar}
    <div className="min-w-0 flex-1">
      <div className="font-medium">{title}</div>
      {subheader ? <div className="text-xs text-neutral-500">{subheader}</div> : null}
    </div>
    {action}
  </div>
);

export const CardContent = ({ children, className }: DivProps) => (
  <div className={cx('p-2', className)}>{children}</div>
);

export const CardMedia = ({
  children, component, image, src, alt, className, height, style,
}: DivProps & {
  component?: string;
  image?: string;
  src?: string;
  alt?: string;
  height?: number | string;
}) => {
  if (component === 'img' || image || src) {
    return <img className={className} src={image || src} alt={alt || ''} style={{ height, objectFit: 'cover', width: '100%', ...style }} />;
  }
  return <div className={className} style={{ height, ...style }}>{children}</div>;
};

export const Avatar = ({ children, className, style, sx: _sx }: DivProps & { sx?: unknown }) => (
  <div className={cx('flex h-8 w-8 items-center justify-center overflow-hidden rounded-full bg-neutral-200 text-xs dark:bg-neutral-700', className)} style={style}>
    {children}
  </div>
);

type MenuAnchor = { current?: Element | null } | Element | null;

export const Menu = ({
  children, open, onClose, anchorEl,
}: {
  children?: React.ReactNode;
  open?: boolean;
  onClose?: () => void;
  anchorEl?: MenuAnchor;
}) => {
  if (!open) return null;
  return (
    <div className="absolute left-0 z-50 mt-1 min-w-[12rem] rounded border border-neutral-300 bg-white py-1 text-neutral-900 shadow dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-100">
      <div onClick={onClose}>{children}</div>
    </div>
  );
};

export const DateTimePicker = ({
  value, onChange, label, className,
}: {
  value?: Date | null;
  onChange?: (date: Date | null) => void;
  label?: React.ReactNode;
  className?: string;
}) => (
  <label className={cx('flex flex-col gap-1 text-sm', className)}>
    {label}
    <DatePicker
      format="yyyy-MM-dd HH:mm:ss"
      value={value ?? undefined}
      onChange={(next: Date | null) => onChange?.(next)}
    />
  </label>
);

export const LocalizationProvider = ({ children }: { children?: React.ReactNode; dateAdapter?: unknown }) => <>{children}</>;

export const AdapterDateFns = function AdapterDateFns() {
  return null;
};

export function styled(Component: React.ComponentType<any> | keyof JSX.IntrinsicElements) {
  return function styledFactory(_styles?: unknown) {
    const Styled = (props: Record<string, unknown>) => {
      if (typeof Component === 'string') {
        return React.createElement(Component, props);
      }
      return <Component {...props} />;
    };
    Styled.displayName = 'WarehouseStyled';
    return Styled;
  };
}

export default {
  Box, Button, Chip, Select, Switch, TextField, Tooltip,
};
