// SPDX-License-Identifier: MIT
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { FilterControls } from '../../lib-src/components/FilterControls';

jest.mock('@nemo-agent-toolkit/ui');

jest.mock('@nvidia/foundations-react-core', () => {
  const React = require('react');
  return {
    Button: React.forwardRef(({ children, ...rest }: any, ref: any) =>
      React.createElement('button', { ...rest, ref, 'data-foundation': 'Button' }, children),
    ),
    Select: React.forwardRef(({ items, onValueChange, value, ...rest }: any, ref: any) =>
      React.createElement(
        'select',
        {
          ...rest,
          ref,
          'data-foundation': 'Select',
          value,
          onChange: (e: any) => onValueChange?.(e.target.value),
        },
        items?.map((item: any) =>
          React.createElement('option', { key: item.value, value: item.value }, item.children),
        ),
      ),
    ),
    Switch: React.forwardRef(({ checked, onCheckedChange, ...rest }: any, ref: any) =>
      React.createElement('input', {
        ...rest,
        ref,
        type: 'checkbox',
        checked,
        'data-foundation': 'Switch',
        onChange: (e: any) => onCheckedChange?.(e.target.checked),
      }),
    ),
    TextInput: React.forwardRef(({ onValueChange, ...rest }: any, ref: any) =>
      React.createElement('input', {
        ...rest,
        ref,
        'data-foundation': 'TextInput',
        onChange: (e: any) => onValueChange?.(e.target.value),
      }),
    ),
  };
});

const defaultProps = {
  isDark: false,
  loading: false,
  autoRefreshEnabled: false,
  autoRefreshInterval: 5000,
  onRefresh: jest.fn(),
  onAutoRefreshToggle: jest.fn(),
  onAutoRefreshIntervalChange: jest.fn(),
};

describe('FilterControls', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('renders without crashing', () => {
    render(<FilterControls {...defaultProps} />);
    expect(screen.getByTitle('Refresh alerts now')).toBeInTheDocument();
  });

  it('renders auto-refresh control button', () => {
    render(<FilterControls {...defaultProps} />);
    expect(screen.getByTitle('Auto-refresh is off')).toBeInTheDocument();
  });

  it('calls onRefresh when refresh button is clicked', () => {
    const onRefresh = jest.fn();
    render(<FilterControls {...defaultProps} onRefresh={onRefresh} />);

    const refreshButton = screen.getByTitle('Refresh alerts now');
    fireEvent.click(refreshButton);

    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it('shows auto-refresh indicator when enabled', () => {
    render(<FilterControls {...defaultProps} autoRefreshEnabled={true} />);
    expect(screen.getByTestId('auto-refresh-indicator')).toBeInTheDocument();
  });

  it('does not show auto-refresh indicator when disabled', () => {
    render(<FilterControls {...defaultProps} autoRefreshEnabled={false} />);
    expect(screen.queryByTestId('auto-refresh-indicator')).not.toBeInTheDocument();
  });

  it('shows auto-refresh interval in tooltip when enabled', () => {
    render(<FilterControls {...defaultProps} autoRefreshEnabled autoRefreshInterval={5000} />);
    expect(screen.getByTitle('Auto-refresh every 5s')).toBeInTheDocument();
  });

  it('renders with dark theme', () => {
    render(<FilterControls {...defaultProps} isDark={true} />);
    expect(screen.getByTitle('Refresh alerts now')).toBeInTheDocument();
  });
});
