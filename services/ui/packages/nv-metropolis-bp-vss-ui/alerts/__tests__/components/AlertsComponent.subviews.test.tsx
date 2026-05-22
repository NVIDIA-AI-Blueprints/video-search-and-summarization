// SPDX-License-Identifier: MIT

import React from 'react';
import { render, screen } from '@testing-library/react';
import { AlertsComponent } from '../../lib-src/AlertsComponent';

jest.mock('@nvidia/foundations-react-core', () => {
  const React = require('react');
  return {
    Button: React.forwardRef(({ children, ...rest }: any, ref: any) =>
      React.createElement('button', { ...rest, ref }, children),
    ),
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
    Switch: React.forwardRef(({ checked, onCheckedChange, ...rest }: any, ref: any) =>
      React.createElement('input', {
        ...rest,
        ref,
        type: 'checkbox',
        checked,
        onChange: (e: any) => onCheckedChange?.(e.target.checked),
      }),
    ),
  };
});

jest.mock('@aiqtoolkit-ui/common', () => ({
  VideoModal: jest.fn(() => null),
  useVideoModal: jest.fn(() => ({
    videoModal: { isOpen: false, videoUrl: '', title: '' },
    openVideoModalFromAlert: jest.fn(),
    closeVideoModal: jest.fn(),
    loadingAlertId: null,
  })),
}));

jest.mock('../../lib-src/hooks/useAlerts', () => ({
  useAlerts: jest.fn(() => ({
    alerts: [],
    loading: false,
    loadingMore: false,
    error: null,
    refetch: jest.fn(),
    loadMoreAlerts: jest.fn(),
    canLoadMore: false,
  })),
}));

jest.mock('../../lib-src/hooks/useFilters', () => ({
  useFilters: jest.fn(() => ({
    addFilter: jest.fn(),
    removeFilter: jest.fn(),
    filteredAlerts: [],
    uniqueValues: {
      sensors: [],
      alertTypes: [],
      alertTriggered: [],
      byVlmVerified: {
        enabled: { alertTypes: [], alertTriggered: [] },
        disabled: { alertTypes: [], alertTriggered: [] },
      },
    },
  })),
  createEmptyFilterState: jest.fn(() => ({
    sensors: new Set(),
    alertTypes: new Set(),
    alertTriggered: new Set(),
  })),
}));

jest.mock('../../lib-src/hooks/useTimeWindow', () => ({
  useTimeWindow: jest.fn(() => ({
    timeWindow: 3600,
    setTimeWindow: jest.fn(),
    showCustomTimeInput: false,
    customTimeValue: '',
    customTimeError: null,
    maxTimeLimitInMinutes: 60,
    handleCustomTimeChange: jest.fn(),
    handleSetCustomTime: jest.fn(),
    handleCancelCustomTime: jest.fn(),
    openCustomTimeInput: jest.fn(),
  })),
}));

jest.mock('../../lib-src/hooks/useAutoRefresh', () => ({
  useAutoRefresh: jest.fn(() => ({
    isEnabled: false,
    interval: 30,
    setInterval: jest.fn(),
    toggleEnabled: jest.fn(),
  })),
}));

jest.mock('../../lib-src/components/CreateAlertRulesView', () => ({
  CreateAlertRulesView: () => <div data-testid="create-alert-rules-view-stub" />,
  triggerRealtimeAddDraft: jest.fn(() => false),
}));

/** jest.setup.js replaces sessionStorage with jest.fn() — wire a real in-memory store. */
const installSessionStorageMock = () => {
  const store = new Map<string, string>();
  (sessionStorage.getItem as jest.Mock).mockImplementation(
    (key: string) => store.get(key) ?? null,
  );
  (sessionStorage.setItem as jest.Mock).mockImplementation((key: string, value: string) => {
    store.set(key, value);
  });
  (sessionStorage.removeItem as jest.Mock).mockImplementation((key: string) => {
    store.delete(key);
  });
  (sessionStorage.clear as jest.Mock).mockImplementation(() => {
    store.clear();
  });
  return store;
};

describe('AlertsComponent sub-views', () => {
  beforeEach(() => {
    installSessionStorageMock();
  });

  it('does not mount Manage Alerts until the user opens that sub-view', () => {
    sessionStorage.setItem('alertsTabView', JSON.stringify('view'));

    render(
      <AlertsComponent
        theme="light"
        isActive
        alertsData={{
          systemStatus: 'active',
          apiUrl: 'http://alerts.example',
          vstApiUrl: 'http://vst.example',
          alertsApiUrl: 'http://bridge.example/api/v1',
        }}
      />,
    );

    expect(screen.queryByTestId('create-alert-rules-view-stub')).not.toBeInTheDocument();
  });

  it('mounts Manage Alerts when that sub-view is the persisted default', () => {
    sessionStorage.setItem('alertsTabView', JSON.stringify('create'));

    render(
      <AlertsComponent
        theme="light"
        isActive
        alertsData={{
          systemStatus: 'active',
          apiUrl: 'http://alerts.example',
          vstApiUrl: 'http://vst.example',
          alertsApiUrl: 'http://bridge.example/api/v1',
        }}
      />,
    );

    expect(screen.getByTestId('create-alert-rules-view-stub')).toBeInTheDocument();
    const managePanel = document.getElementById('alerts-panel-create');
    expect(managePanel).toBeInTheDocument();
  });
});
