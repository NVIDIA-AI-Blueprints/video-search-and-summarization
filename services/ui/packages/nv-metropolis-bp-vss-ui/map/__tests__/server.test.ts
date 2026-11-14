// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

const envStore: Record<string, string | undefined> = {};

jest.mock('next-runtime-env', () => ({
  env: (key: string) => envStore[key],
}));

describe('fetchMapData', () => {
  const originalEnv = process.env;

  beforeEach(() => {
    for (const key of Object.keys(envStore)) {
      delete envStore[key];
    }
    process.env = { ...originalEnv };
    delete process.env.NEXT_PUBLIC_MAP_TAB_APP;
    delete process.env.NEXT_PUBLIC_WAREHOUSE_UI_CONFIG_URL;
    delete process.env.NEXT_PUBLIC_TRAFFIC_LIGHT_UI_CONFIG_URL;
    jest.resetModules();
  });

  afterAll(() => {
    process.env = originalEnv;
  });

  it('returns warehouse ui config by default', async () => {
    const { fetchMapData } = await import('../lib-src/server');
    const data = await fetchMapData();
    expect(data.mapTabApp).toBe('warehouse');
    expect(data.uiConfigUrl).toBe('/warehouse-analytics/default-ui-config.json');
  });

  it('returns traffic-light ui config when MAP_TAB_APP is traffic-light', async () => {
    envStore.NEXT_PUBLIC_MAP_TAB_APP = 'traffic-light';
    const { fetchMapData } = await import('../lib-src/server');
    const data = await fetchMapData();
    expect(data.mapTabApp).toBe('traffic-light');
    expect(data.uiConfigUrl).toBe('/traffic-light/default-ui-config.json');
  });
});
