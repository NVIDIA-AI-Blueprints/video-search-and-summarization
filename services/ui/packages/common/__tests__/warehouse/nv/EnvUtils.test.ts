// SPDX-FileCopyrightText: Copyright (c) 2009-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
// @ts-nocheck
import { getEnv, isDevelopment, isProduction, isObtainDynamicServiceUrls } from '../../../lib-src/warehouse/nv/js/EnvUtils';

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

describe('isObtainDynamicServiceUrls', () => {
  afterEach(() => {
    delete window.__ENV;
    delete process.env.REACT_APP_OBTAIN_DYNAMIC_SERVICE_URLS;
  });

  it('returns true when env is "true"', () => {
    process.env.REACT_APP_OBTAIN_DYNAMIC_SERVICE_URLS = 'true';
    expect(isObtainDynamicServiceUrls()).toBe(true);
  });

  it('returns false when env is not set', () => {
    expect(isObtainDynamicServiceUrls()).toBe(false);
  });

  it('returns false when env is "false"', () => {
    process.env.REACT_APP_OBTAIN_DYNAMIC_SERVICE_URLS = 'false';
    expect(isObtainDynamicServiceUrls()).toBe(false);
  });
});
