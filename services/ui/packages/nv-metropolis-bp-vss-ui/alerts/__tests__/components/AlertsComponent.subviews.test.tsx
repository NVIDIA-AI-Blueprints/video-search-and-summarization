// SPDX-License-Identifier: MIT

import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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

jest.mock('common', () => ({
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
  CreateAlertRulesView: ({
    activeKind,
    onActiveKindChange,
    enableRealtimeAlerts,
    enableCvAlertsVerification,
  }: {
    activeKind: string;
    onActiveKindChange: (kind: string) => void;
    enableRealtimeAlerts: boolean;
    enableCvAlertsVerification: boolean;
  }) => (
    <div
      data-testid="create-alert-rules-view-stub"
      data-kind={activeKind}
      data-realtime-enabled={String(enableRealtimeAlerts)}
      data-cv-enabled={String(enableCvAlertsVerification)}
    >
      <button onClick={() => onActiveKindChange('verification')}>Select verification</button>
    </div>
  ),
  triggerRealtimeAddDraft: jest.fn(() => false),
}));

jest.mock('../../lib-src/components/CvAlertsVerificationTab', () => ({
  triggerVerificationAddDraft: jest.fn(() => false),
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

  it('restores and persists the selected Manage Alerts kind', async () => {
    sessionStorage.setItem('alertsTabView', JSON.stringify('create'));
    sessionStorage.setItem('alertsTabRulesKind', JSON.stringify('verification'));

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

    await waitFor(() =>
      expect(screen.getByTestId('create-alert-rules-view-stub')).toHaveAttribute(
        'data-kind',
        'verification',
      ),
    );
    fireEvent.click(screen.getByText('Select verification'));
    await waitFor(() =>
      expect(sessionStorage.setItem).toHaveBeenCalledWith(
        'alertsTabRulesKind',
        JSON.stringify('verification'),
      ),
    );
  });

  it('falls back to CV verification when real-time rules are disabled', async () => {
    sessionStorage.setItem('alertsTabView', JSON.stringify('create'));
    sessionStorage.setItem('alertsTabRulesKind', JSON.stringify('real-time'));

    render(
      <AlertsComponent
        theme="light"
        isActive
        alertsData={{
          systemStatus: 'active',
          apiUrl: 'http://alerts.example',
          alertsApiUrl: 'http://bridge.example/api/v1',
          enableRealtimeAlerts: false,
          enableCvAlertsVerification: true,
        }}
      />,
    );

    await waitFor(() =>
      expect(screen.getByTestId('create-alert-rules-view-stub')).toHaveAttribute(
        'data-kind',
        'verification',
      ),
    );
    expect(screen.getByTestId('create-alert-rules-view-stub')).toHaveAttribute(
      'data-realtime-enabled',
      'false',
    );
  });

  it('does not mount Manage Alerts when both rule kinds are disabled', () => {
    sessionStorage.setItem('alertsTabView', JSON.stringify('create'));

    render(
      <AlertsComponent
        theme="light"
        isActive
        alertsData={{
          systemStatus: 'active',
          apiUrl: 'http://alerts.example',
          enableRealtimeAlerts: false,
          enableCvAlertsVerification: false,
        }}
      />,
    );

    expect(screen.queryByTestId('create-alert-rules-view-stub')).not.toBeInTheDocument();
    expect(document.getElementById('alerts-panel-create')).not.toBeInTheDocument();
  });
});
