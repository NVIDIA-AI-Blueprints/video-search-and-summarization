// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { AlertsSidebarControls } from '../../lib-src/components/AlertsSidebarControls';

jest.mock('@nvidia/foundations-react-core', () => {
  const React = require('react');
  return {
    Select: React.forwardRef(({ items, onValueChange, value, ...rest }: any, ref: any) =>
      React.createElement(
        'select',
        {
          ...rest,
          ref,
          value,
          onChange: (e: any) => onValueChange?.(e.target.value),
        },
        items?.map((item: any) =>
          React.createElement('option', { key: item.value, value: item.value }, item.children),
        ),
      ),
    ),
    Switch: React.forwardRef(({ checked, onCheckedChange, slotLabel, labelSide, ...rest }: any, ref: any) =>
      React.createElement('input', {
        ...rest,
        ref,
        type: 'checkbox',
        checked,
        'aria-label': slotLabel,
        onChange: (e: any) => onCheckedChange?.(e.target.checked),
      }),
    ),
    Tag: React.forwardRef(({ children, ...rest }: any, ref: any) =>
      React.createElement('button', { ...rest, ref, type: 'button' }, children),
    ),
  };
});

const defaultViewProps = {
  isDark: false,
  alertsView: 'view' as const,
  onAlertsViewChange: jest.fn(),
  onAddNewAlertRule: jest.fn(),
  vlmVerified: true,
  vlmVerdict: 'all' as const,
  uniqueValues: {
    sensors: ['Cam-A'],
    alertTypes: ['Tailgating'],
    alertTriggered: ['Motion'],
    byVlmVerified: {
      enabled: { alertTypes: ['Tailgating'], alertTriggered: ['Motion'] },
      disabled: { alertTypes: ['Intrusion'], alertTriggered: ['Thermal'] },
    },
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
  createActiveKind: 'real-time' as const,
  streamFilter: '',
  typeFilter: '',
  onStreamFilterChange: jest.fn(),
  onTypeFilterChange: jest.fn(),
  loading: false,
  autoRefreshEnabled: false,
  autoRefreshInterval: 5000,
  onRefresh: jest.fn(),
  onAutoRefreshToggle: jest.fn(),
  onAutoRefreshIntervalChange: jest.fn(),
};

describe('Alerts Controls', () => {
  it('hides Manage Alerts when every rule kind is disabled', () => {
    render(
      <AlertsSidebarControls
        {...defaultViewProps}
        manageAlertsEnabled={false}
      />,
    );

    expect(screen.getByRole('tab', { name: 'View Alerts' })).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: 'Manage Alerts' })).not.toBeInTheDocument();
    expect(screen.queryByText('Create alert rule')).not.toBeInTheDocument();
  });
});
