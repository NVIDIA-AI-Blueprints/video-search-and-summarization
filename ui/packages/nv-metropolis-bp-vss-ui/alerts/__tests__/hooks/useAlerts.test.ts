// SPDX-License-Identifier: MIT
import { renderHook, waitFor } from '@testing-library/react';
import { useAlerts } from '../../lib-src/hooks/useAlerts';
import { VLM_VERDICT } from '../../lib-src/types';

const mockFetchResponse = (data: any, ok = true, status = 200) =>
  jest.fn().mockResolvedValue({
    ok,
    status,
    json: () => Promise.resolve(data),
    text: () => Promise.resolve(JSON.stringify(data)),
  });

const mockSensors = [
  { name: 'Cam-A', sensorId: 'id-a', state: 'online' },
  { name: 'Cam-B', sensorId: 'id-b', state: 'online' },
  { name: 'Cam-C', sensorId: 'id-c', state: 'offline' },
];

const mockIncidents = {
  incidents: [
    {
      Id: 'inc-1',
      timestamp: '2024-01-15T09:00:00Z',
      end: '2024-01-15T09:05:00Z',
      sensorId: 'cam-1',
      category: 'Tailgating',
      analyticsModule: {
        info: { triggerModules: 'Motion Detected' },
        description: 'Tailgating at entrance',
      },
    },
    {
      uniqueId: 'inc-2',
      timestamp: '2024-01-15T10:00:00Z',
      end: '2024-01-15T10:02:00Z',
      sensorId: 'cam-2',
      category: 'Loitering',
    },
  ],
};

describe('useAlerts', () => {
  let originalFetch: typeof global.fetch;

  beforeEach(() => {
    originalFetch = global.fetch;
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it('sets error when apiUrl is not provided', async () => {
    global.fetch = mockFetchResponse({ incidents: [] });

    const { result } = renderHook(() => useAlerts({ apiUrl: undefined }));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.error).toContain('API URL is not configured');
    expect(result.current.alerts).toEqual([]);
  });

  it('fetches and transforms alerts', async () => {
    // First call: sensor list, second call: incidents
    let callCount = 0;
    global.fetch = jest.fn().mockImplementation(() => {
      callCount++;
      if (callCount === 1) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSensors) });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(mockIncidents) });
    });

    const { result } = renderHook(() =>
      useAlerts({ apiUrl: 'http://api.test', vstApiUrl: 'http://vst.test', timeWindow: 10 })
    );

    await waitFor(() => {
      expect(result.current.alerts).toHaveLength(2);
    });

    expect(result.current.alerts[0].id).toBe('inc-1');
    expect(result.current.alerts[0].alertType).toBe('Tailgating');
    expect(result.current.alerts[0].alertTriggered).toBe('Motion Detected');
    expect(result.current.alerts[0].alertDescription).toBe('Tailgating at entrance');

    expect(result.current.alerts[1].id).toBe('inc-2');
    expect(result.current.alerts[1].alertType).toBe('Loitering');
    expect(result.current.alerts[1].alertTriggered).toBe('');
    expect(result.current.alerts[1].alertDescription).toBe('');

    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it('fetches sensor list and builds sensor map', async () => {
    global.fetch = jest.fn().mockImplementation((url: string) => {
      if (url.includes('/v1/sensor/list')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSensors) });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ incidents: [] }) });
    });

    const { result } = renderHook(() =>
      useAlerts({ apiUrl: 'http://api.test', vstApiUrl: 'http://vst.test' })
    );

    await waitFor(() => {
      expect(result.current.sensorList).toHaveLength(2);
    });

    expect(result.current.sensorMap.get('Cam-A')).toBe('id-a');
    expect(result.current.sensorMap.get('Cam-B')).toBe('id-b');
    expect(result.current.sensorMap.has('Cam-C')).toBe(false); // offline
    expect(result.current.sensorList).toEqual(['Cam-A', 'Cam-B']);
  });

  it('handles fetch error for alerts', async () => {
    global.fetch = jest.fn().mockImplementation((url: string) => {
      if (url.includes('/v1/sensor/list')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      }
      return Promise.resolve({ ok: false, status: 500 });
    });
    const consoleSpy = jest.spyOn(console, 'error').mockImplementation();

    const { result } = renderHook(() =>
      useAlerts({ apiUrl: 'http://api.test', vstApiUrl: 'http://vst.test' })
    );

    await waitFor(() => {
      expect(result.current.error).toContain('HTTP error');
    });

    expect(result.current.loading).toBe(false);
    consoleSpy.mockRestore();
  });

  it('builds URL with vlmVerified and vlmVerdict params', async () => {
    global.fetch = jest.fn().mockImplementation((url: string) => {
      if (url.includes('/v1/sensor/list')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ incidents: [] }) });
    });

    renderHook(() =>
      useAlerts({
        apiUrl: 'http://api.test',
        vstApiUrl: 'http://vst.test',
        vlmVerified: true,
        vlmVerdict: VLM_VERDICT.CONFIRMED,
        timeWindow: 30,
      })
    );

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalled();
    });

    const incidentCall = (global.fetch as jest.Mock).mock.calls.find(
      (c: any) => c[0].includes('/incidents')
    );
    expect(incidentCall).toBeTruthy();
    const url = incidentCall[0];
    expect(url).toContain('vlmVerified=true');
    expect(url).toContain('vlmVerdict=confirmed');
  });

  it('does not append vlmVerdict when it is ALL', async () => {
    global.fetch = jest.fn().mockImplementation((url: string) => {
      if (url.includes('/v1/sensor/list')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ incidents: [] }) });
    });

    renderHook(() =>
      useAlerts({
        apiUrl: 'http://api.test',
        vlmVerified: true,
        vlmVerdict: VLM_VERDICT.ALL,
      })
    );

    await waitFor(() => {
      const incidentCall = (global.fetch as jest.Mock).mock.calls.find(
        (c: any) => c[0].includes('/incidents')
      );
      expect(incidentCall).toBeTruthy();
    });

    const incidentCall = (global.fetch as jest.Mock).mock.calls.find(
      (c: any) => c[0].includes('/incidents')
    );
    expect(incidentCall[0]).not.toContain('vlmVerdict=');
  });

  it('builds URL with queryString from active filters', async () => {
    global.fetch = jest.fn().mockImplementation((url: string) => {
      if (url.includes('/v1/sensor/list')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ incidents: [] }) });
    });

    renderHook(() =>
      useAlerts({
        apiUrl: 'http://api.test',
        activeFilters: {
          sensors: new Set(['cam-1']),
          alertTypes: new Set(['Loitering']),
          alertTriggered: new Set(),
        },
      })
    );

    await waitFor(() => {
      const incidentCall = (global.fetch as jest.Mock).mock.calls.find(
        (c: any) => c[0].includes('/incidents')
      );
      expect(incidentCall).toBeTruthy();
    });

    const incidentCall = (global.fetch as jest.Mock).mock.calls.find(
      (c: any) => c[0].includes('/incidents')
    );
    expect(incidentCall[0]).toContain('queryString=');
  });

  it('does not fetch sensor list when vstApiUrl is not provided', async () => {
    global.fetch = jest.fn().mockImplementation(() =>
      Promise.resolve({ ok: true, json: () => Promise.resolve({ incidents: [] }) })
    );

    const { result } = renderHook(() => useAlerts({ apiUrl: 'http://api.test' }));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    const sensorCalls = (global.fetch as jest.Mock).mock.calls.filter(
      (c: any) => c[0].includes('/v1/sensor/list')
    );
    expect(sensorCalls).toHaveLength(0);
  });
});
