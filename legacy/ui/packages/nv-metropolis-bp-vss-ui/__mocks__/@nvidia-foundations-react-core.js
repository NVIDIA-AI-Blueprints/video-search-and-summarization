// SPDX-License-Identifier: MIT
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

module.exports = { Button, TextInput, Select, Tag };
