// SPDX-License-Identifier: MIT
/**
 * Regression guard for the render loop behind the "VLM Verified toggle crashes
 * the Alerts view" bug.
 *
 * When the controls live in the host app's left sidebar, AlertsComponent hands
 * its memoized controls JSX up through `onControlsReady` from an effect and the
 * host stores it in state. If any input to that memo has a fresh identity every
 * render, the effect refires on every render, the host re-renders, and the cycle
 * never settles — in the browser it burns CPU until a synchronously-updating
 * child (the Kaizen VLM verdict Select) turns it into React error #185.
 *
 * These tests use the real hooks, so an unstable handler identity resurfaces
 * here as a bounded-push assertion failure instead of a hung suite.
 */

import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';

import { AlertsComponent } from '../../lib-src/AlertsComponent';

const alertsData: any = {
  apiUrl: 'http://api.test/video-analytics-api',
  vstApiUrl: 'http://api.test/vst/api',
  alertsApiUrl: 'http://api.test/alert-bridge/api/v1',
  maxResults: 100,
  pageSize: 20,
  defaultTimeWindow: 10,
  defaultAutoRefreshInterval: 1000,
  // Matches a deployment with NEXT_PUBLIC_ALERTS_TAB_VERIFIED_FLAG_DEFAULT=false,
  // where the verdict Select mounts only once the toggle is switched on.
  defaultVlmVerified: false,
  maxSearchTimeLimit: 0,
  enableRealtimeAlerts: true,
  enableCvAlertsVerification: true,
};

/** Beyond this many pushes the controls are not settling; stop feeding the cycle. */
const PUSH_BUDGET = 25;

const incident = (index: number) => ({
  Id: `alert-${index}`,
  timestamp: '2026-01-01T00:00:00.000Z',
  end: '2026-01-01T00:00:05.000Z',
  sensorId: `Camera-${index}`,
  category: 'Loitering',
  analyticsModule: { info: { triggerModules: 'Motion' }, description: 'desc' },
});

let pushCount = 0;

/** Stands in for the host app's sidebar (apps/.../Home.tsx + ModeControlsSection). */
const SidebarHost: React.FC = () => {
  const [handlers, setHandlers] = React.useState<any>(null);
  const onControlsReady = React.useCallback((next: any) => {
    pushCount += 1;
    if (pushCount > PUSH_BUDGET) return;
    setHandlers((prev: any) =>
      prev && prev.controlsComponent === next.controlsComponent ? prev : next,
    );
  }, []);

  return (
    <div>
      <aside data-testid="sidebar">{handlers?.controlsComponent}</aside>
      <AlertsComponent
        theme="dark"
        isActive
        alertsData={alertsData}
        renderControlsInLeftSidebar
        onControlsReady={onControlsReady}
      />
    </div>
  );
};

const settle = (milliseconds: number) =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, milliseconds));
  });

beforeEach(() => {
  pushCount = 0;
  global.fetch = jest.fn(async (url: unknown) => {
    if (String(url).includes('/v1/sensor/list')) {
      return {
        ok: true,
        json: async () => [{ name: 'Camera-1', sensorId: 'Camera-1', state: 'online' }],
      } as any;
    }
    return { ok: true, json: async () => ({ incidents: [incident(1), incident(2)] }) } as any;
  }) as any;
});

describe('AlertsComponent sidebar controls', () => {
  it('stops pushing controls to the host once mounted', async () => {
    await act(async () => {
      render(<SidebarHost />);
    });
    await settle(1200);

    expect(pushCount).toBeLessThanOrEqual(PUSH_BUDGET);
  });

  it('settles after VLM Verified is toggled on', async () => {
    await act(async () => {
      render(<SidebarHost />);
    });
    await settle(1200);

    const toggle = screen.getByTestId('vlm-verified-toggle');
    expect(toggle.getAttribute('aria-checked')).toBe('false');

    await act(async () => {
      fireEvent.click(toggle);
    });
    await settle(1200);

    expect(pushCount).toBeLessThanOrEqual(PUSH_BUDGET);
    expect(screen.getByTestId('vlm-verified-toggle').getAttribute('aria-checked')).toBe('true');
    expect(screen.getByTestId('vlm-verdict-select')).toBeTruthy();
  });
});
