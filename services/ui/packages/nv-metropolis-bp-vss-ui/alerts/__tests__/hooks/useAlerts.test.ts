// SPDX-License-Identifier: MIT
import { renderHook, waitFor, act } from '@testing-library/react';
import {
  useAlerts,
  addMillisecondsIso,
  addOneMillisecondIso,
  subtractMillisecondsIso,
  getMinEndIsoForPaging,
  isoUtcToEpochNanoseconds,
  LOAD_MORE_TO_TIMESTAMP_SUBTRACT_MS,
} from '../../lib-src/hooks/useAlerts';
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

describe('isoUtcToEpochNanoseconds', () => {
  it('preserves sub-millisecond fractional seconds (no float rounding in comparison)', () => {
    const a = isoUtcToEpochNanoseconds('2026-01-01T00:00:00.100Z')!;
    const b = isoUtcToEpochNanoseconds('2026-01-01T00:00:00.0999Z')!;
    expect(b < a).toBe(true);
  });

  it('parses +00:00 like Z', () => {
    const z = isoUtcToEpochNanoseconds('2026-01-01T00:00:00.000Z');
    const p = isoUtcToEpochNanoseconds('2026-01-01T00:00:00.000+00:00');
    expect(z).toBe(p);
  });
});

describe('getMinEndIsoForPaging', () => {
  it('returns smallest end', () => {
    const loaded = [
      { id: '1', end: '2026-01-02T00:00:00Z', timestamp: '', sensor: '', alertType: '', alertTriggered: '', alertDescription: '', metadata: {} },
      { id: '2', end: '2026-01-01T00:00:00Z', timestamp: '', sensor: '', alertType: '', alertTriggered: '', alertDescription: '', metadata: {} },
    ] as any[];
    expect(getMinEndIsoForPaging(loaded)).toBe('2026-01-01T00:00:00Z');
  });

  it('returns verbatim smallest end without rounding fractional seconds', () => {
    const loaded = [
      { id: '1', end: '2026-01-01T00:00:00.100Z', timestamp: '', sensor: '', alertType: '', alertTriggered: '', alertDescription: '', metadata: {} },
      { id: '2', end: '2026-01-01T00:00:00.0999Z', timestamp: '', sensor: '', alertType: '', alertTriggered: '', alertDescription: '', metadata: {} },
    ] as any[];
    expect(getMinEndIsoForPaging(loaded)).toBe('2026-01-01T00:00:00.0999Z');
  });

  it('falls back to smallest timestamp when no end', () => {
    const loaded = [
      { id: '1', end: '', timestamp: '2026-01-03T00:00:00Z', sensor: '', alertType: '', alertTriggered: '', alertDescription: '', metadata: {} },
      { id: '2', end: '', timestamp: '2026-01-01T00:00:00Z', sensor: '', alertType: '', alertTriggered: '', alertDescription: '', metadata: {} },
    ] as any[];
    expect(getMinEndIsoForPaging(loaded)).toBe('2026-01-01T00:00:00Z');
  });
});

describe('addMillisecondsIso', () => {
  it('adds whole milliseconds', () => {
    expect(addMillisecondsIso('2026-01-01T00:00:00.000Z', 10)).toBe('2026-01-01T00:00:00.010Z');
  });

  it('returns null for non-positive or non-integer delta', () => {
    expect(addMillisecondsIso('2026-01-01T00:00:00.000Z', 0)).toBeNull();
    expect(addMillisecondsIso('2026-01-01T00:00:00.000Z', -1)).toBeNull();
    expect(addMillisecondsIso('2026-01-01T00:00:00.000Z', 1.5)).toBeNull();
  });
});

describe('subtractMillisecondsIso', () => {
  it('subtracts whole milliseconds', () => {
    expect(subtractMillisecondsIso('2026-01-01T00:00:00.010Z', 10)).toBe('2026-01-01T00:00:00.000Z');
  });

  it('returns null for non-positive or non-integer delta', () => {
    expect(subtractMillisecondsIso('2026-01-01T00:00:00.000Z', 0)).toBeNull();
    expect(subtractMillisecondsIso('2026-01-01T00:00:00.000Z', -1)).toBeNull();
    expect(subtractMillisecondsIso('2026-01-01T00:00:00.000Z', 1.5)).toBeNull();
  });

  it('returns null when result would be before epoch', () => {
    expect(subtractMillisecondsIso('1970-01-01T00:00:00.005Z', 10)).toBeNull();
  });
});

describe('addOneMillisecondIso', () => {
  it('returns ISO string one ms later', () => {
    expect(addOneMillisecondIso('2026-01-01T00:00:00.000Z')).toBe('2026-01-01T00:00:00.001Z');
  });

  it('advances from sub-millisecond instants past the same wall-clock millisecond (ceil when needed)', () => {
    expect(addOneMillisecondIso('2026-01-01T00:00:00.9999Z')).toBe('2026-01-01T00:00:01.001Z');
  });

  it('returns null for invalid input', () => {
    expect(addOneMillisecondIso('')).toBeNull();
    expect(addOneMillisecondIso('not-a-date')).toBeNull();
  });
});

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

  it('loadMoreAlerts uses fromTimestamp = now - period and toTimestamp = min(loaded end) - LOAD_MORE_TO_TIMESTAMP_SUBTRACT_MS', async () => {
    const baseMs = Date.now();
    // Keep every `end` strictly after (now - 10m): min end = baseMs - 9min - 499ms
    const incidents = Array.from({ length: 500 }, (_, i) => {
      const endMs = baseMs - (9 * 60 * 1000 + i);
      return {
        Id: `i-${i}`,
        timestamp: new Date(endMs - 2000).toISOString(),
        end: new Date(endMs).toISOString(),
        sensorId: 's',
        category: 'c',
      };
    });
    const minEndIso = incidents[499].end;
    const expectedTo = subtractMillisecondsIso(minEndIso, LOAD_MORE_TO_TIMESTAMP_SUBTRACT_MS);

    global.fetch = jest.fn().mockImplementation((url: string) => {
      if (url.includes('/v1/sensor/list')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ incidents }),
      });
    });

    const { result } = renderHook(() =>
      useAlerts({
        apiUrl: 'http://api.test',
        vstApiUrl: 'http://vst.test',
        maxResults: 500,
        timeWindow: 10,
      }),
    );

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.canLoadMore).toBe(true);

    const firstIncidentUrl = (global.fetch as jest.Mock).mock.calls
      .map((c: unknown[]) => c[0] as string)
      .find((u) => u.includes('/incidents'))!;
    const firstUrl = new URL(firstIncidentUrl);
    const firstFrom = firstUrl.searchParams.get('fromTimestamp')!;

    await act(async () => {
      await result.current.loadMoreAlerts();
    });

    const incidentUrls = (global.fetch as jest.Mock).mock.calls
      .map((c: unknown[]) => c[0] as string)
      .filter((u) => u.includes('/incidents'));
    expect(incidentUrls.length).toBeGreaterThanOrEqual(2);
    const loadMoreUrl = new URL(incidentUrls[incidentUrls.length - 1]);
    const loadMoreFrom = loadMoreUrl.searchParams.get('fromTimestamp')!;
    expect(Math.abs(Date.parse(loadMoreFrom) - Date.parse(firstFrom))).toBeLessThan(3000);
    expect(loadMoreUrl.searchParams.get('toTimestamp')).toBe(expectedTo);
    expect(loadMoreUrl.searchParams.get('maxResultSize')).toBe('500');
  });

  it('refetch merges primary batch with load-more rows when search params are unchanged', async () => {
    const baseMs = Date.now();
    const endA = new Date(baseMs - 6 * 60 * 1000).toISOString();
    const endB = new Date(baseMs - 5 * 60 * 1000).toISOString();
    const endC = new Date(baseMs - 8 * 60 * 1000).toISOString();
    const endD = new Date(baseMs - 7 * 60 * 1000).toISOString();

    let incidentCalls = 0;
    global.fetch = jest.fn().mockImplementation((url: string) => {
      if (url.includes('/v1/sensor/list')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve([]) });
      }
      if (url.includes('/incidents')) {
        incidentCalls += 1;
        if (incidentCalls === 1) {
          return Promise.resolve({
            ok: true,
            json: () =>
              Promise.resolve({
                incidents: [
                  {
                    Id: 'a',
                    timestamp: new Date(baseMs - 6 * 60 * 1000 - 1000).toISOString(),
                    end: endA,
                    sensorId: 's',
                    category: 'c',
                  },
                  {
                    Id: 'b',
                    timestamp: new Date(baseMs - 5 * 60 * 1000 - 1000).toISOString(),
                    end: endB,
                    sensorId: 's',
                    category: 'c',
                  },
                ],
              }),
          });
        }
        if (incidentCalls === 2) {
          return Promise.resolve({
            ok: true,
            json: () =>
              Promise.resolve({
                incidents: [
                  {
                    Id: 'c',
                    timestamp: new Date(baseMs - 8 * 60 * 1000 - 1000).toISOString(),
                    end: endC,
                    sensorId: 's',
                    category: 'c',
                  },
                  {
                    Id: 'd',
                    timestamp: new Date(baseMs - 7 * 60 * 1000 - 1000).toISOString(),
                    end: endD,
                    sensorId: 's',
                    category: 'c',
                  },
                ],
              }),
          });
        }
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              incidents: [
                {
                  Id: 'a',
                  timestamp: new Date(baseMs - 6 * 60 * 1000 - 1000).toISOString(),
                  end: endA,
                  sensorId: 's',
                  category: 'c',
                },
                {
                  Id: 'b',
                  timestamp: new Date(baseMs - 5 * 60 * 1000 - 1000).toISOString(),
                  end: endB,
                  sensorId: 's',
                  category: 'c',
                },
              ],
            }),
        });
      }
      return Promise.reject(new Error('unexpected url'));
    });

    const { result } = renderHook(() =>
      useAlerts({ apiUrl: 'http://api.test', vstApiUrl: 'http://vst.test', maxResults: 2 }),
    );

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.alerts).toHaveLength(2);

    await act(async () => {
      await result.current.loadMoreAlerts();
    });
    expect(result.current.alerts).toHaveLength(4);

    await act(async () => {
      await result.current.refetch();
    });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.alerts.map((a) => a.id).sort()).toEqual(['a', 'b']);
  });
});
