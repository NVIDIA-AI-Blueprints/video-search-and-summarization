// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
const React = require('react');

const Button = React.forwardRef(({ children, onClick, disabled, ...rest }, ref) =>
  React.createElement('button', { ref, onClick, disabled, ...rest }, children)
);
Button.displayName = 'Button';

const TextInput = React.forwardRef(({ onValueChange, ...rest }, ref) =>
  React.createElement('input', {
    ref,
    onChange: (e) => onValueChange?.(e.target.value),
    ...rest,
  })
);
TextInput.displayName = 'TextInput';

const Select = ({ onValueChange, items, value, ...rest }) =>
  React.createElement(
    'select',
    { value, onChange: (e) => onValueChange?.(e.target.value), ...rest },
    (items || []).map((item) => {
      const val = typeof item === 'object' ? item.value : item;
      const label = typeof item === 'object' ? item.children : item;
      return React.createElement('option', { key: val, value: val }, label);
    })
  );

const Tag = ({ children, ...rest }) =>
  React.createElement('span', rest, children);

const Switch = ({ slotLabel, labelSide, checked, onCheckedChange, disabled, ...rest }) =>
  React.createElement(
    'label',
    null,
    React.createElement('button', {
      type: 'button',
      role: 'switch',
      'aria-checked': Boolean(checked),
      disabled,
      onClick: () => onCheckedChange?.(!checked),
      ...rest,
    }),
    slotLabel
  );

const ThemeProvider = ({ children, asChild, className, theme: _theme, ...rest }) => {
  const merged = ['nv-light', className].filter(Boolean).join(' ');
  if (asChild && React.isValidElement(children)) {
    return React.cloneElement(children, {
      className: [children.props.className, merged].filter(Boolean).join(' '),
      ...rest,
    });
  }
  return React.createElement('div', { className: merged, ...rest }, children);
};

const DatePicker = ({ children, ...rest }) =>
  React.createElement('div', rest, children);
const TimeInput = TextInput;
const Spinner = (props) => React.createElement('div', { role: 'status', ...props });
const Slider = (props) => React.createElement('input', { type: 'range', ...props });
const Tooltip = ({ children, slotContent, ...rest }) =>
  React.createElement('span', rest, children, slotContent);
const Checkbox = ({ checked, onCheckedChange, ...rest }) =>
  React.createElement('input', {
    type: 'checkbox',
    checked,
    onChange: (e) => onCheckedChange?.(e.target.checked),
    ...rest,
  });
const Divider = (props) => React.createElement('hr', props);

module.exports = {
  Button,
  TextInput,
  Select,
  Tag,
  Switch,
  ThemeProvider,
  DatePicker,
  TimeInput,
  Spinner,
  Slider,
  Tooltip,
  Checkbox,
  Divider,
};
