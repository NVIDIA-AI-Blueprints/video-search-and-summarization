/**
 * Tests that custom agent parameter values cannot be edited while
 * valuesChangeDisabled is set (e.g. during an in-flight query).
 */

import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import {
  CustomAgentParams,
  type ParamField,
} from '@/components/Chat/CustomAgentParams';

const sampleFields: ParamField[] = [
  {
    id: 'field-string',
    name: 'mode',
    label: 'Mode',
    type: 'string',
    'default-value': 'fast',
    value: 'fast',
  },
  {
    id: 'field-bool',
    name: 'verbose',
    label: 'Verbose',
    type: 'boolean',
    'default-value': false,
    value: false,
  },
];

function renderParams(
  props: Partial<React.ComponentProps<typeof CustomAgentParams>> = {},
) {
  const onFieldsChange = jest.fn();
  const onClose = jest.fn();

  render(
    <CustomAgentParams
      isOpen
      onClose={onClose}
      fields={sampleFields}
      onFieldsChange={onFieldsChange}
      {...props}
    />,
  );

  return { onFieldsChange, onClose };
}

describe('CustomAgentParams – valuesChangeDisabled', () => {
  it('allows editing string and boolean fields when valuesChangeDisabled is false', () => {
    const { onFieldsChange } = renderParams({ valuesChangeDisabled: false });

    const textInput = screen.getByDisplayValue('fast');
    expect(textInput).not.toBeDisabled();
    fireEvent.change(textInput, { target: { value: 'slow' } });
    expect(onFieldsChange).toHaveBeenCalled();

    onFieldsChange.mockClear();
    fireEvent.click(screen.getByRole('button'));
    expect(onFieldsChange).toHaveBeenCalled();
  });

  it('disables all value inputs when valuesChangeDisabled is true', () => {
    renderParams({ valuesChangeDisabled: true });

    expect(screen.getByDisplayValue('fast')).toBeDisabled();
    expect(screen.getByRole('button')).toBeDisabled();
  });

  it('does not call onFieldsChange when valuesChangeDisabled is true', () => {
    const { onFieldsChange } = renderParams({ valuesChangeDisabled: true });

    fireEvent.click(screen.getByRole('button'));
    expect(onFieldsChange).not.toHaveBeenCalled();
  });

  it('still respects per-field changeable: false when valuesChangeDisabled is false', () => {
    const lockedField: ParamField = {
      ...sampleFields[0],
      changeable: false,
    };
    renderParams({
      fields: [lockedField],
      valuesChangeDisabled: false,
    });

    expect(screen.getByDisplayValue('fast')).toBeDisabled();
  });
});
