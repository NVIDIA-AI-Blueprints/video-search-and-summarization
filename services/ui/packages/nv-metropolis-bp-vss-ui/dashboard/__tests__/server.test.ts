// SPDX-License-Identifier: MIT
/**
 * @jest-environment node
 */

const mockEnvStore: Record<string, string | undefined> = {};

jest.mock('next-runtime-env', () => ({
  env: jest.fn((key: string) => mockEnvStore[key]),
}));

describe('fetchDashboardData', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    jest.resetModules();
    for (const key of Object.keys(mockEnvStore)) {
      delete mockEnvStore[key];
    }
    delete process.env.NEXT_PUBLIC_ENABLE_DASHBOARD_TAB;
    delete process.env.NEXT_PUBLIC_DASHBOARD_TAB_KIBANA_BASE_URL;
    global.fetch = jest.fn() as typeof fetch;
  });

  afterAll(() => {
    global.fetch = originalFetch;
  });

  async function loadFetchDashboardData() {
    const mod = await import('../lib-src/server');
    return mod.fetchDashboardData;
  }

  it('does not call Kibana when dashboard tab is disabled', async () => {
    mockEnvStore.NEXT_PUBLIC_ENABLE_DASHBOARD_TAB = 'false';
    mockEnvStore.NEXT_PUBLIC_DASHBOARD_TAB_KIBANA_BASE_URL = 'http://kibana.example.com';

    const fetchDashboardData = await loadFetchDashboardData();
    const result = await fetchDashboardData();

    expect(global.fetch).not.toHaveBeenCalled();
    expect(result).toEqual({
      systemStatus: 'operational',
      kibanaBaseUrl: null,
      dashboards: [],
    });
  });

  it('fetches dashboards when tab is enabled and Kibana URL is set', async () => {
    mockEnvStore.NEXT_PUBLIC_ENABLE_DASHBOARD_TAB = 'true';
    mockEnvStore.NEXT_PUBLIC_DASHBOARD_TAB_KIBANA_BASE_URL = 'http://kibana.example.com';
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      json: async () => ({
        saved_objects: [{ id: 'dash-1', attributes: { title: 'Ops' } }],
      }),
    });

    const fetchDashboardData = await loadFetchDashboardData();
    const result = await fetchDashboardData();

    expect(global.fetch).toHaveBeenCalledWith(
      'http://kibana.example.com/api/saved_objects/_find?type=dashboard&fields=title&fields=description',
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(result.dashboards).toHaveLength(1);
    expect(result.kibanaBaseUrl).toBe('http://kibana.example.com');
    expect(result.systemStatus).toBe('operational');
  });

  it('returns empty dashboards without fetch when Kibana URL is unset', async () => {
    mockEnvStore.NEXT_PUBLIC_ENABLE_DASHBOARD_TAB = 'true';

    const fetchDashboardData = await loadFetchDashboardData();
    const result = await fetchDashboardData();

    expect(global.fetch).not.toHaveBeenCalled();
    expect(result.dashboards).toEqual([]);
    expect(result.kibanaBaseUrl).toBeNull();
  });

  it('returns empty dashboards when Kibana responds with an error', async () => {
    mockEnvStore.NEXT_PUBLIC_ENABLE_DASHBOARD_TAB = 'true';
    mockEnvStore.NEXT_PUBLIC_DASHBOARD_TAB_KIBANA_BASE_URL = 'http://kibana.example.com';
    const consoleError = jest.spyOn(console, 'error').mockImplementation(() => {});
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: false,
      statusText: 'Service Unavailable',
    });

    const fetchDashboardData = await loadFetchDashboardData();
    const result = await fetchDashboardData();

    expect(result.dashboards).toEqual([]);
    expect(consoleError).toHaveBeenCalledWith(
      'Failed to fetch dashboards: Service Unavailable',
    );
    consoleError.mockRestore();
  });

  it('treats missing ENABLE_DASHBOARD_TAB as enabled (default)', async () => {
    mockEnvStore.NEXT_PUBLIC_DASHBOARD_TAB_KIBANA_BASE_URL = 'http://kibana.example.com';
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      json: async () => ({ saved_objects: [] }),
    });

    const fetchDashboardData = await loadFetchDashboardData();
    await fetchDashboardData();

    expect(global.fetch).toHaveBeenCalled();
  });
});
