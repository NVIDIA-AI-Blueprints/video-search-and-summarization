// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
/**
 * Theme replacements for the Material UI widgets used by warehouse analytics.
 * Built from @nvidia/foundations-react-core (Kaizen 1.17) and Tailwind.
 */
// @ts-nocheck
import React from 'react';
import {
  Button as KaizenButton,
  Checkbox as KaizenCheckbox,
  DatePicker as KaizenDatePicker,
  Divider as KaizenDivider,
  Select as KaizenSelect,
  Spinner as KaizenSpinner,
  Switch as KaizenSwitch,
  Tag as KaizenTag,
  TextInput,
  ThemeProvider,
  TimeInput as KaizenTimeInput,
  Tooltip as KaizenTooltip,
} from '@nvidia/foundations-react-core';

import './kaizen-light.css';

type DivProps = React.HTMLAttributes<HTMLDivElement>;

const cx = (...parts: Array<string | false | undefined | null>) => parts.filter(Boolean).join(' ');

/** Kaizen ThemeProvider class that resets tokens inside Agent UI `.nv-dark`. */
const KAIZEN_LIGHT_CLASS = 'nv-light';

export const Box = ({ children, className, sx: _sx, ...rest }: DivProps & { sx?: unknown }) => (
  <div className={className} {...rest}>{children}</div>
);

export const Paper = ({ children, className, elevation: _e, ...rest }: DivProps & { elevation?: number }) => (
  <div className={cx('rounded border border-neutral-300 bg-white p-2 text-neutral-900', className)} {...rest}>
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
  children, onClick, disabled, className, title, style, ...rest
}: {
  children?: React.ReactNode;
  onClick?: React.MouseEventHandler;
  disabled?: boolean;
  className?: string;
  title?: string;
  style?: React.CSSProperties;
} & React.ButtonHTMLAttributes<HTMLButtonElement>) => (
  <button
    type="button"
    onClick={onClick}
    disabled={disabled}
    title={title}
    className={cx('inline-flex items-center justify-center rounded p-1 hover:bg-black/10', className)}
    style={style}
    {...rest}
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
          className="rounded border border-neutral-400 bg-white p-2 text-neutral-900"
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
      'block w-full cursor-pointer px-3 py-1.5 text-left text-sm text-neutral-900 hover:bg-neutral-100',
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
        className={cx('min-w-[100px] rounded border border-neutral-400 bg-white p-1 text-neutral-900', className)}
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
        attributes={{ SelectContent: { className: KAIZEN_LIGHT_CLASS } }}
        onValueChange={(next: string) => onChange?.({ target: { value: next } })}
      />
    );
  }

  return (
    <select
      id={id}
      aria-labelledby={labelId}
      title={title}
      className={cx('min-w-[100px] rounded border border-neutral-400 bg-white p-1 text-neutral-900', className)}
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
  <KaizenSpinner
    className={className}
    kind="ring"
    size={size >= 48 ? 'large' : size >= 24 ? 'medium' : 'small'}
    aria-label="Loading"
  />
);

const tooltipSide = (placement?: string): 'top' | 'right' | 'bottom' | 'left' => {
  if (placement === 'right' || placement === 'bottom' || placement === 'left') return placement;
  return 'top';
};

export const Tooltip = ({
  title, children, placement,
}: {
  title?: React.ReactNode;
  children: React.ReactElement;
  placement?: string;
}) => {
  if (!title) return children;
  return (
    <KaizenTooltip slotContent={title} side={tooltipSide(placement)} className={KAIZEN_LIGHT_CLASS}>
      {children}
    </KaizenTooltip>
  );
};

export const AccordionSummary = ({ children, className, expandIcon }: DivProps & { expandIcon?: React.ReactNode }) => (
  <summary className={cx('flex cursor-pointer list-none items-center justify-between gap-2 p-2', className)}>
    <span>{children}</span>
    {expandIcon}
  </summary>
);

export const AccordionDetails = ({ children, className }: DivProps) => (
  <div className={cx('p-2', className)}>{children}</div>
);

export const Accordion = ({ children, className, defaultExpanded }: DivProps & { defaultExpanded?: boolean }) => (
  <details
    className={cx('rounded border border-neutral-300', className)}
    {...(defaultExpanded ? { open: true } : {})}
  >
    {children}
  </details>
);

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
  <div className={cx('flex h-8 w-8 items-center justify-center overflow-hidden rounded-full bg-neutral-200 text-xs text-neutral-900', className)} style={style}>
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
    <div className="absolute left-0 z-50 mt-1 min-w-[12rem] rounded border border-neutral-300 bg-white py-1 text-neutral-900 shadow">
      <div onClick={onClose}>{children}</div>
    </div>
  );
};

const padDateTime = (n: number) => String(n).padStart(2, '0');

const isValidDate = (date?: Date | null): date is Date =>
  date instanceof Date && !Number.isNaN(date.getTime());

const toTimeInputValue = (date?: Date | null) => {
  if (!isValidDate(date)) return '';
  return `${padDateTime(date.getHours())}:${padDateTime(date.getMinutes())}:${padDateTime(date.getSeconds())}`;
};

const mergeDateAndTime = (date: Date | null | undefined, timeValue: string) => {
  const next = isValidDate(date) ? new Date(date) : new Date();
  const [hours = '0', minutes = '0', seconds = '0'] = timeValue.split(':');
  next.setHours(Number(hours) || 0, Number(minutes) || 0, Number(seconds) || 0, 0);
  return next;
};

const mergeDateKeepingTime = (nextDate: Date, previous?: Date | null) => {
  const merged = new Date(nextDate);
  if (isValidDate(previous)) {
    merged.setHours(
      previous.getHours(),
      previous.getMinutes(),
      previous.getSeconds(),
      previous.getMilliseconds(),
    );
  }
  return merged;
};

export const DateTimePicker = ({
  value, onChange, label, className, disableFuture,
}: {
  value?: Date | null;
  onChange?: (date: Date | null) => void;
  label?: React.ReactNode;
  className?: string;
  disableFuture?: boolean;
}) => {
  const selectedDate = isValidDate(value) ? value : undefined;
  const todayCutoff = new Date();
  return (
    <ThemeProvider
      theme="light"
      className={cx('flex flex-col gap-1 text-sm', KAIZEN_LIGHT_CLASS, className)}
      style={{ colorScheme: 'light' }}
    >
      {label ? <span className="font-semibold text-neutral-700">{label}</span> : null}
      <KaizenDatePicker
        kind="single"
        value={selectedDate}
        format="yyyy-MM-dd"
        placeholder="Select date"
        portal
        attributes={{ DatePickerContent: { className: KAIZEN_LIGHT_CLASS } }}
        disabledMatcher={disableFuture ? { after: todayCutoff } : undefined}
        endMonth={disableFuture ? todayCutoff : undefined}
        onValueChange={(next?: Date) => {
          if (!next) {
            onChange?.(null);
            return;
          }
          onChange?.(mergeDateKeepingTime(next, selectedDate));
        }}
      />
      <KaizenTimeInput
        aria-label={typeof label === 'string' ? `${label} time` : 'Time'}
        step={1}
        value={toTimeInputValue(selectedDate)}
        onValueChange={(time: string) => onChange?.(mergeDateAndTime(selectedDate, time))}
      />
    </ThemeProvider>
  );
};

export const LocalizationProvider = ({ children }: { children?: React.ReactNode; dateAdapter?: unknown }) => <>{children}</>;

export const AdapterDateFns = function AdapterDateFns() {
  return null;
};

export default {
  Box, Button, Chip, Select, Switch, TextField, Tooltip,
};
