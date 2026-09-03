// SPDX-License-Identifier: MIT
import React from 'react';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { AlertsViewFilterControls } from '../../lib-src/components/AlertsSidebarControls';
import { VLM_VERDICT } from '../../lib-src/types';

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
    Tag: React.forwardRef(({ children, ...rest }: any, ref: any) =>
      React.createElement('button', { ...rest, ref, 'data-foundation': 'Tag', type: 'button' }, children),
    ),
  };
});

const defaultProps = {
  isDark: false,
  vlmVerified: true,
  vlmVerdict: VLM_VERDICT.ALL,
  uniqueValues: {
    sensors: ['Cam-A'],
    alertTypes: ['Tailgating'],
    alertTriggered: ['Motion'],
  },
  onVlmVerifiedChange: jest.fn(),
  onVlmVerdictChange: jest.fn(),
  onAddFilter: jest.fn(),
  activeFilters: {
    sensors: new Set<string>(),
    alertTypes: new Set<string>(),
    alertTriggered: new Set<string>(),
  },
  onRemoveFilter: jest.fn(),
  onClearAllFilters: jest.fn(),
  timeWindow: 10,
  showCustomTimeInput: false,
  customTimeValue: '',
  customTimeError: '',
  onTimeWindowChange: jest.fn(),
  onCustomTimeValueChange: jest.fn(),
  onCustomTimeApply: jest.fn(),
  onCustomTimeCancel: jest.fn(),
  onOpenCustomTime: jest.fn(),
  fetchSize: 100,
  onFetchSizeChange: jest.fn(),
  loading: false,
  autoRefreshEnabled: false,
  autoRefreshInterval: 5000,
  onRefresh: jest.fn(),
  onAutoRefreshToggle: jest.fn(),
  onAutoRefreshIntervalChange: jest.fn(),
};

describe('View Alerts sidebar refresh controls', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('renders without crashing', () => {
    render(<AlertsViewFilterControls {...defaultProps} />);
    expect(screen.getByTitle('Refresh alerts now')).toBeInTheDocument();
  });

  it('groups Refresh now and Auto Refresh settings under Auto Refresh, after Fetch Settings', () => {
    render(<AlertsViewFilterControls {...defaultProps} />);

    expect(screen.getByText('Fetch Settings')).toBeInTheDocument();

    const group = screen.getByTestId('alerts-auto-refresh-group');
    expect(within(group).getByText('Settings')).toBeInTheDocument();
    expect(group).toContainElement(screen.getByTitle('Refresh alerts now'));
    expect(group).toContainElement(screen.getByTestId('alerts-auto-refresh-toggle'));
    expect(screen.getByTestId('alerts-settings-toggle')).not.toContainElement(group);

    fireEvent.click(screen.getByTestId('alerts-auto-refresh-toggle'));
    expect(screen.getByText('Refresh Interval')).toBeInTheDocument();
  });

  it('calls onRefresh when refresh button is clicked', () => {
    const onRefresh = jest.fn();
    render(<AlertsViewFilterControls {...defaultProps} onRefresh={onRefresh} />);

    const refreshButton = screen.getByTitle('Refresh alerts now');
    fireEvent.click(refreshButton);

    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it('shows auto-refresh indicator when enabled', () => {
    render(<AlertsViewFilterControls {...defaultProps} autoRefreshEnabled={true} />);
    expect(screen.getByTestId('auto-refresh-indicator')).toBeInTheDocument();
  });

  it('does not show auto-refresh indicator when disabled', () => {
    render(<AlertsViewFilterControls {...defaultProps} autoRefreshEnabled={false} />);
    expect(screen.queryByTestId('auto-refresh-indicator')).not.toBeInTheDocument();
  });

  it('shows auto-refresh interval in tooltip when enabled', () => {
    render(<AlertsViewFilterControls {...defaultProps} autoRefreshEnabled autoRefreshInterval={5000} />);
    expect(screen.getByTitle('Auto-refresh every 5s')).toBeInTheDocument();
  });

  it('renders with dark theme', () => {
    render(<AlertsViewFilterControls {...defaultProps} isDark={true} />);
    expect(screen.getByTitle('Refresh alerts now')).toBeInTheDocument();
  });
});
