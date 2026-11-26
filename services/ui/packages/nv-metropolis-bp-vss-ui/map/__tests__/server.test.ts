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

  it('uses the Agent UI video analytics URL when no warehouse override is set', async () => {
    envStore.NEXT_PUBLIC_VIDEO_ANALYTICS_WEB_API_URL = 'http://host:7777/video-analytics-api';
    const { fetchMapData } = await import('../lib-src/server');
    const data = await fetchMapData();
    expect(data.apiUrl).toBe('http://host:7777/video-analytics-api');
  });

  it('builds the media streaming URL from the VIOS root', async () => {
    envStore.NEXT_PUBLIC_MAP_TAB_APP = 'traffic-light';
    envStore.NEXT_PUBLIC_VIOS_API_URL = 'http://host:7777/vst/api';
    const { fetchMapData } = await import('../lib-src/server');
    const data = await fetchMapData();
    expect(data.mediaStreamingUrl).toBe('http://host:7777/vst/api/v1');
  });
});
