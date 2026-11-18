// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import { applyMapServiceEndpoints, getEnv, isDevelopment, isProduction, vstApiV1Base } from '../../../lib-src/common/shell/js/EnvUtils';

describe('getEnv', () => {
  afterEach(() => {
    delete window.__ENV;
    delete process.env.TEST_VAR;
  });

  it('returns runtime env value from window.__ENV', () => {
    window.__ENV = { TEST_VAR: 'runtime_value' };
    expect(getEnv('TEST_VAR')).toBe('runtime_value');
  });

  it('falls back to process.env when window.__ENV is not set', () => {
    process.env.TEST_VAR = 'build_value';
    expect(getEnv('TEST_VAR')).toBe('build_value');
  });

  it('returns default value when neither source has the variable', () => {
    expect(getEnv('NONEXISTENT', 'fallback')).toBe('fallback');
  });

  it('returns undefined when no value and no default', () => {
    expect(getEnv('NONEXISTENT')).toBeUndefined();
  });

  it('prefers runtime env over build-time env', () => {
    window.__ENV = { TEST_VAR: 'runtime' };
    process.env.TEST_VAR = 'buildtime';
    expect(getEnv('TEST_VAR')).toBe('runtime');
  });

  it('reads the Agent UI video analytics URL as the API base', () => {
    window.__ENV = {
      NEXT_PUBLIC_VIDEO_ANALYTICS_WEB_API_URL: 'http://host:7777/video-analytics-api',
    };
    expect(getEnv('REACT_APP_API_URL_BASE')).toBe('http://host:7777/video-analytics-api');
  });

  it('reads the Agent UI VIOS URL as the media streaming base', () => {
    window.__ENV = {
      NEXT_PUBLIC_VIOS_API_URL: 'http://host:7777/vst/api',
    };
    expect(getEnv('REACT_APP_MEDIA_STREAMING_URL_BASE')).toBe('http://host:7777/vst/api');
  });
});

describe('vstApiV1Base', () => {
  it('appends /v1 to the VIOS root', () => {
    expect(vstApiV1Base('http://host:7777/vst/api')).toBe('http://host:7777/vst/api/v1');
  });

  it('leaves a base that already includes a version', () => {
    expect(vstApiV1Base('http://host:7777/vst/api/v1/')).toBe('http://host:7777/vst/api/v1');
  });
});

describe('isDevelopment', () => {
  it('returns true when NODE_ENV is development', () => {
    const original = process.env.NODE_ENV;
    process.env.NODE_ENV = 'development';
    expect(isDevelopment()).toBe(true);
    process.env.NODE_ENV = original;
  });

  it('returns false when NODE_ENV is not development', () => {
    expect(isDevelopment()).toBe(false);
  });
});

describe('isProduction', () => {
  it('returns true when NODE_ENV is production', () => {
    const original = process.env.NODE_ENV;
    process.env.NODE_ENV = 'production';
    expect(isProduction()).toBe(true);
    process.env.NODE_ENV = original;
  });

  it('returns false when NODE_ENV is not production', () => {
    expect(isProduction()).toBe(false);
  });
});

describe('getEnv with defaultValue', () => {
  afterEach(() => {
    delete window.__ENV;
    delete process.env.TEST_DEFAULT;
  });

  it('returns defaultValue when env is empty string', () => {
    process.env.TEST_DEFAULT = '';
    expect(getEnv('TEST_DEFAULT', 'fallback')).toBe('fallback');
  });

  it('returns runtime value even when defaultValue is provided', () => {
    window.__ENV = { TEST_DEFAULT: 'runtime' };
    expect(getEnv('TEST_DEFAULT', 'fallback')).toBe('runtime');
  });
});

describe('applyMapServiceEndpoints', () => {
  afterEach(() => {
    delete window.__ENV;
  });

  it('copies analytics and VIOS props into the runtime env the map reads', () => {
    window.__ENV = { NEXT_PUBLIC_ENABLE_MAP_TAB: 'true' };
    applyMapServiceEndpoints({
      apiUrl: 'http://host:7777/video-analytics-api',
      mediaStreamingUrl: 'http://host:7777/vst/api/v1',
      googleMapsApiKey: 'maps-key', // pragma: allowlist secret
    });
    expect(getEnv('REACT_APP_API_URL_BASE')).toBe('http://host:7777/video-analytics-api');
    expect(getEnv('REACT_APP_MEDIA_STREAMING_URL_BASE')).toBe('http://host:7777/vst/api');
    expect(getEnv('REACT_APP_GOOGLE_MAPS_API_KEY')).toBe('maps-key');
    expect(window.__ENV.NEXT_PUBLIC_ENABLE_MAP_TAB).toBe('true');
  });
});
